"""duration_advisor — 时长闭环工具（V19 双路径 + recommend；无 LLM 纯计算）。

方案定位：编剧自审与导演审的共同输入（V7）；时长贴不入导演评分，只作客观校验。

operation:
- estimate：对 series_bible 逐场估时。
  路径 A（无 shots[] 字数估）：``section_spoken_text`` 字数 / wps →
  ``snap_duration_seconds`` 贴网格（与 compile 侧 ``_section_duration`` 同算法，
  此处提升为独立实现并做对齐测试防漂移）。
  路径 B（有 shots[] 镜头估）：显式 duration_seconds 优先；未给的镜按镜头内
  对白字数估并贴网格——与 converter 的 ``_shot_durations`` 权重分配语义对齐
  （本工具不重排权重，只按对白字数比例分配，用途是"显式 vs 估时偏差"校验）。
  输出逐场 {char_estimate, explicit, delta} + 全片 total + 建议（add_shots/
  trim_dialogue/adjust_duration，含供应商网格 snap）。
- recommend：format_card(媒介/类型/幕数) + 类型节奏锚点 → {min, recommended,
  max, rationale}。参考锚点，导演可推翻。

输入三选一：bible（显式对象）/ project_dir（读 artifacts/series_bible.json）/
script（显式剧本，验证用）。video_loop 缺省从 proposal_packet 读。
"""

from __future__ import annotations

import math
from typing import Any

from montage.engine.artifacts import ArtifactStore
from montage.engine.policy import load_loop_policy
from montage.providers.capabilities import (
    policy_for_loop,
    policy_max,
    snap_duration_seconds,
)
from montage.script_fields import section_spoken_text
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus
from montage.tools.script_validator import normalize_video_loop

_DEFAULT_WPS = 5.0

# 类型节奏锚点（V7/V19）：按 genre 关键词给出的合理成片时长区间（秒）。
# 参考值非硬约束——导演可推翻（recommend 只是自审与导演审的共同输入）。
_GENRE_PACE: list[tuple[tuple[str, ...], dict[str, Any]]] = [
    (("comedy", "喜剧", "sitcom"), {"min": 60, "recommended": 120, "max": 300,
     "rationale": "喜剧节奏密、金句留白短，超出 5 分钟易拖"}),
    (("thriller", "悬疑", "惊悚", "horror", "恐怖"), {"min": 90, "recommended": 180, "max": 480,
     "rationale": "悬疑需要铺垫-揭示节奏，过短藏不住伏笔"}),
    (("action", "动作", "武打", "chase"), {"min": 60, "recommended": 150, "max": 300,
     "rationale": "动作片按拍计，单条动作镜 3-15s，总量压在 5 分钟内最经济"}),
    (("documentary", "纪实", "纪录"), {"min": 90, "recommended": 240, "max": 600,
     "rationale": "纪实允许舒缓，但单集超过 10 分钟完播率显著下滑"}),
    (("drama", "剧情", "文艺"), {"min": 90, "recommended": 210, "max": 540,
     "rationale": "剧情片依人物弧光定长，中位数约 3-4 分钟"}),
    (("healing", "治愈", "日常", "slice"), {"min": 60, "recommended": 180, "max": 420,
     "rationale": "治愈系重氛围，中速即可"}),
]

_MEDIUM_PACE: dict[str, dict[str, Any]] = {
    "douyin_vertical": {"min": 30, "recommended": 60, "max": 180,
                        "rationale": "竖屏短剧完播优先"},
    "short_drama": {"min": 60, "recommended": 120, "max": 300,
                    "rationale": "短剧单集 1-3 分钟"},
    "film": {"min": 120, "recommended": 240, "max": 600,
             "rationale": "单本短片常规区间"},
    # 长片档（v8.2 P0-1）：30/60/120min 三锚点；吞吐模型配套
    # estimate_throughput（tokenplan+免费 ≈ 700-1000s/天，2h 约 8-10 天）。
    "longform": {"min": 1800, "recommended": 3600, "max": 7200,
                 "rationale": "长片 30/60/120min；按混合档位吞吐需跨天分章拍摄"},
}


# 混合档位吞吐常量（v8.2 P0-1；与 AGNES_RPM 同源口径）：
# tokenplan 500s/天（官方日配额）+ 免费档 1 RPM 无日限。免费档无日限但 429
# 重试与排队吃掉实际产出——用户拍板口径：混合总吞吐 700-1000s/天。
DAILY_QUOTA_TOKENPLAN_SECONDS = 500.0
_DAILY_WINDOW_HOURS = 8.0
DEFAULT_CHAPTER_SCENES = 40  # 与 story_outline.AUTO_CHAPTER_TARGET_SCENES 对齐


