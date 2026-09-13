"""produce 导演 / IDEA 收编流程（--idea 与导演停点编排）。

从 produce.py 抽出，依赖 _produce_common 与外部模块，不 import produce。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from montage.engine.artifacts import ArtifactStore
from montage.engine.director import (
    gold_line_findings,
    has_critical,
    idea_ignored_finding,
    validate_outline,
    validate_setup,
    write_director_review,
)
from montage.engine.policy import load_loop_policy, normalize_frames_mode
from montage.engine.runtime import run_tool
from montage.toolbase import BaseTool, ToolResult
from montage.tools.shot_runner import (
    _cast_needs_url,
    cast_missing,
    collect_shots,
    frames_missing,
    shot_final_ready,
)

from montage.engine._produce_common import (
    ProduceError,
    _call,
    _default_idea_tools,
    _mark,
    _step_done,
    acquire_lock,
    load_progress,
    release_lock,
    save_progress,
)

def _director_stop(
    root: Path,
    progress: dict[str, Any],
    status: str,
    *,
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    notes = [f for f in (findings or []) if isinstance(f, dict)]
    write_director_review(root, status, findings=notes)
    progress["status"] = status
    progress["mode"] = "idea"
    progress["review"] = "director"
    progress["findings"] = notes
    progress.pop("error", None)
    save_progress(root, progress)
    return {"success": True, "error": "", "progress": progress, "code": 0}


def _compile_from_bible(
    root: Path,
    progress: dict[str, Any],
    bag: dict[str, BaseTool],
    runner: Callable[..., ToolResult],
    *,
    idea: str = "",
) -> dict[str, Any] | None:
    """校验并编译圣经。失败返回 produce 结果；成功返回 None 并写入 script/scene_plan。"""
    from montage.tools.idea_developer import IdeaDeveloper

    store = ArtifactStore(root)
    bible = store.read("series_bible")
    if not isinstance(bible, dict):
        progress["status"] = "fail"
        progress["error"] = "await_bible 后缺少 series_bible.json"
        save_progress(root, progress)
        return {"success": False, "error": progress["error"], "progress": progress, "code": 2}
    card = store.read("format_card") if isinstance(store.read("format_card"), dict) else None
    episode = store.read("episode_plan") if isinstance(store.read("episode_plan"), dict) else None
    dev = bag.get("idea_developer") or IdeaDeveloper()
    result = _call(
        dev,
        {
            "operation": "validate",
            "idea": idea,
            "bible": bible,
            "format_card": card,
            "project_dir": str(root),
        },
        runner,
    )
    data = result.data if isinstance(result.data, dict) else {}
    if not result.success or not data.get("pass", True):
        err = result.error or "bible 未通过门禁"
        _mark(progress, "validate_bible", "fail", error=err)
        progress["status"] = "fail"
        progress["error"] = err
        save_progress(root, progress)
        return {"success": False, "error": err, "progress": progress, "code": 2}
    _mark(progress, "validate_bible", "ok")
    compile_payload: dict[str, Any] = {
        "operation": "compile",
        "idea": idea,
        "bible": bible,
        "project_dir": str(root),
    }
    if isinstance(card, dict):
        compile_payload["format_card"] = card
    if isinstance(episode, dict):
        compile_payload["episode_plan"] = episode
    result = _call(
        dev,
        compile_payload,
        runner,
    )
    if not result.success:
        err = result.error or "compile 失败"
        _mark(progress, "compile", "fail", error=err)
        progress["status"] = "fail"
        progress["error"] = err
        save_progress(root, progress)
        return {"success": False, "error": err, "progress": progress, "code": 2}
    _mark(progress, "compile", "ok", artifact=str(root / "artifacts" / "script.json"))
    save_progress(root, progress)
    return None


def _director_compile(
    root: Path,
    progress: dict[str, Any],
    bible: dict[str, Any],
    bag: dict[str, BaseTool],
    runner: Callable[..., ToolResult],
    extra: list[dict[str, Any]],
) -> dict[str, Any]:
    store = ArtifactStore(root)
    card = store.read("format_card") if isinstance(store.read("format_card"), dict) else None
    episode = store.read("episode_plan") if isinstance(store.read("episode_plan"), dict) else None
    dev = bag.get("idea_developer")
    if dev is None:
        raise ProduceError("缺少 idea_developer")
    result = _call(
        dev,
        {
            "operation": "validate",
            "bible": bible,
            "format_card": card,
            "project_dir": str(root),
        },
        runner,
    )
    data = result.data if isinstance(result.data, dict) else {}
    findings = list(extra)
    findings.extend(f for f in (data.get("findings") or []) if isinstance(f, dict))
    if not result.success or not data.get("pass", True) or has_critical(findings):
        err = result.error or "bible 未通过门禁"
        _mark(progress, "validate_bible", "fail", error=err)
        return _director_stop(root, progress, "await_design", findings=findings)
    _mark(progress, "validate_bible", "ok")
    compile_payload: dict[str, Any] = {
        "operation": "compile",
        "bible": bible,
        "project_dir": str(root),
    }
    if isinstance(card, dict):
        compile_payload["format_card"] = card
    if isinstance(episode, dict):
        compile_payload["episode_plan"] = episode
    result = _call(
        dev,
        compile_payload,
        runner,
    )
    if not result.success:
        err = result.error or "compile 失败"
        _mark(progress, "compile", "fail", error=err)
        findings.append({
            "severity": "critical",
            "field": "compile",
            "message": err,
            "proposed_fix": "修 series_bible.json 后再 --resume",
        })
        return _director_stop(root, progress, "await_design", findings=findings)
    compiled = result.data if isinstance(result.data, dict) else {}
    script = compiled.get("script") if isinstance(compiled.get("script"), dict) else store.read("script")
    if isinstance(script, dict):
        findings.extend(gold_line_findings(bible, script))
    findings.extend(f for f in (compiled.get("findings") or []) if isinstance(f, dict))
    _mark(progress, "compile", "ok", artifact=str(root / "artifacts" / "script.json"))
    return _director_stop(root, progress, "await_shots", findings=findings)


def _director_loop(root: Path) -> str:
    return str(load_loop_policy(root).get("video_loop") or "").strip().lower()


def _frames_mode(root: Path) -> str:
    return normalize_frames_mode(load_loop_policy(root).get("frames_mode"))


def _preview_skips_frames(root: Path) -> bool:
    """preview 下 Agnes 首帧不参与 v25 生成，跳过 await_frames 省图片配额与墙钟。"""
    return _director_loop(root) == "agnes" and _frames_mode(root) == "preview"


def _cast_lookup(root: Path) -> dict[str, Any]:
    policy = load_loop_policy(root)
    store = ArtifactStore(root)
    plan = store.read("scene_plan")
    return {
        "require_url": _cast_needs_url(policy),
        "scene_plan": plan if isinstance(plan, dict) else None,
        "video_loop": str(policy.get("video_loop") or "").strip().lower(),
    }


def _skip_director_cast(bible: dict[str, Any], root: Path) -> bool:
    from montage.tools.script_validator import is_spoken_mode

    store = ArtifactStore(root)
    card = store.read("format_card") if isinstance(store.read("format_card"), dict) else None
    return is_spoken_mode(bible=bible, format_card=card)


def _director_cast(
    root: Path,
    progress: dict[str, Any],
    bible: dict[str, Any],
    bag: dict[str, BaseTool],
    runner: Callable[..., ToolResult],
    extra: list[dict[str, Any]],
    retry_ids: list[str] | None = None,
) -> dict[str, Any]:
    """compile 过后（可灵）或 design 过后（其它环）只出定妆静图。失败也停 await_cast。"""
    from montage.tools.shot_runner import ShotRunner

    runner_tool = bag.get("shot_runner") or ShotRunner()
    payload: dict[str, Any] = {
        "project_dir": str(root),
        "dry_run": False,
        "stage": "cast",
        "bible": bible,
        "record_ledger": True,
    }
    if retry_ids:
        payload["retry_ids"] = list(retry_ids)
    result = _call(runner_tool, payload, runner)
    data = result.data if isinstance(result.data, dict) else {}
    findings = list(extra)
    findings.extend(f for f in (data.get("findings") or []) if isinstance(f, dict))
    if not result.success and data.get("blocked"):
        err = result.error or "定妆超预算"
        _mark(progress, "cast", "fail", error=err)
        return _director_stop(root, progress, "await_design", findings=findings)
    retryable = [str(x) for x in (data.get("retryable_ids") or []) if x]
    extra_info: dict[str, Any] = {"retryable_ids": retryable}
    if data.get("estimated_usd") is not None:
        extra_info["estimated_usd"] = data.get("estimated_usd")
    missing = cast_missing(
        bible,
        ArtifactStore(root).read("asset_manifest"),
        str(root),
        **_cast_lookup(root),
    )
    if missing:
        _mark(progress, "cast", "fail", extra=extra_info)
        for job in missing:
            subject = str(job.get("subject") or "")
            if any(str(f.get("field") or "") == subject for f in findings):
                continue
            findings.append({
                "severity": "critical",
                "field": subject,
                "message": f"{subject} 尚未就绪",
                "proposed_fix": f"--retry {subject} --resume",
            })
    else:
        _mark(progress, "cast", "ok", extra=extra_info)
    return _director_stop(root, progress, "await_cast", findings=findings)


def _director_frames(
    root: Path,
    progress: dict[str, Any],
    bag: dict[str, BaseTool],
    runner: Callable[..., ToolResult],
    extra: list[dict[str, Any]],
    retry_ids: list[str] | None = None,
) -> dict[str, Any]:
    """shots 过后只出每镜首帧，停 await_frames；不出 video clip。"""
    from montage.tools.shot_runner import ShotRunner

    runner_tool = bag.get("shot_runner") or ShotRunner()
    payload: dict[str, Any] = {
        "project_dir": str(root),
        "dry_run": False,
        "stage": "frames",
        "record_ledger": True,
    }
    if retry_ids:
        payload["retry_ids"] = list(retry_ids)
    result = _call(runner_tool, payload, runner)
    data = result.data if isinstance(result.data, dict) else {}
    findings = list(extra)
    findings.extend(f for f in (data.get("findings") or []) if isinstance(f, dict))
    if not result.success and data.get("blocked"):
        err = result.error or "首帧超预算"
        _mark(progress, "shot_frames", "fail", error=err)
        return _director_stop(root, progress, "await_shots", findings=findings)
    retryable = [str(x) for x in (data.get("retryable_ids") or []) if x]
    extra_info: dict[str, Any] = {"retryable_ids": retryable}
    if data.get("estimated_usd") is not None:
        extra_info["estimated_usd"] = data.get("estimated_usd")
    store = ArtifactStore(root)
    missing = frames_missing(store.read("scene_plan"), store.read("asset_manifest"), str(root))
    if missing:
        _mark(progress, "shot_frames", "fail", extra=extra_info)
        for shot in missing:
            sid = str(shot.get("shot_id") or "")
            if any(str(f.get("field") or "") == sid for f in findings):
                continue
            findings.append({
                "severity": "critical",
                "field": sid,
                "message": f"{sid} 首帧尚未就绪",
                "proposed_fix": f"--retry {sid} --resume",
            })
    else:
        _mark(progress, "shot_frames", "ok", extra=extra_info)
    return _director_stop(root, progress, "await_frames", findings=findings)


def _director_prompts_preview(
    root: Path,
    progress: dict[str, Any],
    bag: dict[str, BaseTool],
    runner: Callable[..., ToolResult],
    extra: list[dict[str, Any]],
) -> dict[str, Any]:
    """frames 全过后只构建最终提示词（prompt_preview 不落盘媒体），停 await_final_prompt。"""
    from montage.tools.shot_runner import ShotRunner

    runner_tool = bag.get("shot_runner") or ShotRunner()
    result = _call(
        runner_tool,
        {
            "project_dir": str(root),
            "dry_run": False,
            "stage": "prompt_preview",
            "record_ledger": False,
        },
        runner,
    )
    data = result.data if isinstance(result.data, dict) else {}
    findings = list(extra)
    findings.extend(f for f in (data.get("findings") or []) if isinstance(f, dict))
    retryable = [str(x) for x in (data.get("retryable_ids") or []) if x]
    lifted = data.get("shot_prompts")
    prompt_shots = (lifted or {}).get("shots") if isinstance(lifted, dict) else None
    if not result.success or retryable or not isinstance(prompt_shots, list) or not prompt_shots:
        err = result.error or "提示词构建失败"
        for sid in retryable:
            if any(str(f.get("field") or "") == sid for f in findings):
                continue
            findings.append({
                "severity": "critical",
                "field": sid,
                "message": f"{sid} 最终提示词构建失败",
                "proposed_fix": f"--retry {sid} --resume 后重进预览",
            })
        _mark(progress, "shot_prompts", "fail", error=err)
        return _director_stop(root, progress, "await_frames", findings=findings)
    _mark(progress, "shot_prompts", "ok")
    return _director_stop(root, progress, "await_final_prompt", findings=findings)


def run_director_produce(
    project_dir: str | Path,
    *,
    resume: bool = False,
    idea_ignored: bool = False,
    tools: dict[str, BaseTool] | None = None,
    run_tool_fn: Callable[..., ToolResult] | None = None,
    retry_ids: list[str] | None = None,
) -> dict[str, Any]:
    """导演停点：只推进 await_*，不跑 GEN。await_shots + resume 由 run_produce 放行。"""
    root = Path(project_dir)
    runner = run_tool_fn or run_tool
    bag = tools or _default_idea_tools()
    progress = load_progress(root)
    owned = False
    try:
        acquire_lock(root)
        owned = True
        extra = [idea_ignored_finding()] if idea_ignored else []
        status = str(progress.get("status") or "")
        store = ArtifactStore(root)
        bible = store.read("series_bible")
        if not isinstance(bible, dict):
            progress["status"] = "need_bible"
            progress["findings"] = extra
            save_progress(root, progress)
            return {
                "success": False,
                "error": "无 series_bible.json：只写出 format_card，不编假剧情",
                "progress": progress,
                "code": 2,
            }
        if not resume:
            return _director_stop(root, progress, status, findings=extra)
        if status == "await_setup":
            findings = extra + validate_setup(bible)
            nxt = "await_outline" if not has_critical(findings) else "await_setup"
            return _director_stop(root, progress, nxt, findings=findings)
        if status == "await_outline":
            findings = extra + validate_outline(bible)
            nxt = "await_design" if not has_critical(findings) else "await_outline"
            return _director_stop(root, progress, nxt, findings=findings)
        if status == "await_design":
            card = store.read("format_card") if isinstance(store.read("format_card"), dict) else None
            episode = store.read("episode_plan") if isinstance(store.read("episode_plan"), dict) else None
            dev = bag.get("idea_developer")
            if dev is None:
                raise ProduceError("缺少 idea_developer")
            result = _call(
                dev,
                {
                    "operation": "validate",
                    "bible": bible,
                    "format_card": card,
                    "project_dir": str(root),
                },
                runner,
            )
            data = result.data if isinstance(result.data, dict) else {}
            findings = list(extra)
            findings.extend(f for f in (data.get("findings") or []) if isinstance(f, dict))
            if not result.success or not data.get("pass", True) or has_critical(findings):
                err = result.error or "bible 未通过门禁"
                _mark(progress, "validate_bible", "fail", error=err)
                return _director_stop(root, progress, "await_design", findings=findings)
            _mark(progress, "validate_bible", "ok")
            if _skip_director_cast(bible, root) or _director_loop(root) == "kling":
                return _director_compile(root, progress, bible, bag, runner, findings)
            return _director_cast(root, progress, bible, bag, runner, findings, retry_ids=retry_ids)
        if status == "await_cast":
            lookup = _cast_lookup(root)
            if _skip_director_cast(bible, root):
                return _director_compile(root, progress, bible, bag, runner, extra)
            missing = cast_missing(
                bible,
                store.read("asset_manifest"),
                str(root),
                retry_ids=set(retry_ids or []),
                **lookup,
            )
            if missing or retry_ids:
                stopped = _director_cast(
                    root, progress, bible, bag, runner, extra, retry_ids=retry_ids,
                )
                still = cast_missing(
                    bible, ArtifactStore(root).read("asset_manifest"), str(root), **lookup,
                )
                if still:
                    return stopped
            if _director_loop(root) == "kling":
                plan = store.read("scene_plan")
                if isinstance(plan, dict) and collect_shots(plan, None):
                    return _director_frames(
                        root, progress, bag, runner, extra, retry_ids=retry_ids,
                    )
                return _director_compile(root, progress, bible, bag, runner, extra)
            return _director_compile(root, progress, bible, bag, runner, extra)
        if status == "await_shots":
            if _director_loop(root) == "kling" and not _skip_director_cast(bible, root):
                lookup = _cast_lookup(root)
                missing = cast_missing(
                    bible,
                    store.read("asset_manifest"),
                    str(root),
                    retry_ids=set(retry_ids or []),
                    **lookup,
                )
                if missing or retry_ids:
                    return _director_cast(
                        root, progress, bible, bag, runner, extra, retry_ids=retry_ids,
                    )
            if _preview_skips_frames(root):
                # preview：首帧不参与生成，跳过 frames 出图与 await_frames 停点。
                return _director_prompts_preview(root, progress, bag, runner, extra)
            return _director_frames(
                root, progress, bag, runner, extra, retry_ids=retry_ids,
            )
        if status == "await_frames":
            if _preview_skips_frames(root):
                return _director_prompts_preview(root, progress, bag, runner, extra)
            missing = frames_missing(
                store.read("scene_plan"),
                store.read("asset_manifest"),
                str(root),
                retry_ids=set(retry_ids or []),
            )
            if missing or retry_ids:
                return _director_frames(
                    root, progress, bag, runner, extra, retry_ids=retry_ids,
                )
            return _director_prompts_preview(root, progress, bag, runner, extra)
        if status == "await_final_prompt":
            return _director_stop(root, progress, "await_final_prompt", findings=extra)
        if status == "await_clips":
            findings = list(extra)
            for shot in collect_shots(store.read("scene_plan"), None):
                if shot_final_ready(shot, store.read("asset_manifest"), str(root)):
                    continue
                sid = str(shot.get("shot_id") or "")
                findings.append({
                    "severity": "critical",
                    "field": sid,
                    "message": f"{sid} 成片尚未就绪",
                    "proposed_fix": f"--retry {sid} --resume",
                })
            return _director_stop(root, progress, "await_clips", findings=findings)
        return _director_stop(root, progress, status, findings=extra)
    except ProduceError as exc:
        progress["status"] = "fail"
        progress["error"] = str(exc)
        save_progress(root, progress)
        return {"success": False, "error": str(exc), "progress": progress, "code": 2}
    finally:
        if owned:
            release_lock(root)


def run_idea_produce(
    project_dir: str | Path,
    idea: str,
    *,
    resume: bool = False,
    review: str = "bible",
    tools: dict[str, BaseTool] | None = None,
    run_tool_fn: Callable[..., ToolResult] | None = None,
) -> dict[str, Any]:
    """W1 --idea：cascade 后按 review 停。bible 默认先停不编译；director 不 validate/compile；none 立刻校验+编译。"""
    root = Path(project_dir)
    runner = run_tool_fn or run_tool
    bag = tools or _default_idea_tools()
    idea = (idea or "").strip()
    progress = load_progress(root)
    owned = False
    try:
        if not (root / "project.json").is_file():
            raise ProduceError("缺少 project.json（先 montage init）")
        if not idea:
            raise ProduceError("--idea 不能为空")
        acquire_lock(root)
        owned = True
        if not resume:
            progress = {"version": "1", "status": "running", "mode": "idea", "steps": {}}
        store = ArtifactStore(root)
        dev = bag.get("idea_developer")
        if dev is None:
            raise ProduceError("缺少 idea_developer")

        if not _step_done(progress, "cascade", resume):
            result = _call(
                dev,
                {"operation": "skeleton", "idea": idea, "project_dir": str(root)},
                runner,
            )
            if not result.success:
                _mark(progress, "cascade", "fail", error=result.error or "cascade 失败")
                progress["status"] = "fail"
                save_progress(root, progress)
                return {"success": False, "error": result.error, "progress": progress, "code": 2}
            _mark(progress, "cascade", "ok", artifact=str(root / "artifacts" / "format_card.json"))
            save_progress(root, progress)

        bible = store.read("series_bible")
        if not isinstance(bible, dict):
            progress["status"] = "need_bible"
            save_progress(root, progress)
            return {
                "success": False,
                "error": "无 series_bible.json：只写出 format_card，不编假剧情",
                "progress": progress,
                "code": 2,
            }

        if review == "director":
            return _director_stop(root, progress, "await_setup")

        if review != "none":
            progress["status"] = "await_bible"
            progress["mode"] = "idea"
            progress["review"] = "bible"
            save_progress(root, progress)
            return {"success": True, "error": "", "progress": progress, "code": 0}

        card = store.read("format_card") if isinstance(store.read("format_card"), dict) else None
        episode = store.read("episode_plan") if isinstance(store.read("episode_plan"), dict) else None

        if not _step_done(progress, "validate_bible", resume):
            result = _call(
                dev,
                {
                    "operation": "validate",
                    "idea": idea,
                    "bible": bible,
                    "format_card": card,
                    "project_dir": str(root),
                },
                runner,
            )
            data = result.data if isinstance(result.data, dict) else {}
            if not result.success or not data.get("pass", True):
                err = result.error or "bible 未通过门禁"
                _mark(progress, "validate_bible", "fail", error=err)
                progress["status"] = "fail"
                save_progress(root, progress)
                return {"success": False, "error": err, "progress": progress, "code": 2}
            _mark(progress, "validate_bible", "ok")
            save_progress(root, progress)

        if not _step_done(progress, "compile", resume):
            compile_payload: dict[str, Any] = {
                "operation": "compile",
                "idea": idea,
                "bible": bible,
                "project_dir": str(root),
            }
            if isinstance(card, dict):
                compile_payload["format_card"] = card
            if isinstance(episode, dict):
                compile_payload["episode_plan"] = episode
            result = _call(
                dev,
                compile_payload,
                runner,
            )
            if not result.success:
                _mark(progress, "compile", "fail", error=result.error or "compile 失败")
                progress["status"] = "fail"
                save_progress(root, progress)
                return {"success": False, "error": result.error, "progress": progress, "code": 2}
            _mark(
                progress, "compile", "ok",
                artifact=str(root / "artifacts" / "script.json"),
            )
            save_progress(root, progress)

        progress["status"] = "await_bible" if review != "none" else "compiled"
        save_progress(root, progress)
        return {"success": True, "error": "", "progress": progress, "code": 0}
    except ProduceError as exc:
        progress["status"] = "fail"
        progress["error"] = str(exc)
        save_progress(root, progress)
        return {"success": False, "error": str(exc), "progress": progress, "code": 2}
    finally:
        if owned:
            release_lock(root)


