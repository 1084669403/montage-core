"""shot_language — 节拍→运镜确定性表（P4）。

只补空字段。不改 shot_prompt_builder 模板。运镜值必须是 builder
``_MOVEMENT_PHRASES`` 已认识的键（dolly_in 不是 push_in）。
"""

from __future__ import annotations

from typing import Any

# 与 lib.shot_prompt_builder._MOVEMENT_PHRASES 对齐；禁止写 push_in / pull_out
LEGAL_MOVEMENT = frozenset({
    "static", "pan_left", "pan_right", "tilt_up", "tilt_down",
    "dolly_in", "dolly_out", "tracking_left", "tracking_right",
    "crane_up", "crane_down", "handheld", "steadicam", "whip_pan",
    "orbital", "zoom_in", "zoom_out", "rack_focus",
})

LEGAL_SHOT_SIZE = frozenset({
    "extreme_wide", "wide", "medium_wide", "medium", "medium_close",
    "close_up", "extreme_close_up", "over_shoulder", "insert", "establishing",
    "close",
})

CLOSE_SIZES = frozenset({
    "close", "close_up", "extreme_close_up", "insert", "medium_close",
    "特写", "近景",
})
WIDE_SIZES = frozenset({
    "extreme_wide", "wide", "establishing", "全景",
})

CANONICAL_BEATS = ("hook", "escalation", "reveal", "landing")

ROLE_ALIASES = {
    "hook": "hook",
    "establish_context": "hook",
    "escalation": "escalation",
    "build_tension": "escalation",
    "conflict": "escalation",
    "climax": "escalation",
    "reveal": "reveal",
    "deliver_payload": "reveal",
    "hero_moment": "reveal",
    "landing": "landing",
    "resolution": "landing",
    "farewell": "landing",
    "chase": "chase",
    "transition": "transition",
    "emotional_beat": "transition",
}

BEAT_TO_CAMERA = {
    "hook": "dolly_in",
    "escalation": "handheld",
    "reveal": "zoom_in",
    "landing": "dolly_out",
    "chase": "handheld",
    "transition": "static",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def canonical_role(raw: Any) -> str:
    key = _text(raw).lower()
    return ROLE_ALIASES.get(key, "")


def camera_for_beat(role: Any, playbook: dict[str, Any] | None = None) -> str:
    """查表：playbook.motion.beat_camera 覆盖默认表；非法值回落。"""
    overlay = ((playbook or {}).get("motion") or {}).get("beat_camera")
    canon = canonical_role(role)
    raw_role = _text(role).lower()
    if isinstance(overlay, dict):
        for key in (raw_role, canon):
            if not key:
                continue
            val = _text(overlay.get(key))
            if val in LEGAL_MOVEMENT:
                return val
    if canon and canon in BEAT_TO_CAMERA:
        return BEAT_TO_CAMERA[canon]
    return "static"


def shot_size_for_index(index: int, total: int) -> str:
    if total <= 1:
        return "medium"
    if index <= 0:
        return "wide"
    if index >= total - 1:
        return "close"
    return "medium"


def language_for_shot(
    index: int,
    total: int,
    narrative_role: Any = "",
    playbook: dict[str, Any] | None = None,
    *,
    fill: bool = True,
    defer: bool = False,
) -> dict[str, str]:
    out: dict[str, str] = {"shot_size": shot_size_for_index(index, total)}
    if not fill:
        out["camera_movement"] = "static"
    elif not defer:
        out["camera_movement"] = camera_for_beat(narrative_role, playbook)
    return out


def _empty(value: Any) -> bool:
    return not _text(value)


def _fill_one(
    sl: dict[str, Any],
    *,
    index: int,
    total: int,
    role: Any,
    playbook: dict[str, Any] | None,
) -> bool:
    changed = False
    if _empty(sl.get("camera_movement")):
        sl["camera_movement"] = camera_for_beat(role, playbook)
        changed = True
    if _empty(sl.get("shot_size")):
        sl["shot_size"] = shot_size_for_index(index, total)
        changed = True
    return changed


def fill_shot_language(
    scene_plan: dict[str, Any] | None,
    playbook: dict[str, Any] | None = None,
    *,
    enabled: bool = True,
) -> list[dict[str, str]]:
    """缺 camera_movement / shot_size 才补。显式 static 不改。"""
    findings: list[dict[str, str]] = []
    if not enabled or not isinstance(scene_plan, dict):
        return findings
    for scene in scene_plan.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        role = scene.get("narrative_role")
        shots = [s for s in (scene.get("shots") or []) if isinstance(s, dict)]
        n = len(shots) or 1
        scene_sl = dict(scene.get("shot_language") or {}) if isinstance(scene.get("shot_language"), dict) else {}
        if _fill_one(scene_sl, index=0, total=n, role=role, playbook=playbook):
            scene["shot_language"] = scene_sl
            findings.append({
                "severity": "info",
                "stage": "shot_language",
                "field": str(scene.get("id") or "scene"),
                "message": "幕级 shot_language 已按节拍补空",
                "proposed_fix": "导演要固定机位请显式写 camera_movement=static",
            })
        for ji, shot in enumerate(shots):
            sl = dict(shot.get("shot_language") or {}) if isinstance(shot.get("shot_language"), dict) else {}
            if not _fill_one(sl, index=ji, total=n, role=role, playbook=playbook):
                continue
            shot["shot_language"] = sl
            sid = str(shot.get("shot_id") or f"{scene.get('id') or 'sc'}_{ji + 1:02d}")
            findings.append({
                "severity": "info",
                "stage": "shot_language",
                "field": sid,
                "message": f"缺运镜/景别，已按 {canonical_role(role) or 'unknown'} 补 {sl.get('camera_movement')}/{sl.get('shot_size')}",
                "proposed_fix": "精修分镜时显式写 shot_language，补全不会覆盖已有值",
            })
    return findings
