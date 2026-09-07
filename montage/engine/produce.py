"""produce — 七阶段成片薄编排器（只排序、写进度、调 run_tool）。

W0：磁盘上已有分镜片段时，按死顺序
soundtrack → compose_plan → realize → place_audio → assemble → finish → release → export。
finish/release 无显式触发时 skip（不改 final.mp4、不打 ffmpeg）。
W2：cinematic/documentary 且成片未齐时，先 shot_dry_run → 默认只生成样品镜后停
await_sample；--resume 再全量 generate → 可选 voice，再进 W0。
不写剧本、不注册成 BaseTool。 --idea 仍不调 shot_runner。样品停不是人审。
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
from montage.engine.director import (
    gold_line_findings,
    has_critical,
    idea_ignored_finding,
    is_director_await,
    leave_for_generate,
    validate_outline,
    validate_setup,
    write_director_review,
)
from montage.engine.episodes import is_series_root, materialize_episodes
from montage.engine.finish import TITLE_CARD_SECONDS, LOWER_THIRD_SECONDS, inspect_finish
from montage.engine.runtime import run_tool
from montage.toolbase import BaseTool, ToolResult
from montage.engine.policy import load_loop_policy
from montage.tools.compose_planner import clip_path_for_shot, is_still_image
from montage.tools.shot_runner import (
    _cast_needs_url,
    cast_missing,
    clips_compose_ready,
    collect_cast_jobs,
    collect_shots,
    frames_missing,
    shot_final_ready,
)
from montage.tools.voice_director import collect_dialogue, shots_with_timeline

from montage.engine._produce_common import (
    STEP_IDS,
    IDEA_STEP_IDS,
    GEN_STEP_IDS,
    GEN_PIPELINES,
    ProduceError,
    _call,
    _clean_retry_ids,
    _clear_retry_steps,
    _default_idea_tools,
    _default_tools,
    _events,
    _episode_status,
    _has_available_bgm,
    _has_progress_file,
    _HEADLESS_DIRECTOR,
    _HEADLESS_PROMPT,
    _HEADLESS_RETRY,
    _HEADLESS_SAMPLE,
    _HEADLESS_SERIES,
    _headless_fail,
    headless_enabled,
    _known_retry_ids,
    _lock_path,
    _machine_complete,
    _mark,
    _normalize_manifest_paths,
    _now,
    _overlay_failed,
    _OVERLAY_FAIL_PREFIX,
    _pid_alive,
    _pipeline_type,
    _progress_path,
    _retry_error,
    _SEASON_CONCAT_IGNORED,
    _step_done,
    _still_clips,
    _want_bible_cast,
    _with_season_concat_ignored,
    acquire_lock,
    cleanup_temps,
    collect_shot_clips,
    generation_needed,
    load_progress,
    match_retry_ids,
    next_action,
    pick_sample_shot_id,
    sample_window_ids,
    release_lock,
    resolve_media_path,
    save_progress,
    validate_gen_startup,
    validate_startup,
    write_review_md,
)
from montage.engine.produce_director import (
    run_director_produce,
    run_idea_produce,
    _compile_from_bible,
    _director_cast,
    _director_compile,
    _director_frames,
    _director_stop,
    _skip_director_cast,
)

def _run_generate(
    root: Path,
    progress: dict[str, Any],
    *,
    bag: dict[str, BaseTool],
    runner: Callable[..., ToolResult],
    resume: bool,
    tts: bool,
    sample_hero: bool,
    retry_ids: list[str] | None = None,
    director_clips: bool = False,
    prompt_fallback: bool = False,
    headless: bool | None = None,
    force_final_prompt_stop: bool = False,
) -> dict[str, Any] | None:
    """GEN 三步。成功返回 None，失败或样品停返回 produce 结果。"""
    store = ArtifactStore(root)
    runner_tool = bag.get("shot_runner")
    if runner_tool is None:
        raise ProduceError("缺少 shot_runner")

    policy = load_loop_policy(root)
    loop = str(policy.get("video_loop") or "").strip().lower()
    kling_loop = loop == "kling"
    forced_early = _clean_retry_ids(retry_ids)
    status_now = str(progress.get("status") or "")
    kling_sample = (
        kling_loop
        and sample_hero
        and not forced_early
        and status_now not in ("await_sample", "await_clips", "await_prompt")
    )
    sample_id_early = pick_sample_shot_id(root) if kling_sample else ""
    sample_window = sample_window_ids(root, sample_id_early) if sample_id_early else []
    sample_narrow = bool(kling_sample and sample_window)

    if _want_bible_cast(root, progress) and not _step_done(progress, "shot_cast", resume):
        bible = store.read("series_bible")
        cast_payload: dict[str, Any] = {
            "project_dir": str(root),
            "dry_run": False,
            "stage": "cast",
            "bible": bible,
            "record_ledger": True,
        }
        if sample_narrow:
            cast_payload["sample_shot_ids"] = [sample_id_early]
        result = _call(
            runner_tool,
            cast_payload,
            runner,
        )
        data = result.data if isinstance(result.data, dict) else {}
        retryable = [str(x) for x in (data.get("retryable_ids") or []) if x]
        plan = ArtifactStore(root).read("scene_plan")
        missing = cast_missing(
            bible if isinstance(bible, dict) else None,
            ArtifactStore(root).read("asset_manifest"),
            str(root),
            require_url=_cast_needs_url(policy),
            scene_plan=plan if isinstance(plan, dict) else None,
            video_loop=loop,
            sample_shot_ids=[sample_id_early] if sample_narrow else None,
        )
        if not result.success or retryable or missing:
            err = result.error or "定妆尚未就绪"
            if missing:
                subj = missing[0].get("subject")
                if _cast_needs_url(policy):
                    err = f"{subj} 尚未就绪（缺公网 URL）"
                else:
                    err = f"{subj} 尚未就绪"
            _mark(
                progress, "shot_cast", "fail",
                error=err, extra={"retryable_ids": retryable},
            )
            progress["status"] = "fail"
            progress["error"] = err
            progress["retryable_ids"] = retryable or [
                str(j.get("subject") or "") for j in missing if j.get("subject")
            ]
            save_progress(root, progress)
            return {"success": False, "error": err, "progress": progress, "code": 2}
        leftover: list[str] = []
        if sample_narrow and isinstance(bible, dict):
            full_jobs = collect_cast_jobs(
                bible,
                scene_plan=plan if isinstance(plan, dict) else None,
                video_loop=loop,
            )
            sample_jobs = collect_cast_jobs(
                bible,
                scene_plan=plan if isinstance(plan, dict) else None,
                video_loop=loop,
                sample_shot_ids=[sample_id_early],
            )
            sample_subj = {str(j.get("subject") or "") for j in sample_jobs}
            leftover = [
                str(j.get("subject") or "")
                for j in full_jobs
                if str(j.get("subject") or "") and str(j.get("subject") or "") not in sample_subj
            ]
        if leftover:
            _mark(
                progress, "shot_cast", "partial",
                extra={"pending_subjects": leftover},
            )
        else:
            _mark(progress, "shot_cast", "ok")
        save_progress(root, progress)

    if not _step_done(progress, "shot_dry_run", resume):
        result = _call(
            runner_tool,
            {"project_dir": str(root), "dry_run": True, "record_ledger": False},
            runner,
        )
        data = result.data if isinstance(result.data, dict) else {}
        estimated = float(data.get("estimated_usd") or 0)
        if not result.success:
            _mark(progress, "shot_dry_run", "fail", error=result.error or "dry_run 失败")
            progress["status"] = "fail"
            save_progress(root, progress)
            return {"success": False, "error": result.error, "progress": progress, "code": 2}
        if data.get("blocked") or data.get("over_budget"):
            msg = ""
            for item in data.get("findings") or []:
                if isinstance(item, dict) and item.get("field") == "budget_ceiling_usd":
                    msg = str(item.get("message") or "")
                    break
            msg = msg or result.error or "超预算"
            _mark(progress, "shot_dry_run", "fail", error=msg, extra={"estimated_usd": estimated})
            progress["status"] = "over_budget"
            save_progress(root, progress)
            return {"success": False, "error": msg, "progress": progress, "code": 2}
        _mark(progress, "shot_dry_run", "ok", extra={"estimated_usd": estimated})
        save_progress(root, progress)

    if force_final_prompt_stop and str(progress.get("status") or "") != "await_final_prompt":
        if headless_enabled(headless):
            return _headless_fail(root, progress, _HEADLESS_DIRECTOR)
        preview = _call(
            runner_tool,
            {
                "project_dir": str(root),
                "dry_run": False,
                "stage": "prompt_preview",
                "record_ledger": False,
            },
            runner,
        )
        pdata = preview.data if isinstance(preview.data, dict) else {}
        notes = [f for f in (pdata.get("findings") or []) if isinstance(f, dict)]
        retryable = [str(x) for x in (pdata.get("retryable_ids") or []) if x]
        lifted = pdata.get("shot_prompts")
        prompt_shots = (lifted or {}).get("shots") if isinstance(lifted, dict) else None
        if not preview.success or retryable or not isinstance(prompt_shots, list) or not prompt_shots:
            err = preview.error or "提示词构建失败"
            _mark(progress, "shot_prompts", "fail", error=err, extra={"retryable_ids": retryable})
            progress["status"] = "fail"
            progress["error"] = err
            progress["retryable_ids"] = retryable
            save_progress(root, progress)
            return {"success": False, "error": err, "progress": progress, "code": 2}
        write_director_review(root, "await_final_prompt", findings=notes)
        progress["status"] = "await_final_prompt"
        progress["review"] = "director"
        progress["findings"] = notes
        progress.pop("error", None)
        save_progress(root, progress)
        return {"success": True, "error": "", "progress": progress, "code": 0}

    if not _step_done(progress, "shot_generate", resume):
        payload: dict[str, Any] = {"project_dir": str(root), "dry_run": False}
        sample_id = ""
        stop_after_sample = False
        forced = _clean_retry_ids(retry_ids)
        if prompt_fallback or bool(progress.get("prompt_fallback")):
            payload["prompt_fallback"] = True
        if forced:
            payload["retry_ids"] = forced
        elif sample_hero and str(progress.get("status") or "") not in (
            "await_sample", "await_clips", "await_prompt",
        ):
            sample_id = pick_sample_shot_id(root)
            if sample_id:
                if kling_loop:
                    window = sample_window_ids(root, sample_id)
                    payload["retry_ids"] = window or [sample_id]
                    payload["video_ids"] = [sample_id]
                else:
                    payload["retry_ids"] = [sample_id]
                stop_after_sample = True
        else:
            sid = str(progress.get("sample_id") or "")
            if sid:
                payload["force_ids"] = [sid]
        result = _call(runner_tool, payload, runner)
        data = result.data if isinstance(result.data, dict) else {}
        retryable = [str(x) for x in (data.get("retryable_ids") or []) if x]

        def _gen_fail(err: str) -> dict[str, Any]:
            _mark(
                progress, "shot_generate", "fail",
                error=err, extra={"retryable_ids": retryable},
            )
            progress["status"] = "fail"
            progress["error"] = err
            progress["retryable_ids"] = retryable
            save_progress(root, progress)
            return {"success": False, "error": err, "progress": progress, "code": 2}

        if not result.success:
            return _gen_fail(result.error or "生成失败")

        if data.get("prompt_too_long"):
            over = [str(x) for x in (data.get("prompt_over_ids") or []) if x]
            if headless_enabled(headless):
                return _headless_fail(root, progress, _HEADLESS_PROMPT)
            progress["status"] = "await_prompt"
            progress["prompt_over_ids"] = over
            progress["error"] = ""
            save_progress(root, progress)
            return {"success": True, "error": "", "progress": progress, "code": 0}

        def _unready(ids: list[str]) -> list[str]:
            shots = collect_shots(store.read("scene_plan"), None)
            by_id = {str(s.get("shot_id") or ""): s for s in shots}
            manifest = ArtifactStore(root).read("asset_manifest")
            bad: list[str] = []
            for sid in ids:
                shot = by_id.get(sid)
                if shot is None or not shot_final_ready(shot, manifest, str(root)):
                    bad.append(sid)
            return bad

        if stop_after_sample:
            sample_bad = bool(sample_id) and (
                sample_id in _unready([sample_id]) or sample_id in _unready(retryable)
            )
            if sample_bad:
                if sample_id and sample_id not in retryable:
                    retryable.append(sample_id)
                retryable = _unready(retryable) or [sample_id]
                return _gen_fail(f"样品镜 {sample_id} 生成失败")
            progress["status"] = "await_sample"
            progress["sample_id"] = sample_id
            save_progress(root, progress)
            return {"success": True, "error": "", "progress": progress, "code": 0}
        if director_clips:
            notes = [f for f in (data.get("findings") or []) if isinstance(f, dict)]
            write_director_review(root, "await_clips", findings=notes)
            progress["status"] = "await_clips"
            progress["review"] = "director"
            progress["findings"] = notes
            progress["retryable_ids"] = retryable
            save_progress(root, progress)
            return {"success": True, "error": "", "progress": progress, "code": 0}
        bad = _unready(retryable)
        if bad:
            retryable = bad
            return _gen_fail("有坏镜未通过")
        _mark(progress, "shot_generate", "ok")
        save_progress(root, progress)

    if not _step_done(progress, "voice", resume):
        lines = collect_dialogue(store.read("scene_plan"), store.read("script"))
        voice_tool = bag.get("voice_director")
        if not tts or not lines:
            _mark(progress, "voice", "skip")
            save_progress(root, progress)
        else:
            if voice_tool is None:
                raise ProduceError("缺少 voice_director")
            result = _call(
                voice_tool,
                {"project_dir": str(root), "synthesize": True},
                runner,
            )
            if not result.success:
                _mark(progress, "voice", "fail", error=result.error or "tts 失败")
                progress["status"] = "fail"
                save_progress(root, progress)
                return {"success": False, "error": result.error, "progress": progress, "code": 2}
            _mark(progress, "voice", "ok")
            save_progress(root, progress)
    return None



def maybe_concat_season(
    root: Path,
    runnable: list[dict[str, Any]],
    *,
    bag: dict[str, BaseTool],
    runner: Callable[..., ToolResult],
    keep_scratch: bool,
    season_concat: bool,
    progress: dict[str, Any],
) -> str:
    """全集 ok 且要拼季时写 renders/season.mp4。返回空串或含 season-concat 的错误。"""
    if not season_concat:
        _mark(progress, "season", "skip")
        return ""
    pending = [e for e in runnable if _episode_status(Path(e["path"])) != "ok"]
    if pending:
        _mark(progress, "season", "skip")
        return ""
    clips: list[str] = []
    for ep in runnable:
        eid = str(ep.get("episode_id") or "")
        final = Path(ep["path"]) / "renders" / "final.mp4"
        if not final.is_file():
            msg = f"season-concat：缺 {eid}/renders/final.mp4"
            _mark(progress, "season", "fail", error=msg)
            return msg
        clips.append(str(final))
    ff = bag.get("ffmpeg_compose")
    if ff is None:
        msg = "season-concat：缺少 ffmpeg_compose"
        _mark(progress, "season", "fail", error=msg)
        return msg
    scratch = root / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    tmp = scratch / "season.mp4"
    result = _call(
        ff,
        {"operation": "concat", "clips": clips, "output_path": str(tmp)},
        runner,
    )
    if not result.success or not tmp.is_file():
        msg = str(result.error or "season-concat：拼接失败")
        if "season-concat" not in msg:
            msg = f"season-concat：{msg}"
        _mark(progress, "season", "fail", error=msg)
        return msg
    dest = root / "renders" / "season.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp, dest)
    cleanup_temps(root, keep_scratch=keep_scratch)
    _mark(progress, "season", "ok", artifact=str(dest))
    return ""


def run_series_produce(
    project_dir: str | Path,
    *,
    resume: bool = False,
    skip_export: bool = False,
    strict_audio: bool = False,
    keep_scratch: bool = False,
    review: str = "bible",
    tts: bool = False,
    sample_hero: bool = True,
    trim_hero: bool = False,
    all_video: bool = False,
    skip_finish: bool = False,
    burn_subs: bool = False,
    profile: str = "",
    tools: dict[str, BaseTool] | None = None,
    run_tool_fn: Callable[..., ToolResult] | None = None,
    headless: bool | None = None,
    season_concat: bool = False,
) -> dict[str, Any]:
    """系列根：物化子集后按集调度。不对根跑 GEN/W0。"""
    root = Path(project_dir)
    runner = run_tool_fn or run_tool
    bag = tools or _default_tools()
    owned = False
    progress = load_progress(root)
    incoming_status = str(progress.get("status") or "")
    want_concat = bool(season_concat)
    try:
        acquire_lock(root)
        owned = True
        mat = materialize_episodes(root)
        findings = [f for f in (mat.get("findings") or []) if isinstance(f, dict)]
        runnable = [e for e in (mat.get("runnable") or []) if isinstance(e, dict) and e.get("path")]
        if not resume:
            progress = {
                "version": "1",
                "status": "running",
                "mode": "series",
                "steps": {},
                "findings": findings,
            }
        else:
            progress["mode"] = "series"
            if findings:
                existing = [f for f in (progress.get("findings") or []) if isinstance(f, dict)]
                progress["findings"] = existing + findings
        save_progress(root, progress)

        def pack(status: str, *, success: bool, error: str = "", episode_id: str = "", code: int | None = None) -> dict[str, Any]:
            progress["status"] = status
            progress["mode"] = "series"
            if episode_id:
                progress["episode_id"] = episode_id
            elif "episode_id" in progress and status == "ok":
                progress.pop("episode_id", None)
            if error:
                progress["error"] = error
            elif status != "fail":
                progress.pop("error", None)
            save_progress(root, progress)
            if code is None:
                code = 0 if success else 2
            return {"success": success, "error": error, "progress": progress, "code": code}

        def finish_ok(*, episode_id: str = "") -> dict[str, Any]:
            err = maybe_concat_season(
                root,
                runnable,
                bag=bag,
                runner=runner,
                keep_scratch=keep_scratch,
                season_concat=want_concat,
                progress=progress,
            )
            if err:
                return pack("fail", success=False, error=err)
            return pack("ok", success=True, episode_id=episode_id)

        if not runnable:
            msg = "没有可跑的集（检查 episodes.json 的 scene_ids）"
            for item in findings:
                if item.get("severity") == "critical":
                    msg = str(item.get("message") or msg)
                    break
            return pack("fail", success=False, error=msg)

        def first_unfinished() -> dict[str, Any] | None:
            for ep in runnable:
                if _episode_status(Path(ep["path"])) != "ok":
                    return ep
            return None

        if headless_enabled(headless):
            need_ep = first_unfinished() is not None
            if need_ep and review != "none" and not (resume and incoming_status == "await_sample"):
                return _headless_fail(root, progress, _HEADLESS_SERIES)

        def next_after(eid: str) -> dict[str, Any] | None:
            ids = [str(e.get("episode_id") or "") for e in runnable]
            start = ids.index(eid) + 1 if eid in ids else 0
            for ep in runnable[start:]:
                if _episode_status(Path(ep["path"])) != "ok":
                    return ep
            return None

        def call_ep(ep: dict[str, Any]) -> dict[str, Any]:
            ep_dir = Path(ep["path"])
            child_resume = _has_progress_file(ep_dir)
            nested_review = "none" if review == "none" else "bible"
            want = bool(sample_hero) and review != "none"
            return run_produce(
                ep_dir,
                resume=child_resume,
                skip_export=skip_export,
                strict_audio=strict_audio,
                keep_scratch=keep_scratch,
                review=nested_review,
                tts=tts,
                sample_hero=want,
                trim_hero=trim_hero,
                all_video=all_video,
                skip_finish=skip_finish,
                burn_subs=burn_subs,
                profile=profile,
                tools=bag,
                run_tool_fn=runner,
                headless=headless,
                force_final_prompt_stop=False,
            )

        def map_child(ep: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
            child = result.get("progress") if isinstance(result.get("progress"), dict) else {}
            child_status = str(child.get("status") or "")
            eid = str(ep.get("episode_id") or "")
            err = str(result.get("error") or child.get("error") or "")
            if child_status == "ok":
                nxt = next_after(eid)
                if nxt:
                    return pack("await_episode", success=True, episode_id=eid)
                return finish_ok(episode_id=eid)
            if child_status == "await_sample":
                return pack("await_sample", success=True, episode_id=eid)
            if child_status in ("over_hero", "over_budget", "fail"):
                return pack(child_status, success=False, error=err or child_status, episode_id=eid)
            return pack(child_status or "fail", success=bool(result.get("success")), error=err, episode_id=eid)

        target: dict[str, Any] | None = None
        if resume:
            st = str(progress.get("status") or "")
            eid = str(progress.get("episode_id") or "")
            if st == "ok":
                return finish_ok()
            if st == "await_episode":
                target = next_after(eid)
            elif st in ("await_sample", "fail", "over_hero", "over_budget", "running"):
                target = next((e for e in runnable if str(e.get("episode_id") or "") == eid), None) or first_unfinished()
            else:
                target = first_unfinished()
        else:
            target = first_unfinished()

        if target is None:
            return finish_ok()

        chain = review == "none"
        if chain:
            start = 0
            for i, ep in enumerate(runnable):
                if ep is target or str(ep.get("episode_id") or "") == str(target.get("episode_id") or ""):
                    start = i
                    break
            last: dict[str, Any] | None = None
            for ep in runnable[start:]:
                if _episode_status(Path(ep["path"])) == "ok":
                    continue
                result = call_ep(ep)
                last = map_child(ep, result)
                child_st = str((result.get("progress") or {}).get("status") or "")
                if child_st == "ok":
                    continue
                return last
            if last is None:
                return finish_ok()
            if str((last.get("progress") or {}).get("status") or "") == "await_episode":
                return pack("ok", success=True)
            return last

        result = call_ep(target)
        return map_child(target, result)
    except ProduceError as exc:
        progress["status"] = "fail"
        progress["mode"] = "series"
        progress["error"] = str(exc)
        save_progress(root, progress)
        return {"success": False, "error": str(exc), "progress": progress, "code": 2}
    finally:
        if owned:
            release_lock(root)


def _commit_final(tmp: Path, film: Path) -> None:
    try:
        os.replace(str(tmp), str(film))
    except OSError:
        shutil.copy2(tmp, film)


def _apply_film_health(
    root: Path,
    progress: dict[str, Any],
    *,
    bag: dict[str, BaseTool],
    runner: Callable[..., ToolResult],
    block: bool,
) -> dict[str, Any] | None:
    """export 前整片探测。bag 无工具则跳过。critical 仅在 block=True 时挡 zip。"""
    tool = bag.get("film_health")
    if tool is None:
        return None
    result = _call(tool, {"project_dir": str(root)}, runner)
    data = result.data if isinstance(result.data, dict) else {}
    passed = bool(data.get("pass", True))
    if passed or not block or os.environ.get("MONTAGE_RELAX_GATES") == "1":
        return None
    crit = data.get("critical") or []
    bits = []
    for item in crit:
        if isinstance(item, dict) and item.get("message"):
            bits.append(str(item["message"]))
    err = "；".join(bits) or result.error or "film_health critical"
    _mark(progress, "export", "fail", error=err)
    progress["status"] = "fail"
    save_progress(root, progress)
    return {"success": False, "error": err, "progress": progress, "code": 2}


def _apply_finish(
    root: Path,
    progress: dict[str, Any],
    *,
    bag: dict[str, BaseTool],
    runner: Callable[..., ToolResult],
    skip_finish: bool,
    burn_subs: bool,
    profile: str,
) -> dict[str, Any] | None:
    """跑 finish 子项。返回 produce 失败结果，成功返回 None。"""
    if skip_finish:
        _mark(progress, "finish", "skip")
        save_progress(root, progress)
        return None
    jobs = inspect_finish(root, cli_profile=profile, burn_subs=burn_subs)
    did = False
    extra: dict[str, Any] = {"findings": [], "title_dur": 0.0}
    film = root / "renders" / "final.mp4"
    current = film
    scratch = root / "scratch"
    ff = bag.get("ffmpeg_compose")

    def fail(err: str) -> dict[str, Any]:
        _mark(progress, "finish", "fail", error=err, extra=extra)
        progress["status"] = "fail"
        save_progress(root, progress)
        return {"success": False, "error": err, "progress": progress, "code": 2}

    if jobs["lut_path"]:
        if ff is None:
            extra["findings"].append({"severity": "warning", "field": "lut", "message": "缺少 ffmpeg_compose，跳过 LUT"})
        else:
            scratch.mkdir(parents=True, exist_ok=True)
            graded = scratch / "graded.mp4"
            result = _call(
                ff,
                {
                    "operation": "apply_lut",
                    "input_path": str(current),
                    "output_path": str(graded),
                    "lut_path": jobs["lut_path"],
                },
                runner,
            )
            if not result.success or not graded.is_file():
                return fail(result.error or "apply_lut 失败")
            _commit_final(graded, film)
            current = film
            did = True
    if jobs["profile"]:
        if ff is None:
            extra["findings"].append({
                "severity": "warning",
                "field": "profile",
                "message": "缺少 ffmpeg_compose，跳过 profile",
            })
        else:
            scratch.mkdir(parents=True, exist_ok=True)
            profiled = scratch / "profiled.mp4"
            result = _call(
                ff,
                {
                    "operation": "apply_profile",
                    "input_path": str(current),
                    "output_path": str(profiled),
                    "profile": jobs["profile"],
                },
                runner,
            )
            if not result.success or not profiled.is_file():
                return fail(result.error or "apply_profile 失败")
            _commit_final(profiled, film)
            current = film
            did = True
    if jobs["drawtext"]:
        if ff is None:
            extra["findings"].append({
                "severity": "warning",
                "field": "title",
                "message": "缺少 ffmpeg_compose，跳过片头",
            })
        else:
            scratch.mkdir(parents=True, exist_ok=True)
            card = scratch / "title.mp4"
            result = _call(
                ff,
                {
                    "operation": "title_card",
                    "input_path": str(current),
                    "output_path": str(card),
                    "title": jobs["title"],
                    "duration_seconds": TITLE_CARD_SECONDS,
                },
                runner,
            )
            if not result.success or not card.is_file():
                extra["findings"].append({
                    "severity": "warning",
                    "field": "title",
                    "message": result.error or "title_card 失败",
                })
            else:
                joined = scratch / "titled.mp4"
                result = _call(
                    ff,
                    {
                        "operation": "concat",
                        "clips": [str(card), str(current)],
                        "output_path": str(joined),
                    },
                    runner,
                )
                if not result.success or not joined.is_file():
                    return fail(result.error or "片头 concat 失败")
                _commit_final(joined, film)
                current = film
                extra["title_dur"] = TITLE_CARD_SECONDS
                did = True
                l3 = str(jobs.get("lower_third") or "")
                if l3:
                    overlay = scratch / "lower.mp4"
                    result = _call(
                        ff,
                        {
                            "operation": "lower_third",
                            "input_path": str(current),
                            "output_path": str(overlay),
                            "title": l3,
                            "start_seconds": TITLE_CARD_SECONDS,
                            "duration_seconds": LOWER_THIRD_SECONDS,
                        },
                        runner,
                    )
                    if not result.success or not overlay.is_file():
                        extra["findings"].append({
                            "severity": "warning",
                            "field": "lower_third",
                            "message": result.error or "lower_third 失败",
                        })
                    else:
                        _commit_final(overlay, film)
                        current = film
    if jobs["need_srt"]:
        srt_path = root / "renders" / "final.srt"
        sub = bag.get("subtitle_builder")
        if sub is None:
            extra["findings"].append({"severity": "warning", "field": "srt", "message": "缺少 subtitle_builder，跳过字幕旁路"})
        else:
            result = _call(
                sub,
                {
                    "sentences": jobs["cues"],
                    "format": "srt",
                    "output_path": str(srt_path),
                },
                runner,
            )
            if not result.success:
                extra["findings"].append({
                    "severity": "warning",
                    "field": "srt",
                    "message": result.error or "subtitle_builder 失败",
                })
            else:
                did = True
    if jobs["burn_subs"]:
        srt_path = root / "renders" / "final.srt"
        if not srt_path.is_file():
            extra["findings"].append({
                "severity": "warning",
                "field": "burn_subs",
                "message": "无 SRT，跳过烧录",
            })
        elif ff is None:
            extra["findings"].append({
                "severity": "warning",
                "field": "burn_subs",
                "message": "缺少 ffmpeg_compose，跳过烧录",
            })
        else:
            scratch.mkdir(parents=True, exist_ok=True)
            burned = scratch / "burned.mp4"
            result = _call(
                ff,
                {
                    "operation": "burn_subtitles",
                    "input_path": str(current),
                    "srt_path": str(srt_path),
                    "output_path": str(burned),
                },
                runner,
            )
            if not result.success or not burned.is_file():
                extra["findings"].append({
                    "severity": "warning",
                    "field": "burn_subs",
                    "message": result.error or "烧录失败（缺字体不 fail）",
                })
            else:
                _commit_final(burned, film)
                did = True
    if did:
        _mark(progress, "finish", "ok", extra=extra)
    else:
        _mark(progress, "finish", "skip", extra=extra)
    save_progress(root, progress)
    return None


def run_produce(
    project_dir: str | Path,
    *,
    resume: bool = False,
    skip_export: bool = False,
    strict_audio: bool = False,
    keep_scratch: bool = False,
    idea: str | None = None,
    review: str = "bible",
    tts: bool = False,
    sample_hero: bool = True,
    trim_hero: bool = False,
    all_video: bool = False,
    skip_finish: bool = False,
    burn_subs: bool = False,
    profile: str = "",
    retry_ids: list[str] | None = None,
    retry_confirmed: bool = False,
    tools: dict[str, BaseTool] | None = None,
    run_tool_fn: Callable[..., ToolResult] | None = None,
    headless: bool | None = None,
    season_concat: bool = False,
    force_final_prompt_stop: bool | None = None,
) -> dict[str, Any]:
    """跑 W0 合成链，或 W1 --idea 收编，或 W2 缺 clip 时生成后再拼片。"""
    incoming = _clean_retry_ids(retry_ids)
    retry_requested = retry_ids is not None
    want_season = bool(season_concat)
    if idea and retry_requested:
        root = Path(project_dir)
        try:
            return _retry_error(load_progress(root), "--retry 与 --idea 互斥")
        except ProduceError as exc:
            return {"success": False, "error": str(exc), "progress": {"version": "1", "status": "fail", "error": str(exc), "steps": {}}, "code": 2}
    root = Path(project_dir)
    try:
        incoming_status = str(load_progress(root).get("status") or "")
    except ProduceError as exc:
        return {"success": False, "error": str(exc), "progress": {"version": "1", "status": "fail", "error": str(exc), "steps": {}}, "code": 2}
    idea_ignored = False
    if is_director_await(incoming_status) and review != "none":
        if idea:
            idea_ignored = True
            idea = None
        dir_resume = bool(resume)
        dir_retry = incoming if retry_requested else None
        if incoming_status == "await_cast" and retry_requested:
            dir_resume = True
        if incoming_status == "await_frames" and retry_requested:
            dir_resume = True
        if incoming_status == "await_clips" and retry_requested:
            dir_resume = True
        frames_ok = True
        clips_ok = True
        if incoming_status == "await_frames":
            store = ArtifactStore(root)
            frames_ok = not bool(frames_missing(
                store.read("scene_plan"),
                store.read("asset_manifest"),
                str(root),
            ))
        if incoming_status == "await_clips":
            store = ArtifactStore(root)
            clips_ok = clips_compose_ready(
                store.read("scene_plan"),
                store.read("asset_manifest"),
                str(root),
            )
        if headless_enabled(headless):
            return _headless_fail(root, load_progress(root), _HEADLESS_DIRECTOR)
        if not leave_for_generate(
            incoming_status,
            dir_resume,
            frames_ok=frames_ok,
            clips_ok=clips_ok,
            retry=bool(dir_retry) and incoming_status in ("await_frames", "await_clips"),
        ):
            return _with_season_concat_ignored(
                run_director_produce(
                    root,
                    resume=dir_resume,
                    idea_ignored=idea_ignored,
                    tools=tools,
                    run_tool_fn=run_tool_fn,
                    retry_ids=dir_retry,
                ),
                want_season,
                project_dir,
            )
    if idea:
        return _with_season_concat_ignored(
            run_idea_produce(
                project_dir,
                idea,
                resume=resume,
                review=review,
                tools=tools,
                run_tool_fn=run_tool_fn,
            ),
            want_season,
            project_dir,
        )
    root = Path(project_dir)
    try:
        progress = load_progress(root)
    except ProduceError as exc:
        return {"success": False, "error": str(exc), "progress": {"version": "1", "status": "fail", "error": str(exc), "steps": {}}, "code": 2}
    status = str(progress.get("status") or "")
    confirmed = bool(retry_confirmed)
    if resume and status == "await_retry":
        retry_requested = True
        confirmed = True
        if not incoming:
            incoming = _clean_retry_ids(progress.get("retry_ids") or [])
    if retry_requested and is_series_root(root):
        return _retry_error(progress, "系列根不能 --retry，请对子集目录跑")
    if is_series_root(root):
        return run_series_produce(
            root,
            resume=resume,
            skip_export=skip_export,
            strict_audio=strict_audio,
            keep_scratch=keep_scratch,
            review=review,
            tts=tts,
            sample_hero=sample_hero,
            trim_hero=trim_hero,
            all_video=all_video,
            skip_finish=skip_finish,
            burn_subs=burn_subs,
            profile=profile,
            tools=tools,
            run_tool_fn=run_tool_fn,
            headless=headless,
            season_concat=want_season,
        )
    if retry_requested and status == "await_sample":
        return _retry_error(progress, "await_sample 时不能 --retry（先 --resume 或 --review none）")
    if retry_requested and _pipeline_type(root) not in GEN_PIPELINES:
        return _retry_error(progress, "切片厂不能 retry 生成")
    if retry_requested and not incoming:
        return _retry_error(progress, "retry_ids 不能为空")
    matched = match_retry_ids(root, incoming) if retry_requested else []
    if retry_requested and not matched:
        return _retry_error(progress, "retry_ids 对不上任何 shot/portrait/turnaround/prop/scene_ref")
    if headless_enabled(headless) and retry_requested and not confirmed:
        return _headless_fail(root, progress, _HEADLESS_RETRY)

    want_sample = bool(sample_hero) and review != "none"
    if retry_requested and confirmed:
        want_sample = False
    if incoming_status in ("await_frames", "await_prompt", "await_final_prompt"):
        want_sample = False
    use_all_video = bool(all_video)
    use_trim = bool(trim_hero) and not use_all_video
    runner = run_tool_fn or run_tool
    bag = tools or _default_tools()
    owned = False
    ran_gen = False
    try:
        acquire_lock(root)
        owned = True
        if status == "await_bible":
            compiled = _compile_from_bible(root, progress, bag, runner)
            if compiled is not None:
                return compiled
            resume = True
            progress["mode"] = "generate"
            progress["status"] = "running"
            save_progress(root, progress)
        if status == "await_prompt":
            if headless_enabled(headless):
                return _headless_fail(root, progress, _HEADLESS_PROMPT)
            if resume:
                progress["prompt_fallback"] = True
            else:
                compiled = _compile_from_bible(root, progress, bag, runner)
                if compiled is not None:
                    return compiled
                progress.pop("prompt_fallback", None)
            progress["mode"] = "generate"
            progress["status"] = "running"
            steps = progress.setdefault("steps", {})
            if isinstance(steps, dict):
                steps.pop("shot_generate", None)
            save_progress(root, progress)
            resume = True
        if retry_requested and not confirmed:
            runner_tool = bag.get("shot_runner")
            if runner_tool is None:
                raise ProduceError("缺少 shot_runner")
            result = _call(
                runner_tool,
                {"project_dir": str(root), "dry_run": True, "record_ledger": False},
                runner,
            )
            data = result.data if isinstance(result.data, dict) else {}
            estimated = float(data.get("estimated_usd") or 0)
            if not result.success:
                progress["status"] = "fail"
                progress["error"] = result.error or "dry_run 失败"
                save_progress(root, progress)
                return {"success": False, "error": result.error, "progress": progress, "code": 2}
            if data.get("blocked") or data.get("over_budget"):
                msg = ""
                for item in data.get("findings") or []:
                    if isinstance(item, dict) and item.get("field") == "budget_ceiling_usd":
                        msg = str(item.get("message") or "")
                        break
                msg = msg or result.error or "超预算"
                progress["status"] = "over_budget"
                progress["error"] = msg
                save_progress(root, progress)
                return {"success": False, "error": msg, "progress": progress, "code": 2}
            progress["status"] = "await_retry"
            progress["mode"] = "generate"
            progress["retry_ids"] = matched
            progress["estimated_usd"] = estimated
            write_director_review(root, "await_retry", retry_ids=matched)
            save_progress(root, progress)
            return {"success": True, "error": "", "progress": progress, "code": 0}
        if retry_requested and confirmed:
            _clear_retry_steps(progress)
            progress["retry_ids"] = matched
            progress["status"] = "running"
            progress["mode"] = "generate"
            resume = True
        elif not resume:
            kept_review = progress.get("review")
            progress = {"version": "1", "status": "running", "steps": {}}
            if kept_review:
                progress["review"] = kept_review
        elif progress.get("mode") == "idea":
            kept_review = progress.get("review")
            progress = {"version": "1", "status": "running", "mode": "generate", "steps": {}}
            if kept_review:
                progress["review"] = kept_review
        if matched or generation_needed(root):
            validate_gen_startup(root)
            from montage.engine.shot_budget import apply_produce_budget

            layered = apply_produce_budget(
                root, trim_hero=use_trim, all_video=use_all_video,
            )
            if layered.get("findings"):
                progress["findings"] = list(layered.get("findings") or [])
            if not layered.get("ok"):
                msg = ""
                for item in layered.get("findings") or []:
                    if isinstance(item, dict) and item.get("severity") == "critical":
                        msg = str(item.get("message") or "")
                        break
                msg = msg or "pending hero 超过 30%"
                progress["mode"] = "generate"
                progress["status"] = "over_hero"
                progress["error"] = msg
                save_progress(root, progress)
                return {"success": False, "error": msg, "progress": progress, "code": 2}
            if (
                headless_enabled(headless)
                and want_sample
                and not (resume and str(progress.get("status") or "") == "await_sample")
                and not (retry_requested and confirmed)
            ):
                return _headless_fail(root, progress, _HEADLESS_SAMPLE)
            progress["mode"] = "generate"
            save_progress(root, progress)
            gen_result = _run_generate(
                root,
                progress,
                bag=bag,
                runner=runner,
                resume=resume,
                tts=tts,
                sample_hero=want_sample,
                retry_ids=matched or None,
                director_clips=str(progress.get("review") or "") == "director" and review != "none",
                prompt_fallback=bool(progress.get("prompt_fallback")),
                headless=headless,
                force_final_prompt_stop=(
                    force_final_prompt_stop
                    if force_final_prompt_stop is not None
                    else (review == "none" and not (retry_requested and confirmed))
                ),
            )
            if gen_result is not None:
                return gen_result
            ran_gen = True
        elif use_all_video or use_trim:
            note = {
                "severity": "warning",
                "stage": "shot_budget",
                "field": "shot_budget_class",
                "message": "成片已齐，--all-video / --trim-hero 未改写",
                "proposed_fix": "要重生成请先去掉已有 clip 再跑",
            }
            existing = list(progress.get("findings") or [])
            existing.append(note)
            progress["findings"] = existing
            save_progress(root, progress)
        validate_startup(root)
        store = ArtifactStore(root)
        (root / "scratch").mkdir(parents=True, exist_ok=True)
        (root / "renders").mkdir(parents=True, exist_ok=True)
        (root / "assets" / "placed").mkdir(parents=True, exist_ok=True)
        (root / "assets" / "kenburns").mkdir(parents=True, exist_ok=True)

        # soundtrack
        if not _step_done(progress, "soundtrack", resume):
            result = _call(
                bag["soundtrack_planner"],
                {"project_dir": str(root), "resolve": True},
                runner,
            )
            if not result.success:
                _mark(progress, "soundtrack", "fail", error=result.error or "soundtrack 失败")
                progress["status"] = "fail"
                save_progress(root, progress)
                return {"success": False, "error": result.error, "progress": progress, "code": 2}
            soundtrack = result.data if isinstance(result.data, dict) else store.read("soundtrack")
            if strict_audio and not _has_available_bgm(soundtrack):
                msg = "缺 BGM（--strict-audio）"
                _mark(progress, "soundtrack", "fail", error=msg)
                progress["status"] = "fail"
                save_progress(root, progress)
                return {"success": False, "error": msg, "progress": progress, "code": 2}
            art = str(root / "artifacts" / "soundtrack.json")
            _mark(progress, "soundtrack", "ok", artifact=art)
            save_progress(root, progress)

        soundtrack = store.read("soundtrack") or {}

        # compose_plan
        if not _step_done(progress, "compose_plan", resume):
            result = _call(
                bag["compose_planner"],
                {"project_dir": str(root), "overwrite": True, "write_plan": True},
                runner,
            )
            if not result.success:
                _mark(progress, "compose_plan", "fail", error=result.error or "compose_plan 失败")
                progress["status"] = "fail"
                save_progress(root, progress)
                return {"success": False, "error": result.error, "progress": progress, "code": 2}
            plan_ok = store.exists("compose_plan") and store.exists("edit_decisions")
            if not plan_ok:
                msg = "未写出 compose_plan / edit_decisions"
                _mark(progress, "compose_plan", "fail", error=msg)
                progress["status"] = "fail"
                save_progress(root, progress)
                return {"success": False, "error": msg, "progress": progress, "code": 2}
            _mark(
                progress, "compose_plan", "ok",
                artifact=str(root / "artifacts" / "edit_decisions.json"),
            )
            save_progress(root, progress)

        # realize
        if not _step_done(progress, "realize", resume):
            stills = _still_clips(root, store)
            if not stills:
                _mark(progress, "realize", "skip")
                save_progress(root, progress)
            else:
                result = _call(
                    bag["compose_planner"],
                    {"project_dir": str(root), "realize": True, "write_plan": True},
                    runner,
                )
                if not result.success:
                    _mark(progress, "realize", "fail", error=result.error or "realize 失败")
                    progress["status"] = "fail"
                    save_progress(root, progress)
                    return {"success": False, "error": result.error, "progress": progress, "code": 2}
                leftover = _still_clips(root, store)
                if leftover:
                    msg = "静图未转成视频: " + leftover[0]
                    _mark(progress, "realize", "fail", error=msg)
                    progress["status"] = "fail"
                    save_progress(root, progress)
                    return {"success": False, "error": msg, "progress": progress, "code": 2}
                _mark(progress, "realize", "ok")
                save_progress(root, progress)

        # place_audio
        music_path = ""
        music_segments: list[Any] = []
        mix_source = False
        if _step_done(progress, "place_audio", resume):
            prev = (progress.get("steps") or {}).get("place_audio") or {}
            music_path = str(prev.get("music_path") or "")
            segs = prev.get("music_segments")
            music_segments = segs if isinstance(segs, list) else []
            mix_source = bool(prev.get("mix_source_audio"))
        else:
            events = _events(soundtrack)
            if not events:
                _mark(progress, "place_audio", "skip")
                save_progress(root, progress)
            else:
                result = _call(
                    bag["place_audio"],
                    {"project_dir": str(root)},
                    runner,
                )
                overlay_err = _overlay_failed(result)
                if not result.success or overlay_err:
                    err = overlay_err or result.error or "place_audio 失败"
                    _mark(progress, "place_audio", "fail", error=err)
                    progress["status"] = "fail"
                    save_progress(root, progress)
                    return {"success": False, "error": err, "progress": progress, "code": 2}
                data = result.data if isinstance(result.data, dict) else {}
                hints = data.get("assemble_hints") if isinstance(data.get("assemble_hints"), dict) else {}
                music_path = str(data.get("music_path") or hints.get("music_path") or "")
                segs = data.get("music_segments")
                if not isinstance(segs, list):
                    segs = hints.get("music_segments")
                music_segments = segs if isinstance(segs, list) else []
                mix_source = bool(data.get("mix_source_audio", hints.get("mix_source_audio")))
                _mark(
                    progress, "place_audio", "ok",
                    extra={
                        "music_path": music_path,
                        "music_segments": music_segments,
                        "mix_source_audio": mix_source,
                    },
                )
                save_progress(root, progress)

        # assemble
        if not _step_done(progress, "assemble", resume):
            out = root / "renders" / "final.mp4"
            payload: dict[str, Any] = {
                "operation": "assemble",
                "project_dir": str(root),
                "edit_decisions_path": str(root / "artifacts" / "edit_decisions.json"),
                "output_path": str(out),
                "mix_source_audio": mix_source,
            }
            if music_path:
                payload["music_path"] = music_path
            if isinstance(music_segments, list) and len(music_segments) >= 2:
                payload["music_segments"] = music_segments
                payload.pop("music_path", None)
            result = _call(bag["ffmpeg_compose"], payload, runner)
            if not result.success or not out.is_file():
                err = result.error or "renders/ 无成片"
                _mark(progress, "assemble", "fail", error=err)
                progress["status"] = "fail"
                save_progress(root, progress)
                return {"success": False, "error": err, "progress": progress, "code": 2}
            _mark(progress, "assemble", "ok", artifact=str(out))
            save_progress(root, progress)

        # finish：仅显式 lut / profile / script.title / 字幕 cues 才动手。
        if not _step_done(progress, "finish", resume):
            failed = _apply_finish(
                root, progress,
                bag=bag,
                runner=runner,
                skip_finish=skip_finish,
                burn_subs=burn_subs,
                profile=profile,
            )
            if failed is not None:
                return failed
        # release：封面/简介/publish_log（无 ffmpeg 仍写简介）。
        if not _step_done(progress, "release", resume):
            packer = bag.get("release_pack")
            if packer is None:
                err = "缺少 release_pack"
                _mark(progress, "release", "fail", error=err)
                progress["status"] = "fail"
                save_progress(root, progress)
                return {"success": False, "error": err, "progress": progress, "code": 2}
            finish_step = (progress.get("steps") or {}).get("finish") or {}
            try:
                title_dur = float(finish_step.get("title_dur") or 0)
            except (TypeError, ValueError):
                title_dur = 0.0
            result = _call(packer, {"project_dir": str(root), "title_dur": title_dur}, runner)
            if not result.success:
                err = result.error or "release_pack 失败"
                _mark(progress, "release", "fail", error=err)
                progress["status"] = "fail"
                save_progress(root, progress)
                return {"success": False, "error": err, "progress": progress, "code": 2}
            data = result.data if isinstance(result.data, dict) else {}
            _mark(
                progress, "release", "ok",
                artifact=str(data.get("publish_log_path") or root / "artifacts" / "publish_log.json"),
            )
            save_progress(root, progress)

        # export
        export_zip = ""
        if skip_export:
            _apply_film_health(root, progress, bag=bag, runner=runner, block=False)
            if not _step_done(progress, "export", resume):
                _mark(progress, "export", "skip")
                save_progress(root, progress)
        elif not _step_done(progress, "export", resume):
            blocked = _apply_film_health(root, progress, bag=bag, runner=runner, block=True)
            if blocked is not None:
                return blocked
            export_dir = root / "exports"
            result = _call(
                bag["export_bundle"],
                {"project_dir": str(root), "output_dir": str(export_dir)},
                runner,
            )
            data = result.data if isinstance(result.data, dict) else {}
            export_zip = str(data.get("output") or "")
            if not result.success or not (export_zip and Path(export_zip).is_file()):
                err = result.error or "zip 未写出"
                _mark(progress, "export", "fail", error=err)
                progress["status"] = "fail"
                save_progress(root, progress)
                return {"success": False, "error": err, "progress": progress, "code": 2}
            _mark(progress, "export", "ok", artifact=export_zip)
            save_progress(root, progress)
        else:
            export_zip = str(((progress.get("steps") or {}).get("export") or {}).get("artifact") or "")

        cleanup_temps(root, keep_scratch=keep_scratch)
        bgm_id = ""
        for ev in _events(soundtrack):
            if str(ev.get("kind") or "") == "bgm":
                bgm_id = str(ev.get("asset_id") or "")
                break
        write_review_md(
            root,
            film="renders/final.mp4",
            soundtrack_id=bgm_id or "artifacts/soundtrack.json",
            export_zip=export_zip or "exports/",
        )
        progress["status"] = "ok"
        save_progress(root, progress)
        _machine_complete(root, ran_gen=ran_gen)
        return {"success": True, "error": "", "progress": progress, "code": 0}
    except ProduceError as exc:
        progress["status"] = "fail"
        progress["error"] = str(exc)
        save_progress(root, progress)
        return {"success": False, "error": str(exc), "progress": progress, "code": 2}
    finally:
        if owned:
            release_lock(root)
