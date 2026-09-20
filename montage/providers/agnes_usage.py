"""agnes_usage - Agnes Token Plan 每日配额记账。

官方 Token Plan：图片 4000 张/天、视频 500 秒/天。default/enterprise 档没有
订阅配额。状态保存在用户级 ``~/.montage/agnes_usage.json``，可用
``MONTAGE_AGNES_USAGE_PATH`` 覆写。跨自然日读取时归零。

写入语义：
- 进程内使用 ``RLock`` 串行化读改写，保证多线程生成不丢账；
- 落盘使用唯一临时文件 + ``os.replace``，避免并发争用同一个 ``.tmp``；
- 写盘失败仍然静默降级，不阻断出片。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import date
from pathlib import Path
from typing import Any

_QUOTA: dict[str, dict[str, float]] = {
    "tokenplan": {"image": 4000.0, "video": 500.0},
}
_KNOWN_TIERS = ("default", "enterprise", "tokenplan")
_USAGE_LOCK = threading.RLock()


def usage_path() -> Path:
    """返回账本路径；import 期间不做文件 I/O。"""
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
    """原子写账本；每个写入线程使用唯一临时文件。"""
    path = usage_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".",
            suffix=".tmp",
            dir=str(path.parent),
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink()
    except OSError:
        return


def read_usage(tier: str) -> dict[str, float]:
    """读某 tier 当日用量；文件损坏或缺失时返回零值。"""
    with _USAGE_LOCK:
        bucket = (_load().get("tiers") or {}).get(str(tier or "default")) or {}
        return {
            "images": float(bucket.get("images") or 0),
            "video_seconds": float(bucket.get("video_seconds") or 0.0),
        }


def add_images(tier: str, n: int = 1) -> None:
    with _USAGE_LOCK:
        data = _load()
        bucket = data["tiers"].setdefault(str(tier or "default"), {})
        bucket["images"] = float(bucket.get("images") or 0) + int(n)
        _save(data)


def add_video_seconds(tier: str, seconds: float) -> None:
    with _USAGE_LOCK:
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
    """doctor 使用的当前档位用量快照。"""
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
