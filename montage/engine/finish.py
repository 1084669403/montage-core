"""produce finish 步：只在显式 lut/profile/片头/字幕 cues 时动手。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from montage.compose.effects import sanitize_drawtext
from montage.compose.profiles import get_profile
from montage.engine.artifacts import ArtifactStore
from montage.tools.compose_planner import resolve_lut_file as _resolve_lut_str

TITLE_CARD_SECONDS = 2.0
LOWER_THIRD_SECONDS = 4.0


def _plan(root: Path) -> dict[str, Any]:
    data = ArtifactStore(root).read("compose_plan") or {}
    return data if isinstance(data, dict) else {}


def lut_cube_path(root: Path) -> Path | None:
    """compose_plan 里能解析到磁盘 .cube 才返回路径。"""
    plan = _plan(root)
    hints = plan.get("assemble_hints") if isinstance(plan.get("assemble_hints"), dict) else {}
    raw = str(plan.get("lut") or hints.get("lut") or "").strip()
    if not raw:
        return None
    from montage.compose.ffmpeg_engine import resolve_lut_file

    found = resolve_lut_file(raw)
    if found is not None and found.is_file():
        return found
    # compose_planner.resolve 找不到会原样返回 id，不算成功
    fallback = Path(_resolve_lut_str(raw))
    if fallback.is_file():
        return fallback
    return None


def explicit_profile(root: Path, *, cli_profile: str = "") -> str:
    named = str(cli_profile or "").strip()
    if named:
        return named
    packet = ArtifactStore(root).read("proposal_packet") or {}
    return str(packet.get("output_profile") or "").strip()


def script_title(root: Path) -> str:
    script = ArtifactStore(root).read("script") or {}
    return str(script.get("title") or "").strip()


def lower_third_source(root: Path) -> str:
    """片头同一 script.title，可加 characters[0].name；无合法片头则空。"""
    title = script_title(root)
    if not sanitize_drawtext(title):
        return ""
    script = ArtifactStore(root).read("script") or {}
    chars = script.get("characters") or []
    name = ""
    if isinstance(chars, list) and chars and isinstance(chars[0], dict):
        name = str(chars[0].get("name") or "").strip()
    if name:
        return f"{title} · {name}"
    return title


def flatten_cues(root: Path) -> list[dict[str, Any]]:
    """compose_plan.shots[].subtitle_cues 已是时间轴绝对秒，禁止再加偏移。"""
    plan = _plan(root)
    out: list[dict[str, Any]] = []
    for shot in plan.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        for cue in shot.get("subtitle_cues") or []:
            if not isinstance(cue, dict):
                continue
            text = str(cue.get("text") or "").strip()
            if not text:
                continue
            try:
                start = float(cue.get("start_seconds") or 0)
                end = float(cue.get("end_seconds") or start)
            except (TypeError, ValueError):
                continue
            out.append({
                "text": text,
                "start_seconds": start,
                "end_seconds": end,
            })
    return out


def inspect_finish(
    root: Path,
    *,
    cli_profile: str = "",
    burn_subs: bool = False,
) -> dict[str, Any]:
    cube = lut_cube_path(root)
    raw_profile = explicit_profile(root, cli_profile=cli_profile)
    profile = raw_profile if get_profile(raw_profile) else ""
    title = script_title(root)
    draw = sanitize_drawtext(title)
    l3_raw = lower_third_source(root) if draw else ""
    cues = flatten_cues(root)
    return {
        "lut_path": str(cube) if cube is not None else "",
        "profile": profile,
        "title": title,
        "drawtext": draw,
        "lower_third": l3_raw,
        "cues": cues,
        "burn_subs": bool(burn_subs) and bool(cues),
        "need_ffmpeg": bool(cube) or bool(profile) or bool(draw) or (bool(burn_subs) and bool(cues)),
        "need_srt": bool(cues),
    }