def _words_per_second(bible: dict[str, Any]) -> float:
    try:
        wps = float(bible.get("words_per_second") or 0)
    except (TypeError, ValueError):
        wps = 0.0
    return wps if wps > 0 else _DEFAULT_WPS


def _scene_shots(scene: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in (scene.get("shots") or []) if isinstance(s, dict)]


def _chars_of(scene: dict[str, Any]) -> int:
    """场景口播字数：lines 拼接优先，否则 narration（与 compile 同源）。"""
    return len(section_spoken_text(scene))


def _shot_chars(shot: dict[str, Any]) -> int:
    total = 0
    for sub in (shot.get("dialogue") or []):
        if isinstance(sub, dict):
            total += len(str(sub.get("dialogue_text") or ""))
    return total


def _estimate_scene(scene: dict[str, Any], wps: float, policy: dict[str, Any] | None) -> float:
    """路径 A：无 shots 字数估（compile 侧 _section_duration 同算法）。"""
    given = float(scene.get("duration_seconds") or 0)
    if given > 0:
        return given
    # 与 compile 同源：未给显式时长时，对白字数 / wps → 贴网格。
    # policy=None = 旧 5/10 网格（compile 的缺省）；kind=none 是显式不贴网格，别混。
    needed = _chars_of(scene) / wps if wps else 0.0
    if policy is None:
        return snap_duration_seconds(needed, None)
    return snap_duration_seconds(needed, policy)


def _estimate_shots(
    scene: dict[str, Any], wps: float, policy: dict[str, Any] | None
) -> list[float]:
    """路径 B：有 shots[] 时逐镜估（显式优先，未给按镜内对白字数贴网格）。

    注：方案字面曾要求复用 converter 的 ``_shot_durations`` 防漂移，经评审有意
    不采纳——两者场景语义不同（converter 是"已知场景时长按权重分摊"，本工具是
    "未知时长按对白估"）。防漂移由路径 A 对齐测试
    （tests/test_duration_advisor.py::test_path_a_matches_compile_section_duration）
    承担。
    """
    shots = _scene_shots(scene)
    if not shots:
        return []
    out: list[float] = []
    for shot in shots:
        try:
            given = float(shot.get("duration_seconds") or 0)
        except (TypeError, ValueError):
            given = 0.0
        if given > 0:
            out.append(given)
            continue
        chars = _shot_chars(shot)
        if chars > 0:
            out.append(snap_duration_seconds(chars / wps, policy))
        else:
            # 无对白镜：按场景显式时长/镜数或政策网格最小档兜底
            floor = policy_max(policy) or 5.0
            out.append(snap_duration_seconds(min(floor, 5.0), policy))
    return out


def _suggestion(
    scene: dict[str, Any], sid: str, char_est: float, explicit: float, policy: dict[str, Any] | None
) -> dict[str, Any] | None:
    """超/欠时建议：无对白超单镜上限→add_shots；对白超时→trim_dialogue；反之 adjust。"""
    shots = _scene_shots(scene)
    delta = round(char_est - explicit, 3)
    max_one = policy_max(policy) or 12.0
    if not shots and explicit > max_one:
        return {
            "scene_id": sid,
            "action": "add_shots",
            "detail": (
                f"{sid} 无对白且时长 {explicit:.0f}s 超过单镜上限 {max_one:.0f}s，"
                "补 shots[] 拆镜（生成期会按网格切段）"
            ),
        }
    if delta > 0.5:
        return {
            "scene_id": sid,
            "action": "trim_dialogue",
            "detail": (
                f"{sid} 对白按 {char_est:.0f}s 估、显式时长 {explicit:.0f}s，"
                f"超 {delta:.0f}s——压缩对白或把显式时长调到 "
                f"{snap_duration_seconds(char_est, policy):.0f}s"
            ),
        }
    if delta < -0.5:
        return {
            "scene_id": sid,
            "action": "adjust_duration",
            "detail": f"{sid} 显式时长 {explicit:.0f}s 高于口播估时 {char_est:.0f}s，确认留白是否有意",
        }
    return None


