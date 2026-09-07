"""playbooks — 风格 playbook 资产（原创，Python dict 零依赖）。

proposal 阶段锁定一个 playbook（写入决策日志 category=style），其 dict 作为
``style_context`` 传给 ``visual_prompt_builder`` / ``lib.shot_prompt_builder``，
消费点对齐：

- ``identity.mood`` / ``visual_language.aesthetic`` → 首帧图的【风格】段
  （``_image_style_text``）；
- ``asset_generation.character_appearance_default`` → 角色外貌兜底
  （``_resolve_appearance``）；
- ``asset_generation.consistency_anchors`` → 首帧图风格锚点（≤2 条注入）；
- ``asset_generation.image_negative_prompt`` → 图片负向词
  （``_image_negative_text`` / ``_is_stylized_playbook``）；
- ``motion.transitions`` / ``quality_rules`` → edit-director 与 reviewer 的风格门禁。
- ``motion.beat_camera``（可选）→ P4 节拍→运镜覆盖默认表。

新增 playbook：在本包加一个模块导出 ``PLAYBOOK`` dict 即可，``list_playbooks``
自动发现。
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

_PKG = __name__  # "montage.playbooks"

_CACHE: dict[str, dict[str, Any]] = {}


def _discover() -> dict[str, dict[str, Any]]:
    """扫描本包全部模块，收集 PLAYBOOK（幂等）。"""
    if _CACHE:
        return _CACHE
    for info in pkgutil.iter_modules(__path__):  # type: ignore[attr-defined]
        if info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f"{_PKG}.{info.name}")
        except Exception:  # noqa: BLE001
            continue
        playbook = getattr(module, "PLAYBOOK", None)
        if isinstance(playbook, dict) and playbook.get("id"):
            _CACHE[playbook["id"]] = playbook
    return _CACHE


def list_playbooks() -> list[dict[str, Any]]:
    """返回全部 playbook 的摘要列表（id/title/mood/best_for）。"""
    result = []
    for pid, pb in sorted(_discover().items()):
        identity = pb.get("identity") or {}
        result.append({
            "id": pid,
            "title": pb.get("title") or pid,
            "mood": identity.get("mood", ""),
            "best_for": identity.get("best_for", ""),
        })
    return result


def get_playbook(name: str) -> dict[str, Any] | None:
    """按 id 取 playbook dict（可直接作为 style_context 传入）；未知返回 None。"""
    if not name:
        return None
    return _discover().get(name)
