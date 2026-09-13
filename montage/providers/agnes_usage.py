"""agnes_usage — Agnes Token Plan 每日配额记帐（best-effort）。

官方 Token Plan FAQ（视频 RPM 更新 2026-06-28）：
  - 图片：4000 张/天
  - 视频：500 秒/天（v2.0 与 2.5-flash 同池）
  - default / enterprise 档无订阅配额；配额与 RPM 同时生效。

状态存用户级 ``~/.montage/agnes_usage.json``（``MONTAGE_AGNES_USAGE_PATH`` 覆写），
按 tier 分桶（同一天多档互不污染），跨本地日期归零（文档未规定时区，取本地日期）。
只统计 + 告警，不阻断出片；写失败静默降级。

重要：本模块在 import 期不得有文件 I/O / 副作用——``montage.registry`` 会 import
``montage.providers`` 下所有非 ``_`` 开头模块做工具发现，import 报错会进 doctor。
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date
from pathlib import Path
from typing import Any

# 文档配额；图片列只写 agnes-image-2.1-flash，保守假设 2.5 共用同池（文档单列再改）。
_QUOTA: dict[str, dict[str, float]] = {
    "tokenplan": {"image": 4000.0, "video": 500.0},
}
_KNOWN_TIERS = ("default", "enterprise", "tokenplan")


def usage_path() -> Path:
    """惰性解析：import 期不触发文件系统访问。"""
    override = str(os.environ.get("MONTAGE_AGNES_USAGE_PATH") or "").strip()
    if override:
        return Path(override)
    return Path.home() / ".montage" / "agnes_usage.json"


def _today() -> str:
    return date.today().isoformat()


def _empty() -> dict[str, Any]:
    return {"day": _today(), "tiers": {}}


def _load() -> dict[str, Any]:
    path = usage_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(raw, dict) or str(raw.get("day") or "") != _today():
        return _empty()
    if not isinstance(raw.get("tiers"), dict):
        raw["tiers"] = {}
    return raw


def _save(data: dict[str, Any]) -> None:
    # 原子写（同 generation_cache）：tmp + move；失败静默，不阻断出片。
    path = usage_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.move(str(tmp), str(path))
    except OSError:
        return


def read_usage(tier: str) -> dict[str, float]:
    """读某 tier 当日用量；文件损坏/缺失返回零值（静默降级）。"""
    bucket = (_load().get("tiers") or {}).get(str(tier or "default")) or {}
    return {
        "images": float(bucket.get("images") or 0),
        "video_seconds": float(bucket.get("video_seconds") or 0.0),
    }


def add_images(tier: str, n: int = 1) -> None:
    data = _load()
    bucket = data["tiers"].setdefault(str(tier or "default"), {})
    bucket["images"] = float(bucket.get("images") or 0) + int(n)
    _save(data)


def add_video_seconds(tier: str, seconds: float) -> None:
    data = _load()
    bucket = data["tiers"].setdefault(str(tier or "default"), {})
    bucket["video_seconds"] = float(bucket.get("video_seconds") or 0.0) + float(seconds or 0)
    _save(data)


def quota_status(tier: str, kind: str) -> tuple[float, float | None, float | None]:
    """返回 (used, limit, remaining)；非 tokenplan 档 limit/remaining 为 None。"""
    used = read_usage(tier)
    key = "images" if kind == "image" else "video_seconds"
    limit = (_QUOTA.get(str(tier or "").strip().lower()) or {}).get(kind)
    got = float(used.get(key) or 0.0)
    if limit is None:
        return got, None, None
    return got, float(limit), max(0.0, float(limit) - got)


def usage_snapshot() -> dict[str, Any]:
    """doctor 用：当前 tier 与当日图片/视频用量。"""
    tier = "default"
    try:
        from montage.providers.capabilities import agnes_access_tier

        tier = agnes_access_tier()
    except Exception:  # noqa: BLE001
        pass
    images, img_limit, img_left = quota_status(tier, "image")
    seconds, vid_limit, vid_left = quota_status(tier, "video")
    return {
        "tier": tier,
        "declared": str(os.environ.get("AGNES_ACCESS_TYPE") or "").strip().lower()
        in _KNOWN_TIERS,
        "images": images,
        "image_limit": img_limit,
        "image_remaining": img_left,
        "video_seconds": seconds,
        "video_limit": vid_limit,
        "video_remaining": vid_left,
    }