def estimate_durations(
    bible: dict[str, Any],
    *,
    video_loop: str = "none",
    words_per_second: float = 0.0,
) -> dict[str, Any]:
    """V19 双路径估时。bible=series_bible 对象；返回逐场/全片/建议。

    网格语义与 compile 对齐：video_loop 能映射到政策（kling/agnes/jimeng/ark）
    时贴该网格；``"none"`` 走 compile 缺省的旧 5/10 网格（policy=None）。
    """
    loop = normalize_video_loop(video_loop)
    policy = policy_for_loop(loop) if loop != "none" else None
    wps = words_per_second if words_per_second > 0 else _words_per_second(bible)
    target = bible.get("target_duration_seconds")
    try:
        target_f = float(target) if target else 0.0
    except (TypeError, ValueError):
        target_f = 0.0

    per_scene: list[dict[str, Any]] = []
    suggestions: list[dict[str, Any]] = []
    total = 0.0
    for idx, scene in enumerate(bible.get("scenes") or []):
        if not isinstance(scene, dict):
            continue
        sid = str(scene.get("id") or f"sc{idx + 1:02d}")
        shots = _scene_shots(scene)
        explicit = float(scene.get("duration_seconds") or 0)
        if shots:
            shot_durs = _estimate_shots(scene, wps, policy)
            est = round(sum(shot_durs), 3)
        else:
            est = round(_estimate_scene(scene, wps, policy), 3)
        explicit_eff = explicit if explicit > 0 else est  # 未给显式时长时 delta 无意义
        total += est
        per_scene.append({
            "scene_id": sid,
            "path": "shots" if shots else "char",
            "char_estimate_seconds": round(_chars_of(scene) / wps, 3) if wps else 0.0,
            "shot_estimate_seconds": est if shots else None,
            "explicit_duration_seconds": explicit if explicit > 0 else None,
            "effective_estimate_seconds": est,
            "delta": round(est - explicit_eff, 3) if explicit > 0 else None,
            "snap_estimate": snap_duration_seconds(est, policy),
        })
        sug = _suggestion(scene, sid, _chars_of(scene) / wps if wps else 0.0,
                          explicit if explicit > 0 else est, policy)
        if sug:
            suggestions.append(sug)

    total_r = round(total, 3)
    out: dict[str, Any] = {
        "video_loop": loop,
        "per_scene": per_scene,
        "total_estimate": total_r,
        "total_snap": snap_duration_seconds(total_r, policy),
        "target": target_f if target_f > 0 else None,
        "suggestions": suggestions,
    }
    if target_f > 0:
        out["total_delta"] = round(total_r - target_f, 3)
        if abs(out["total_delta"]) / target_f > 0.2:
            out["total_warning"] = (
                f"全片估时 {total_r:.0f}s 相对目标 {target_f:.0f}s 偏差超 20%"
            )
    return out


def recommend_duration(
    *,
    medium: str = "",
    genres: list[str] | None = None,
    n_scenes: int = 0,
    video_loop: str = "none",
) -> dict[str, Any]:
    """类型节奏锚点 + 幕数修正 → {min, recommended, max, rationale}。导演可推翻。"""
    loop = normalize_video_loop(video_loop)
    policy = policy_for_loop(loop)
    base: dict[str, Any] | None = None
    matched = ""
    for keys, row in _GENRE_PACE:
        for g in genres or []:
            if str(g).strip().lower() in keys:
                base = dict(row)
                matched = str(g)
                break
        if base:
            break
    med = _MEDIUM_PACE.get(str(medium or "").strip().lower())
    if base is None:
        base = dict(med) if med else {
            "min": 60, "recommended": 180, "max": 480,
            "rationale": "未匹配类型/媒介，给出通用中位数锚点",
        }
        matched = matched or (str(medium) if med else "generic")
    elif med:
        # 媒介与类型同时命中：取交集收窄（max 取小者，min 取大者）
        base = {
            "min": max(base["min"], med["min"]),
            "max": min(base["max"], med["max"]),
            "recommended": base["recommended"],
            "rationale": base["rationale"] + "；并按媒介区间收窄",
        }
        base["recommended"] = max(base["min"], min(base["max"], base["recommended"]))

    # 幕数修正：每幕至少要能放下一个网格镜（即梦 5s）
    if n_scenes > 0:
        floor_shots = n_scenes * 5.0
        if base["min"] < floor_shots:
            base["min"] = int(math.ceil(floor_shots / 5.0) * 5)
            base["recommended"] = max(base["recommended"], base["min"])
            base["rationale"] += f"；{n_scenes} 幕至少各一格镜，下限抬到 {base['min']}s"

    # 单镜上限反推绝对上限：全片 ≤ 幕数×单镜上限才不需要拼接切段
    max_one = policy_max(policy)
    if max_one and n_scenes > 0:
        hard = n_scenes * max_one
        if base["max"] > hard:
            base["max"] = int(hard)
            base["rationale"] += f"；{loop} 单镜上限 {max_one:.0f}s × {n_scenes} 幕，硬上限收窄到 {hard:.0f}s"
    return {
        "matched_genre": matched,
        "video_loop": loop,
        **base,
    }


