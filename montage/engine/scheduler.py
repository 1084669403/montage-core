"""scheduler — 跨天断点续跑调度 + 档位池（v8.2 P0-scheduler）。

长片跨天跑：tokenplan（500s/天 日配额，5 RPM）优先耗尽，随后确定性切到
池内下一档（default 免费 @1RPM 无日限），当天能拍多少拍多少；次日配额归零
后 ``restore`` 回主档。三件事全部确定性、无 LLM：

- ``day_capacity``：跑前盘点池内各档 remaining（视频秒 / 图片张），决定当日批次
- ``pick_next_tier``：当前档耗尽时按池序给下一候选（只读，不落任何状态）
- ``activate_tier`` / ``restore_tier``：进程内切档（写 ``AGNES_ACCESS_TYPE``），
  ``agnes_access_tier()``、``agnes_usage`` 按档记账、``agnes_credentials()``
  取键、RPM（``agnes_video_rpm``）全部经环境变量自动跟档

设计对齐既有基建：配额记账走 ``agnes_usage``（tier 分桶），缓存命中不耗配额
（``generation_cache``），停点续跑靠 ``produce`` 进度文件——本模块只做
「这一镜用哪个档发请求」的确定性决策，不发明 --autopilot。
"""

from __future__ import annotations

import os
from typing import Any

# 池顺序即切换顺序：tokenplan（有日限、RPM 高）先耗尽，default（免费、无日限）
# 兜底。与 agnes.AGNES_TIER_POOL 同源——这里复制常量而非 import，避免
# engine → providers.agnes 的新依赖边（capabilities 已是既有依赖）。
TIER_POOL: tuple[str, ...] = ("tokenplan", "default")

_ACCESS_ENV = "AGNES_ACCESS_TYPE"
# tokenplan 日配额（官方 FAQ：视频 500s/天、图片 4000 张/天）；default 无日限。
_DAILY_LIMITS: dict[str, dict[str, float | None]] = {
    "tokenplan": {"video": 500.0, "image": 4000.0},
    "default": {"video": None, "image": None},
}


def _quota_status(tier: str, kind: str) -> tuple[float, float | None]:
    """读 agnes_usage 当日用量与该档限额；失败静默降级为 (0, None)。"""
    try:
        from montage.providers.agnes_usage import quota_status

        used, limit, _remaining = quota_status(tier, kind)
        return float(used or 0.0), (float(limit) if limit is not None else None)
    except Exception:  # noqa: BLE001
        return 0.0, None


def day_capacity(tier: str | None = None) -> list[dict[str, Any]]:
    """跑前盘点：池内每档的当日 remaining（视频秒 / 图片张）。

    返回按池序排列的行：``{tier, video_remaining, image_remaining, unlimited}``。
    default（无日限档）remaining 记 None、``unlimited=True``——批排量级口径：
    剩余视频秒无限 = 当日还能跑章节拆批的整章。
    """
    start = str(tier or "").strip().lower() or _current_tier()
    rows: list[dict[str, Any]] = []
    for t in [start] + [x for x in TIER_POOL if x != start]:
        v_used, v_limit = _quota_status(t, "video")
        i_used, i_limit = _quota_status(t, "image")
        unlimited = t not in _DAILY_LIMITS or _DAILY_LIMITS[t]["video"] is None
        rows.append({
            "tier": t,
            "video_used": v_used,
            "video_limit": v_limit,
            "video_remaining": (max(0.0, v_limit - v_used) if v_limit is not None else None),
            "image_used": i_used,
            "image_limit": i_limit,
            "image_remaining": (max(0.0, i_limit - i_used) if i_limit is not None else None),
            "unlimited": bool(unlimited),
        })
    return rows


def tier_has_video_quota(tier: str, seconds: float) -> bool:
    """该档视频余量是否够本镜；无日限档恒 True（图片侧同理不拦截）。"""
    _used, limit = _quota_status(tier, "video")
    return limit is None or limit - _used >= max(0.0, seconds)


def tier_exhausted(tier: str) -> bool:
    """该档视频日配额是否已耗尽（有日限且余量 ≤ 0）；无日限档恒 False。

    RPM 限流的瞬时 429 不是耗尽——切档只看本地配额账（agnes_usage），
    不看 HTTP 状态码，避免把限流抖动误判成日配额用完。
    """
    used, limit = _quota_status(tier, "video")
    return limit is not None and limit - used <= 0.0


def pick_next_tier(tier: str | None = None) -> str | None:
    """当前档耗尽时给池内下一候选；池尽（都耗尽/无候选）返回 None。

    判定口径：候选档视频侧「无日限」或「剩余 > 0」即可作为下一档——
    到达后 agnes_usage 按新档记账，本镜实际能不能拍由 caller 结合
    ``tier_has_video_quota`` 再判。只读，不写任何状态。
    """
    start = str(tier or "").strip().lower() or _current_tier()
    for cand in [x for x in TIER_POOL if x != start]:
        _used, limit = _quota_status(cand, "video")
        if limit is None or limit - _used > 0.0:
            return cand
    return None


def _current_tier() -> str:
    try:
        from montage.providers.capabilities import agnes_access_tier

        return agnes_access_tier()
    except Exception:  # noqa: BLE001
        return "default"


def activate_tier(tier: str) -> bool:
    """进程内切档：写 AGNES_ACCESS_TYPE。返回是否真的切换（含同档 False）。"""
    t = str(tier or "").strip().lower()
    if not t or t == _current_tier():
        return False
    os.environ[_ACCESS_ENV] = t
    return True


def restore_tier(default_tier: str = "") -> bool:
    """恢复主档（``AGNES_ACCESS_TYPE`` 的声明值）。返回是否真的切换。

    scheduler 切档只在进程内生效（os.environ），不回写 .env / project.json；
    恢复即把 env 还原为用户声明值，声明为空则移除变量（回 default）。
    """
    declared = str(os.environ.get("AGNES_ACCESS_TYPE_DECLARED") or "").strip().lower()
    if not declared:
        # .env 注入不区分原名/声明名：直接读当前值再判断是否 scheduler 改的。
        # 声明值由 caller（shot_runner 入口）在切档前快照进 _ACCESS_DECLARED。
        declared = str(os.environ.get("MONTAGE_TIER_DECLARED") or "").strip().lower()
    if not declared:
        declared = str(default_tier or "").strip().lower()
    current = _current_tier()
    if not declared or declared == current:
        return False
    os.environ[_ACCESS_ENV] = declared
    return True


def tier_note(tier: str, seconds: float = 0.0) -> str:
    """人读摘要：切档 finding / review_card 用。"""
    _used, limit = _quota_status(tier, "video")
    if limit is None:
        return f"档位 {tier}（无日限）"
    left = max(0.0, limit - _used)
    extra = f"，余 {left:g}s" if seconds <= 0 else f"，余 {left:g}s / 本镜 {seconds:g}s"
    return f"档位 {tier}（日限 {limit:g}s{extra}）"
