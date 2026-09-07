"""style_packs — AutoEditor 成片风格包（剪辑节奏 + LUT + 转场 + 可选 playbook 元数据）。

与 playbook 分工：本模块管「怎么剪」（节奏/LUT/转场/输出档案）；playbook 管「画面长什么样」
（仅 bind_playbook 写入 plan 元数据，MVP render 不消费 visual_language）。

LUT 存 catalog id（如 ``luts/teal-orange``），与 ``assets/luts/INDEX.md`` 对齐。
"""

from __future__ import annotations

from typing import Any

from montage.compose.ffmpeg_engine import TRANSITION_NAMES
from montage.compose.profiles import PROFILES

# ---------------------------------------------------------------------------
# 六个预置包
# ---------------------------------------------------------------------------

STYLE_PACKS: dict[str, dict[str, Any]] = {
    "cinematic": {
        "id": "cinematic",
        "title": "电影感",
        "pacing": {"min_hold": 2.0, "max_hold": 8.0, "transition_duration": 0.5},
        "lut": "luts/teal-orange",
        "transitions": ["crossfade"],
        "bind_playbook": None,
        "output_profile": "youtube_landscape",
    },
    "documentary": {
        "id": "documentary",
        "title": "纪录片",
        "pacing": {"min_hold": 4.0, "max_hold": 12.0, "transition_duration": 0.0},
        "lut": "luts/muted-documentary",
        "transitions": ["cut"],
        "bind_playbook": "documentary_restraint",
        "output_profile": "youtube_landscape",
    },
    "beat": {
        "id": "beat",
        "title": "卡点",
        "pacing": {"min_hold": 0.4, "max_hold": 2.0, "transition_duration": 0.0},
        "lut": "",
        "transitions": ["cut"],
        "bind_playbook": None,
        "output_profile": "youtube_landscape",
    },
    "classic": {
        "id": "classic",
        "title": "古风",
        "pacing": {"min_hold": 3.5, "max_hold": 12.0, "transition_duration": 0.8},
        "lut": "luts/warm-film",
        "transitions": ["fade_black"],
        "bind_playbook": "chinese_elegance",
        "output_profile": "youtube_landscape",
    },
    "fresh": {
        "id": "fresh",
        "title": "清新",
        "pacing": {"min_hold": 1.5, "max_hold": 5.0, "transition_duration": 0.4},
        "lut": "luts/cool-clean",
        "transitions": ["dissolve"],
        "bind_playbook": "healing_japanese",
        "output_profile": "youtube_landscape",
    },
    "cyber": {
        "id": "cyber",
        "title": "赛博",
        "pacing": {"min_hold": 0.8, "max_hold": 3.0, "transition_duration": 0.2},
        "lut": "luts/dark-moody",
        "transitions": ["wipe"],
        "bind_playbook": "cyberpunk_neon",
        "output_profile": "youtube_landscape",
    },
    "anime": {
        "id": "anime",
        "title": "动漫",
        "pacing": {"min_hold": 1.5, "max_hold": 6.0, "transition_duration": 0.2},
        "lut": "luts/cool-clean",
        "transitions": ["cut", "wipe"],
        "bind_playbook": "anime_shonen",
        "output_profile": "youtube_landscape",
    },
    "manga": {
        "id": "manga",
        "title": "漫画",
        "pacing": {"min_hold": 1.2, "max_hold": 4.5, "transition_duration": 0.0},
        "lut": "luts/bw-high-contrast",
        "transitions": ["cut"],
        "bind_playbook": "manga_panel",
        "output_profile": "youtube_landscape",
    },
    "spoken": {
        "id": "spoken",
        "title": "口播",
        "pacing": {"min_hold": 2.5, "max_hold": 8.0, "transition_duration": 0.0},
        "lut": "",
        "transitions": ["cut"],
        "bind_playbook": "spoken_explain",
        "output_profile": "douyin_vertical",
    },
}

_LUT_IDS = {
    "luts/teal-orange",
    "luts/dark-moody",
    "luts/warm-film",
    "luts/cool-clean",
    "luts/bw-high-contrast",
    "luts/muted-documentary",
}


def list_style_packs() -> list[dict[str, Any]]:
    """摘要列表（id/title/lut/transitions/output_profile）。"""
    result = []
    for pid, pack in sorted(STYLE_PACKS.items()):
        result.append({
            "id": pid,
            "title": pack["title"],
            "lut": pack.get("lut") or "",
            "transitions": list(pack.get("transitions") or []),
            "bind_playbook": pack.get("bind_playbook"),
            "output_profile": pack.get("output_profile") or "youtube_landscape",
        })
    return result


def get_style_pack(name: str) -> dict[str, Any] | None:
    """按 id 取风格包（返回副本，调用方可覆写）。未知返回 None。"""
    if not name:
        return None
    pack = STYLE_PACKS.get(name)
    return dict(pack) if pack else None


def validate_overrides(overrides: dict[str, Any]) -> list[str]:
    """校验自定义覆写（lut / transitions / pacing / output_profile / bind_playbook）。

    返回错误列表，空列表 = 通过。不做自由文本解析。
    """
    errors: list[str] = []
    if not isinstance(overrides, dict):
        return ["overrides 必须是对象"]

    lut = overrides.get("lut")
    if lut is not None and lut != "":
        if lut not in _LUT_IDS:
            errors.append(f"未知 lut: {lut}（须为 assets/luts/INDEX.md 的 id）")

    transitions = overrides.get("transitions")
    if transitions is not None:
        if not isinstance(transitions, list) or not transitions:
            errors.append("transitions 必须是非空数组")
        else:
            for t in transitions:
                if t not in TRANSITION_NAMES:
                    errors.append(f"未知转场: {t}（须为 TRANSITION_NAMES 中的键）")

    pacing = overrides.get("pacing")
    if pacing is not None:
        if not isinstance(pacing, dict):
            errors.append("pacing 必须是对象")
        else:
            for key in ("min_hold", "max_hold", "transition_duration"):
                if key in pacing:
                    try:
                        float(pacing[key])
                    except (TypeError, ValueError):
                        errors.append(f"pacing.{key} 必须是数字")
            try:
                mn = float(pacing["min_hold"]) if "min_hold" in pacing else None
                mx = float(pacing["max_hold"]) if "max_hold" in pacing else None
            except (TypeError, ValueError):
                mn = mx = None
            if mn is not None and mx is not None and mn > mx:
                errors.append("pacing.min_hold 不能大于 max_hold")

    profile = overrides.get("output_profile")
    if profile is not None and profile not in PROFILES:
        errors.append(f"未知 output_profile: {profile}")

    playbook = overrides.get("bind_playbook")
    if playbook:
        from montage.playbooks import get_playbook

        if get_playbook(str(playbook)) is None:
            errors.append(f"未知 bind_playbook: {playbook}")

    return errors


def apply_overrides(pack: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    """在 StylePack 副本上应用覆写；校验失败抛 ValueError（可读信息）。"""
    result = dict(pack)
    if not overrides:
        return result
    errors = validate_overrides(overrides)
    if errors:
        raise ValueError("；".join(errors))
    if "lut" in overrides:
        result["lut"] = overrides["lut"] or ""
    if "transitions" in overrides:
        result["transitions"] = list(overrides["transitions"])
    if "pacing" in overrides:
        pacing = dict(result.get("pacing") or {})
        pacing.update(overrides["pacing"])
        result["pacing"] = pacing
    if "output_profile" in overrides:
        result["output_profile"] = overrides["output_profile"]
    if "bind_playbook" in overrides:
        result["bind_playbook"] = overrides["bind_playbook"] or None
    return result
