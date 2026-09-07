"""place_audio — 把 soundtrack 事件叠到各 clip（SFX/分镜 BGM），不解析第二套时间轴给 assemble。

全局 BGM（kind=bgm 且无 shot_id）不叠 clip：仅 1 条时作为 ``music_path`` 交给 assemble；
≥2 条带时间窗时写入 ``music_segments`` 并清空 ``music_path``（adelay+amix duration=longest）。
clip 上已叠的 SFX 需要 assemble 传 ``mix_source_audio=true`` 才不会被旁白轨盖掉。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from montage.engine.artifacts import ArtifactStore
from montage.engine.policy import keep_embedded_audio, shot_keeps_embedded_audio
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus
from montage.tools.voice_director import shots_with_timeline


def _clip_windows(
    scene_plan: dict[str, Any] | None,
    cuts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """cuts[] 对齐镜头时间：优先 cut.shot_id / from_scene，否则按顺序对齐 shots。"""
    shots = shots_with_timeline(scene_plan)
    windows: list[dict[str, Any]] = []
    for i, cut in enumerate(cuts):
        path = cut.get("clip_path") or cut.get("path") or cut.get("output") or ""
        shot_id = str(cut.get("shot_id") or "")
        scene_id = str(cut.get("from_scene") or cut.get("scene_id") or "")
        matched = None
        if shot_id:
            matched = next((s for s in shots if str(s.get("shot_id") or "") == shot_id), None)
        if matched is None and scene_id:
            matched = next((s for s in shots if str(s.get("scene_id") or "") == scene_id), None)
        if matched is None and i < len(shots):
            matched = shots[i]
        start = float((matched or {}).get("start_seconds") or 0)
        dur = float((matched or {}).get("duration_seconds") or cut.get("duration_seconds") or 0)
        windows.append({
            "clip_path": str(path),
            "shot_id": shot_id or str((matched or {}).get("shot_id") or ""),
            "scene_id": scene_id or str((matched or {}).get("scene_id") or ""),
            "start_seconds": start,
            "end_seconds": start + dur,
            "duration_seconds": dur,
        })
    return windows


def events_for_clip(clip: dict[str, Any], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """本 clip 时间窗内的 SFX，以及绑在该镜上的 BGM。"""
    c0 = float(clip.get("start_seconds") or 0)
    c1 = float(clip.get("end_seconds") or c0)
    shot_id = str(clip.get("shot_id") or "")
    hits: list[dict[str, Any]] = []
    for ev in events:
        kind = str(ev.get("kind") or "")
        if kind == "sfx":
            t = float(ev.get("start_seconds") or 0) + float(ev.get("offset_seconds") or 0)
            if ev.get("shot_id") and str(ev.get("shot_id")) != shot_id and shot_id:
                if not (c0 - 0.05 <= t < c1 + 0.05):
                    continue
            elif not (c0 - 0.05 <= t < c1 + 0.05):
                continue
            item = dict(ev)
            item["delay_seconds"] = round(max(t - c0, 0.0), 3)
            hits.append(item)
        elif kind == "bgm" and ev.get("shot_id") and str(ev.get("shot_id")) == shot_id:
            item = dict(ev)
            item["delay_seconds"] = 0.0
            hits.append(item)
    return hits


def global_music_path(events: list[dict[str, Any]]) -> str:
    """整片 BGM：kind=bgm 且未绑 shot_id 的第一条已下载文件。"""
    tracks = timed_bgm_tracks(events)
    if len(tracks) >= 2:
        return ""
    if tracks:
        return str(tracks[0]["path"])
    return ""


def timed_bgm_tracks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """无 shot_id、已落盘的 BGM（含时间窗）。"""
    tracks: list[dict[str, Any]] = []
    for ev in events:
        if ev.get("kind") != "bgm" or ev.get("shot_id"):
            continue
        if not (ev.get("available") and ev.get("path") and Path(str(ev["path"])).exists()):
            continue
        tracks.append({
            "path": str(ev["path"]),
            "start_seconds": float(ev.get("start_seconds") or 0),
            "end_seconds": float(ev.get("end_seconds") or 0),
            "volume": float(ev.get("volume") or 0.25),
            "scene_id": str(ev.get("scene_id") or ""),
        })
    return tracks


class PlaceAudio(BaseTool):
    name = "place_audio"
    version = "0.1.0"
    capability = "compose"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "properties": {
            "edit_decisions": {"type": "object"},
            "scene_plan": {"type": "object"},
            "events": {"type": "array", "items": {"type": "object"}},
            "soundtrack": {"type": "object", "description": "soundtrack_planner 的返回值"},
            "project_dir": {"type": "string"},
            "output_dir": {"type": "string"},
        },
    }

    def __init__(
        self,
        *,
        overlay_fn: Callable[..., Path] | None = None,
    ) -> None:
        self._overlay_fn = overlay_fn

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        project_dir = str(inputs.get("project_dir") or "")
        store = ArtifactStore(project_dir) if project_dir else None
        decisions = inputs.get("edit_decisions") if isinstance(inputs.get("edit_decisions"), dict) else None
        scene_plan = inputs.get("scene_plan") if isinstance(inputs.get("scene_plan"), dict) else None
        soundtrack = inputs.get("soundtrack") if isinstance(inputs.get("soundtrack"), dict) else None
        events = inputs.get("events") if isinstance(inputs.get("events"), list) else None
        if store:
            decisions = decisions or store.read("edit_decisions")
            scene_plan = scene_plan or store.read("scene_plan")
            if soundtrack is None:
                soundtrack = store.read("soundtrack")
        if events is None:
            events = (soundtrack or {}).get("events") if isinstance(soundtrack, dict) else []
        if not decisions or not (decisions.get("cuts") or []):
            return ToolResult(success=False, error="需要 edit_decisions.cuts[]")
        prompts = store.read("shot_prompts") if store else None
        if keep_embedded_audio(prompts, project_dir):
            return ToolResult(
                success=True,
                data={
                    "edit_decisions": decisions,
                    "placed": [],
                    "music_path": "",
                    "music_segments": [],
                    "findings": [{
                        "severity": "warning",
                        "field": "audio_source",
                    "message": "片内音轨跳过配乐叠加，保留模型原生对白",
                    "proposed_fix": "不要对该镜调用 TTS / replace 音轨",
                    }],
                    "assemble_hints": {
                        "mix_source_audio": True,
                        "music_path": "",
                        "music_segments": [],
                        "ducking": False,
                        "loudnorm": True,
                    },
                },
            )
        native_ids = {
            str(s.get("shot_id") or "")
            for s in (prompts or {}).get("shots") or []
            if isinstance(s, dict) and shot_keeps_embedded_audio(s)
        }
        if not isinstance(events, list):
            events = []

        cuts = list(decisions.get("cuts") or [])
        windows = _clip_windows(scene_plan, cuts)
        out_dir = Path(inputs["output_dir"]) if inputs.get("output_dir") else (
            Path(project_dir) / "assets" / "placed" if project_dir else Path("assets/placed")
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        overlay = self._overlay_fn
        if overlay is None:
            from montage.compose.ffmpeg_engine import overlay_clip_audio

            overlay = overlay_clip_audio

        new_cuts: list[dict[str, Any]] = []
        placed: list[dict[str, Any]] = []
        findings: list[dict[str, str]] = []
        for i, (cut, clip) in enumerate(zip(cuts, windows)):
            src = Path(clip["clip_path"])
            local_events = [
                e for e in events_for_clip(clip, events)
                if e.get("path") and Path(str(e["path"])).exists()
            ]
            dest = out_dir / f"{clip.get('shot_id') or f'cut{i:02d}'}.mp4"
            if clip.get("shot_id") and str(clip.get("shot_id")) in native_ids:
                new_cuts.append(dict(cut))
                placed.append({
                    "shot_id": clip.get("shot_id"),
                    "path": str(src),
                    "overlaid": False,
                    "keep_embedded": True,
                })
                continue
            if not src.exists():
                findings.append({
                    "severity": "warning",
                    "field": clip.get("shot_id") or str(i),
                    "message": f"clip 不存在: {src}",
                    "proposed_fix": "先跑 shot_runner / 填 edit_decisions.clip_path",
                })
                new_cuts.append(dict(cut))
                continue
            if not local_events:
                new_cuts.append(dict(cut))
                placed.append({"shot_id": clip.get("shot_id"), "path": str(src), "overlaid": False})
                continue
            try:
                overlay(src, local_events, dest)
            except Exception as exc:  # noqa: BLE001
                findings.append({
                    "severity": "warning",
                    "field": str(clip.get("shot_id") or i),
                    "message": f"叠音失败: {exc}",
                    "proposed_fix": "安装 ffmpeg 或检查音效文件",
                })
                new_cuts.append(dict(cut))
                continue
            updated = dict(cut)
            updated["clip_path"] = str(dest)
            new_cuts.append(updated)
            placed.append({"shot_id": clip.get("shot_id"), "path": str(dest), "overlaid": True})

        tracks = timed_bgm_tracks(events)
        music_segments = tracks if len(tracks) >= 2 else []
        music_path = "" if music_segments else global_music_path(events)
        missing = [
            {
                "asset_id": e.get("asset_id"),
                "kind": e.get("kind"),
                "path": e.get("path"),
            }
            for e in events
            if isinstance(e, dict) and e.get("asset_id") and not (
                e.get("path") and Path(str(e["path"])).exists()
            )
        ]
        if missing:
            findings.append({
                "severity": "warning",
                "field": "soundtrack",
                "message": f"{len(missing)} 条音频未落盘",
                "proposed_fix": "soundtrack_planner resolve=true 或 asset_retriever operation=resolve",
            })
        out_decisions = dict(decisions)
        out_decisions["cuts"] = new_cuts
        if music_segments:
            out_decisions["music_segments"] = music_segments
        elif "music_segments" in out_decisions:
            out_decisions.pop("music_segments", None)
        if store:
            store.write("edit_decisions", out_decisions)
        return ToolResult(
            success=True,
            data={
                "edit_decisions": out_decisions,
                "placed": placed,
                "music_path": music_path,
                "music_segments": music_segments,
                "missing": missing,
                "findings": findings,
                "assemble_hints": {
                    "mix_source_audio": bool(native_ids) or any(p.get("overlaid") for p in placed),
                    "music_path": music_path,
                    "music_segments": music_segments,
                    "ducking": True,
                    "loudnorm": True,
                    "audio_incomplete": bool(missing),
                },
            },
        )
