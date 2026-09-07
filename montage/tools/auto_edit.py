"""auto_edit — AutoEditor 编排器（快速路径，不进 7 阶段管线）。

纯逻辑层（无 ffmpeg）：build_plan / replan / rebuild_edits / 路径校验 / 指纹。
副作用层：probe / analyze / preview / render 调现有 ffmpeg 函数。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_engine import check_ffmpeg, probe as ffprobe
from montage.style_packs import apply_overrides, get_style_pack, validate_overrides
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus
from montage.tools.edit_advisor import suggest_transitions

SCHEMA_VERSION = "1.0"
RENDER_STEPS = ("workspace", "effects", "stitch", "grade", "profile", "final")


def ensure_dirs(project_dir: str | Path) -> tuple[Path, Path]:
    """幂等创建 auto_edit/ 与 tmp_autoedit/。不改 init_project。"""
    root = Path(project_dir)
    auto = root / "auto_edit"
    tmp = root / "tmp_autoedit"
    auto.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return auto, tmp


def assert_output_inside(project_dir: str | Path, path: str | Path) -> Path:
    """输出必须落在 auto_edit/ 或 tmp_autoedit/ 内。"""
    root = Path(project_dir).resolve()
    resolved = Path(path).resolve()
    for base in (root / "auto_edit", root / "tmp_autoedit"):
        try:
            resolved.relative_to(base.resolve())
            return resolved
        except ValueError:
            continue
    raise ValueError(f"输出路径必须在 auto_edit/ 或 tmp_autoedit/ 内: {resolved}")


def hash_file(path: str | Path) -> str:
    """分块 sha256，避免大文件一次性进内存。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_integrity(files: list[dict[str, Any]]) -> dict[str, Any]:
    details = []
    unchanged = True
    for item in files:
        path = Path(item["path"])
        current = hash_file(path) if path.is_file() else ""
        match = current == item.get("sha256")
        if not match:
            unchanged = False
        details.append({
            "path": str(path), "expected": item.get("sha256"),
            "actual": current, "match": match,
        })
    return {"unchanged": unchanged, "files": details}


