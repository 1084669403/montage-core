"""produce 共享基础设施：锁 / 进度 / 校验 / 工具包 / 编排辅助。

从 produce.py 抽出，供 produce 与 produce_director 依赖的公共层。
不 import produce / produce_director，避免双向 import 环。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from montage.engine.artifacts import ArtifactStore
from montage.engine.director import is_director_await
from montage.engine.policy import load_loop_policy
from montage.toolbase import BaseTool, ToolResult
from montage.tools._shot_refs import (
    character_forms,
    dedupe_items_by_id,
    form_id_of,
    form_subject,
)
from montage.tools.compose_planner import clip_path_for_shot, is_still_image
from montage.tools.shot_runner import clips_compose_ready, collect_cast_jobs, collect_shots
from montage.tools.voice_director import shots_with_timeline

STEP_IDS = (
    "soundtrack",
    "compose_plan",
    "realize",
    "place_audio",
    "assemble",
    "finish",
    "release",
    "export",
)

IDEA_STEP_IDS = (
    "cascade",
    "validate_bible",
    "compile",
)

GEN_STEP_IDS = (
    "shot_dry_run",
    "shot_generate",
    "shot_bind",
    "voice",
)

GEN_PIPELINES = frozenset({"cinematic", "documentary"})

_OVERLAY_FAIL_PREFIX = "叠音失败:"


def _want_bible_cast(root: Path, progress: dict[str, Any]) -> bool:
    """bible 默认路径在 dry_run 前出定妆；导演档已有 await_cast，口播跳过。"""
    if str(progress.get("review") or "") == "director":
        return False
    if _pipeline_type(root) not in GEN_PIPELINES:
        return False
    from montage.providers.agnes import is_video_v25, want_video_v20
    from montage.tools.script_validator import is_spoken_mode

    store = ArtifactStore(root)
    bible = store.read("series_bible")
    if not isinstance(bible, dict):
        return False
    card = store.read("format_card")
    if is_spoken_mode(bible=bible, format_card=card if isinstance(card, dict) else None):
        return False
    loop = str(load_loop_policy(root).get("video_loop") or "").strip().lower()
    scene_plan = store.read("scene_plan")
    plan = scene_plan if isinstance(scene_plan, dict) else None
    if loop == "kling":
        return bool(collect_cast_jobs(bible, scene_plan=plan, video_loop="kling"))
    if want_video_v20() or not is_video_v25():
        return False
    if loop not in ("", "agnes"):
        return False
    return bool(collect_cast_jobs(bible))


class ProduceError(RuntimeError):
    """启动校验或锁冲突。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(process_query, False, int(pid))
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return int(code.value) == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _lock_path(project_dir: Path) -> Path:
    return project_dir / "artifacts" / "produce.lock"


def _progress_path(project_dir: Path) -> Path:
    return project_dir / "artifacts" / "produce_progress.json"


def acquire_lock(project_dir: Path) -> None:
    path = _lock_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"pid": os.getpid(), "created_at": _now()},
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    for attempt in range(2):
        try:
            fd = os.open(str(path), flags)
        except FileExistsError:
            other = 0
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                other = int(data.get("pid") or 0)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                other = 0
            if other and _pid_alive(other) and other != os.getpid():
                raise ProduceError(
                    f"produce 已在运行（pid={other}），请等待或删 artifacts/produce.lock"
                )
            try:
                path.unlink()
            except OSError:
                pass
            if attempt == 0:
                continue
            raise ProduceError("produce 已在运行，请等待或删 artifacts/produce.lock")
        else:
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
            return