def estimate_throughput(
    *,
    total_seconds: float = 0.0,
    daily_seconds: float = 0.0,
) -> dict[str, Any]:
    """混合档位吞吐估算（v8.2 P0-1；纯计算无 API 调用）。

    档位池口径（用户拍板）：tokenplan 500s/天（官方日配额，5 RPM）+ 免费档
    1 RPM 无日限，混合总吞吐 ≈ 700-1000s/天。输出逐档吞吐与全片天数预估；
    2h 长片约 8-10 天——「按日拆批次 = 章节粒度」的量级依据。

    total_seconds：全片估时（estimate.total_estimate 或目标时长）；
    daily_seconds：显式覆盖每日吞吐（缺省用 700-1000 中位 850）。
    """
    tier_rows: list[dict[str, Any]] = [
        {
            "tier": "tokenplan",
            "rpm": 5.0,
            "daily_seconds": DAILY_QUOTA_TOKENPLAN_SECONDS,
            "daily_limit": True,
            "note": "官方日配额 500s（视频）；图片另按 2K 80 RPM 计，不占视频配额",
        },
        {
            "tier": "default",
            "rpm": 1.0,
            "daily_seconds": 350.0,
            "daily_limit": False,
            "note": "无日限；1 RPM × 8h 窗口，429 重试与排队吃掉约半产出",
        },
    ]
    daily_est = float(daily_seconds) if daily_seconds > 0 else 850.0
    out: dict[str, Any] = {
        "tiers": tier_rows,
        "daily_seconds_est": daily_est,
        "daily_seconds_range": [700.0, 1000.0],
        "rationale": (
            "tokenplan 500s/天 + 免费档 1RPM 无日限 ≈ 700-1000s/天（用户拍板口径）；"
            "同档多密钥共享限额池不叠加"
        ),
    }
    if total_seconds > 0:
        days_lo = math.ceil(total_seconds / 1000.0)
        days_hi = math.ceil(total_seconds / 700.0)
        out["total_seconds"] = round(float(total_seconds), 3)
        out["days_est"] = days_lo if days_lo == days_hi else [days_lo, days_hi]
        if days_lo > 1:
            out["note"] = f"约 {days_lo}-{days_hi} 天跑完；按日拆批次 = 章节粒度"
    return out


def chapter_daily_batches(
    chapters: list[dict[str, Any]],
    *,
    daily_seconds: float = 0.0,
    total_estimate_seconds: float = 0.0,
    default_scene_seconds: float = 8.0,
) -> dict[str, Any]:
    """按日拆批次 = 章节粒度（v8.2 P0-1；纯计算）。

    输入 chapters（scene_plan.chapters 快照形状：id/start_scene，可带
    target_duration_seconds）；逐章估时（显式 target > 0 优先，否则按
    场数 × default_scene_seconds 贴网格中值）→ 贪心装箱：按顺序把整章
    填进天桶，单章超日吞吐时该章独占一天（不切章，批次断点落在章边界）。
    """
    daily = float(daily_seconds) if daily_seconds > 0 else 850.0
    rows: list[dict[str, Any]] = []
    for i, ch in enumerate(chapters):
        if not isinstance(ch, dict):
            continue
        cid = str(ch.get("id") or f"ch{i + 1:02d}")
        target = float(ch.get("target_duration_seconds") or 0)
        if target <= 0 and "target_duration_seconds" not in ch:
            # 无显式目标时长：按场数 × 单镜中值估（与 P0-1 网格中值口径一致）
            target = 0.0  # 场数未知时留 0，仅作顺序批次
        est = target if target > 0 else default_scene_seconds * 40
        rows.append({
            "chapter_id": cid,
            "start_scene": str(ch.get("start_scene") or ""),
            "estimated_seconds": round(est, 3),
            "target_declared": "target_duration_seconds" in ch and target > 0,
        })
    # 贪心装箱
    days: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    used = 0.0
    for row in rows:
        cost = row["estimated_seconds"]
        if cur is None or (used + cost > daily and used > 0):
            if cur is not None:
                days.append(cur)
            cur = {"day": len(days) + 1, "chapters": [], "estimated_seconds": 0.0}
            used = 0.0
        cur["chapters"].append(row["chapter_id"])
        cur["estimated_seconds"] = round(cur["estimated_seconds"] + cost, 3)
        used += cost
    if cur is not None:
        days.append(cur)
    out: dict[str, Any] = {
        "daily_seconds": daily,
        "per_chapter": rows,
        "days": days,
        "n_days": len(days),
    }
    if total_estimate_seconds > 0 and days:
        out["total_estimate_seconds"] = round(float(total_estimate_seconds), 3)
    return out