def new_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_project_meta(project_dir: str | Path) -> Path:
    """缺 project.json 时原地写最小元数据（不调用 init_project）。"""
    root = Path(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    meta_path = root / "project.json"
    if meta_path.exists():
        return root
    meta = {
        "version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_id": root.name,
        "title": root.name,
        "pipeline_type": "cinematic",
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def _unique_points(duration: float, scene_changes: list[float]) -> list[float]:
    points = [0.0]
    for t in scene_changes:
        if 0.05 < t < duration - 0.05:
            points.append(float(t))
    points.append(float(duration))
    points = sorted(set(round(p, 3) for p in points))
    if points[0] != 0.0:
        points.insert(0, 0.0)
    last = round(duration, 3)
    if points[-1] != last:
        points.append(last)
    return points


def shots_from_cuts(
    duration: float,
    scene_changes: list[float],
    *,
    min_hold: float,
    max_hold: float,
    segment_id: str,
) -> list[dict[str, Any]]:
    """scene_changes → shots；过短合并、过长按 max_hold 切开。"""
    points = _unique_points(duration, scene_changes)
    merged = [points[0]]
    for p in points[1:-1]:
        if p - merged[-1] >= min_hold:
            merged.append(p)
    merged.append(points[-1])
    if len(merged) > 2 and merged[-1] - merged[-2] < min_hold:
        merged.pop(-2)

    shots: list[dict[str, Any]] = []
    idx = 0
    for i in range(len(merged) - 1):
        start, end = merged[i], merged[i + 1]
        span = end - start
        if max_hold > 0 and span > max_hold + 0.05:
            n = max(2, int(round(span / max_hold)))
            step = span / n
            for k in range(n):
                s = start + k * step
                e = end if k == n - 1 else start + (k + 1) * step
                shots.append({
                    "shot_id": f"{segment_id}_shot_{idx}",
                    "start": round(s, 3), "end": round(e, 3), "speed": 1.0,
                })
                idx += 1
        else:
            shots.append({
                "shot_id": f"{segment_id}_shot_{idx}",
                "start": round(start, 3), "end": round(end, 3), "speed": 1.0,
            })
            idx += 1
    return shots


def _effective_pack(plan: dict[str, Any], pack: dict[str, Any] | None = None) -> dict[str, Any]:
    """replan 时优先用 plan.params 里已生效的转场/时长，避免丢掉首次 pack 覆写。"""
    base = dict(pack or get_style_pack(plan.get("style_pack") or "") or {})
    params = plan.get("params") or {}
    if params.get("transitions"):
        base["transitions"] = list(params["transitions"])
    if "transition_duration" in params:
        pacing = dict(base.get("pacing") or {})
        pacing["transition_duration"] = params["transition_duration"]
        base["pacing"] = pacing
    if "lut" in params:
        base["lut"] = params.get("lut") or ""
    return base


def rebuild_edits(plan: dict[str, Any], pack: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """只对相邻 keep 段之镜头生成转场；调 suggest_transitions 后用 StylePack 覆写。"""
    pack = _effective_pack(plan, pack)
    kept: list[tuple[str, dict[str, Any]]] = []
    for seg in plan.get("segments") or []:
        if seg.get("action", "keep") != "keep":
            continue
        for shot in seg.get("shots") or []:
            kept.append((seg["segment_id"], shot))
    if len(kept) < 2:
        return []

    fake_scenes = [{"id": shot["shot_id"]} for _, shot in kept]
    advisor_style = "documentary" if plan.get("style_pack") == "documentary" else "cinematic"
    suggestions = suggest_transitions(fake_scenes, advisor_style)

    default_t = (pack.get("transitions") or ["cut"])[0]
    tdur = float((pack.get("pacing") or {}).get("transition_duration") or 0.0)
    if default_t == "cut":
        tdur = 0.0

    edits: list[dict[str, Any]] = []
    for i, _sug in enumerate(suggestions):
        from_seg, from_shot = kept[i]
        to_seg, to_shot = kept[i + 1]
        edits.append({
            "from": from_shot["shot_id"],
            "to": to_shot["shot_id"],
            "from_segment": from_seg,
            "to_segment": to_seg,
            "transition": default_t,
            "transition_duration": tdur,
        })
    return edits


def build_plan(
    *,
    source_decl: dict[str, Any],
    style_pack_id: str,
    scene_changes_by_source: list[list[float]],
    session_id: str | None = None,
    target_duration: float | None = None,
    pack_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pack = get_style_pack(style_pack_id)
    if pack is None:
        raise ValueError(f"未知风格包: {style_pack_id}")
    pack = apply_overrides(pack, pack_overrides)
    pacing = pack.get("pacing") or {}
    min_hold = float(pacing.get("min_hold") or 1.0)
    max_hold = float(pacing.get("max_hold") or 8.0)
    is_speech = bool(source_decl.get("is_speech"))

    files = list(source_decl.get("files") or [])
    segments: list[dict[str, Any]] = []
    for i, item in enumerate(files):
        duration = float(item.get("duration") or 0.0)
        sid = f"seg_{i}"
        changes = scene_changes_by_source[i] if i < len(scene_changes_by_source) else []
        shots = shots_from_cuts(
            duration, changes, min_hold=min_hold, max_hold=max_hold, segment_id=sid,
        )
        if is_speech:
            for shot in shots:
                shot["speed"] = 1.0
        segments.append({
            "segment_id": sid,
            "source_index": i,
            "src_start": 0.0,
            "src_end": duration,
            "action": "keep",
            "shots": shots,
        })

    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id or new_session_id(),
        "stage": "plan",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source_decl": source_decl,
        "style_pack": style_pack_id,
        "bind_playbook": pack.get("bind_playbook"),
        "output_profile": pack.get("output_profile") or "youtube_landscape",
        "params": {
            "target_duration": target_duration,
            "bpm": None,
            "min_hold": min_hold,
            "max_hold": max_hold,
            "lut": pack.get("lut") or "",
            "transitions": list(pack.get("transitions") or ["cut"]),
            "transition_duration": float(pacing.get("transition_duration") or 0.0),
        },
        "segments": segments,
        "edits": [],
    }
    plan["edits"] = rebuild_edits(plan, pack)
    if target_duration:
        plan = _fit_target_duration(plan, float(target_duration))
        plan["edits"] = rebuild_edits(plan, pack)
    return plan


def _fit_target_duration(plan: dict[str, Any], target: float) -> dict[str, Any]:
    def _total() -> float:
        acc = 0.0
        for seg in plan["segments"]:
            if seg.get("action") != "keep":
                continue
            for shot in seg["shots"]:
                acc += (shot["end"] - shot["start"]) / max(float(shot.get("speed") or 1.0), 0.01)
        return acc

    while _total() > target + 0.05:
        dropped = False
        for seg in reversed(plan["segments"]):
            if seg.get("action") != "keep" or not seg["shots"]:
                continue
            seg["shots"].pop()
            dropped = True
            break
        if not dropped:
            break
    return plan


def replan(plan: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    """幂等应用 overrides。"""
    if not overrides:
        return plan
    result = json.loads(json.dumps(plan))
    is_speech = bool((result.get("source_decl") or {}).get("is_speech"))
    shots_by_id: dict[str, dict[str, Any]] = {}
    segs_by_id: dict[str, dict[str, Any]] = {}
    for seg in result["segments"]:
        segs_by_id[seg["segment_id"]] = seg
        for shot in seg["shots"]:
            shots_by_id[shot["shot_id"]] = shot

    if overrides.get("target_duration") is not None:
        result.setdefault("params", {})["target_duration"] = float(overrides["target_duration"])

    for key, val in overrides.items():
        if key == "target_duration":
            continue
        if not isinstance(val, dict):
            continue
        if key in segs_by_id and "action" in val:
            action = val["action"]
            if action not in ("keep", "drop"):
                raise ValueError(f"未知 action: {action}")
            segs_by_id[key]["action"] = action
        elif key in shots_by_id:
            shot = shots_by_id[key]
            if "start" in val:
                shot["start"] = float(val["start"])
            if "end" in val:
                shot["end"] = float(val["end"])
            if "speed" in val:
                speed = float(val["speed"])
                shot["speed"] = 1.0 if is_speech else max(0.8, min(1.2, speed))
            if shot["end"] <= shot["start"]:
                raise ValueError(f"{key} 的 end 必须大于 start")

    if result.get("params", {}).get("target_duration"):
        result = _fit_target_duration(result, float(result["params"]["target_duration"]))
    result["edits"] = rebuild_edits(result)
    result["stage"] = "plan"
    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    return result


def plan_differs(a: dict[str, Any], b: dict[str, Any]) -> bool:
    def _core(p: dict[str, Any]) -> str:
        slim = {"segments": p.get("segments"), "edits": p.get("edits"), "params": p.get("params")}
        return json.dumps(slim, sort_keys=True, ensure_ascii=False)
    return _core(a) != _core(b)


def progress_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / "tmp_autoedit" / "progress.json"


def load_progress(project_dir: str | Path) -> dict[str, Any]:
    path = progress_path(project_dir)
    if not path.exists():
        return {"steps": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"steps": {}}


def mark_step(project_dir: str | Path, name: str, output: str | Path) -> None:
    ensure_dirs(project_dir)
    data = load_progress(project_dir)
    out = Path(output)
    sha = hash_file(out) if out.is_file() else ""
    data.setdefault("steps", {})[name] = {"output": str(out), "sha256": sha, "done": True}
    progress_path(project_dir).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def step_complete(project_dir: str | Path, name: str) -> Path | None:
    info = (load_progress(project_dir).get("steps") or {}).get(name) or {}
    if not info.get("done"):
        return None
    out = Path(info.get("output") or "")
    if not out.is_file() or hash_file(out) != info.get("sha256"):
        return None
    return out


def probe_sources(
    paths: list[Path],
    *,
    kind: str = "video",
    has_bgm: bool = False,
    is_speech: bool = False,
    language: str = "zh",
) -> dict[str, Any]:
    files = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"输入不存在或不可读: {path}")
        info = ffprobe(path)
        duration = float((info.get("format") or {}).get("duration") or 0.0)
        files.append({
            "path": str(path.resolve()),
            "sha256": hash_file(path),
            "duration": duration,
        })
    return {
        "kind": kind, "has_bgm": has_bgm, "is_speech": is_speech,
        "language": language, "files": files,
    }


def analyze_scene_changes(path: Path, *, threshold: float = 0.3) -> list[float]:
    from montage.tools.video_probe import SceneDetect

    result = SceneDetect().execute({"path": str(path), "threshold": threshold})
    if not result.success:
        raise RuntimeError(result.error or "scene_detect 失败")
    return list(result.data.get("scene_changes") or [])


def resolve_lut_path(lut_id: str) -> Path | None:
    from montage.compose.ffmpeg_engine import resolve_lut_file

    return resolve_lut_file(lut_id)


def _log_decision(project_dir: Path, session_id: str, choice: str) -> None:
    from montage.engine.budget import BudgetLedger
    from montage.engine.decisions import DecisionLog

    meta_id = project_dir.name
    meta_path = project_dir / "project.json"
    if meta_path.exists():
        try:
            meta_id = json.loads(meta_path.read_text(encoding="utf-8")).get("project_id") or meta_id
        except json.JSONDecodeError:
            pass
    DecisionLog(project_dir / "decisions.jsonl").log(
        "auto_edit",
        f"auto_edit/{meta_id}/{session_id}",
        choice,
        options_considered=[choice],
    )
    ledger = BudgetLedger(project_dir / "cost.jsonl")
    eid = ledger.estimate("auto_edit", f"auto_edit/{meta_id}/{session_id}/local", "auto_edit", 0.0)
    ledger.settle(eid, 0.0)


def _black_video_with_audio(src_path: Path, duration: float, dest: Path) -> Path:
    """audio_only：黑场画面 + 原音频，时长取音频时长。"""
    ff = check_ffmpeg()
    if not ff:
        raise RuntimeError("缺少 ffmpeg")
    from montage.compose.ffmpeg_engine import _run

    dur = max(float(duration), 0.1)
    _run([
        ff, "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=1920x1080:d={dur:.3f}:r=30",
        "-i", str(src_path),
        "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-movflags", "+faststart",
        str(dest),
    ])
    return dest


def _kept_shots(plan: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    out: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    files = (plan.get("source_decl") or {}).get("files") or []
    for seg in plan.get("segments") or []:
        if seg.get("action", "keep") != "keep":
            continue
        src = files[int(seg["source_index"])]
        for shot in seg.get("shots") or []:
            out.append((seg, shot, src))
    return out


def render_plan(
    project_dir: str | Path,
    plan: dict[str, Any],
    *,
    preview: bool = False,
    resume: bool = True,
) -> dict[str, Any]:
    from montage.compose import effects
    from montage.compose import ffmpeg_engine as fe
    from montage.tools.system_probe import build_hardware_profile

    root = Path(project_dir)
    auto_dir, tmp = ensure_dirs(root)
    report: dict[str, Any] = {"output_path": "", "render_log": []}

    def _fail(step: str, err: str, partial: dict[str, Any]) -> dict[str, Any]:
        report.update({"failed_step": step, "error": err, "partial_outputs": partial})
        (auto_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return report

    files = (plan.get("source_decl") or {}).get("files") or []
    try:
        integrity = verify_integrity(files)
    except OSError as exc:
        return _fail("workspace", str(exc), {})
    report["source_integrity"] = integrity

    hw = build_hardware_profile()
    report["hardware_profile"] = hw
    report["hardware_crf"] = int((hw.get("encode") or {}).get("crf") or 18)

    pack = get_style_pack(plan.get("style_pack") or "") or {}
    profile_name = plan.get("output_profile") or pack.get("output_profile") or "youtube_landscape"
    kept = _kept_shots(plan)
    if not kept:
        return _fail("effects", "没有可保留的镜头", {})

    kind = (plan.get("source_decl") or {}).get("kind") or "video"
    is_speech = bool((plan.get("source_decl") or {}).get("is_speech"))
    audio_cache: dict[int, Path] = {}
    clip_paths: list[Path] = []
    for i, (seg, shot, src) in enumerate(kept):
        clip = tmp / f"shot_{i:04d}.mp4"
        assert_output_inside(root, clip)
        if resume and clip.is_file():
            clip_paths.append(clip)
            continue
        src_path = Path(src["path"])
        if kind == "audio_only":
            idx = int(seg["source_index"])
            if idx not in audio_cache:
                placeholder = tmp / f"audio_src_{idx:02d}.mp4"
                assert_output_inside(root, placeholder)
                _black_video_with_audio(src_path, float(src.get("duration") or 0), placeholder)
                audio_cache[idx] = placeholder
            src_path = audio_cache[idx]
        start = float(shot["start"])
        dur = float(shot["end"]) - start
        fe.trim_clip(src_path, clip, start, dur)
        if is_speech:
            silenced = tmp / f"shot_{i:04d}_sil.mp4"
            assert_output_inside(root, silenced)
            effects.cut_silence(clip, silenced, threshold_db=-35.0)
            clip = silenced
        speed = float(shot.get("speed") or 1.0)
        if abs(speed - 1.0) > 0.01:
            sped = tmp / f"shot_{i:04d}_speed.mp4"
            assert_output_inside(root, sped)
            effects.change_speed(clip, sped, factor=speed)
            clip = sped
        clip_paths.append(clip)

    if clip_paths:
        mark_step(root, "effects", clip_paths[-1])

    joined = tmp / "joined.mp4"
    assert_output_inside(root, joined)
    skipped = resume and step_complete(root, "stitch")
    if skipped:
        joined = skipped
    elif len(clip_paths) == 1:
        fe.concat_videos(clip_paths, joined)
        mark_step(root, "stitch", joined)
    else:
        transitions = []
        for edit in plan.get("edits") or []:
            transitions.append({
                "transition_in": edit.get("transition") or "cut",
                "transition_duration": float(edit.get("transition_duration") or 0.0),
            })
        while len(transitions) < len(clip_paths) - 1:
            transitions.append({"transition_in": "cut", "transition_duration": 0.0})
        fe.stitch_with_transitions(clip_paths, transitions[: len(clip_paths) - 1], joined)
        mark_step(root, "stitch", joined)

    current = joined
    lut_id = (plan.get("params") or {}).get("lut") or pack.get("lut") or ""
    lut_path = resolve_lut_path(str(lut_id))
    if lut_path is not None:
        graded = tmp / "graded.mp4"
        assert_output_inside(root, graded)
        skipped = resume and step_complete(root, "grade")
        if skipped:
            current = skipped
        else:
            fe.apply_lut(current, lut_path, graded)
            mark_step(root, "grade", graded)
            current = graded
        report["note"] = ""
    elif lut_id:
        report["note"] = f"LUT {lut_id} 文件不可用，已跳过调色"

    if preview:
        preview_path = tmp / "preview.mp4"
        assert_output_inside(root, preview_path)
        ff = check_ffmpeg()
        if not ff:
            return _fail("preview", "缺少 ffmpeg", {"current": str(current)})
        from montage.compose.ffmpeg_engine import _run

        _run([
            ff, "-y", "-i", str(current),
            "-vf", "scale=-2:480",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", "-movflags", "+faststart", str(preview_path),
        ])
        report["output_path"] = str(preview_path)
        report["encoding"] = "h264-preview-480p"
        return report

    profiled = tmp / "profiled.mp4"
    assert_output_inside(root, profiled)
    skipped = resume and step_complete(root, "profile")
    if skipped:
        current = skipped
    else:
        fe.apply_profile(current, profile_name, profiled)
        mark_step(root, "profile", profiled)
        current = profiled

    final = auto_dir / "final.mp4"
    assert_output_inside(root, final)
    if current.resolve() != final.resolve():
        final.write_bytes(current.read_bytes())
    mark_step(root, "final", final)
    report["output_path"] = str(final)
    report["encoding"] = "h264"
    try:
        info = ffprobe(final)
        report["duration_seconds"] = float((info.get("format") or {}).get("duration") or 0)
    except Exception:  # noqa: BLE001
        report["duration_seconds"] = 0
    report["source_integrity"] = verify_integrity(files)
    (auto_dir / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (auto_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return report


class AutoEdit(BaseTool):
    name = "auto_edit"
    version = "0.1.0"
    capability = "auto_edit"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["operation", "project_dir"],
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["probe", "analyze", "plan", "preview", "replan", "render"],
            },
            "project_dir": {"type": "string"},
            "video": {"type": "string"},
            "clips": {"type": "array", "items": {"type": "string"}},
            "audio_only": {"type": "string"},
            "style": {"type": "string", "default": "documentary"},
            "profile": {"type": "string"},
            "overrides": {"type": "object"},
            "has_bgm": {"type": "boolean", "default": False},
            "is_speech": {"type": "boolean", "default": False},
            "language": {"type": "string", "default": "zh"},
            "target_duration": {"type": "number"},
            "resume": {"type": "boolean", "default": True},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if check_ffmpeg() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        op = inputs.get("operation")
        project_dir = Path(inputs.get("project_dir") or "")
        if not str(project_dir):
            return ToolResult(success=False, error="缺少 project_dir")
        try:
            ensure_project_meta(project_dir)
            auto_dir, _tmp = ensure_dirs(project_dir)
            if op == "probe":
                return self._probe(inputs)
            if op == "analyze":
                return self._analyze(inputs)
            if op == "plan":
                return self._plan(inputs, project_dir, auto_dir)
            if op == "replan":
                return self._replan(inputs, project_dir, auto_dir)
            if op == "preview":
                return self._render(inputs, project_dir, auto_dir, preview=True)
            if op == "render":
                return self._render(inputs, project_dir, auto_dir, preview=False)
            return ToolResult(success=False, error=f"未知 operation: {op}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=str(exc))

    def _collect_paths(self, inputs: dict[str, Any]) -> tuple[list[Path], str]:
        video = inputs.get("video")
        clips = inputs.get("clips") or []
        audio_only = inputs.get("audio_only")
        present = sum(bool(x) for x in (video, clips, audio_only))
        if present != 1:
            raise ValueError("video / clips / audio_only 必须三选一")
        if video:
            return [Path(video)], "video"
        if audio_only:
            return [Path(audio_only)], "audio_only"
        return [Path(p) for p in clips], "video"

    def _probe(self, inputs: dict[str, Any]) -> ToolResult:
        paths, kind = self._collect_paths(inputs)
        decl = probe_sources(
            paths, kind=kind,
            has_bgm=bool(inputs.get("has_bgm")),
            is_speech=bool(inputs.get("is_speech")),
            language=str(inputs.get("language") or "zh"),
        )
        return ToolResult(success=True, data={"source_decl": decl})

    def _analyze(self, inputs: dict[str, Any]) -> ToolResult:
        paths, kind = self._collect_paths(inputs)
        style = inputs.get("style") or "documentary"
        threshold = 0.15 if style == "beat" else 0.3
        if kind == "audio_only":
            changes = [[] for _ in paths]
        else:
            changes = [analyze_scene_changes(p, threshold=threshold) for p in paths]
        return ToolResult(success=True, data={"scene_changes_by_source": changes, "threshold": threshold})

    def _plan(self, inputs: dict[str, Any], project_dir: Path, auto_dir: Path) -> ToolResult:
        paths, kind = self._collect_paths(inputs)
        style = str(inputs.get("style") or "documentary")
        if get_style_pack(style) is None:
            return ToolResult(success=False, error=f"未知风格包: {style}")
        overrides = inputs.get("overrides") or {}
        pack_over = {
            k: overrides[k]
            for k in ("lut", "transitions", "pacing", "output_profile", "bind_playbook")
            if k in overrides
        }
        if inputs.get("profile"):
            pack_over["output_profile"] = inputs["profile"]
        if pack_over:
            errs = validate_overrides(pack_over)
            if errs:
                return ToolResult(success=False, error="；".join(errs))
        decl = probe_sources(
            paths, kind=kind,
            has_bgm=bool(inputs.get("has_bgm")),
            is_speech=bool(inputs.get("is_speech")),
            language=str(inputs.get("language") or "zh"),
        )
        threshold = 0.15 if style == "beat" else 0.3
        if kind == "audio_only":
            changes = [[] for _ in decl["files"]]
        else:
            changes = [analyze_scene_changes(Path(f["path"]), threshold=threshold) for f in decl["files"]]
        plan = build_plan(
            source_decl=decl, style_pack_id=style,
            scene_changes_by_source=changes,
            target_duration=inputs.get("target_duration"),
            pack_overrides=pack_over or None,
        )
        (auto_dir / "plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        _log_decision(project_dir, plan["session_id"], style)
        return ToolResult(success=True, data={"plan": plan, "path": str(auto_dir / "plan.json")})

    def _replan(self, inputs: dict[str, Any], project_dir: Path, auto_dir: Path) -> ToolResult:
        plan_path = auto_dir / "plan.json"
        if not plan_path.exists():
            return ToolResult(success=False, error="缺少 plan.json，请先 plan")
        first = json.loads(plan_path.read_text(encoding="utf-8"))
        nxt = replan(first, inputs.get("overrides") or {})
        plan_path.write_text(json.dumps(nxt, ensure_ascii=False, indent=2), encoding="utf-8")
        _log_decision(project_dir, nxt.get("session_id") or "", nxt.get("style_pack") or "")
        return ToolResult(
            success=True,
            data={"plan": nxt, "changed": plan_differs(first, nxt), "path": str(plan_path)},
        )

    def _render(self, inputs: dict[str, Any], project_dir: Path, auto_dir: Path, *, preview: bool) -> ToolResult:
        plan_path = auto_dir / "plan.json"
        if not plan_path.exists():
            return ToolResult(success=False, error="缺少 plan.json，请先 plan")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        report = render_plan(
            project_dir, plan, preview=preview, resume=bool(inputs.get("resume", True)),
        )
        if report.get("failed_step"):
            return ToolResult(success=False, error=report.get("error") or "render 失败", data=report)
        return ToolResult(success=True, data=report)
