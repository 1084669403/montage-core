"""rework — 镜头返工路由（P5）。

retry 点名且已有成片时，按能力表在 edit / extend / splice / regenerate /
feature 之间选择。禁止 LLM，禁止放进 director.py。无公网 URL 不能假 edit。
可灵 Omni 未写 mode 默认 regenerate（不要静默 edit）；feature 仅 Omni 且 ≤10s。
"""

from __future__ import annotations

from typing import Any

LEGAL_REWORK = frozenset({"edit", "extend", "splice", "regenerate", "feature"})
FEATURE_MAX_SECONDS = 10

_DIALOGUE_HINTS = ("native",)


def clip_http_url(item: dict[str, Any] | None) -> str:
    """成片公网地址。非 http 不能进方舟/可灵 edit。"""
    if not isinstance(item, dict):
        return ""
    for key in ("url", "video_url", "source_url"):
        raw = str(item.get(key) or "").strip()
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
    return ""


def _has_dialogue(shot: dict[str, Any]) -> bool:
    audio = shot.get("audio_prompt") if isinstance(shot.get("audio_prompt"), dict) else {}
    if str(audio.get("dialogue") or "").strip():
        return True
    mode = str(shot.get("dialogue_audio_mode") or "").strip().lower()
    if mode in _DIALOGUE_HINTS:
        return True
    if str(shot.get("_sound") or "").strip().lower() == "on":
        return True
    if str(shot.get("audio_source") or "").strip() in ("kling_prompt", "jimeng_prompt", "agnes_prompt"):
        return True
    return False


def _segment(shot: dict[str, Any]) -> dict[str, float] | None:
    raw = shot.get("retake_segment")
    if not isinstance(raw, dict):
        return None
    try:
        start = float(raw.get("start_seconds") or 0)
        dur = float(raw.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        return None
    if dur <= 0:
        return None
    return {"start_seconds": max(start, 0.0), "duration_seconds": dur}


def _wanted_seconds(shot: dict[str, Any]) -> float:
    try:
        return float(shot.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        return 0.0


def _existing_seconds(item: dict[str, Any] | None) -> float:
    if not isinstance(item, dict):
        return 0.0
    for key in ("duration_seconds", "seconds", "duration"):
        try:
            val = float(item.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if val > 0:
            return val
    return 0.0


def rework_prompt(shot: dict[str, Any], mode: str) -> str:
    """局部编辑提示词。不要把整段 generate 词当 edit 指令。"""
    note = str(shot.get("revision_note") or "").strip()
    if mode == "extend":
        extra = _wanted_seconds(shot)
        tail = note or "人物动作自然延续，场景与光线保持不变"
        return f"向后延长@视频1约{max(int(extra), 4)}秒。{tail}"
    if mode == "feature":
        return note or "修正画面问题，其余保持不变"
    body = note or "修正画面问题，其余保持不变"
    return f"修改@视频1。{body}。其余保持不变"


def _clip_seconds(shot: dict[str, Any], existing: dict[str, Any] | None) -> float:
    wanted = _wanted_seconds(shot)
    have = _existing_seconds(existing)
    if wanted > 0 and have > 0:
        return max(wanted, have)
    return wanted or have


def pick_rework_mode(
    shot: dict[str, Any] | None,
    *,
    caps: dict[str, Any] | None = None,
    existing_video: dict[str, Any] | None = None,
    retry: bool = False,
    frames_only: bool = False,
) -> dict[str, Any]:
    """返回 {mode, notes, source_url, segment}。非 retry 一律 regenerate（调用方应 skip）。"""
    notes: list[str] = []
    shot = shot if isinstance(shot, dict) else {}
    caps = caps if isinstance(caps, dict) else {}
    wanted = str(shot.get("rework_mode") or "").strip().lower()
    if wanted and wanted not in LEGAL_REWORK:
        notes.append(f"忽略非法 rework_mode={wanted}")
        wanted = ""

    empty = {
        "mode": "regenerate",
        "notes": notes,
        "source_url": "",
        "segment": None,
    }
    if frames_only or not retry:
        return empty
    if str(shot.get("shot_kind") or "video") == "image":
        notes.append("静图镜不走供应商局部编辑")
        return {**empty, "notes": notes}

    source = clip_http_url(existing_video)
    can_edit = bool(caps.get("edit_clip"))
    can_extend = bool(caps.get("extend_clip"))
    api_id = str(caps.get("api_id") or shot.get("api_id") or "")
    omni = api_id == "kling_omni_30"
    dialogue = _has_dialogue(shot)
    segment = _segment(shot)

    if wanted == "regenerate":
        return {**empty, "notes": notes}
    if wanted == "feature":
        if not omni:
            notes.append("feature 仅 Kling Omni，已回落 regenerate")
            return {**empty, "notes": notes}
        if not source:
            notes.append("成片无公网 URL，不能走 feature")
            return {**empty, "notes": notes, "source_url": ""}
        dur = _clip_seconds(shot, existing_video)
        if dur > FEATURE_MAX_SECONDS:
            notes.append(
                f"成片 {dur:.0f}s 超过 {FEATURE_MAX_SECONDS}s，不能 feature，已回落 regenerate"
            )
            return {**empty, "notes": notes, "source_url": source}
        if dialogue:
            notes.append("对白镜走 feature 会丢掉 native 口播（audio=off）")
        return {"mode": "feature", "notes": notes, "source_url": source, "segment": None}
    if omni and not wanted:
        if dialogue:
            notes.append("Omni 编辑必须 sound=off，对白镜整镜重抽")
        return {**empty, "notes": notes, "source_url": source}
    if wanted == "splice" or (segment and not can_edit):
        if segment is None:
            notes.append("splice 需要 retake_segment.start_seconds/duration_seconds")
            return {**empty, "notes": notes}
        return {"mode": "splice", "notes": notes, "source_url": source, "segment": segment}

    if not source:
        notes.append("成片无公网 URL，不能走 edit/extend")
        if wanted in ("edit", "extend"):
            notes.append(f"已回落 regenerate（原 {wanted}）")
        return {**empty, "notes": notes, "source_url": ""}

    if omni and dialogue and wanted != "splice":
        notes.append("Omni 编辑必须 sound=off，对白镜整镜重抽")
        return {**empty, "notes": notes, "source_url": source}

    grow = False
    have = _existing_seconds(existing_video)
    if have > 0:
        grow = _wanted_seconds(shot) > have + 0.5
    if wanted == "extend" or (not wanted and grow and can_extend):
        if not can_extend:
            notes.append("当前 API 面无 extend_clip，回落 regenerate")
            return {**empty, "notes": notes, "source_url": source}
        return {"mode": "extend", "notes": notes, "source_url": source, "segment": None}

    if wanted == "edit" or (not wanted and can_edit):
        if not can_edit:
            notes.append("当前 API 面无 edit_clip，回落 regenerate")
            return {**empty, "notes": notes, "source_url": source}
        if omni and dialogue:
            notes.append("Omni 对白镜忽略 edit，整镜重抽")
            return {**empty, "notes": notes, "source_url": source}
        return {"mode": "edit", "notes": notes, "source_url": source, "segment": None}

    return {**empty, "notes": notes, "source_url": source}
