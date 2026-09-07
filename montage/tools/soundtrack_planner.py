"""soundtrack_planner — BGM/SFX 时间轴（只规划，不混音）。

默认整片一条 BGM（playbook.audio.bgm_id 或 music_mood）。scene_plan 编译进 ≥2 个
不同 bgm_id 时按场时间窗多轨（shot_id 空）。skip_bgm 用布尔，不用「无配乐」子串。
有 project_dir 时写 artifacts/soundtrack.json。不改 assets/bgm/INDEX.md。
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from lib.asset_catalog import get_catalog
from lib.licensing import collect_attributions
from montage.engine.artifacts import ArtifactStore
from montage.playbooks import get_playbook
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus
from montage.tools.voice_director import shots_with_timeline


def _playbook_dict(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict) and raw.get("id"):
        return raw
    if isinstance(raw, str) and raw.strip():
        return get_playbook(raw.strip())
    return None


def _pick(catalog, query: str, category: str) -> dict[str, Any] | None:
    hits = catalog.search(query or "", category=category, top_k=5)
    if hits:
        return hits[0]
    hits = catalog.search("", category=category, top_k=1)
    return hits[0] if hits else None


def _pick_bgm(catalog, audio: dict[str, Any]) -> dict[str, Any] | None:
    bgm_id = str(audio.get("bgm_id") or "").strip()
    if bgm_id and hasattr(catalog, "get"):
        hit = catalog.get(bgm_id)
        if hit:
            return hit
    mood = str(audio.get("music_mood") or "").strip() or "氛围"
    return _pick(catalog, mood, "bgm")


def _onset_offset(onset: str, duration: float) -> float:
    token = (onset or "start").strip().lower()
    if token in ("hit", "peak", "action", "中"):
        return round(max(duration * 0.4, 0.0), 3)
    if token in ("end", "tail", "尾"):
        return round(max(duration - 0.3, 0.0), 3)
    return 0.0


def _sfx_volume(hit: dict[str, Any]) -> float:
    tags = " ".join(str(t) for t in (hit.get("tags") or []))
    blob = f"{tags} {hit.get('id') or ''}"
    dur = float(hit.get("duration_seconds") or 0)
    if dur >= 30 or "环境音" in blob or "rain" in blob or "room-tone" in blob:
        return 0.35
    return 0.5


def _timeline_span(scenes: list[dict[str, Any]], shots: list[dict[str, Any]]) -> tuple[float, float]:
    starts: list[float] = []
    ends: list[float] = []
    for s in scenes:
        starts.append(float(s.get("start_seconds") or 0))
        ends.append(float(s.get("end_seconds") or 0))
    for sh in shots:
        starts.append(float(sh.get("start_seconds") or 0))
        end = float(sh.get("end_seconds") or 0)
        if end <= 0:
            end = float(sh.get("start_seconds") or 0) + float(sh.get("duration_seconds") or 0)
        ends.append(end)
    if not ends:
        return 0.0, 0.0
    return min(starts or [0.0]), max(ends)


def _scene_window(scene: dict[str, Any], shots: list[dict[str, Any]]) -> tuple[float, float]:
    sid = str(scene.get("id") or "")
    start = scene.get("start_seconds")
    end = scene.get("end_seconds")
    if start is not None and end is not None:
        try:
            s0, s1 = float(start), float(end)
            if s1 > s0:
                return s0, s1
        except (TypeError, ValueError):
            pass
    spans: list[tuple[float, float]] = []
    for sh in shots:
        if str(sh.get("scene_id") or "") != sid:
            continue
        s0 = float(sh.get("start_seconds") or 0)
        s1 = float(sh.get("end_seconds") or 0)
        if s1 <= s0:
            s1 = s0 + float(sh.get("duration_seconds") or 0)
        spans.append((s0, s1))
    if spans:
        return min(p[0] for p in spans), max(p[1] for p in spans)
    return float(scene.get("start_seconds") or 0), float(scene.get("end_seconds") or 0)


def _append_bgm_event(
    events: list[dict[str, Any]],
    findings: list[dict[str, str]],
    hit: dict[str, Any],
    *,
    scene_id: str,
    start: float,
    end: float,
    volume: float,
    root: Any,
) -> None:
    ev = _event_from_hit(
        hit,
        kind="bgm",
        scene_id=scene_id,
        shot_id="",
        start=start,
        end=end,
        volume=volume,
        assets_root=root,
    )
    events.append(ev)
    if not ev["available"]:
        findings.append({
            "severity": "warning",
            "field": f"bgm/{ev['asset_id']}",
            "message": f"{ev['asset_id']} 未下载",
            "proposed_fix": ev["download_hint"],
        })


def _plan_bgm_events(
    scenes: list[dict[str, Any]],
    shots: list[dict[str, Any]],
    audio: dict[str, Any],
    catalog: Any,
    findings: list[dict[str, str]],
    root: Any,
    music_volume: float,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    explicit = [str(s.get("bgm_id") or "").strip() for s in scenes]
    has_explicit = any(explicit)
    default_id = str(audio.get("bgm_id") or "").strip()
    if not default_id:
        picked = _pick_bgm(catalog, audio)
        default_id = str((picked or {}).get("id") or "")

    def emit_global(hit: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not hit:
            findings.append({
                "severity": "warning",
                "field": "bgm",
                "message": "资产目录无 BGM 条目",
                "proposed_fix": "检查 assets/bgm/INDEX.md 或 playbook.audio.bgm_id",
            })
            return []
        start, end = _timeline_span(scenes, shots)
        sid = str(scenes[0].get("id") or "") if scenes else ""
        _append_bgm_event(
            events, findings, hit,
            scene_id=sid, start=start, end=end, volume=music_volume, root=root,
        )
        return events

    if not has_explicit:
        return emit_global(_pick_bgm(catalog, audio))

    filled: list[tuple[dict[str, Any], str]] = []
    for scene in scenes:
        token = str(scene.get("bgm_id") or "").strip() or default_id
        filled.append((scene, token))
    ids = [fid for _, fid in filled if fid]
    illegal = [fid for fid in ids if catalog.get(fid) is None]
    unique: list[str] = []
    for fid in ids:
        if fid not in unique:
            unique.append(fid)
    if illegal:
        findings.append({
            "severity": "warning",
            "field": "bgm_id",
            "message": "非法 bgm_id: " + ", ".join(dict.fromkeys(illegal)),
            "proposed_fix": "改成 assets/bgm/INDEX.md 里已有的 id，将回落整片一首",
        })
        return emit_global(_pick_bgm(catalog, audio))
    if len(unique) <= 1:
        hit = catalog.get(unique[0]) if unique else _pick_bgm(catalog, audio)
        return emit_global(hit)

    for scene, fid in filled:
        hit = catalog.get(fid)
        if not hit:
            continue
        start, end = _scene_window(scene, shots)
        _append_bgm_event(
            events, findings, hit,
            scene_id=str(scene.get("id") or ""),
            start=start, end=end, volume=music_volume, root=root,
        )
    return events


def _event_from_hit(
    hit: dict[str, Any],
    *,
    kind: str,
    scene_id: str,
    shot_id: str = "",
    start: float,
    end: float,
    offset: float = 0.0,
    volume: float = 0.25,
    assets_root: Path | None = None,
) -> dict[str, Any]:
    file_val = str(hit.get("file") or "")
    available = bool(hit.get("available"))
    path = str(hit.get("path") or "")
    root = assets_root
    if root is None:
        from lib.asset_catalog import get_catalog as _gc

        root = _gc().root
    if not path and file_val and not file_val.startswith("("):
        path = str(root / file_val)
    if available and path and not Path(path).is_file():
        available = False
    return {
        "kind": kind,
        "scene_id": scene_id,
        "shot_id": shot_id,
        "start_seconds": round(start, 3),
        "end_seconds": round(end, 3),
        "offset_seconds": round(offset, 3),
        "asset_id": hit.get("id") or "",
        "title": hit.get("title") or "",
        "path": path,
        "available": available,
        "attribution": hit.get("attribution") or "",
        "license": hit.get("license") or "",
        "bpm": hit.get("bpm") or 0,
        "volume": volume,
        "download_hint": "" if available else (hit.get("download_hint") or "asset_retriever operation=resolve"),
    }


def plan_soundtrack(
    scene_plan: dict[str, Any] | None,
    playbook: dict[str, Any] | None = None,
    *,
    catalog: Any = None,
) -> dict[str, Any]:
    cat = catalog or get_catalog()
    pb = playbook if isinstance(playbook, dict) else None
    audio = (pb or {}).get("audio") or {}
    skip_bgm = bool(audio.get("skip_bgm"))
    music_volume = float(audio.get("music_volume") or 0.25)
    events: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    root = getattr(cat, "root", None)

    scenes = [s for s in ((scene_plan or {}).get("scenes") or []) if isinstance(s, dict)]
    shots = shots_with_timeline(scene_plan)

    if not skip_bgm:
        events.extend(_plan_bgm_events(
            scenes, shots, audio, cat, findings, root, music_volume,
        ))

    for shot in shots:
        scene_id = str(shot.get("scene_id") or "")
        shot_id = str(shot.get("shot_id") or "")
        start = float(shot.get("start_seconds") or 0)
        dur = float(shot.get("duration_seconds") or 0)
        end = float(shot.get("end_seconds") or start + dur)
        ap = shot.get("audio_prompt") if isinstance(shot.get("audio_prompt"), dict) else {}
        sfx_specs = list(ap.get("sfx") or []) if ap else []
        queries: list[tuple[str, str]] = []
        for spec in sfx_specs:
            if isinstance(spec, dict):
                sound = str(spec.get("sound") or spec.get("action_ref") or "").strip()
                onset = str(spec.get("onset") or "start")
                if sound:
                    queries.append((sound, onset))
            elif isinstance(spec, str) and spec.strip():
                queries.append((spec.strip(), "start"))
        if not queries:
            vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
            env = str(vd.get("environment") or "")
            verbs = []
            for sub in vd.get("subjects") or []:
                if isinstance(sub, dict):
                    action = sub.get("action") if isinstance(sub.get("action"), dict) else {}
                    verb = str(action.get("verb") or "").strip()
                    if verb and verb not in ("说话", "站立"):
                        verbs.append(verb)
            q = " ".join(verbs + ([env] if env else []))
            if q:
                queries.append((q, "start"))
        seen: set[str] = set()
        for query, onset in queries[:2]:
            hit = _pick(cat, query, "sfx")
            if not hit or hit.get("id") in seen:
                continue
            seen.add(str(hit.get("id") or ""))
            ev = _event_from_hit(
                hit,
                kind="sfx",
                scene_id=scene_id,
                shot_id=shot_id,
                start=start,
                end=end,
                offset=_onset_offset(onset, dur),
                volume=_sfx_volume(hit),
                assets_root=root,
            )
            events.append(ev)
            if not ev["available"]:
                findings.append({
                    "severity": "warning",
                    "field": f"sfx/{ev['asset_id']}",
                    "message": f"{ev['asset_id']} 未下载",
                    "proposed_fix": ev["download_hint"],
                })

    return {
        "events": events,
        "attributions": collect_attributions(events),
        "findings": findings,
    }


def copy_into_project_music(project_dir: str | Path, src_path: str) -> str:
    """钉选曲复制/硬链到项目 assets/music/；跨卷 link 失败则 copy。"""
    if not project_dir or not src_path:
        return src_path
    src = Path(src_path)
    if not src.is_file():
        return src_path
    dest_dir = Path(project_dir) / "assets" / "music"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    try:
        if dest.exists() and dest.resolve() == src.resolve():
            return str(dest)
        if dest.exists() and dest.stat().st_size == src.stat().st_size:
            return str(dest)
        if dest.exists():
            dest.unlink()
        try:
            os.link(src, dest)
        except OSError:
            shutil.copy2(src, dest)
        return str(dest)
    except OSError:
        return src_path


class SoundtrackPlanner(BaseTool):
    name = "soundtrack_planner"
    version = "0.2.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "properties": {
            "scene_plan": {"type": "object"},
            "playbook": {"description": "playbook id 或 dict"},
            "project_dir": {"type": "string"},
            "resolve": {"type": "boolean", "description": "规划后按 source_url 下载钉选文件"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        project_dir = str(inputs.get("project_dir") or "")
        store = ArtifactStore(project_dir) if project_dir else None
        scene_plan = inputs.get("scene_plan") if isinstance(inputs.get("scene_plan"), dict) else None
        if store:
            scene_plan = scene_plan or store.read("scene_plan")
        if not scene_plan:
            return ToolResult(success=False, error="需要 scene_plan")
        pb = _playbook_dict(inputs.get("playbook"))
        if pb is None and store:
            packet = store.read("proposal_packet") or {}
            pb = _playbook_dict(packet.get("playbook"))
        data = plan_soundtrack(scene_plan, pb)
        if inputs.get("resolve"):
            from montage.tools.asset_retriever import resolve_hit

            cat = get_catalog()
            findings = list(data.get("findings") or [])
            for ev in data["events"]:
                hit = cat.get(str(ev.get("asset_id") or ""))
                if not hit:
                    continue
                resolved = resolve_hit(hit, assets_root=cat.root)
                ev["available"] = bool(resolved.get("available"))
                if resolved.get("path"):
                    ev["path"] = resolved["path"]
                    if project_dir:
                        ev["path"] = copy_into_project_music(project_dir, ev["path"])
                if ev["available"]:
                    ev["download_hint"] = ""
                    findings = [f for f in findings if ev["asset_id"] not in (f.get("field") or "")]
                elif resolved.get("error"):
                    findings.append({
                        "severity": "warning",
                        "field": str(ev.get("asset_id") or ""),
                        "message": str(resolved.get("error")),
                        "proposed_fix": "补 INDEX source_url 或 asset_retriever operation=resolve",
                    })
            data["findings"] = findings
            data["attributions"] = collect_attributions(data["events"])
        if store:
            store.write("soundtrack", data)
        return ToolResult(success=True, data=data)