def release_lock(project_dir: Path) -> None:
    path = _lock_path(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if int(data.get("pid") or 0) in (0, os.getpid()):
            path.unlink(missing_ok=True)
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def load_progress(project_dir: Path) -> dict[str, Any]:
    path = _progress_path(project_dir)
    if not path.is_file():
        return {"version": "1", "status": "running", "steps": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProduceError("进度文件损坏") from exc
    if not isinstance(data, dict):
        raise ProduceError("进度文件损坏")
    data.setdefault("version", "1")
    data.setdefault("steps", {})
    return data


def next_action(progress: dict[str, Any], project_dir: str | Path) -> dict[str, Any]:
    """给 Skill 看的下一步 argv（本机 sys.executable；换机请只用 produce 起的参数）。"""
    st = str(progress.get("status") or "")
    root = str(project_dir)
    base = [sys.executable, "-m", "montage", "produce", root]
    if st == "await_bible":
        return {
            "argv": base,
            "note": "尚未编译；改完 series_bible.json 后再 produce，不要 --idea",
        }
    if st == "await_prompt":
        return {
            "argv": [],
            "note": "提示词超 3000 字。要改圣经就改完后 produce（不要 --resume）；不改则 produce --resume 走压缩兜底",
        }
    if is_director_await(st):
        return {
            "argv": base + ["--resume"],
            "note": "确认 REVIEW 摘要（要改则先改 bible/scene_plan）后再 --resume；不要 --idea",
        }
    if st == "compiled":
        return {
            "argv": base + ["--review", "none"],
            "note": "已跳过 bible 停，直接拼/生成",
        }
    if st in ("await_sample", "await_retry", "await_episode"):
        return {"argv": base + ["--resume"], "note": "继续（不是人审）"}
    if st == "ok":
        return {"argv": [], "note": "结束"}
    if st == "need_bible":
        return {"argv": [], "note": "写入 series_bible.json 后再次 produce --idea"}
    if st == "over_hero":
        return {"argv": [], "note": "--trim-hero 或 --all-video"}
    err = str(progress.get("error") or "")
    ids = _clean_retry_ids(progress.get("retryable_ids") or [])
    if st == "fail" and ids:
        return {
            "argv": base + ["--retry", ",".join(ids)],
            "note": err or "有坏镜，先 dry_run 再 --yes",
        }
    if st == "fail" and "season-concat" in err:
        return {"argv": base + ["--season-concat", "--resume"], "note": err}
    if st == "fail" and ("--yes" in err or "await_retry" in err):
        return {"argv": base + ["--resume"], "note": err}
    if st == "fail" and ("--review none" in err or "MONTAGE_HEADLESS" in err):
        return {"argv": base + ["--review", "none"], "note": err}
    return {"argv": [], "note": err or ""}


_SEASON_CONCAT_IGNORED = {
    "severity": "warning",
    "field": "season_concat",
    "message": "--season-concat 仅系列根在全集完成后写入 renders/season.mp4（已忽略）",
}


def _with_season_concat_ignored(
    result: dict[str, Any],
    want: bool,
    project_dir: str | Path | None = None,
) -> dict[str, Any]:
    """扁平 / --idea 带着 --season-concat：不 fail，只记 warning。"""
    if not want or not isinstance(result, dict):
        return result
    progress = result.get("progress")
    if not isinstance(progress, dict):
        return result
    findings = [f for f in (progress.get("findings") or []) if isinstance(f, dict)]
    if not any(f.get("field") == "season_concat" for f in findings):
        findings.append(dict(_SEASON_CONCAT_IGNORED))
        progress["findings"] = findings
        result["progress"] = progress
        if project_dir is not None:
            save_progress(Path(project_dir), progress)
    return result


def headless_enabled(explicit: bool | None) -> bool:
    """显式参数优先。pytest 下 None 视为关，避免开发者 shell 污染旧测。"""
    if explicit is True:
        return True
    if explicit is False:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    raw = (os.environ.get("MONTAGE_HEADLESS") or "").strip().lower()
    return raw in {"1", "true", "yes"}


_HEADLESS_SAMPLE = (
    "MONTAGE_HEADLESS：无人值守请 --review none，或去掉该环境变量后交互出样品再 --resume"
)
_HEADLESS_PROMPT = (
    "MONTAGE_HEADLESS：提示词超 3000 字，请去掉该环境变量后改圣经或 --resume 压缩兜底"
)
_HEADLESS_DIRECTOR = (
    "MONTAGE_HEADLESS：导演停点请去掉该环境变量后确认 REVIEW，或 --review none"
)
_HEADLESS_RETRY = "MONTAGE_HEADLESS：未确认 retry，请 --yes 或 --resume"
_HEADLESS_SERIES = (
    "MONTAGE_HEADLESS：系列无人值守请 --review none，或去掉该环境变量后交互 --resume"
)


def _headless_fail(root: Path, progress: dict[str, Any], message: str) -> dict[str, Any]:
    progress["status"] = "fail"
    progress["error"] = message
    save_progress(root, progress)
    return {"success": False, "error": message, "progress": progress, "code": 2}


def save_progress(project_dir: Path, progress: dict[str, Any]) -> None:
    st = str(progress.get("status") or "")
    if st and st != "running":
        progress["next"] = next_action(progress, project_dir)
    else:
        progress.pop("next", None)
    path = _progress_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def resolve_media_path(project_dir: Path, raw: str) -> Path | None:
    """相对路径相对项目根；绝对路径原样。存在则返回 resolve 后的 Path。"""
    text = str(raw or "").strip()
    if not text:
        return None
    p = Path(text)
    candidates = [p] if p.is_absolute() else [project_dir / p, p]
    for cand in candidates:
        try:
            if cand.is_file():
                return cand.resolve()
        except OSError:
            continue
    return None


def collect_shot_clips(
    scene_plan: dict[str, Any],
    manifest: dict[str, Any],
) -> list[tuple[str, str, str]]:
    """(shot_id, scene_id, raw_path) 来自 clip_path_for_shot，与 compose_planner 一致。"""
    rows: list[tuple[str, str, str]] = []
    for shot in shots_with_timeline(scene_plan):
        shot_id = str(shot.get("shot_id") or "")
        scene_id = str(shot.get("scene_id") or "")
        raw = clip_path_for_shot(shot_id, scene_id, manifest)
        rows.append((shot_id, scene_id, raw))
    return rows


def _normalize_manifest_paths(project_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """把相对 clip 写成绝对路径 + 按 id 去重（旧项目自愈），让后续工具不依赖 cwd。"""
    out = dict(manifest)
    items = []
    for item in manifest.get("items") or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        raw = str(row.get("path") or "")
        found = resolve_media_path(project_dir, raw)
        if found is not None:
            row["path"] = str(found)
        items.append(row)
    out["items"] = dedupe_items_by_id(items)
    return out


def validate_startup(project_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """校验 project.json / scene_plan / asset_manifest / 每镜 clip 文件。"""
    hint = "先放 fixture 或等 W1/W2"
    if not (project_dir / "project.json").is_file():
        raise ProduceError(f"缺少 project.json（{hint}）")
    store = ArtifactStore(project_dir)
    scene_plan = store.read("scene_plan")
    manifest = store.read("asset_manifest")
    if not isinstance(scene_plan, dict):
        raise ProduceError(f"缺少 artifacts/scene_plan.json（{hint}）")
    if not isinstance(manifest, dict):
        raise ProduceError(f"缺少 artifacts/asset_manifest.json（{hint}）")
    missing: list[str] = []
    rows = collect_shot_clips(scene_plan, manifest)
    if not rows:
        raise ProduceError(f"scene_plan 没有可拼镜头（{hint}）")
    for shot_id, _scene_id, raw in rows:
        label = shot_id or raw or "?"
        if not raw:
            missing.append(f"{label}: manifest 无 clip_path")
            continue
        if resolve_media_path(project_dir, raw) is None:
            missing.append(f"{label}: 文件不存在 {raw}")
    if missing:
        raise ProduceError("缺 clip（" + hint + "）: " + "; ".join(missing[:6]))
    normalized = _normalize_manifest_paths(project_dir, manifest)
    if normalized.get("items") != manifest.get("items"):
        store.write("asset_manifest", normalized)
        manifest = normalized
    return scene_plan, manifest


def _pipeline_type(project_dir: Path) -> str:
    path = project_dir / "project.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "cinematic"
    if not isinstance(meta, dict):
        return "cinematic"
    return str(meta.get("pipeline_type") or "cinematic")


def validate_gen_startup(project_dir: Path) -> dict[str, Any]:
    """GEN 只需 project.json + scene_plan；不要求 asset_manifest / clip。"""
    hint = "先 montage produce --idea 编译或放 fixture"
    if not (project_dir / "project.json").is_file():
        raise ProduceError("缺少 project.json（先 montage init）")
    store = ArtifactStore(project_dir)
    scene_plan = store.read("scene_plan")
    if not isinstance(scene_plan, dict):
        raise ProduceError(f"缺少 artifacts/scene_plan.json（{hint}）")
    if not collect_shots(scene_plan, None):
        raise ProduceError(f"scene_plan 没有可拍镜头（{hint}）")
    return scene_plan


def _clean_retry_ids(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        items = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        items = [raw]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        out.append(token)
        seen.add(token)
    return out


def _known_retry_ids(project_dir: Path) -> set[str]:
    store = ArtifactStore(project_dir)
    known: set[str] = set()
    for shot in collect_shots(store.read("scene_plan"), store.read("shot_prompts")):
        sid = str(shot.get("shot_id") or "").strip()
        if sid:
            known.add(sid)
        details = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
        for sub in details.get("subjects") or []:
            if not isinstance(sub, dict):
                continue
            cid = str(sub.get("id") or "").strip()
            if cid:
                known.add(cid)
                known.add(f"portrait/{cid}")
                fid = str(sub.get("form_id") or "").strip()
                if fid:
                    known.add(form_subject("portrait", cid, fid))
                    known.add(form_subject("turnaround", cid, fid))
    for src in (store.read("script") or {}, store.read("series_bible") or {}):
        if not isinstance(src, dict):
            continue
        for ch in src.get("characters") or []:
            if not isinstance(ch, dict):
                continue
            cid = str(ch.get("id") or "").strip()
            if cid:
                known.add(cid)
                known.add(f"portrait/{cid}")
                known.add(f"turnaround/{cid}")
                for form in character_forms(ch):
                    fid = form_id_of(form)
                    if fid:
                        known.add(form_subject("portrait", cid, fid))
                        known.add(form_subject("turnaround", cid, fid))
        for loc in src.get("locations") or []:
            if not isinstance(loc, dict):
                continue
            lid = str(loc.get("id") or loc.get("name") or "").strip()
            if lid:
                known.add(lid)
                known.add(f"scene_ref/{lid}")
                known.add(f"location/{lid}")
        for prop in src.get("props") or []:
            if not isinstance(prop, dict):
                continue
            pid = str(prop.get("id") or "").strip()
            if pid:
                known.add(pid)
                known.add(f"prop/{pid}")
    return known


def match_retry_ids(project_dir: Path, ids: list[str]) -> list[str]:
    known = _known_retry_ids(project_dir)
    return [token for token in _clean_retry_ids(ids) if token in known]


def _clear_retry_steps(progress: dict[str, Any]) -> None:
    """清 GEN 生成步与 W0 STEP_IDS；保留 IDEA 与 voice。"""
    steps = progress.get("steps")
    if not isinstance(steps, dict):
        progress["steps"] = {}
        return
    keep = set(IDEA_STEP_IDS) | {"voice"}
    for sid in list(steps):
        if sid in keep:
            continue
        if sid in STEP_IDS or sid in GEN_STEP_IDS:
            steps.pop(sid, None)


def _retry_error(progress: dict[str, Any], message: str) -> dict[str, Any]:
    return {"success": False, "error": message, "progress": progress, "code": 2}


def generation_needed(project_dir: Path) -> bool:
    if _pipeline_type(project_dir) not in GEN_PIPELINES:
        return False
    store = ArtifactStore(project_dir)
    scene_plan = store.read("scene_plan")
    if not isinstance(scene_plan, dict):
        return False
    manifest = store.read("asset_manifest")
    if not isinstance(manifest, dict):
        manifest = {}
    return not clips_compose_ready(scene_plan, manifest, str(project_dir))


def pick_sample_shot_id(project_dir: Path) -> str:
    """时间线第一个 hero，否则第一镜。"""
    store = ArtifactStore(project_dir)
    shots = collect_shots(store.read("scene_plan"), store.read("shot_prompts"))
    for shot in shots:
        if str(shot.get("shot_budget_class") or "") == "hero":
            sid = str(shot.get("shot_id") or "")
            if sid:
                return sid
    if shots:
        return str(shots[0].get("shot_id") or "")
    return ""


def sample_window_ids(project_dir: Path, sample_id: str = "") -> list[str]:
    """可灵样品 Phase A 窗：样品镜 + 时间线下一镜（填 last_frame）。"""
    sid = str(sample_id or pick_sample_shot_id(project_dir) or "").strip()
    if not sid:
        return []
    store = ArtifactStore(project_dir)
    shots = collect_shots(store.read("scene_plan"), store.read("shot_prompts"))
    ordered = [str(s.get("shot_id") or "") for s in shots if str(s.get("shot_id") or "")]
    out = [sid]
    if sid in ordered:
        idx = ordered.index(sid)
        if idx + 1 < len(ordered) and ordered[idx + 1]:
            out.append(ordered[idx + 1])
    return out


def _machine_complete(root: Path, *, ran_gen: bool) -> None:
    """GEN+W0 成功后机器收口 assets/compose/publish。禁止写 human_approved，禁止碰 script。"""
    if not ran_gen:
        return
    from montage.engine.gates import validate_completion
    from montage.engine.stages import CheckpointStore, StageStatus

    store = CheckpointStore(root)
    vlm = ArtifactStore(root).read("vlm_review")
    vlm_blocks = (
        os.environ.get("MONTAGE_RELAX_GATES") != "1"
        and isinstance(vlm, dict)
        and not vlm.get("skipped")
        and vlm.get("pass") is False
    )
    notes: list[dict[str, str]] = []
    for stage in ("assets", "compose", "publish"):
        if vlm_blocks and stage == "assets":
            continue
        try:
            report = validate_completion(root, stage, strict=False)
        except Exception as exc:  # noqa: BLE001
            notes.append({
                "severity": "warning",
                "field": f"checkpoint_{stage}",
                "message": f"机器收口跳过 {stage}: {exc}",
            })
            continue
        if report.get("missing") or report.get("invalid"):
            continue
        try:
            store.write(
                stage,
                StageStatus.COMPLETED.value,
                human_approved=False,
                approved_by="produce",
                note="produce 机器收口，不是人审",
            )
        except ValueError as exc:
            notes.append({
                "severity": "warning",
                "field": f"checkpoint_{stage}",
                "message": f"机器收口未写入 {stage}: {exc}",
            })
            continue
    if notes:
        progress = load_progress(root)
        existing = [f for f in (progress.get("findings") or []) if isinstance(f, dict)]
        existing.extend(notes)
        progress["findings"] = existing
        save_progress(root, progress)


def _step_done(progress: dict[str, Any], step_id: str, resume: bool) -> bool:
    if not resume:
        return False
    st = (progress.get("steps") or {}).get(step_id) or {}
    return st.get("status") in ("ok", "skip")


def _mark(
    progress: dict[str, Any],
    step_id: str,
    status: str,
    *,
    artifact: str = "",
    error: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    steps = progress.setdefault("steps", {})
    row: dict[str, Any] = {
        "status": status,
        "artifact": artifact,
        "error": error,
        "updated_at": _now(),
    }
    if extra:
        row.update(extra)
    steps[step_id] = row


def _call(
    tool: BaseTool,
    inputs: dict[str, Any],
    run_tool_fn: Callable[..., ToolResult],
) -> ToolResult:
    return run_tool_fn(tool, inputs)


def _overlay_failed(result: ToolResult) -> str:
    data = result.data if isinstance(result.data, dict) else {}
    for item in data.get("findings") or []:
        if not isinstance(item, dict):
            continue
        msg = str(item.get("message") or "")
        if msg.startswith(_OVERLAY_FAIL_PREFIX):
            return msg
    return ""


def _events(soundtrack: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(soundtrack, dict):
        return []
    raw = soundtrack.get("events") or []
    return [e for e in raw if isinstance(e, dict)]


def _has_available_bgm(soundtrack: dict[str, Any] | None) -> bool:
    for ev in _events(soundtrack):
        if str(ev.get("kind") or "") != "bgm":
            continue
        path = str(ev.get("path") or "")
        if ev.get("available") or (path and Path(path).is_file()):
            return True
    return False


def _still_clips(project_dir: Path, store: ArtifactStore) -> list[str]:
    decisions = store.read("edit_decisions") or {}
    stills: list[str] = []
    for cut in decisions.get("cuts") or []:
        if not isinstance(cut, dict):
            continue
        raw = str(cut.get("clip_path") or cut.get("path") or "")
        found = resolve_media_path(project_dir, raw)
        check = str(found) if found is not None else raw
        if check and is_still_image(check):
            stills.append(check)
    return stills


def _default_tools() -> dict[str, BaseTool]:
    from montage.compose.ffmpeg_engine import FFmpegCompose
    from montage.tools.compose_planner import ComposePlanner
    from montage.tools.export_bundle import ExportBundle
    from montage.tools.film_health import FilmHealth
    from montage.tools.place_audio import PlaceAudio
    from montage.tools.release_pack import ReleasePack
    from montage.tools.shot_runner import ShotRunner
    from montage.tools.soundtrack_planner import SoundtrackPlanner
    from montage.tools.subtitle_builder import SubtitleBuilder
    from montage.tools.voice_director import VoiceDirector

    planner = ComposePlanner()
    return {
        "soundtrack_planner": SoundtrackPlanner(),
        "compose_planner": planner,
        "place_audio": PlaceAudio(),
        "ffmpeg_compose": FFmpegCompose(),
        "export_bundle": ExportBundle(),
        "film_health": FilmHealth(),
        "shot_runner": ShotRunner(),
        "voice_director": VoiceDirector(),
        "release_pack": ReleasePack(),
        "subtitle_builder": SubtitleBuilder(),
    }


def cleanup_temps(project_dir: Path, *, keep_scratch: bool) -> None:
    scratch = project_dir / "scratch"
    leftovers = list(project_dir.glob("renders/*.joined.mp4")) + list(
        project_dir.glob("renders/*.concat.txt")
    )
    if keep_scratch:
        scratch.mkdir(parents=True, exist_ok=True)
        for src in leftovers:
            dest = scratch / src.name
            try:
                if dest.exists():
                    dest.unlink()
                shutil.move(str(src), str(dest))
            except OSError:
                pass
        return
    for src in leftovers:
        try:
            src.unlink()
        except OSError:
            pass
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)


def write_review_md(
    project_dir: Path,
    *,
    stub: bool = False,
    film: str = "",
    soundtrack_id: str = "",
    export_zip: str = "",
) -> None:
    if stub:
        body = (
            "# REVIEW\n\n"
            "本目录是一部片子的项目文件夹。成片跑 `python -m montage produce .` "
            "（前提：磁盘上已有 clip）。\n"
        )
    else:
        body = (
            "# REVIEW\n\n"
            f"- 成片: `{film or 'renders/final.mp4'}`\n"
            f"- soundtrack: `{soundtrack_id or 'artifacts/soundtrack.json'}`\n"
            f"- produce 进度: `artifacts/produce_progress.json`\n"
            f"- 账本: `cost.jsonl`\n"
            f"- 导出包: `{export_zip or 'exports/'}`\n"
        )
    (project_dir / "REVIEW.md").write_text(body, encoding="utf-8")


def _default_idea_tools() -> dict[str, BaseTool]:
    from montage.tools.idea_developer import IdeaDeveloper

    return {"idea_developer": IdeaDeveloper()}


def _has_progress_file(project_dir: Path) -> bool:
    return (project_dir / "artifacts" / "produce_progress.json").is_file()


def _episode_status(ep_dir: Path) -> str:
    if not _has_progress_file(ep_dir):
        return ""
    return str(load_progress(ep_dir).get("status") or "")