class DurationAdvisor(BaseTool):
    """时长闭环：estimate（双路径逐场估时+偏差+建议）与 recommend（节奏锚点）。"""

    name = "duration_advisor"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["estimate", "recommend", "throughput"],
                "default": "estimate",
            },
            "project_dir": {"type": "string"},
            "bible": {"type": "object", "description": "显式 bible；缺省时从 project_dir 直读"},
            "script": {"type": "object", "description": "显式剧本（等价 sections 逐段估时）"},
            "video_loop": {"type": "string"},
            "words_per_second": {"type": "number"},
            "medium": {"type": "string"},
            "genres": {"type": "array", "items": {"type": "string"}},
            "n_scenes": {"type": "integer"},
            "daily_seconds": {
                "type": "number",
                "description": "throughput：覆盖每日吞吐秒（缺省 850 = 混合档位中位）",
            },
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        op = str(inputs.get("operation") or "estimate")
        if op == "recommend":
            loop = str(inputs.get("video_loop") or "")
            if not loop and inputs.get("project_dir"):
                loop = str(load_loop_policy(inputs["project_dir"]).get("video_loop") or "")
            data = recommend_duration(
                medium=str(inputs.get("medium") or ""),
                genres=[g for g in (inputs.get("genres") or []) if isinstance(g, str)],
                n_scenes=int(inputs.get("n_scenes") or 0),
                video_loop=loop,
            )
            return ToolResult(success=True, data=data, meta={"operation": "recommend"})

        if op == "throughput":
            # 混合档位吞吐（v8.2 P0-1）：全片估时 → 天数预估 + 章节拆批。
            # 输入优先级：显式 bible/script > project_dir 直读；无输入时只出
            # 档位池口径（天数留空），供导演在 setup 早期看量级。
            try:
                daily = float(inputs.get("daily_seconds") or 0)
            except (TypeError, ValueError):
                daily = 0.0
            bible_t = inputs.get("bible") if isinstance(inputs.get("bible"), dict) else None
            script_t = inputs.get("script") if isinstance(inputs.get("script"), dict) else None
            project_dir_t = str(inputs.get("project_dir") or "").strip()
            if bible_t is None and script_t is not None:
                bible_t = {"scenes": script_t.get("sections") or []}
            if bible_t is None and project_dir_t:
                stored_t = ArtifactStore(project_dir_t).read("series_bible")
                bible_t = stored_t if isinstance(stored_t, dict) else None
            total = 0.0
            chapters: list[dict[str, Any]] = []
            if bible_t is not None:
                try:
                    wps_t = float(inputs.get("words_per_second") or 0)
                except (TypeError, ValueError):
                    wps_t = 0.0
                est = estimate_durations(
                    bible_t,
                    video_loop=str(inputs.get("video_loop") or "") or "none",
                    words_per_second=wps_t,
                )
                total = float(est.get("total_estimate") or 0)
                chapters = [c for c in (bible_t.get("chapters") or []) if isinstance(c, dict)]
            data = estimate_throughput(total_seconds=total, daily_seconds=daily)
            if chapters:
                data["batches"] = chapter_daily_batches(
                    chapters,
                    daily_seconds=daily,
                    total_estimate_seconds=total,
                )
            return ToolResult(success=True, data=data, meta={"operation": "throughput"})

        if op != "estimate":
            return ToolResult(success=False, error=f"未知 operation: {op}")

        bible = inputs.get("bible") if isinstance(inputs.get("bible"), dict) else None
        script = inputs.get("script") if isinstance(inputs.get("script"), dict) else None
        project_dir = str(inputs.get("project_dir") or "").strip()
        loop = str(inputs.get("video_loop") or "")
        if not loop and project_dir:
            loop = str(load_loop_policy(project_dir).get("video_loop") or "")

        if bible is None and script is not None:
            # 显式剧本路径：sections 当 scenes 估（自审"稿还没进 bible"场景）
            bible = {"scenes": script.get("sections") or [], "words_per_second": script.get("words_per_second")}
        if bible is None and project_dir:
            stored = ArtifactStore(project_dir).read("series_bible")
            bible = stored if isinstance(stored, dict) else None
        if bible is None:
            return ToolResult(success=False, error="需要 bible / script / project_dir 之一")

        try:
            wps = float(inputs.get("words_per_second") or 0)
        except (TypeError, ValueError):
            wps = 0.0
        data = estimate_durations(bible, video_loop=loop or "none", words_per_second=wps)
        return ToolResult(success=True, data=data, meta={"operation": "estimate"})
