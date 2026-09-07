"""release_pack — 封面抽帧 + 三平台简介模板 + publish_log（零 LLM）。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from montage.engine.artifacts import ArtifactStore
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

BLURB_LIMITS: dict[str, dict[str, int]] = {
    "douyin": {"title": 30, "body": 100, "hook": 24},
    "bilibili": {"title": 80, "body": 250, "hook": 24},
    "youtube": {"title": 100, "body": 500, "hook": 100},
}


def clip_chars(text: str, limit: int) -> str:
    """按 Unicode 码位截断，不切半个 surrogate。"""
    raw = str(text or "")
    if limit <= 0:
        return ""
    chars = list(raw)
    if len(chars) <= limit:
        return raw
    return "".join(chars[:limit])


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _first_narration(script: dict[str, Any]) -> str:
    for sec in script.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        text = str(sec.get("narration") or "").strip()
        if text:
            return text
    return ""


def collect_copy(project_dir: str | Path) -> dict[str, str]:
    root = Path(project_dir)
    script = _read_json(root / "artifacts" / "script.json")
    bible = _read_json(root / "artifacts" / "series_bible.json")
    project = _read_json(root / "project.json")
    structure = script.get("structure") if isinstance(script.get("structure"), dict) else {}
    title = str(script.get("title") or "").strip() or str(project.get("title") or "").strip()
    logline = str(bible.get("logline") or "").strip()
    hook = str(structure.get("hook") or "").strip() or logline
    body = _first_narration(script) or logline
    return {"title": title, "body": body, "hook": hook, "logline": logline}


def build_blurbs(copy: dict[str, str]) -> dict[str, dict[str, str]]:
    title = str(copy.get("title") or "")
    body = str(copy.get("body") or "")
    hook = str(copy.get("hook") or "")
    logline = str(copy.get("logline") or "")
    out: dict[str, dict[str, str]] = {}
    for platform, limits in BLURB_LIMITS.items():
        use_hook = logline if platform == "youtube" else hook
        out[platform] = {
            "title": clip_chars(title, limits["title"]),
            "body": clip_chars(body, limits["body"]),
            "hook": clip_chars(use_hook, limits["hook"]),
        }
    return out


def _cover_ok(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _ffmpeg_bin() -> str:
    from montage.compose.ffmpeg_engine import check_ffmpeg

    return check_ffmpeg() or ""


def _ffmpeg_run(cmd: list[str], timeout: int = 60) -> None:
    from montage.compose.ffmpeg_engine import _run

    _run(cmd, timeout=timeout)


def _film_duration(film: Path) -> float:
    from montage.compose.ffmpeg_engine import probe

    try:
        info = probe(film)
        return float((info.get("format") or {}).get("duration") or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _extract_last_cover(film: Path, dest: Path) -> str:
    from montage.compose.ffmpeg_engine import extract_last_frame

    try:
        extract_last_frame(film, dest)
    except Exception:  # noqa: BLE001
        return str(dest) if _cover_ok(dest) else ""
    return str(dest) if _cover_ok(dest) else ""


def _extract_cover(film: Path, dest: Path, *, seek: float) -> str:
    """封面抽帧：seek = 1.0 + title_dur；片长短于该点则抽最后一帧。"""
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg or not film.is_file():
        return ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    ss = max(0.0, float(seek))
    duration = _film_duration(film)
    if duration > 0 and ss >= duration:
        return _extract_last_cover(film, dest)
    try:
        _ffmpeg_run([
            ffmpeg, "-y", "-ss", f"{ss:.2f}", "-i", str(film),
            "-frames:v", "1", "-q:v", "3", str(dest),
        ], timeout=60)
    except Exception:  # noqa: BLE001
        pass
    if _cover_ok(dest):
        return str(dest)
    return _extract_last_cover(film, dest)


def write_release_pack(
    project_dir: str | Path,
    *,
    title_dur: float = 0.0,
) -> dict[str, Any]:
    root = Path(project_dir)
    store = ArtifactStore(root)
    copy = collect_copy(root)
    blurbs = build_blurbs(copy)
    findings: list[dict[str, str]] = []
    film = root / "renders" / "final.mp4"
    cover_path = root / "renders" / "cover.jpg"
    seek = 1.0 + max(0.0, float(title_dur or 0.0))
    cover = _extract_cover(film, cover_path, seek=seek)
    if not cover:
        findings.append({
            "severity": "warning",
            "field": "cover",
            "message": "封面未抽出（缺 ffmpeg 或成片不可抽帧）",
        })

    pack = {
        "cover": cover,
        "blurbs": blurbs,
        "title_dur": max(0.0, float(title_dur or 0.0)),
        "findings": findings,
    }
    store.write("release_pack", pack)

    existing = store.read("publish_log") or {}
    status = str(existing.get("status") or "")
    if status != "published":
        notes = " / ".join(
            f"{p}:{(blurbs.get(p) or {}).get('title') or ''}"
            for p in ("douyin", "bilibili", "youtube")
        )
        store.write("publish_log", {
            "status": "exported",
            "output_path": cover or "renders/final.mp4",
            "notes": clip_chars(notes, 200),
            "exported_at": datetime.now(timezone.utc).isoformat(),
        })
    return {
        "cover": cover,
        "blurbs": blurbs,
        "publish_log_path": str(root / "artifacts" / "publish_log.json"),
        "findings": findings,
    }


class ReleasePack(BaseTool):
    name = "release_pack"
    version = "0.1.0"
    capability = "export"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["project_dir"],
        "properties": {
            "project_dir": {"type": "string"},
            "title_dur": {"type": "number", "description": "片头秒数，封面抽帧会跳过这段"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        project_dir = Path(inputs.get("project_dir") or "")
        if not (project_dir / "project.json").is_file():
            return ToolResult(success=False, error=f"项目目录无效（缺 project.json）: {project_dir}")
        try:
            data = write_release_pack(
                project_dir,
                title_dur=float(inputs.get("title_dur") or 0),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"release_pack 失败: {exc}")
        return ToolResult(success=True, data=data)
