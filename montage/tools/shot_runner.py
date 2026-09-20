"""shot_runner — 定妆照 → 首帧 → 质检 → 图生视频 编排器。

- 默认 dry_run=true：按镜调用供应商 estimate_cost 求和，超 budget_ceiling_usd 则停，不打 API。
- dry_run=false：写磁盘（媒体 + asset_manifest.reference_assets + 把嵌套 shots lift 成 shot_prompts）。
- 单镜失败不中断，返回 retryable_ids。默认串行。
- 复用 generation_cache / asset_quality_gate；成片后抽尾帧给下一镜 I2V 首帧（不填本镜 last_frame）。
"""

from __future__ import annotations

import os
import threading
import uuid
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from montage.engine.artifacts import ArtifactStore
from montage.engine.budget import BudgetLedger
from montage.engine.continuity import (
    format_continuity_note,
    load_or_seed_continuity,
    update_continuity,
    write_continuity,
)
from montage.engine.episodes import copy_sibling_still_refs
from montage.engine.policy import (
    MAX_REF_SEGMENTS,
    load_loop_policy,
    normalize_frames_mode,
    normalize_ref_overflow_mode,
    resolve_allowed_providers,
)
from montage.engine.rework import pick_rework_mode, rework_prompt
from montage.engine.retry_policy import decide_retry
from montage.providers.capabilities import (
    agnes_access_tier,
    agnes_image_ref_entries,
    agnes_image_rpm,
    agnes_video_rpm,
    apply_image_refs,
    apply_video_frames,
    image_caps,
    snap_duration_seconds,
    video_caps,
    video_surface,
    VIDEO_SURFACES,
)
from montage.providers.agnes import AGNES_IMAGE_RATIOS, AGNES_VIDEO_RATIOS
from montage.providers.agnes_usage import (
    add_images as agnes_add_images,
    add_video_seconds as agnes_add_video_seconds,
    quota_status as agnes_quota_status,
)
from montage.providers.prompt_adapter import adapt_visual_prompt
from montage.providers.selectors import ImageSelector, VideoSelector
from montage.providers.video_prompts import prompt_profile
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus
from montage.tools.generation_events import append_event

from montage.tools._shot_constants import (
    MAX_ATTEMPTS,
    _AGNES_FLASH_MAX_IMAGES,
    _CAST_EST_IMAGE_WARN,
    _DEFAULT_IMAGE_TOOL,
    _DEFAULT_VIDEO_TOOL,
    _MAX_PROPS,
    _FAMILY_APIS,
    _SEEDANCE_APIS,
    _EXPLICIT_FRAME_APIS,
    _REF_ROLES,
    _REFINE_HINT,
)
from montage.tools._shot_route import (
    _nested_plan_shots,
    collect_shots,
    overlay_plan_rework,
    lift_shot_prompts,
    _shot_cut,
    _clears_bridge,
    _next_hero_tail,
    _want_last_frame,
    _is_agnes_loop,
    loop_family,
    _env_model,
    _shot_duration,
    _shot_is_hero,
    _shot_dialogue,
    _shot_has_dialogue,
    _adapter_refs,
    _route_caps,
    _pick_seedance,
    _pick_kling,
    _route_shot,
    _prompt_inputs,
    _next_still_urls,
    _agnes_is_v25,
    agnes_duration_chunks,
    resolved_agnes_mode,
    build_mode_plan,
    shot_audio_ref_urls,
    audios_compatible_with_mode,
)
from montage.engine.identity import (
    clear_drift,
    drift_issues,
    entry_of as identity_entry,
    evaluate_candidate,
    findings as identity_findings,
    identity_key,
    identity_subject,
    load_memory as load_identity_memory,
    mark_retaken,
    record_drift,
    retake_subjects,
    save_memory as save_identity_memory,
)
from montage.tools._shot_refs import (
    _http_video_urls,
    _http_still_urls,
    _identity_http_refs,
    _agnes_flash_images,
    _agnes_flash_image_plan,
    agnes_ref_findings,
    agnes_plan_adapter_refs,
    plan_reference_segments,
    _vlm_expected,
    _needs_agnes_refine,
    _media_item,
    probe_seconds,
    _character_ids,
    _shot_character_forms,
    missing_identity_refs,
    _registry_map,
    _media_exists,
    _item_ready,
    _ref_ready,
    _agnes_cast_needs_url,
    _cast_needs_url,
    _items_of,
    shot_final_ready,
    clips_compose_ready,
    _retry_covers,
    _job_already_done,
    _spoken_skip_portraits,
    _ref_index,
    _portrait_index,
    _turnaround_index,
    _portrait_refs_by_form,
    _turnaround_refs_by_form,
    form_ref_id,
    form_subject,
    parse_form_subject,
    character_forms,
    form_id_of,
    blend_character_form,
    effective_skip_turnaround,
    _scene_ref_index,
    _prop_index,
    _char_for_prompt,
    _append_note,
    _location_scene,
    collect_cast_jobs,
    _cast_ref_for_job,
    first_frame_item,
    frames_missing,
    cast_missing,
    _upsert_ref,
    _upsert_item,
    name_refs_for_prompt,
    _script_prop_map,
    collect_prop_ids,
    resolve_shot_refs,
    _prop_prompt,
    _media_path,
    _media_url,
    _skip_pacing,
    _critical_fail,
    build_image_bindings,
    merge_image_bindings,
    reconcile_image_bindings,
)


class _IntervalPacer:
    def __init__(self, interval: float) -> None:
        self._lock = threading.Lock()
        self._interval = max(float(interval), 0.0)
        self._next_at = 0.0

    def set_interval(self, interval: float) -> None:
        self._interval = max(float(interval), 0.0)

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            wait = self._next_at - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._next_at = time.monotonic() + self._interval


class ShotRunner(BaseTool):
    name = "shot_runner"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.HYBRID
    input_schema = {
        "type": "object",
        "properties": {
            "scene_plan": {"type": "object"},
            "shot_prompts": {"type": "object"},
            "asset_manifest": {"type": "object"},
            "project_dir": {"type": "string"},
            "dry_run": {
                "type": "boolean",
                "default": True,
                "description": "默认 true：只估算不打真实 API",
            },
            "overwrite": {"type": "boolean", "default": False},
            "retry_ids": {"type": "array", "items": {"type": "string"}},
            "force_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "全量时强制重抽这些镜，不裁镜头列表",
            },
            "prompt_fallback": {
                "type": "boolean",
                "default": False,
                "description": "await_prompt 后用户拒绝改圣经：压缩再截到 3000 字",
            },
            "max_shots": {"type": "integer"},
            "record_ledger": {
                "type": "boolean",
                "default": True,
                "description": "dry_run 是否写入 cost.jsonl；produce 的 dry_run 传 false",
            },
            "generate_scene_refs": {"type": "boolean", "default": False},
            "stage": {
                "type": "string",
                "enum": ["", "cast", "frames", "prompt_preview"],
                "description": "cast：只出定妆；frames：只出每镜首帧，不出视频；prompt_preview：只构建提示词写 shot_prompts.json，不落盘媒体",
            },
            "bible": {"type": "object", "description": "stage=cast 时的 series_bible；缺省读产物"},
            "script": {"type": "object", "description": "可选；读 props[] 与镜头 objects 对齐"},
        },
    }

    def __init__(
        self,
        *,
        image_execute: Callable[[dict[str, Any]], ToolResult] | None = None,
        video_execute: Callable[[dict[str, Any]], ToolResult] | None = None,
        image_estimate: Callable[[dict[str, Any]], float] | None = None,
        video_estimate: Callable[[dict[str, Any]], float] | None = None,
        quality_check: Callable[..., dict[str, Any]] | None = None,
        extract_last_frame: Callable[[str, str], str | None] | None = None,
        vlm_review: Callable[..., dict[str, Any]] | None = None,
        event_emitter: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._image_execute = image_execute
        self._video_execute = video_execute
        self._image_estimate = image_estimate
        self._video_estimate = video_estimate
        self._quality_check = quality_check
        self._extract_last_frame = extract_last_frame
        self._vlm_review = vlm_review
        self._event_emitter = event_emitter
        self._event_context: dict[str, Any] = {}
        self._event_errors: list[dict[str, Any]] = []
        self._event_lock = threading.Lock()
        self._image_selector = ImageSelector()
        self._video_selector = VideoSelector()

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # 编排器本身零成本；dry_run/execute 内部按镜走供应商单价。
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        project_dir = str(inputs.get("project_dir") or "")
        store = ArtifactStore(project_dir) if project_dir else None
        if self._event_emitter is None and project_dir:
            events_path = Path(project_dir) / "artifacts" / "generation_events.jsonl"
            self._event_emitter = lambda row: append_event(events_path, row)
        if self._event_emitter is not None:
            run_id = str(inputs.get("run_id") or uuid.uuid4().hex)
            self._event_context = {
                "run_id": run_id,
                "batch_id": str(inputs.get("batch_id") or run_id),
                "stage": str(inputs.get("stage") or ""),
            }
            self._event_errors = []
        scene_plan = inputs.get("scene_plan") if isinstance(inputs.get("scene_plan"), dict) else None
        shot_prompts = inputs.get("shot_prompts") if isinstance(inputs.get("shot_prompts"), dict) else None
        manifest = inputs.get("asset_manifest") if isinstance(inputs.get("asset_manifest"), dict) else None
        script = inputs.get("script") if isinstance(inputs.get("script"), dict) else None
        if store:
            scene_plan = scene_plan or store.read("scene_plan")
            shot_prompts = shot_prompts or store.read("shot_prompts")
            manifest = manifest or store.read("asset_manifest")
            script = script or store.read("script")

        stage = str(inputs.get("stage") or "").strip()
        if stage == "cast":
            bible = inputs.get("bible") if isinstance(inputs.get("bible"), dict) else None
            if store and not isinstance(bible, dict):
                bible = store.read("series_bible")
            return self._execute_cast(
                inputs=inputs,
                project_dir=project_dir,
                store=store,
                bible=bible if isinstance(bible, dict) else {},
                manifest=manifest if isinstance(manifest, dict) else None,
                script=script if isinstance(script, dict) else None,
                scene_plan=scene_plan if isinstance(scene_plan, dict) else None,
            )

        shots = collect_shots(scene_plan, shot_prompts)
        overlay_plan_rework(shots, scene_plan)
        timeline_shots = list(shots)
        retry_ids = {str(x) for x in (inputs.get("retry_ids") or []) if x}
        force_ids = {str(x) for x in (inputs.get("force_ids") or []) if x}
        video_ids = {str(x) for x in (inputs.get("video_ids") or []) if x}
        if retry_ids:
            shots = [s for s in shots if str(s.get("shot_id") or "") in retry_ids]
        max_shots = inputs.get("max_shots")
        remaining_ids: list[str] = []
        if isinstance(max_shots, int) and max_shots > 0 and len(shots) > max_shots:
            remaining_ids = [str(s.get("shot_id") or "") for s in shots[max_shots:]]
            shots = shots[:max_shots]
        if not shots:
            still_retry = any(
                str(t).startswith("portrait/")
                or str(t).startswith("prop/")
                or str(t).startswith("turnaround/")
                or str(t).startswith("scene_ref/")
                for t in retry_ids
            )
            if not (timeline_shots and still_retry):
                return ToolResult(success=False, error="没有可跑的镜头（需要 scene_plan.scenes[].shots 或 shot_prompts.shots）")

        dry_run = inputs.get("dry_run")
        if dry_run is None:
            dry_run = True
        dry_run = bool(dry_run)

        policy = load_loop_policy(project_dir) if project_dir else {}
        ceiling = policy.get("budget_ceiling_usd")
        try:
            ceiling_f = float(ceiling) if ceiling is not None else None
        except (TypeError, ValueError):
            ceiling_f = None

        selector_inputs = {"project_dir": project_dir} if project_dir else {}
        img_tool = self._catalog_tool(self._image_selector, selector_inputs)
        vid_tool = self._catalog_tool(self._video_selector, selector_inputs)
        img_name = getattr(img_tool, "name", "") or _DEFAULT_IMAGE_TOOL
        img_prov = getattr(img_tool, "provider", "") or "volcengine"
        vid_name = getattr(vid_tool, "name", "") or _DEFAULT_VIDEO_TOOL
        vid_prov = getattr(vid_tool, "provider", "") or "volcengine"
        i_caps = image_caps(tool=img_name, provider=img_prov)
        v_caps = video_caps(tool=vid_name, provider=vid_prov)
        agnes_loop = _is_agnes_loop(policy, vid_prov)
        cast_ref_kind = str(policy.get("cast_ref_kind") or "")
        video_loop = str(policy.get("video_loop") or "")
        frames_mode = normalize_frames_mode(policy.get("frames_mode"))
        ref_overflow_mode = normalize_ref_overflow_mode(policy.get("ref_overflow_mode"))
        rpm = agnes_video_rpm() if agnes_loop else 0.0
        self._pace_video_s = (60.0 / rpm) if rpm > 0 else 0.0
        self._video_pacer = _IntervalPacer(self._pace_video_s)
        self._last_video_at = 0.0
        self._last_image_at = 0.0
        self._image_pacers = {}
        self._agnes_image_active = img_name == "agnes_image"
        self._agnes_video_active = agnes_loop
        self._video_pacer = _IntervalPacer(self._pace_video_s)

        portraits = _portrait_index(manifest)
        portraits_by_form = _portrait_refs_by_form(manifest)
        registry = _registry_map(scene_plan)
        existing_props = _prop_index(manifest)
        bible = inputs.get("bible") if isinstance(inputs.get("bible"), dict) else None
        if store and not isinstance(bible, dict):
            bible = store.read("series_bible")
        bible_chars: dict[str, dict[str, Any]] = {}
        if isinstance(bible, dict):
            for char in bible.get("characters") or []:
                if isinstance(char, dict) and char.get("id"):
                    bible_chars[str(char["id"])] = char

        def forms_for_char(cid: str) -> list[dict[str, Any]]:
            """角色形态：优先 bible（含显式 forms），否则 registry/隐式单形态。"""
            char = bible_chars.get(cid)
            if not isinstance(char, dict):
                char = registry.get(cid)
            return character_forms(char if isinstance(char, dict) else {"id": cid})

        record_ledger = inputs.get("record_ledger")
        if record_ledger is None:
            record_ledger = True
        record_ledger = bool(record_ledger)
        format_card = store.read("format_card") if store else None
        skip_portraits = _spoken_skip_portraits(scene_plan, script, format_card)

        used_portrait_forms: list[tuple[str, str]] = []
        for shot in timeline_shots:
            for cid in _character_ids(shot, scene_plan):
                if cid not in registry:
                    continue
                for form in forms_for_char(cid):
                    key = (cid, form_id_of(form))
                    if key not in used_portrait_forms:
                        used_portrait_forms.append(key)
        used_prop_ids = collect_prop_ids(timeline_shots, script)
        if project_dir:
            if not isinstance(manifest, dict):
                manifest = {"items": [], "reference_assets": []}
            else:
                manifest.setdefault("items", [])
                manifest.setdefault("reference_assets", [])
            copy_sibling_still_refs(
                project_dir,
                portrait_ids=[cid for cid, _fid in used_portrait_forms],
                portrait_forms=used_portrait_forms,
                prop_ids=used_prop_ids,
                retry_ids=retry_ids,
                manifest=manifest,
            )
            portraits = _portrait_index(manifest)
            portraits_by_form = _portrait_refs_by_form(manifest)
            existing_props = _prop_index(manifest)

        needed_portrait_forms: list[dict[str, Any]] = []
        seen_needed_portraits: set[tuple[str, str]] = set()
        need_http = _agnes_cast_needs_url(policy)

        def _add_needed_portrait(cid: str, form: dict[str, Any]) -> None:
            fid = form_id_of(form)
            key = (cid, fid)
            if key in seen_needed_portraits:
                return
            if _retry_covers(retry_ids, portrait_id=cid, form_id=fid) or not _ref_ready(
                project_dir, portraits_by_form.get(key), require_url=need_http,
            ):
                seen_needed_portraits.add(key)
                needed_portrait_forms.append(
                    {"character_id": cid, "form_id": fid, "form": form}
                )

        for shot in shots:
            for cid in _character_ids(shot, scene_plan):
                if cid not in registry:
                    continue
                for form in forms_for_char(cid):
                    _add_needed_portrait(cid, form)
        for token in retry_ids:
            text = str(token)
            if text.startswith("portrait/"):
                cid, fid = parse_form_subject(text)
            elif text in registry:
                cid, fid = text, ""
            else:
                continue
            if not cid or cid not in registry:
                continue
            forms = forms_for_char(cid)
            if fid:
                picked = [f for f in forms if form_id_of(f) == fid]
                forms = picked or [{"id": fid}]
            for form in forms:
                _add_needed_portrait(cid, form)
        needed_props = [
            pid for pid in collect_prop_ids(shots, script)
            if _retry_covers(retry_ids, prop_id=pid) or not _ref_ready(
                project_dir, existing_props.get(pid), require_url=need_http,
            )
        ]
        for token in retry_ids:
            pid = ""
            text = str(token)
            if text.startswith("prop/"):
                pid = text.split("/", 1)[1]
            elif text in used_prop_ids:
                pid = text
            if (
                pid
                and pid in used_prop_ids
                and pid not in needed_props
                and (
                    _retry_covers(retry_ids, prop_id=pid)
                    or not _ref_ready(project_dir, existing_props.get(pid), require_url=need_http)
                )
            ):
                needed_props.append(pid)

        jobs: list[dict[str, Any]] = []
        findings_pre: list[dict[str, str]] = []
        # mode_plan（v8.2 P0-mode-allocation）：审核快照 + findings 载体，不进
        # 运行时查表——运行时真源仍是 scene_plan.shots[].agnes_mode 经
        # overlay_plan_rework 覆盖后的 shot dict。冲突镜 finding 在此上屏。
        mode_plan = build_mode_plan(shots, frames_mode=frames_mode)
        findings_pre.extend(mode_plan.get("findings") or [])
        if store and mode_plan.get("modes"):
            store.write("mode_plan", mode_plan, schema=None)
        for item in needed_portrait_forms:
            cid = str(item.get("character_id") or "")
            fid = str(item.get("form_id") or "")
            jobs.append({
                "kind": "portrait",
                "subject": form_subject("portrait", cid, fid),
                "character_id": cid,
                "form_id": fid,
                "form": item.get("form"),
                "category": "image_generation",
                "item": img_name,
            })
        for pid in needed_props:
            jobs.append({
                "kind": "prop",
                "subject": f"prop/{pid}",
                "prop_id": pid,
                "category": "image_generation",
                "item": img_name,
            })
        frames_only = stage == "frames"
        prompt_only = stage == "prompt_preview"
        if prompt_only:
            frames_only = True

        def _still_for(sid: str) -> dict[str, Any] | None:
            """本镜已落盘的首帧（非定妆），供 reference_first 占位判定。"""
            for item in reversed(list((manifest or {}).get("items") or [])):
                if not isinstance(item, dict):
                    continue
                if str(item.get("kind") or "") != "image":
                    continue
                if str(item.get("shot_id") or "") != sid:
                    continue
                if "portrait" in str(item.get("id") or ""):
                    continue
                if _item_ready(project_dir, item):
                    return item
            return None

        for shot in shots:
            sid = str(shot.get("shot_id") or "")
            jobs.append({
                "kind": "first_frame",
                "subject": f"{shot.get('scene_id')}/{sid}",
                "shot_id": sid,
                "category": "image_generation",
                "item": img_name,
            })
            if frames_only:
                continue
            if video_ids and sid not in video_ids:
                continue
            if str(shot.get("shot_kind") or "video") != "image":
                seconds = float(shot.get("duration_seconds") or 5)
                route = _route_shot(
                    shot,
                    video_loop=str(policy.get("video_loop") or ""),
                    vid_prov=vid_prov,
                    vid_name=vid_name,
                    has_first_frame=True,
                    video_surface_id=str(policy.get("video_surface") or ""),
                )
                if agnes_loop:
                    chunk_secs = agnes_duration_chunks(seconds)
                    if _agnes_is_v25() and ref_overflow_mode == "segment" and frames_mode != "keyframe":
                        existing = _still_for(sid) if sid else None
                        first_url = str((existing or {}).get("url") or "")
                        ident = _identity_http_refs(
                            shot, list((manifest or {}).get("reference_assets") or []),
                            script, scene_plan,
                            cast_ref_kind=cast_ref_kind, video_loop=video_loop,
                        )
                        seg_plan = plan_reference_segments(
                            ident,
                            max_images=int(route["caps"].get("max_ref_images") or _AGNES_FLASH_MAX_IMAGES),
                            wanted_seconds=seconds,
                            min_seconds=float(
                                (route["caps"].get("duration_policy") or {}).get("min") or 4
                            ),
                            max_seconds=float(
                                (route["caps"].get("duration_policy") or {}).get("max") or 12
                            ),
                            reserve_first=(
                                frames_mode == "reference_first" and first_url.startswith("http")
                            ),
                            max_segments=MAX_REF_SEGMENTS,
                        )
                        if seg_plan.get("mode") == "segment":
                            segs = list(seg_plan.get("segments") or [])
                            chunk_secs = [float(s.get("seconds") or seconds) for s in segs]
                            n_bridge = max(0, len(segs) - 1)
                            for b in range(n_bridge):
                                # 桥接首帧是额外图片调用（尾帧 + 本段参考合成）。
                                jobs.append({
                                    "kind": "bridge_frame",
                                    "subject": f"{shot.get('scene_id')}/{sid}#bridge{b + 1}",
                                    "shot_id": sid,
                                    "seq": b + 1,
                                    "category": "image_generation",
                                    "item": img_name,
                                })
                            findings_pre.append({
                                "severity": "info",
                                "field": f"{shot.get('scene_id')}/{sid}",
                                "message": (
                                    f"参考图溢出：dry_run 按镜内 {len(segs)} 段续拍估算"
                                    f"（另加 {n_bridge} 张续接首帧）"
                                ),
                                "proposed_fix": "ref_overflow_mode=single 可回旧行为",
                            })
                else:
                    chunk_secs = [snap_duration_seconds(seconds, route["caps"].get("duration_policy"))]
                for cs in chunk_secs:
                    jobs.append({
                        "kind": "video",
                        "subject": f"{shot.get('scene_id')}/{sid}",
                        "shot_id": sid,
                        "seconds": cs,
                        "api_id": route["api_id"],
                        "category": "video_generation",
                        "item": vid_name,
                    })

        skipped_ids: list[str] = []
        kept_jobs: list[dict[str, Any]] = []
        for job in jobs:
            if _job_already_done(
                job,
                manifest=manifest,
                project_dir=project_dir,
                retry_ids=retry_ids,
                portraits=portraits_by_form,
                props=existing_props,
                frames_only=frames_only,
                force_ids=force_ids,
            ):
                skipped_ids.append(str(job.get("subject") or job.get("shot_id") or ""))
                continue
            kept_jobs.append(job)
        jobs = kept_jobs

        estimated = 0.0
        for job in jobs:
            usd = self._estimate_job(job, img_tool, vid_tool, project_dir)
            job["estimate_usd"] = usd
            estimated += usd
        estimated = round(estimated, 6)

        ledger = None
        booked_raw = 0.0
        settled = 0.0
        if project_dir:
            ledger = BudgetLedger(Path(project_dir) / "cost.jsonl", budget_ceiling_usd=ceiling_f)
            totals = ledger.totals()
            booked_raw = float(totals["estimated_raw_usd"])
            settled = float(totals["settled_usd"])
        booked = booked_raw if record_ledger else settled
        projected = booked + estimated
        over_budget = ceiling_f is not None and projected > ceiling_f
        soft = os.environ.get("MONTAGE_BUDGET_SOFT") == "1"
        findings: list[dict[str, str]] = list(findings_pre)
        if over_budget:
            findings.append({
                "severity": "critical" if not dry_run else "warning",
                "field": "budget_ceiling_usd",
                "message": (
                    f"估算 ${estimated:.4f} + 已记账 ${booked:.4f} 超过封顶 "
                    f"${ceiling_f:.4f}"
                ),
                "proposed_fix": "提高 budget_ceiling_usd、减少镜头，或设 MONTAGE_BUDGET_SOFT=1",
            })

        payload = {
            "dry_run": dry_run,
            "over_budget": over_budget,
            "blocked": False,
            "budget_ceiling_usd": ceiling_f,
            "estimated_usd": estimated,
            "jobs": jobs,
            "skipped_ids": skipped_ids,
            "image_tool": img_name,
            "video_tool": vid_name,
            "image_caps": i_caps,
            "video_caps": v_caps,
            "remaining_ids": remaining_ids,
            "retryable_ids": [],
            "findings": findings,
            "shot_prompts": lift_shot_prompts(timeline_shots),
            "stage": stage,
        }

        if dry_run:
            if record_ledger and ledger is not None and estimated > 0:
                ledger.estimate_checked(
                    "analysis", "shot_runner/dry_run", self.name, estimated,
                )
            payload["blocked"] = over_budget and ceiling_f is not None
            if agnes_loop:
                tier = agnes_access_tier()
                v_rpm = agnes_video_rpm()
                i_rpm = agnes_image_rpm("2K")
                n_vid = sum(1 for j in jobs if j.get("kind") == "video")
                n_img = sum(
                    1 for j in jobs
                    if j.get("kind") in ("portrait", "prop", "first_frame", "bridge_frame")
                )
                est_vid_s = sum(
                    float(j.get("seconds") or 0) for j in jobs if j.get("kind") == "video"
                )
                vid_min = (n_vid * 60.0 / v_rpm) / 60.0 if v_rpm > 0 else 0.0
                img_sec = n_img * 60.0 / i_rpm if i_rpm > 0 else 0.0
                payload["pacing_note"] = (
                    f"Agnes 访问档位 {tier}：视频 {v_rpm:g} RPM，{n_vid} 段约 {vid_min:.1f} 分钟；"
                    f"图片 2K {i_rpm:g} RPM，{n_img} 张约 {img_sec:.0f} 秒"
                    "（pytest / MONTAGE_SKIP_PACING=1 关闭等待）"
                )
                # Token Plan 每日配额排片提示（只告警不阻断）；field 必须独立，
                # 不能用 subject/shot_id，否则会抑制 director 补 critical（R6-①）
                if tier == "tokenplan":
                    _, _, img_left = agnes_quota_status(tier, "image")
                    _, _, vid_left = agnes_quota_status(tier, "video")
                    overs: list[str] = []
                    if img_left is not None and n_img > img_left:
                        overs.append(f"图片 {n_img} 张 > 今日剩余 {img_left:g} 张")
                    if vid_left is not None and est_vid_s > vid_left:
                        overs.append(f"视频 {est_vid_s:g} 秒 > 今日剩余 {vid_left:g} 秒")
                    if overs:
                        findings.append({
                            "severity": "warning",
                            "field": "agnes_quota",
                            "message": "Token Plan 今日配额可能不足：" + "；".join(overs),
                            "proposed_fix": "拆到次日分批 / 临时切免费密钥 / 降规格",
                        })
            return ToolResult(success=True, data=payload, meta={"dry_run": True})

        if over_budget and ceiling_f is not None and not soft:
            payload["blocked"] = True
            return ToolResult(
                success=False,
                error=findings[0]["message"] if findings else "超预算",
                data=payload,
            )
        if not project_dir:
            return ToolResult(success=False, error="dry_run=false 需要 project_dir 以写入媒体与产物")

        skipped_first = {
            sid for sid in {str(s.get("shot_id") or "") for s in shots}
            if any(
                _item_ready(project_dir, item)
                for item in _items_of(manifest, kind="image", shot_id=sid)
            ) and not (frames_only and _retry_covers(retry_ids, shot_id=sid))
            and sid not in force_ids
        }
        skipped_video = {
            sid for sid in {str(s.get("shot_id") or "") for s in shots}
            if any(
                _item_ready(project_dir, item)
                for item in _items_of(manifest, kind="video", shot_id=sid)
            ) and not _retry_covers(retry_ids, shot_id=sid)
            and sid not in force_ids
        }

        payload["prompt_fallback"] = bool(inputs.get("prompt_fallback"))
        return self._execute_jobs(
            shots=shots,
            timeline_shots=timeline_shots,
            scene_plan=scene_plan or {},
            script=script or {},
            manifest=manifest or {"items": []},
            store=store,
            project_dir=project_dir,
            ledger=ledger,
            portraits=portraits,
            registry=registry,
            needed_portrait_forms=needed_portrait_forms,
            needed_props=needed_props,
            img_tool=img_tool,
            vid_tool=vid_tool,
            i_caps=i_caps,
            v_caps=v_caps,
            payload=payload,
            skip_portraits=skip_portraits,
            skipped_first=skipped_first,
            skipped_video=skipped_video,
            retry_ids=retry_ids,
            frames_only=frames_only,
            prompt_only=prompt_only,
            force_ids=force_ids,
            video_ids=video_ids,
        )

    def _cast_still_prompt(
        self,
        job: dict[str, Any],
        *,
        bible: dict[str, Any],
        project_dir: str,
        kling_loop: bool = False,
    ) -> tuple[str, dict[str, Any] | None]:
        """返回 (prompt, finding)。finding 非空表示无法生图。"""
        from lib.shot_prompt_builder import build_kling_look_sheet_prompt, build_kling_prop_prompt
        from montage.tools.visual_prompt_builder import VisualPromptBuilder

        kind = str(job.get("kind") or "")
        builder = VisualPromptBuilder()
        if kind in ("portrait", "turnaround"):
            cid = str(job.get("character_id") or "")
            fid = str(job.get("form_id") or "")
            subject = str(job.get("subject") or f"{kind}/{cid}")
            form = job.get("form") if isinstance(job.get("form"), dict) else None
            char = None
            for item in bible.get("characters") or []:
                if isinstance(item, dict) and str(item.get("id") or "") == cid:
                    char = _char_for_prompt(item)
                    break
            char = char or _char_for_prompt({"id": cid})
            if fid and form:
                # form 覆盖 name/appearance/outfit；空值回落角色字段。
                char = blend_character_form(char, form)
            if kling_loop and kind == "portrait":
                prompt = build_kling_look_sheet_prompt(char)
                if not prompt:
                    return "", {
                        "severity": "critical",
                        "field": subject,
                        "message": "拼板提示词为空",
                        "proposed_fix": "补 characters[].appearance",
                    }
                return _append_note(prompt, str(char.get("cast_note") or "")), None
            try:
                built = builder.execute({
                    "purpose": kind,
                    "character": char,
                    "project_dir": project_dir,
                    # 定妆/四视图是全片身份锚点：禁词库注入（enrich 会把
                    # 外观词弱匹配到的「霓虹都市」等噪声条目塞进定妆
                    # 提示词，污染人物本身）。与 scene_ref 分支同语义。
                    "enrich_first_frame": False,
                })
            except ValueError as exc:
                return "", {
                    "severity": "critical",
                    "field": subject,
                    "message": str(exc),
                    "proposed_fix": "补 characters[].appearance",
                }
            prompt = ""
            if built.success and isinstance(built.data, dict):
                prompt = str(built.data.get("first_frame_prompt") or "")
            if not prompt:
                return "", {
                    "severity": "critical",
                    "field": subject,
                    "message": (built.error if built else "") or "定妆提示词为空",
                    "proposed_fix": "检查人物卡 appearance",
                }
            return _append_note(prompt, str(char.get("cast_note") or "")), None
        if kind == "scene_ref":
            lid = str(job.get("location_id") or "")
            loc = None
            for item in bible.get("locations") or []:
                if isinstance(item, dict) and str(item.get("id") or item.get("name") or "") == lid:
                    loc = item
                    break
            loc = loc or {"id": lid, "name": lid}
            try:
                built = builder.execute({
                    "purpose": "scene_ref",
                    "scene": _location_scene(loc),
                    "project_dir": project_dir,
                    "english_visual": False,
                    "enrich_first_frame": False,
                })
            except ValueError as exc:
                return "", {
                    "severity": "critical",
                    "field": f"scene_ref/{lid}",
                    "message": str(exc),
                    "proposed_fix": "补 locations[].appearance 或 sensory",
                }
            prompt = ""
            if built.success and isinstance(built.data, dict):
                prompt = str(built.data.get("first_frame_prompt") or "")
            if not prompt:
                return "", {
                    "severity": "critical",
                    "field": f"scene_ref/{lid}",
                    "message": (built.error if built else "") or "空镜提示词为空",
                    "proposed_fix": "补地点空镜描述",
                }
            return prompt, None
        if kind == "prop":
            pid = str(job.get("prop_id") or "")
            prop = None
            for item in bible.get("props") or []:
                if isinstance(item, dict) and str(item.get("id") or item.get("name") or "") == pid:
                    prop = item
                    break
                if isinstance(item, str) and item.strip() == pid:
                    prop = {"id": pid, "name": pid, "appearance": pid}
                    break
            prop = prop or {"id": pid, "name": pid, "appearance": pid}
            raw = build_kling_prop_prompt(prop) if kling_loop else _prop_prompt(prop)
            prompt = _append_note(raw, str(prop.get("cast_note") or "") if isinstance(prop, dict) else "")
            return prompt, None
        return "", {
            "severity": "warning",
            "field": kind,
            "message": f"未知定妆 kind={kind}",
            "proposed_fix": "只用 portrait/turnaround/scene_ref/prop",
        }

    def _kling_sheet_followup(
        self,
        *,
        sheet_path: str,
        cid: str,
        char: dict[str, Any],
        img_dir: Path,
        url: str,
        provider: str,
        findings: list[dict[str, Any]],
        attempt: int,
        role: str = "character",
    ) -> dict[str, Any]:
        """拼板裁切 + QC + 工牌。retry=True 时调用方再打一张。道具不绑音色。"""
        from montage.providers._kling_looksheet import (
            LOOK_SHEET_ELEMENT_KEYS,
            crop_look_sheet,
            element_slots_from_crops,
            look_sheet_next_action,
        )
        from montage.providers.kling import kling_create_element, kling_create_voice

        is_prop = str(role or "character").strip().lower() == "prop"
        subject_field = f"prop/{cid}" if is_prop else f"portrait/{cid}"
        cropped = crop_look_sheet(sheet_path, img_dir, cid)
        action = look_sheet_next_action(qc_ok=bool(cropped.get("ok")), attempt=attempt)
        if action == "retry_sheet":
            return {"retry": True, "notes": cropped.get("notes") or []}
        cells = cropped.get("cells") if isinstance(cropped.get("cells"), dict) else {}
        portrait_path = str(cells.get("portrait") or sheet_path)
        views = [k for k in ("front", "side", "back", "three_quarter") if cells.get(
            {"front": "portrait", "side": "side", "back": "back", "three_quarter": "tq"}[k]
        )]
        view_list = views or ["front", "side", "back", "three_quarter"]
        if is_prop:
            refs: list[dict[str, Any]] = [{
                "id": f"prop_{cid}",
                "kind": "prop",
                "prop_id": cid,
                "path": portrait_path,
                "url": url if portrait_path == sheet_path else "",
                "provider": provider,
                "views": view_list,
                "look_sheet": sheet_path,
            }]
        else:
            refs = [{
                "id": f"portrait_{cid}",
                "kind": "portrait",
                "character_id": cid,
                "path": portrait_path,
                "url": url if portrait_path == sheet_path else "",
                "provider": provider,
                "look_sheet": sheet_path,
            }]
            turn_path = str(cells.get("side") or cells.get("tq") or sheet_path)
            refs.append({
                "id": f"turnaround_{cid}",
                "kind": "turnaround",
                "character_id": cid,
                "path": turn_path,
                "url": "",
                "provider": provider,
                "views": view_list,
                "look_sheet": sheet_path,
            })
        element_id = ""
        series = action == "series" or not cropped.get("ok")
        key = str(os.environ.get("KLING_API_KEY") or "").strip()
        if not series and key:
            voice_id = ""
            if not is_prop:
                voice_url = str(
                    char.get("voice_url") or char.get("voice_reference_url") or ""
                ).strip()
                if voice_url.startswith("https://"):
                    try:
                        voiced = kling_create_voice(voice_name=cid, voice_url=voice_url)
                    except (ValueError, OSError) as exc:
                        voiced = {"ok": False, "skipped": True, "notes": [str(exc)]}
                    if voiced.get("ok"):
                        voice_id = str(voiced.get("voice_id") or "")
                    elif not voiced.get("skipped"):
                        findings.append({
                            "severity": "warning",
                            "field": subject_field,
                            "message": "音色克隆失败，工牌不绑 voice",
                            "proposed_fix": "检查 voice_url 为 https",
                        })
            slots = element_slots_from_crops(cells)
            desc = str(
                char.get("appearance") or char.get("description") or cid
            )
            try:
                created = kling_create_element(
                    element_name=str(char.get("name") or cid),
                    frontal_image=str(slots.get("frontal") or portrait_path),
                    refer_images=list(slots.get("refer") or []),
                    element_description=desc,
                    element_voice_id="" if is_prop else voice_id,
                )
            except (ValueError, OSError) as exc:
                created = {"ok": False, "notes": [str(exc)]}
            if created.get("ok"):
                element_id = str(created.get("element_id") or "")
            else:
                series = True
                findings.append({
                    "severity": "warning",
                    "field": subject_field,
                    "message": "工牌拒收，已按裁切格降级 series（不绑 element_id）",
                    "proposed_fix": "检查拼板后 --retry " + subject_field,
                })
        elif not series and not key:
            findings.append({
                "severity": "info",
                "field": subject_field,
                "message": "无 KLING_API_KEY，跳过工牌创建",
                "proposed_fix": "配置密钥后再出 element_id",
            })
        if element_id:
            for ref in refs:
                ref["element_id"] = element_id
        for key_name in LOOK_SHEET_ELEMENT_KEYS:
            path = str(cells.get(key_name) or "")
            if path:
                extra = {
                    "id": f"look_{key_name}_{cid}",
                    "kind": "image",
                    "path": path,
                    "provider": provider,
                }
                extra["prop_id" if is_prop else "character_id"] = cid
                refs.append(extra)
        return {
            "retry": False,
            "refs": refs,
            "items": [
                {"id": r["id"], "kind": "image", "path": r.get("path"), "provider": provider}
                for r in refs if r.get("path")
            ],
            "element_id": element_id,
            "series": series,
            "notes": cropped.get("notes") or [],
        }

    def _execute_cast(
        self,
        *,
        inputs: dict[str, Any],
        project_dir: str,
        store: ArtifactStore | None,
        bible: dict[str, Any],
        manifest: dict[str, Any] | None,
        script: dict[str, Any] | None,
        scene_plan: dict[str, Any] | None,
    ) -> ToolResult:
        dry_run = inputs.get("dry_run")
        if dry_run is None:
            dry_run = True
        dry_run = bool(dry_run)
        record_ledger = inputs.get("record_ledger")
        if record_ledger is None:
            record_ledger = True
        record_ledger = bool(record_ledger)
        retry_ids = {str(x) for x in (inputs.get("retry_ids") or []) if x}
        # 身份记忆库漂移 → 重拍定妆（P0-identity-memory）：并进 retry 集，复用
        # 既有「retry 覆盖 → _job_already_done 不跳过」路径重跑定妆图。
        identity_memory = load_identity_memory(project_dir) if project_dir else None
        if identity_memory:
            for subject in retake_subjects(identity_memory):
                retry_ids.add(subject)
        format_card = store.read("format_card") if store else None
        skip = _spoken_skip_portraits(scene_plan, script, format_card, bible=bible)
        policy = load_loop_policy(project_dir) if project_dir else {}
        kling_loop = str(policy.get("video_loop") or "").strip().lower() == "kling"
        sample_shot_ids = [str(x) for x in (inputs.get("sample_shot_ids") or []) if x]
        jobs = [] if skip else collect_cast_jobs(
            bible,
            scene_plan=scene_plan,
            video_loop=str(policy.get("video_loop") or ""),
            sample_shot_ids=sample_shot_ids or None,
        )
        for job in jobs:
            job.setdefault("item", "")

        ceiling = policy.get("budget_ceiling_usd")
        try:
            ceiling_f = float(ceiling) if ceiling is not None else None
        except (TypeError, ValueError):
            ceiling_f = None

        selector_inputs = {"project_dir": project_dir} if project_dir else {}
        img_tool = self._catalog_tool(self._image_selector, selector_inputs)
        vid_tool = self._catalog_tool(self._video_selector, selector_inputs)
        img_name = getattr(img_tool, "name", "") or _DEFAULT_IMAGE_TOOL
        img_prov = getattr(img_tool, "provider", "") or "volcengine"
        vid_name = getattr(vid_tool, "name", "") or _DEFAULT_VIDEO_TOOL
        vid_prov = getattr(vid_tool, "provider", "") or "volcengine"
        i_caps = image_caps(tool=img_name, provider=img_prov)
        v_caps = video_caps(tool=vid_name, provider=vid_prov)

        if not isinstance(manifest, dict):
            manifest = {"items": [], "reference_assets": []}
        else:
            manifest.setdefault("items", [])
            manifest.setdefault("reference_assets", [])

        portraits = _portrait_refs_by_form(manifest)
        turnarounds = _turnaround_refs_by_form(manifest)
        scene_refs = _scene_ref_index(manifest)
        props = _prop_index(manifest)
        skipped_ids: list[str] = []
        kept: list[dict[str, Any]] = []
        require_url = _cast_needs_url(policy)
        for job in jobs:
            job["item"] = img_name
            if _job_already_done(
                job,
                manifest=manifest,
                project_dir=project_dir,
                retry_ids=retry_ids,
                portraits=portraits,
                props=props,
                turnarounds=turnarounds,
                scene_refs=scene_refs,
                require_url=require_url,
            ):
                skipped_ids.append(str(job.get("subject") or ""))
                continue
            kept.append(job)
        jobs = kept
        cast_image_count = len([
            j for j in jobs if str(j.get("kind") or "") in ("portrait", "turnaround")
        ])

        estimated = 0.0
        for job in jobs:
            usd = self._estimate_job(job, img_tool, vid_tool, project_dir)
            job["estimate_usd"] = usd
            estimated += usd
        estimated = round(estimated, 6)

        ledger = None
        booked_raw = 0.0
        settled = 0.0
        if project_dir:
            ledger = BudgetLedger(Path(project_dir) / "cost.jsonl", budget_ceiling_usd=ceiling_f)
            totals = ledger.totals()
            booked_raw = float(totals["estimated_raw_usd"])
            settled = float(totals["settled_usd"])
        booked = booked_raw if record_ledger else settled
        projected = booked + estimated
        over_budget = ceiling_f is not None and projected > ceiling_f
        findings: list[dict[str, str]] = []
        if skip:
            findings.append({
                "severity": "info",
                "field": "cast",
                "message": "spoken_explain / 口播模式跳过定妆与四视图",
                "proposed_fix": "对白剧情片才出全身照",
            })
        if over_budget:
            findings.append({
                "severity": "critical" if not dry_run else "warning",
                "field": "budget_ceiling_usd",
                "message": (
                    f"估算 ${estimated:.4f} + 已记账 ${booked:.4f} 超过封顶 "
                    f"${ceiling_f:.4f}"
                ),
                "proposed_fix": "提高 budget_ceiling_usd，或勾选 skip_turnaround",
            })
        if cast_image_count > _CAST_EST_IMAGE_WARN:
            findings.append({
                "severity": "warning",
                "field": "cast",
                "message": (
                    f"定妆生图预计 {cast_image_count} 张（阈值 {_CAST_EST_IMAGE_WARN}）；"
                    "多形态按形态数线性增长"
                ),
                "proposed_fix": "用 form.skip_turnaround 省四视图，或减少 forms 数量",
            })

        payload: dict[str, Any] = {
            "dry_run": dry_run,
            "stage": "cast",
            "over_budget": over_budget,
            "blocked": False,
            "budget_ceiling_usd": ceiling_f,
            "estimated_usd": estimated,
            "jobs": jobs,
            "skipped_ids": skipped_ids,
            "image_tool": img_name,
            "video_tool": vid_name,
            "image_caps": i_caps,
            "video_caps": v_caps,
            "remaining_ids": [],
            "retryable_ids": [],
            "findings": findings,
            "skipped_cast": skip,
        }

        if dry_run:
            if record_ledger and ledger is not None and estimated > 0:
                ledger.estimate_checked(
                    "analysis", "shot_runner/cast", self.name, estimated,
                )
            payload["blocked"] = over_budget and ceiling_f is not None
            return ToolResult(success=True, data=payload, meta={"dry_run": True, "stage": "cast"})

        if over_budget and ceiling_f is not None and os.environ.get("MONTAGE_BUDGET_SOFT") != "1":
            payload["blocked"] = True
            return ToolResult(
                success=False,
                error=findings[0]["message"] if findings else "超预算",
                data=payload,
            )
        if skip:
            payload["asset_manifest"] = manifest
            return ToolResult(success=True, data=payload, meta={"stage": "cast", "skipped": True})
        if not project_dir:
            return ToolResult(success=False, error="dry_run=false 需要 project_dir 以写入媒体与产物")

        cache = self._cache(project_dir)
        items = list(manifest.get("items") or [])
        refs = list(manifest.get("reference_assets") or [])
        results: list[dict[str, Any]] = []
        retryable: list[str] = []
        # 本轮新生成的定妆候选（(cid, fid, ref_id, path, url)），循环后统一过检。
        identity_candidates: list[dict[str, str]] = []
        img_dir = Path(project_dir) / "assets" / "images"
        img_dir.mkdir(parents=True, exist_ok=True)

        def book(subject: str, usd: float) -> str:
            if ledger is None or usd <= 0:
                return ""
            eid, _ok = ledger.estimate_checked("image_generation", subject, img_name, usd)
            return eid

        def settle(eid: str, actual: float) -> None:
            if ledger is None or not eid:
                return
            ledger.settle(eid, actual)

        for job in jobs:
            kind = str(job.get("kind") or "")
            subject = str(job.get("subject") or kind)
            prompt, finding = self._cast_still_prompt(
                job, bible=bible, project_dir=project_dir, kling_loop=kling_loop,
            )
            if finding:
                findings.append(finding)
                retryable.append(subject)
                continue
            if kind == "portrait":
                cid = str(job.get("character_id") or "")
                fid = str(job.get("form_id") or "")
                ref_id = form_ref_id("portrait", cid, fid)
                if kling_loop:
                    out_path = str(img_dir / f"look_sheet_{cid}.png")
                else:
                    out_path = str(img_dir / (
                        f"portrait_{cid}_{fid}.png" if fid else f"portrait_{cid}.png"
                    ))
                id_key, id_val = "character_id", cid
            elif kind == "turnaround":
                cid = str(job.get("character_id") or "")
                fid = str(job.get("form_id") or "")
                out_path = str(img_dir / (
                    f"turnaround_{cid}_{fid}.png" if fid else f"turnaround_{cid}.png"
                ))
                id_key, id_val = "character_id", cid
                ref_id = form_ref_id("turnaround", cid, fid)
            elif kind == "scene_ref":
                lid = str(job.get("location_id") or "")
                out_path = str(img_dir / f"scene_{lid}.png")
                id_key, id_val = "location_id", lid
                ref_id = f"scene_{lid}"
            else:
                pid = str(job.get("prop_id") or "")
                out_path = str(img_dir / (
                    f"look_sheet_prop_{pid}.png" if kling_loop else f"prop_{pid}.png"
                ))
                id_key, id_val = "prop_id", pid
                ref_id = f"prop_{pid}"
            usd = self._estimate_job(job, img_tool, vid_tool, project_dir)
            img_payload: dict[str, Any] = {
                "prompt": prompt,
                "output_path": out_path,
                "project_dir": project_dir,
            }
            if img_name == "kling_image":
                img_payload["result_type"] = "single"
                img_payload["resolution"] = "2k"
            if img_name == "seedream_image":
                img_payload["aspect_ratio"] = self._seedream_aspect(policy)
            if img_name == "agnes_image" and kind == "scene_ref":
                # 空镜确为纯 t2i（apply_image_refs 只在首帧路径调用），按档案给画幅
                img_payload["ratio"] = self._profile_aspect(
                    policy,
                    env_key="AGNES_RATIO",
                    allowed=AGNES_IMAGE_RATIOS,
                    allow_21_9=True,
                )
            elif img_name == "agnes_image" and kind in ("portrait", "turnaround"):
                # 定妆=半身（头顶到腰/胸，头部完整）→ 3:4；
                # 四视图=全身四视角 → 9:16。实测"全身"提示词方差大（会裁头），
                # 定妆改半身后头部稳定入画，全身体态由四视图承担。
                img_payload["size"] = img_payload.get("size") or "2K"
                img_payload["ratio"] = "3:4" if kind == "portrait" else "9:16"
            elif img_name == "agnes_image" and kind == "prop":
                # 道具静物：方形最稳（居中陈列），显式声明而不是靠默认值。
                img_payload["size"] = img_payload.get("size") or "2K"
                img_payload["ratio"] = "1:1"

            if kling_loop and kind in ("portrait", "prop"):
                if kind == "prop":
                    cid = str(job.get("prop_id") or "")
                    raw_prop = next(
                        (
                            item for item in (bible.get("props") or [])
                            if (
                                isinstance(item, dict)
                                and str(item.get("id") or item.get("name") or "") == cid
                            ) or (isinstance(item, str) and item.strip() == cid)
                        ),
                        {"id": cid, "name": cid},
                    )
                    card = (
                        {"id": cid, "name": str(raw_prop), "appearance": str(raw_prop)}
                        if isinstance(raw_prop, str)
                        else dict(raw_prop)
                    )
                    role = "prop"
                    upsert_kinds = ("prop",)
                    ekey = "prop_id"
                else:
                    cid = str(job.get("character_id") or "")
                    card = next(
                        (
                            _char_for_prompt(item)
                            for item in (bible.get("characters") or [])
                            if isinstance(item, dict) and str(item.get("id") or "") == cid
                        ),
                        _char_for_prompt({"id": cid}),
                    )
                    role = "character"
                    upsert_kinds = ("portrait", "turnaround")
                    ekey = "character_id"
                attempt = 1
                sheet_ok = False
                while attempt <= 2:
                    eid = book(subject, usd)
                    result = self._generate_with_retry(
                        kind="image",
                        payload=img_payload,
                        output_path=out_path,
                        cache=cache,
                        cache_params={
                            "prompt": prompt, "kind": "look_sheet",
                            "subject": subject, "attempt": attempt,
                            "ratio": img_payload.get("ratio") or img_payload.get("aspect_ratio") or "",
                            "image_tool": img_name,
                        },
                        expected_duration=None,
                    )
                    settle(eid, result.cost_usd if result.success else 0.0)
                    if not result.success:
                        retryable.append(subject)
                        findings.append({
                            "severity": "critical",
                            "field": subject,
                            "message": result.error or "拼板生成失败",
                            "proposed_fix": f"检查密钥或 --retry {subject} --resume",
                        })
                        break
                    path = _media_path(result, out_path)
                    url = _media_url(result)
                    follow = self._kling_sheet_followup(
                        sheet_path=path,
                        cid=cid,
                        char=card,
                        img_dir=img_dir,
                        url=url,
                        provider=getattr(img_tool, "provider", "") or "kling",
                        findings=findings,
                        attempt=attempt,
                        role=role,
                    )
                    if follow.get("retry"):
                        attempt += 1
                        continue
                    for extra_ref in follow.get("refs") or []:
                        if not isinstance(extra_ref, dict):
                            continue
                        ekind = str(extra_ref.get("kind") or "")
                        if ekind not in upsert_kinds:
                            continue
                        _upsert_ref(
                            refs, extra_ref, kind=ekind,
                            id_key=ekey, id_val=str(extra_ref.get(ekey) or cid),
                        )
                        if ekind == "portrait":
                            identity_candidates.append({
                                "character_id": str(extra_ref.get("character_id") or cid),
                                "form_id": str(extra_ref.get("form_id") or ""),
                                "ref_id": str(extra_ref.get("id") or ref_id),
                                "path": str(extra_ref.get("path") or ""),
                                "url": str(extra_ref.get("url") or ""),
                            })
                    for row in follow.get("items") or []:
                        if isinstance(row, dict) and row.get("path"):
                            _upsert_item(items, row)
                    results.append({
                        "id": ref_id,
                        "ok": True,
                        "path": path,
                        "cached": bool((result.meta or {}).get("cache_hit")),
                        "element_id": follow.get("element_id") or "",
                    })
                    sheet_ok = True
                    break
                if not sheet_ok and subject not in retryable:
                    retryable.append(subject)
                continue

            eid = book(subject, usd)
            result = self._generate_with_retry(
                kind="image",
                payload=img_payload,
                output_path=out_path,
                cache=cache,
                cache_params={"prompt": prompt, "kind": kind, "subject": subject, "ratio": img_payload.get("ratio") or img_payload.get("aspect_ratio") or "", "image_tool": img_name},
                expected_duration=None,
            )
            settle(eid, result.cost_usd if result.success else 0.0)
            if not result.success:
                retryable.append(subject)
                findings.append({
                    "severity": "critical",
                    "field": subject,
                    "message": result.error or "定妆图生成失败",
                    "proposed_fix": f"检查密钥或 --retry {subject} --resume",
                })
                continue
            path = _media_path(result, out_path)
            url = _media_url(result)
            ref = {
                "id": ref_id,
                "kind": kind,
                id_key: id_val,
                "path": path,
                "url": url,
                "provider": getattr(img_tool, "provider", "") or "volcengine",
            }
            job_fid = str(job.get("form_id") or "")
            if job_fid:
                ref["form_id"] = job_fid
            if kind == "turnaround":
                ref["views"] = ["front", "side", "back", "three_quarter"]
            _upsert_ref(refs, ref, kind=kind, id_key=id_key, id_val=id_val)
            if kind == "portrait":
                identity_candidates.append({
                    "character_id": id_val,
                    "form_id": job_fid,
                    "ref_id": ref_id,
                    "path": path,
                    "url": url,
                })
            row = {
                "id": ref_id,
                "kind": "image",
                "path": path,
                "provider": ref["provider"],
            }
            if url:
                row["url"] = url
            _upsert_item(items, row)
            results.append({
                "id": ref_id,
                "ok": True,
                "path": path,
                "cached": bool((result.meta or {}).get("cache_hit")),
            })

        # -- 身份记忆库：定妆候选 VLM 过检 + 保守更新 canonical 锚 -------------
        self._update_identity_anchors(
            memory=identity_memory,
            candidates=identity_candidates,
            refs=refs,
            scene_plan=scene_plan,
            findings=findings,
        )
        if identity_memory is not None:
            findings.extend(identity_findings(identity_memory))
            save_identity_memory(project_dir, identity_memory)
            payload["identity_retakes"] = retake_subjects(identity_memory)

        manifest_out = {"items": items, "reference_assets": refs}
        self._write_image_bindings(
            store=store,
            project_dir=project_dir,
            manifest=manifest_out,
            scene_plan=scene_plan,
            script=script,
            shots=[],
            findings=findings,
        )
        if store:
            store.write("asset_manifest", manifest_out, schema=None)
        payload.update({
            "results": results,
            "retryable_ids": retryable,
            "findings": findings,
            "asset_manifest": manifest_out,
            "blocked": False,
        })
        return ToolResult(
            success=True,
            data=payload,
            meta={"stage": "cast", "failed": len(retryable)},
        )

    def _write_image_bindings(
        self,
        *,
        store: ArtifactStore | None,
        project_dir: str,
        manifest: dict[str, Any] | None,
        scene_plan: dict[str, Any] | None,
        script: dict[str, Any] | None,
        shots: list[dict[str, Any]] | None,
        findings: list[dict[str, Any]],
        reconcile: bool = False,
    ) -> None:
        """合并写 image_bindings（observe-only）。

        失败只 append finding，绝不中断生成；``reconcile=True`` 时走回填+对账，
        保留旧绑定里可信的 ``picture_index``。
        """
        if not store or not project_dir:
            return
        from montage.schemas import get_schema

        try:
            existing = store.read("image_bindings")
            if reconcile:
                merged, drift = reconcile_image_bindings(
                    existing,
                    shots=shots,
                    manifest=manifest,
                    scene_plan=scene_plan,
                    script=script,
                    project_dir=project_dir,
                )
                findings.extend(drift)
            else:
                merged = merge_image_bindings(
                    existing,
                    build_image_bindings(
                        shots,
                        manifest,
                        scene_plan,
                        script,
                        project_dir=project_dir,
                        existing=existing if isinstance(existing, dict) else None,
                    ),
                )
            store.write("image_bindings", merged, schema=get_schema("image_bindings"))
        except Exception as exc:  # noqa: BLE001
            findings.append({
                "severity": "warning",
                "field": "image_bindings",
                "message": f"绑定产物写入失败（不影响生成）: {exc}",
                "proposed_fix": "忽略；或检查 artifacts/image_bindings.json 是否损坏",
            })

    def _catalog_tool(self, selector: ImageSelector | VideoSelector, inputs: dict[str, Any]) -> BaseTool | None:
        picked = selector._pick(inputs)
        if picked:
            return picked
        selector._registry.discover()
        cands = list(selector._registry.by_capability(selector.target_capability))
        policy = selector._policy_for(inputs)
        allowed = resolve_allowed_providers(
            policy, inputs, capability=getattr(selector, "target_capability", "") or "",
        )
        if allowed:
            filtered = [t for t in cands if t.provider in allowed]
            if filtered:
                cands = filtered
        return cands[0] if cands else None

    def _profile_aspect(
        self,
        policy: dict[str, Any],
        *,
        env_key: str,
        allowed: set[str] | frozenset[str] | None = None,
        default: str = "16:9",
        allow_21_9: bool = False,
    ) -> str:
        """画幅取值链：env > output_profile > default（agnes 与 seedream 共用）。

        env 值不在 allowed 内时落回档案推导，避免非法值直发（shot_runner 绕过
        runtime.validate_inputs，必须在此自校验）。allowed=None 表示不校验。
        """
        env_val = str(os.environ.get(env_key) or "").strip()
        if env_val and (allowed is None or env_val in allowed):
            return env_val
        profile = str(policy.get("output_profile") or "").strip().lower()
        if allow_21_9 and profile == "cinematic_21_9":
            return "21:9"
        if profile.endswith("vertical"):
            return "9:16"
        return default

    def _seedream_aspect(self, policy: dict[str, Any]) -> str:
        """Seedream 首帧/定妆画幅：env > output_profile > 默认 16:9。

        allowed=None 保持既有行为（非法 SEEDREAM_ASPECT 由 size_for_aspect 静默落回）。
        """
        return self._profile_aspect(policy, env_key="SEEDREAM_ASPECT")

    def _estimate_job(
        self,
        job: dict[str, Any],
        img_tool: BaseTool | None,
        vid_tool: BaseTool | None,
        project_dir: str,
    ) -> float:
        kind = job["kind"]
        inputs: dict[str, Any] = {"project_dir": project_dir, "prompt": "placeholder"}
        if kind == "video":
            inputs["seconds"] = job.get("seconds", 5)
            inputs["duration"] = str(int(float(job.get("seconds") or 5)))
            if job.get("api_id"):
                inputs["api_id"] = job["api_id"]
            if self._video_estimate:
                return float(self._video_estimate(inputs))
            if vid_tool:
                return float(vid_tool.estimate_cost(inputs) or 0.0)
            from montage.providers.jimeng import JimengVideo

            return float(JimengVideo().estimate_cost(inputs))
        if self._image_estimate:
            return float(self._image_estimate(inputs))
        if img_tool:
            return float(img_tool.estimate_cost(inputs) or 0.0)
        from montage.providers.jimeng import JimengImage

        return float(JimengImage().estimate_cost(inputs))

    def _run_image(self, inputs: dict[str, Any]) -> ToolResult:
        if self._image_execute:
            return self._image_execute(inputs)
        return self._image_selector.execute(inputs)

    def _run_video(self, inputs: dict[str, Any]) -> ToolResult:
        if inputs.get("recover") and inputs.get("provider_task_id"):
            tool = self._catalog_tool(self._video_selector, inputs)
            poll = getattr(tool, "poll_result", None)
            if callable(poll):
                return poll(inputs)
        if self._video_execute:
            return self._video_execute(inputs)
        return self._video_selector.execute(inputs)

    def _check_quality(self, path: str, *, expected_duration: float | None = None) -> dict[str, Any]:
        if self._quality_check:
            return self._quality_check(path, expected_duration=expected_duration)
        from montage.tools.asset_quality_gate import check_asset

        return check_asset(path, expected_duration=expected_duration)

    def _extract_tail(self, video_path: str, output: str) -> str | None:
        if self._extract_last_frame:
            result = self._extract_last_frame(video_path, output)
            if result and Path(str(result)).exists():
                return str(result)
            if Path(output).exists():
                return output
            return None
        try:
            from montage.compose.ffmpeg_engine import extract_last_frame

            return str(extract_last_frame(Path(video_path), Path(output)))
        except Exception:  # noqa: BLE001
            return None

    def _gen_bridge_frame_for(
        self,
        tail_path: str,
        seg_index: int,
        seg_refs: list[dict[str, Any]],
        *,
        owner_shot_id: str,
        owner_subject: str,
        img_tool: BaseTool | None,
        i_caps: dict[str, Any],
        vid_tool: BaseTool | None,
        img_name: str,
        cache,
        findings: list[dict[str, str]],
        retry_ids: set[str],
        force_ids: set[str],
        policy: dict[str, Any],
        img_dir: Path,
        project_dir: str,
        payload: dict[str, Any],
        book,
        settle,
        estimate_img,
    ) -> str:
        """桥帧合成（v8.2 提升）：尾帧 + 参考 → 图片侧合成，返回公网 URL。

        段间桥与跨镜后向锚共用一条路径（payload_v25 只认 http）。ratio 显式
        跟随本镜档案画幅（v8.2 修正：agnes_image 默认 1:1 会画幅漂移）。
        cache_params 带尾帧路径 → 尾帧稳定时 resume 缓存命中不耗图片配额。
        """
        if not img_tool or not tail_path:
            return ""
        from lib.shot_prompt_builder import build_bridge_frame_prompt

        img_refs: list[dict[str, Any]] = [
            {"kind": "first_frame", "bridge": True, "name": "上一段尾帧",
             "path": tail_path},
            *[r for r in seg_refs if isinstance(r, dict)],
        ]
        bridge_path = str(img_dir / f"{owner_shot_id}_bridge{seg_index}.png")
        img_payload: dict[str, Any] = {
            "prompt": "",
            "output_path": bridge_path,
            "project_dir": project_dir,
        }
        if img_name == "agnes_image":
            img_payload["size"] = "2K"
            img_payload["ratio"] = self._profile_aspect(
                policy,
                env_key="AGNES_RATIO",
                allowed=AGNES_IMAGE_RATIOS,
                allow_21_9=True,
            )
        if img_name == "kling_image":
            img_payload["result_type"] = "single"
            img_payload["resolution"] = "2k"
        if img_name == "seedream_image":
            img_payload["aspect_ratio"] = self._seedream_aspect(policy)
        notes = apply_image_refs(img_payload, img_refs, i_caps)
        for note in notes:
            findings.append({
                "severity": "warning", "field": owner_subject, "message": note,
                "proposed_fix": "换支持参考图的供应商或补 URL",
            })
        # 图例必须与实发顺序一致：URL 优先/本地兜底会重排，故按最终有序表生成。
        entries, _extra = agnes_image_ref_entries(img_refs, i_caps)
        img_payload["prompt"] = build_bridge_frame_prompt(entries)
        usd = estimate_img
        eid = book("image_generation", owner_subject, payload.get("image_tool") or img_name, usd)
        res = self._generate_with_retry(
            kind="image",
            payload=img_payload,
            output_path=bridge_path,
            cache=cache,
            cache_params={
                "prompt": img_payload.get("prompt") or "",
                "kind": "bridge_frame",
                "shot_id": owner_shot_id,
                "seg": seg_index,
                "image_tool": img_name,
                "tail": tail_path,
            },
            expected_duration=None,
            skip_cache=_retry_covers(retry_ids, shot_id=owner_shot_id) or owner_shot_id in force_ids,
        )
        settle(eid, res.cost_usd if res.success else 0.0)
        if not res.success:
            findings.append({
                "severity": "warning", "field": owner_subject,
                "message": res.error or "续接首帧生成失败，本段不带桥接继续",
                "proposed_fix": f"shot_runner retry_ids=[{owner_shot_id}]",
            })
            return ""
        return _media_url(res)

    def _cache(self, project_dir: str):
        from montage.tools.generation_cache import GenerationCache

        return GenerationCache(Path(project_dir) / "assets" / ".cache")

    def _cached_or_generate(
        self,
        *,
        cache,
        params: dict[str, Any],
        output_path: str,
        generate: Callable[[dict[str, Any]], ToolResult],
        payload: dict[str, Any],
        skip_cache: bool = False,
        pace: Callable[[], None] | None = None,
    ) -> ToolResult:
        if not skip_cache:
            hit = cache.get({"operation": "get", "params": params})
            data = hit.data if hit.success and isinstance(hit.data, dict) else {}
            if data.get("hit") and data.get("path") and Path(data["path"]).exists():
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                if str(Path(data["path"]).resolve()) != str(Path(output_path).resolve()):
                    shutil.copy2(data["path"], output_path)
                cached: dict[str, Any] = {"output": output_path, "cached": True}
                url_file = Path(str(data["path"]) + ".url")
                if url_file.is_file():
                    url = url_file.read_text(encoding="utf-8").strip()
                    if url.startswith("http"):
                        cached["url"] = url
                return ToolResult(
                    success=True,
                    data=cached,
                    cost_usd=0.0,
                    meta={"cache_hit": True},
                )
        # pacing 只在缓存未命中、真正发请求前执行，缓存命中不白等
        if pace is not None:
            pace()
        result = generate(payload)
        if result.success:
            path = _media_path(result, output_path)
            if path and Path(path).exists():
                put = cache.put({"operation": "put", "params": params, "path": path})
                url = _media_url(result)
                put_data = put.data if put.success and isinstance(put.data, dict) else {}
                cached_path = str(put_data.get("cached_path") or "")
                if url.startswith("http") and cached_path:
                    Path(cached_path + ".url").write_text(url, encoding="utf-8")
        return result

    def _execute_jobs(
        self,
        *,
        shots: list[dict[str, Any]],
        timeline_shots: list[dict[str, Any]] | None = None,
        scene_plan: dict[str, Any],
        script: dict[str, Any],
        manifest: dict[str, Any],
        store: ArtifactStore | None,
        project_dir: str,
        ledger: BudgetLedger | None,
        portraits: dict[str, dict[str, Any]],
        registry: dict[str, dict[str, Any]],
        needed_portrait_forms: list[dict[str, Any]],
        needed_props: list[str],
        img_tool: BaseTool | None,
        vid_tool: BaseTool | None,
        i_caps: dict[str, Any],
        v_caps: dict[str, Any],
        payload: dict[str, Any],
        skip_portraits: bool = False,
        skipped_first: set[str] | None = None,
        skipped_video: set[str] | None = None,
        retry_ids: set[str] | None = None,
        frames_only: bool = False,
        prompt_only: bool = False,
        force_ids: set[str] | None = None,
        video_ids: set[str] | None = None,
    ) -> ToolResult:
        from montage.tools.visual_prompt_builder import VisualPromptBuilder

        cache = self._cache(project_dir)
        prompt_builder = VisualPromptBuilder()
        items = list(manifest.get("items") or [])
        refs = list(manifest.get("reference_assets") or [])
        findings: list[dict[str, str]] = list(payload.get("findings") or [])
        retryable: list[str] = []
        results: list[dict[str, Any]] = []
        vlm_rows: list[dict[str, Any]] = []
        state_lock = threading.RLock()
        continuity_state = load_or_seed_continuity(project_dir) if project_dir else {}
        skipped_first = skipped_first or set()
        skipped_video = skipped_video or set()
        retry_ids = retry_ids or set()
        force_ids = force_ids or set()
        video_ids = video_ids or set()
        # 身份记忆库（P0-identity-memory）：本阶段读观测 + 落盘；迟到定妆
        # （shot 阶段补出的 portrait）也走 canonical 保守更新。重拍定妆由
        # 下一轮 cast 阶段（_execute_cast 读 retake 标记）统一排队。
        identity_memory = load_identity_memory(project_dir) if project_dir else None
        identity_candidates: list[dict[str, str]] = []
        sample_one = len(retry_ids) == 1
        prompt_fallback = bool(payload.get("prompt_fallback"))
        prompt_too_long = False
        blocked_i2v: list[str] = []
        root = Path(project_dir)
        img_dir = root / "assets" / "images"
        vid_dir = root / "assets" / "videos"
        img_dir.mkdir(parents=True, exist_ok=True)
        vid_dir.mkdir(parents=True, exist_ok=True)
        img_name = getattr(img_tool, "name", "") or str(payload.get("image_tool") or "")
        vid_name = getattr(vid_tool, "name", "") or str(payload.get("video_tool") or "")
        vid_prov = getattr(vid_tool, "provider", "") or ""
        policy = load_loop_policy(project_dir)
        agnes_loop = _is_agnes_loop(policy, str(vid_prov))
        kling_loop = str(policy.get("video_loop") or "").strip().lower() == "kling"
        cast_ref_kind = str(policy.get("cast_ref_kind") or "")
        video_loop = str(policy.get("video_loop") or "")
        frames_mode = normalize_frames_mode(policy.get("frames_mode"))
        ref_overflow_mode = normalize_ref_overflow_mode(policy.get("ref_overflow_mode"))
        rpm = agnes_video_rpm() if agnes_loop else 0.0
        self._pace_video_s = (60.0 / rpm) if rpm > 0 else 0.0
        self._last_video_at = float(getattr(self, "_last_video_at", 0) or 0)
        self._last_image_at = float(getattr(self, "_last_image_at", 0) or 0)
        self._agnes_image_active = img_name == "agnes_image"
        self._agnes_video_active = agnes_loop

        self._video_pacer = _IntervalPacer(self._pace_video_s)

        # ---- 档位池（v8.2 P0-scheduler）--------------------------------
        # Agnes 闭环才启用：跑前盘点池（当前档耗尽→确定性切下一档，日志记
        # finding），并快照用户声明档——scheduler 的 env 切档只在进程内生效，
        # 结束时 restore 回声明值，不污染 .env / 其他进程。
        declared_tier = agnes_access_tier() if agnes_loop else ""
        if declared_tier:
            os.environ.setdefault("MONTAGE_TIER_DECLARED", declared_tier)
            from montage.engine import scheduler as _sched

            if _sched.tier_exhausted(declared_tier):
                next_tier = _sched.pick_next_tier(declared_tier)
                if next_tier:
                    _sched.activate_tier(next_tier)
                    self._pace_video_s = (
                        (60.0 / agnes_video_rpm()) if agnes_video_rpm() > 0 else 0.0
                    )
                    self._video_pacer.set_interval(self._pace_video_s)
                    findings.append({
                        "severity": "warning",
                        "field": "agnes_tier",
                        "message": (
                            f"{_sched.tier_note(declared_tier)} 已耗尽，"
                            f"本次切到 {_sched.tier_note(next_tier)} 继续"
                        ),
                        "proposed_fix": "次日配额归零后自动回主档；或补 tokenplan 配额",
                    })

        def book(category: str, subject: str, item: str, usd: float) -> str:
            if ledger is None or usd <= 0:
                return ""
            eid, _ok = ledger.estimate_checked(category, subject, item, usd)
            return eid

        def settle(eid: str, actual: float) -> None:
            if ledger is None or not eid:
                return
            ledger.settle(eid, actual)

        def record_vlm(shot_id: str, meta: dict[str, Any] | None, shot: dict[str, Any] | None = None) -> None:
            vlm = meta.get("vlm") if isinstance(meta, dict) else None
            if not isinstance(vlm, dict):
                return
            vlm_rows.append({
                "shot_id": shot_id,
                "ok": bool(vlm.get("ok", True)),
                "skipped": bool(vlm.get("skipped")),
                "issues": list(vlm.get("issues") or []),
            })
            # 身份漂移观测（P0-identity-memory）：本镜 VLM 报「人物不一致」时给
            # 该角色记一次漂移；连续达阈值 → 下一轮重拍定妆。判定与记录分离：
            # 这里只记观测，重拍在下一轮 _execute_cast 开跑时统一排队。
            if identity_memory is not None and shot:
                body = identity_memory
                for cid, fid, _has_forms in _shot_character_forms(shot, scene_plan):
                    key = identity_key(cid, fid)
                    if not identity_entry(body, key).get("canonical"):
                        continue
                    issues = drift_issues(vlm)
                    if issues:
                        verdict = record_drift(
                            body, key,
                            shot_id=shot_id,
                            message=str(issues[0].get("message") or ""),
                        )
                        findings.append({
                            "severity": "warning" if verdict.get("retake") else "info",
                            "field": f"identity/{identity_subject(cid, fid)}",
                            "message": (
                                f"身份漂移 {verdict.get('count')} 次"
                                + ("，已触发重拍定妆" if verdict.get("retake") else "")
                                + f"（{shot_id}）"
                            ),
                            "proposed_fix": f"重跑定妆 --retry {identity_subject(cid, fid)}，或改 agnes_mode=keyframe",
                        })
                    elif not vlm.get("skipped") and vlm.get("ok"):
                        clear_drift(body, key)
            if vlm.get("skipped") and not any(f.get("field") == "vlm" for f in findings):
                findings.append({
                    "severity": "warning",
                    "field": "vlm",
                    "message": "vlm_skipped：未配置 DASHSCOPE_API_KEY",
                    "proposed_fix": "配置 DASHSCOPE_API_KEY 后才做语义质检",
                })

        # -- portraits -------------------------------------------------------
        for form_job in needed_portrait_forms:
            cid = str(form_job.get("character_id") or "")
            fid = "" if kling_loop else str(form_job.get("form_id") or "")
            form = None if kling_loop else form_job.get("form")
            subject = form_subject("portrait", cid, fid)
            ref_id = form_ref_id("portrait", cid, fid)
            char = registry.get(cid) or {"id": cid}
            char = _char_for_prompt(char if isinstance(char, dict) else {"id": cid})
            if fid and isinstance(form, dict):
                # form 覆盖 name/appearance/outfit；空值回落角色字段。
                char = blend_character_form(char, form)
            out_path = str(img_dir / (
                f"look_sheet_{cid}.png" if kling_loop
                else (f"portrait_{cid}_{fid}.png" if fid else f"portrait_{cid}.png")
            ))
            try:
                if kling_loop:
                    from lib.shot_prompt_builder import build_kling_look_sheet_prompt

                    prompt = build_kling_look_sheet_prompt(_char_for_prompt(char))
                    built = None
                else:
                    built = prompt_builder.execute({
                        "purpose": "portrait",
                        "character": char,
                        "project_dir": project_dir,
                    })
                    prompt = ""
                    if built.success and isinstance(built.data, dict):
                        prompt = str(built.data.get("first_frame_prompt") or "")
            except ValueError as exc:
                findings.append({
                    "severity": "critical" if not skip_portraits else "warning",
                    "field": subject,
                    "message": str(exc),
                    "proposed_fix": "补 character_registry.appearance",
                })
                retryable.append(subject)
                continue
            if not prompt:
                findings.append({
                    "severity": "critical" if not skip_portraits else "warning",
                    "field": subject,
                    "message": (built.error if built else "") or "定妆照提示词为空",
                    "proposed_fix": "检查人物卡 appearance",
                })
                retryable.append(subject)
                continue
            img_payload: dict[str, Any] = {
                "prompt": prompt,
                "output_path": out_path,
                "project_dir": project_dir,
            }
            if img_name == "kling_image":
                img_payload["result_type"] = "single"
                img_payload["resolution"] = "2k"
            if img_name == "seedream_image":
                img_payload["aspect_ratio"] = self._seedream_aspect(policy)
            usd = self._estimate_job(
                {"kind": "portrait"}, img_tool, vid_tool, project_dir,
            )
            if kling_loop:
                attempt = 1
                sheet_ok = False
                while attempt <= 2:
                    eid = book("image_generation", subject, payload["image_tool"], usd)
                    result = self._generate_with_retry(
                        kind="image",
                        payload=img_payload,
                        output_path=out_path,
                        cache=cache,
                        cache_params={
                            "prompt": prompt, "kind": "look_sheet",
                            "character_id": cid, "attempt": attempt,
                            "ratio": img_payload.get("ratio") or img_payload.get("aspect_ratio") or "",
                            "image_tool": img_name,
                        },
                        expected_duration=None,
                    )
                    settle(eid, result.cost_usd if result.success else 0.0)
                    if not result.success:
                        retryable.append(subject)
                        findings.append({
                            "severity": "critical" if not skip_portraits else "warning",
                            "field": subject,
                            "message": result.error or "拼板生成失败",
                            "proposed_fix": "检查密钥或重跑 retry_ids",
                        })
                        break
                    path = _media_path(result, out_path)
                    url = _media_url(result)
                    follow = self._kling_sheet_followup(
                        sheet_path=path,
                        cid=cid,
                        char=_char_for_prompt(char if isinstance(char, dict) else {"id": cid}),
                        img_dir=img_dir,
                        url=url,
                        provider=getattr(img_tool, "provider", "") or "kling",
                        findings=findings,
                        attempt=attempt,
                    )
                    if follow.get("retry"):
                        attempt += 1
                        continue
                    portrait_ref = None
                    for extra_ref in follow.get("refs") or []:
                        if not isinstance(extra_ref, dict):
                            continue
                        if str(extra_ref.get("kind") or "") == "portrait":
                            portrait_ref = extra_ref
                        if str(extra_ref.get("kind") or "") in ("portrait", "turnaround"):
                            _upsert_ref(
                                refs, extra_ref,
                                kind=str(extra_ref.get("kind")),
                                id_key="character_id",
                                id_val=cid,
                            )
                    for row in follow.get("items") or []:
                        if isinstance(row, dict) and row.get("path"):
                            _upsert_item(items, row)
                    if portrait_ref:
                        portraits[cid] = portrait_ref
                        identity_candidates.append({
                            "character_id": cid, "form_id": "",
                            "ref_id": str(portrait_ref.get("id") or ref_id),
                            "path": str(portrait_ref.get("path") or ""),
                            "url": str(portrait_ref.get("url") or ""),
                        })
                    results.append({
                        "id": ref_id,
                        "ok": True,
                        "path": path,
                        "cached": bool((result.meta or {}).get("cache_hit")),
                    })
                    sheet_ok = True
                    break
                if not sheet_ok and subject not in retryable:
                    retryable.append(subject)
                continue
            eid = book("image_generation", subject, payload["image_tool"], usd)
            result = self._generate_with_retry(
                kind="image",
                payload=img_payload,
                output_path=out_path,
                cache=cache,
                cache_params={"prompt": prompt, "kind": "portrait", "character_id": cid, "ratio": img_payload.get("ratio") or img_payload.get("aspect_ratio") or "", "image_tool": img_name},
                expected_duration=None,
            )
            settle(eid, result.cost_usd if result.success else 0.0)
            if not result.success:
                retryable.append(subject)
                findings.append({
                    "severity": "critical" if not skip_portraits else "warning",
                    "field": subject,
                    "message": result.error or "定妆照生成失败",
                    "proposed_fix": "检查密钥或重跑 retry_ids",
                })
                continue
            path = _media_path(result, out_path)
            url = _media_url(result)
            ref = {
                "id": ref_id,
                "kind": "portrait",
                "character_id": cid,
                "path": path,
                "url": url,
                "provider": getattr(img_tool, "provider", "") or "volcengine",
            }
            if fid:
                ref["form_id"] = fid
            portraits[cid] = ref
            _upsert_ref(refs, ref, kind="portrait", id_key="character_id", id_val=cid)
            identity_candidates.append({
                "character_id": cid, "form_id": fid,
                "ref_id": ref["id"], "path": path, "url": url,
            })
            row = {
                "id": ref["id"], "kind": "image", "path": path,
                "provider": ref["provider"],
            }
            if url:
                row["url"] = url
            _upsert_item(items, row)
            results.append({"id": ref["id"], "ok": True, "path": path, "cached": bool((result.meta or {}).get("cache_hit"))})

        props: dict[str, dict[str, Any]] = _prop_index({"reference_assets": refs})
        prop_map = _script_prop_map(script)
        for pid in needed_props:
            prop = prop_map.get(pid) or {"id": pid, "name": pid, "appearance": pid}
            if kling_loop:
                from lib.shot_prompt_builder import build_kling_prop_prompt

                prompt = build_kling_prop_prompt(prop)
                out_path = str(img_dir / f"look_sheet_prop_{pid}.png")
            else:
                prompt = _prop_prompt(prop)
                out_path = str(img_dir / f"prop_{pid}.png")
            img_payload = {
                "prompt": prompt,
                "output_path": out_path,
                "project_dir": project_dir,
            }
            if img_name == "kling_image":
                img_payload["result_type"] = "single"
                img_payload["resolution"] = "2k"
            if img_name == "seedream_image":
                img_payload["aspect_ratio"] = self._seedream_aspect(policy)
            usd = self._estimate_job(
                {"kind": "prop"}, img_tool, vid_tool, project_dir,
            )
            if kling_loop:
                attempt = 1
                sheet_ok = False
                subject = f"prop/{pid}"
                while attempt <= 2:
                    eid = book("image_generation", subject, payload["image_tool"], usd)
                    result = self._generate_with_retry(
                        kind="image",
                        payload=img_payload,
                        output_path=out_path,
                        cache=cache,
                        cache_params={
                            "prompt": prompt, "kind": "look_sheet",
                            "prop_id": pid, "attempt": attempt,
                            "ratio": img_payload.get("ratio") or img_payload.get("aspect_ratio") or "",
                            "image_tool": img_name,
                        },
                        expected_duration=None,
                    )
                    settle(eid, result.cost_usd if result.success else 0.0)
                    if not result.success:
                        retryable.append(subject)
                        findings.append({
                            "severity": "warning",
                            "field": subject,
                            "message": result.error or "道具拼板生成失败",
                            "proposed_fix": "检查密钥或重跑",
                        })
                        break
                    path = _media_path(result, out_path)
                    url = _media_url(result)
                    follow = self._kling_sheet_followup(
                        sheet_path=path,
                        cid=pid,
                        char=prop if isinstance(prop, dict) else {"id": pid, "name": pid},
                        img_dir=img_dir,
                        url=url,
                        provider=getattr(img_tool, "provider", "") or "kling",
                        findings=findings,
                        attempt=attempt,
                        role="prop",
                    )
                    if follow.get("retry"):
                        attempt += 1
                        continue
                    prop_ref = None
                    for extra_ref in follow.get("refs") or []:
                        if not isinstance(extra_ref, dict):
                            continue
                        if str(extra_ref.get("kind") or "") == "prop":
                            prop_ref = extra_ref
                            _upsert_ref(
                                refs, extra_ref, kind="prop",
                                id_key="prop_id", id_val=pid,
                            )
                    for row in follow.get("items") or []:
                        if isinstance(row, dict) and row.get("path"):
                            _upsert_item(items, row)
                    if prop_ref:
                        props[pid] = prop_ref
                    results.append({
                        "id": f"prop_{pid}",
                        "ok": True,
                        "path": path,
                        "cached": bool((result.meta or {}).get("cache_hit")),
                    })
                    sheet_ok = True
                    break
                if not sheet_ok and subject not in retryable:
                    retryable.append(subject)
                continue
            eid = book("image_generation", f"prop/{pid}", payload["image_tool"], usd)
            result = self._generate_with_retry(
                kind="image",
                payload=img_payload,
                output_path=out_path,
                cache=cache,
                cache_params={"prompt": prompt, "kind": "prop", "prop_id": pid, "ratio": img_payload.get("ratio") or img_payload.get("aspect_ratio") or "", "image_tool": img_name},
                expected_duration=None,
            )
            settle(eid, result.cost_usd if result.success else 0.0)
            if not result.success:
                retryable.append(f"prop/{pid}")
                findings.append({
                    "severity": "warning",
                    "field": f"prop/{pid}",
                    "message": result.error or "道具参考图生成失败",
                    "proposed_fix": "检查密钥或重跑",
                })
                continue
            path = _media_path(result, out_path)
            url = _media_url(result)
            ref = {
                "id": f"prop_{pid}",
                "kind": "prop",
                "prop_id": pid,
                "path": path,
                "url": url,
                "provider": getattr(img_tool, "provider", "") or "volcengine",
            }
            props[pid] = ref
            _upsert_ref(refs, ref, kind="prop", id_key="prop_id", id_val=pid)
            row = {
                "id": ref["id"], "kind": "image", "path": path,
                "provider": ref["provider"],
            }
            if url:
                row["url"] = url
            _upsert_item(items, row)
            results.append({"id": ref["id"], "ok": True, "path": path, "cached": bool((result.meta or {}).get("cache_hit"))})

        # -- shots -----------------------------------------------------------
        still_by_id: dict[str, dict[str, Any]] = {}

        def emit_video(
            shot: dict[str, Any],
            first_path: str,
            first_url: str,
            last_path: str,
            last_url: str,
        ) -> None:
            nonlocal continuity_state, prompt_too_long
            scene_id = str(shot.get("scene_id") or "")
            shot_id = str(shot.get("shot_id") or "")
            subject = f"{scene_id}/{shot_id}"
            wanted = float(shot.get("duration_seconds") or 5)
            route_caps = shot.get("_route_caps") if isinstance(shot.get("_route_caps"), dict) else {}
            # 参考溢出分段：planner 已把「时间轴切片」算好，段秒数直接当 chunks。
            run_segments: list[dict[str, Any]] = []
            if agnes_loop and _agnes_is_v25():
                raw_seg = shot.get("_agnes_segment_plan")
                if isinstance(raw_seg, dict) and str(raw_seg.get("mode") or "") == "segment":
                    run_segments = list(raw_seg.get("segments") or [])
            seg_mode = bool(run_segments)
            if seg_mode:
                chunks = [float(s.get("seconds") or wanted) for s in run_segments]
            elif agnes_loop:
                chunks = agnes_duration_chunks(wanted)
            else:
                chunks = [snap_duration_seconds(wanted, route_caps.get("duration_policy") or v_caps.get("duration_policy"))]
            api_id = str(shot.get("api_id") or "")
            if agnes_loop and wanted > sum(chunks) + 0.05:
                findings.append({
                    "severity": "warning",
                    "field": subject,
                    "message": f"时长 {wanted:.0f}s 超过 Agnes 单段上限，已截到 {sum(chunks):.0f}s",
                    "proposed_fix": "在剧本层把长镜拆成 3/5/10/18s（2.5 为 4–12s）",
                })
            prompt0 = str(shot.get("video_prompt") or shot.get("first_frame_prompt") or "")
            if agnes_loop and _agnes_is_v25():
                from lib.shot_prompt_builder import apply_agnes_prompt_limit

                prompt0, over = apply_agnes_prompt_limit(prompt0, fallback=prompt_fallback)
                shot["video_prompt"] = prompt0
                # 剧情可以简洁，但**动作与台词不能省**（2026-09-19 用户拍板）：
                # 压缩/截断后若本镜对白文本消失，显式报警而不是静默吞掉。
                wanted_dialogue = str(_shot_dialogue(shot) or "").strip()
                if wanted_dialogue and wanted_dialogue not in prompt0:
                    findings.append({
                        "severity": "warning",
                        "field": subject,
                        "message": "压缩后视频提示词里丢了本镜对白（台词不可省）",
                        "proposed_fix": "拆镜或缩短环境/参考图描述；台词必须留在提示词里",
                    })
                if over:
                    prompt_too_long = True
                    findings.append({
                        "severity": "warning",
                        "field": subject,
                        "message": f"视频提示词 {len(prompt0)} 字，超过 Agnes 3000 字上限",
                        "proposed_fix": "改圣经后 produce（不要 --resume）；不改则 produce --resume 走压缩兜底",
                    })
                    return
            v25 = _agnes_is_v25()
            user_vids = _http_video_urls(shot, refs) if agnes_loop else []
            still_refs = _http_still_urls(refs) if agnes_loop else []
            final_path = str(vid_dir / f"{shot_id}.mp4")
            segment_paths: list[str] = []
            segment_url = ""
            existing_clip = None
            for item in reversed(items):
                if not isinstance(item, dict):
                    continue
                if str(item.get("kind") or "") != "video":
                    continue
                if str(item.get("shot_id") or "") != shot_id:
                    continue
                if _item_ready(project_dir, item):
                    existing_clip = item
                    break
            rework = pick_rework_mode(
                shot,
                caps=route_caps or v_caps,
                existing_video=existing_clip,
                retry=_retry_covers(retry_ids, shot_id=shot_id) or shot_id in force_ids,
                frames_only=frames_only,
            )
            for note in rework.get("notes") or []:
                findings.append({
                    "severity": "warning",
                    "field": subject,
                    "message": str(note),
                    "proposed_fix": "无公网 URL 则整镜 --retry，或等成片 URL 未过期",
                })
            rework_mode = str(rework.get("mode") or "regenerate")
            if rework_mode in ("edit", "extend", "feature"):
                prompt0 = rework_prompt(shot, rework_mode)
                wanted_sec = float(shot.get("duration_seconds") or 5)
                chunks = [wanted_sec]

            def run_one(vid_payload: dict[str, Any], dest: str, *, extra: dict[str, Any] | None = None) -> ToolResult:
                usd = self._estimate_job(
                    {"kind": "video", "seconds": vid_payload["seconds"], "api_id": api_id},
                    img_tool, vid_tool, project_dir,
                )
                eid = book("video_generation", subject, payload["video_tool"], usd)
                cache_params = {
                    "prompt": vid_payload["prompt"],
                    "kind": "video",
                    "shot_id": shot_id,
                    "seconds": vid_payload["seconds"],
                    "api_id": vid_payload.get("api_id") or api_id,
                    "audio": vid_payload.get("audio") or vid_payload.get("sound") or "",
                    "resolution": vid_payload.get("resolution") or "",
                    "aspect_ratio": vid_payload.get("aspect_ratio") or "",
                    "first_frame": bool(
                        vid_payload.get("image_url") or vid_payload.get("first_frame_url")
                    ),
                    "last_frame": bool(vid_payload.get("last_frame_url")),
                    "element_id": ",".join(
                        str(r.get("element_id") or "")
                        for r in (vid_payload.get("refs") or [])
                        if isinstance(r, dict) and r.get("element_id")
                    ),
                    "first_src": str(
                        vid_payload.get("image_url") or vid_payload.get("first_frame_url") or ""
                    ),
                    "last_src": str(vid_payload.get("last_frame_url") or ""),
                    # 参考集指纹：kind 切换（portrait↔turnaround）或分段换参考时
                    # 不能只靠 prompt/seconds 命中旧缓存。
                    "ref_fingerprint": "|".join(
                        str(u)
                        for u in (
                            list(vid_payload.get("images") or [])
                            + [
                                r.get("url")
                                for r in (vid_payload.get("refs") or [])
                                if isinstance(r, dict)
                            ]
                            + list(vid_payload.get("reference_urls") or [])
                        )
                        if u
                    ),
                }
                if extra:
                    cache_params.update(extra)
                vid_result = self._generate_with_retry(
                    kind="video",
                    payload=vid_payload,
                    output_path=dest,
                    cache=cache,
                    cache_params=cache_params,
                    expected_duration=float(vid_payload["seconds"]),
                    skip_cache=_retry_covers(retry_ids, shot_id=shot_id) or shot_id in force_ids,
                    vlm_context={
                        **_vlm_expected(shot, registry, scene_plan, portraits),
                        "mode": "video_clip",
                    },
                )
                settle(eid, vid_result.cost_usd if vid_result.success else 0.0)
                return vid_result

            prev_tail_path = ""

            def build_seg_video_prompt(plan_rt: dict[str, Any]) -> str:
                """按本段参考有序表重建视频提示词（<Picture N> 逐段重算）。"""
                try:
                    pair = prompt_builder.execute(_prompt_inputs(
                        shot, scene_plan, project_dir,
                        agnes_loop=agnes_loop, vid_prov=vid_prov, api_id=api_id,
                    ))
                except Exception as exc:  # noqa: BLE001
                    findings.append({
                        "severity": "warning", "field": subject,
                        "message": f"分段提示词失败: {exc}",
                        "proposed_fix": "检查 visual_details",
                    })
                    return ""
                builder_data = pair.data if pair.success and isinstance(pair.data, dict) else {}
                pt = bool(prompt_profile(api_id).get("passthrough"))
                adapter_refs = (
                    agnes_plan_adapter_refs(plan_rt) if plan_rt else _adapter_refs(shot, refs)
                )
                adapted = adapt_visual_prompt(
                    api_id, builder_data, refs=adapter_refs,
                    dialogue=_shot_dialogue(shot),
                    duration_seconds=_shot_duration(shot),
                    continuity_note="" if pt else format_continuity_note(
                        continuity_state, shot, scene_plan=scene_plan),
                )
                return str(adapted.get("video_prompt") or "")

            def gen_bridge_frame(
                tail_path: str,
                seg_index: int,
                seg_refs: list[dict[str, Any]],
            ) -> str:
                """尾帧 + 本段参考 → 图片侧合成续接首帧，返回公网 URL（失败 ""）。

                v8.2 起供段间桥与跨镜后向锚共用；跨镜调用走
                ``self._gen_bridge_frame_for(tail, shot, refs)`` 包装。
                """
                return self._gen_bridge_frame_for(
                    tail_path, seg_index, seg_refs,
                    owner_shot_id=shot_id, owner_subject=subject,
                    img_tool=img_tool, i_caps=i_caps, vid_tool=vid_tool,
                    img_name=img_name, cache=cache, findings=findings,
                    retry_ids=retry_ids, force_ids=force_ids,
                    policy=policy, img_dir=img_dir, project_dir=project_dir,
                    payload=payload, book=book, settle=settle,
                    estimate_img=self._estimate_job(
                        {"kind": "first_frame"}, img_tool, vid_tool, project_dir
                    ),
                )

            for idx, chunk_sec in enumerate(chunks):
                dest = final_path if len(chunks) == 1 else str(vid_dir / f"{shot_id}_p{idx + 1}.mp4")
                if rework_mode == "splice" and rework.get("segment"):
                    dest = str(vid_dir / f"{shot_id}_retake.mp4")
                    chunk_sec = float(rework["segment"]["duration_seconds"])
                seg = run_segments[idx] if (seg_mode and idx < len(run_segments)) else None
                seg_prompt = ""
                if seg is not None:
                    seg_refs = [r for r in (seg.get("refs") or []) if isinstance(r, dict)]
                    refs_for_plan: list[dict[str, Any]] = []
                    if idx > 0:
                        bridge_url = gen_bridge_frame(prev_tail_path, idx + 1, seg_refs)
                        if bridge_url:
                            refs_for_plan.append({
                                "url": bridge_url, "kind": "first_frame",
                                "bridge": True, "name": "续接首帧",
                            })
                    refs_for_plan.extend(seg_refs)
                    plan_rt = _agnes_flash_image_plan(refs_for_plan)
                    shot["_agnes_ref_plan"] = plan_rt
                    seg_prompt = build_seg_video_prompt(plan_rt)
                vid_payload: dict[str, Any] = {
                    "prompt": seg_prompt or prompt0,
                    "output_path": dest,
                    "project_dir": project_dir,
                    "seconds": chunk_sec,
                    "duration": str(int(chunk_sec)),
                    "prompt_fallback": prompt_fallback,
                }
                # 显式 seed 透传（v8.2）：导演逐镜声明（scene_plan 经
                # overlay_plan_rework 覆盖进 shot），_generate_with_retry 据此
                # 锁种子重试不换种；未声明时由 retry 循环发 1000+attempt。
                if str(shot.get("seed") or "").strip():
                    vid_payload["seed"] = str(shot.get("seed")).strip()
                if api_id:
                    vid_payload["api_id"] = api_id
                if agnes_loop and v25:
                    # 官方公共参数（默认 16:9，六值，全模式适用）；按 output_profile 推导，
                    # 避免竖屏项目仍产 16:9 再靠 finish 补边。
                    vid_payload["aspect_ratio"] = self._profile_aspect(
                        policy,
                        env_key="AGNES_RATIO",
                        allowed=AGNES_VIDEO_RATIOS,
                        allow_21_9=True,
                    )
                use_first, use_first_url = first_path, first_url
                use_last, use_last_url = last_path, last_url
                if rework_mode in ("edit", "extend", "feature"):
                    use_first = use_first_url = use_last = use_last_url = ""
                    vid_payload["rework_mode"] = rework_mode
                    source_url = str(rework.get("source_url") or "")
                    vid_payload["video_urls"] = [source_url]
                    vid_payload["video_url"] = source_url
                    vid_payload["refer_type"] = "feature" if rework_mode == "feature" else "base"
                    vid_payload["prompt"] = prompt0
                    if api_id == "kling_omni_30":
                        vid_payload["sound"] = "off"
                        if rework_mode == "feature":
                            vid_payload["audio"] = "off"
                    if api_id in _SEEDANCE_APIS:
                        vid_payload["generate_audio"] = bool(shot.get("_generate_audio", True))
                        neg = str(shot.get("negative_prompt") or "")
                        if neg:
                            vid_payload["negative_prompt"] = neg
                elif agnes_loop and idx > 0 and v25:
                    if seg_mode:
                        # 分段已在提示词里写明续接语义，不再叠「补时长」的旧 refine 话术。
                        vid_payload["prompt"] = seg_prompt or prompt0
                    else:
                        vid_payload["prompt"] = f"{seg_prompt or prompt0} {_REFINE_HINT}"
                    if resolved_agnes_mode(shot, frames_mode) == "keyframe":
                        # keyframe 只认首/尾帧，续段不叠参考图。v8.2：续段不再
                        # 空转——上一段成片尾帧经图片侧合成桥帧（拿公网 URL）
                        # 做本段 first_frame，keyframe 续接继承连续性。
                        bridge_url = gen_bridge_frame(prev_tail_path, idx + 1, [])
                        if bridge_url:
                            vid_payload["mode"] = "keyframe"
                            use_first = use_first_url = use_last = use_last_url = ""
                            vid_payload["first_frame_url"] = bridge_url
                        else:
                            vid_payload["mode"] = "text"
                            use_first = use_first_url = use_last = use_last_url = ""
                            findings.append({
                                "severity": "warning",
                                "field": subject,
                                "message": f"第 {idx + 1} 段桥帧合成失败，keyframe 续段回落文生",
                                "proposed_fix": "检查 ffmpeg/图片侧配额，或在剧本层拆镜",
                            })
                    else:
                        plan = shot.get("_agnes_ref_plan")
                        if not isinstance(plan, dict) or not plan:
                            plan = _agnes_flash_image_plan(
                                _identity_http_refs(
                                    shot, refs, script, scene_plan,
                                    cast_ref_kind=cast_ref_kind,
                                    video_loop=video_loop,
                                )
                            )
                        imgs = list(plan["urls"])
                        if imgs:
                            vid_payload["mode"] = "reference"
                            vid_payload["images"] = imgs
                            use_first = use_first_url = use_last = use_last_url = ""
                        else:
                            vid_payload["mode"] = "text"
                elif agnes_loop and idx > 0:
                    vid_payload["prompt"] = f"{prompt0} {_REFINE_HINT}"
                if api_id in _EXPLICIT_FRAME_APIS:
                    first_src = use_first_url or use_first
                    last_src = use_last_url or use_last
                    if first_src:
                        vid_payload["image_url"] = first_src
                        vid_payload["first_frame_url"] = first_src
                    if last_src:
                        vid_payload["last_frame_url"] = last_src
                    if rework_mode not in ("edit", "extend", "feature"):
                        if api_id in _SEEDANCE_APIS:
                            vid_payload["generate_audio"] = bool(shot.get("_generate_audio", True))
                            neg = str(shot.get("negative_prompt") or "")
                            if neg:
                                vid_payload["negative_prompt"] = neg
                        if api_id == "kling_omni_30":
                            vid_payload["sound"] = str(shot.get("_sound") or "off")
                            vid_payload["audio"] = (
                                "native" if vid_payload["sound"] == "on" else "off"
                            )
                    identity = _identity_http_refs(
                        shot, refs, script, scene_plan,
                        skip_urls={use_first_url, use_last_url},
                        cast_ref_kind=cast_ref_kind,
                        video_loop=video_loop,
                    )
                    if api_id == "kling_omni_30" and rework_mode not in ("edit", "extend", "feature"):
                        vid_payload["refs"] = identity
                    if api_id in _SEEDANCE_APIS and not use_first_url and not use_last_url:
                        vid_payload["reference_urls"] = [r["url"] for r in identity]
                    v_notes: list[str] = []
                elif agnes_loop and v25:
                    # Agnes 2.5 的首/尾帧与 mode 由上方分支自己管，交给
                    # apply_video_frames 会按 preview 能力表误报"不支持首帧"。
                    v_notes = []
                else:
                    v_notes = apply_video_frames(
                        vid_payload,
                        first_path=use_first,
                        first_url=use_first_url,
                        last_path=use_last,
                        last_url=use_last_url,
                        caps=route_caps or v_caps,
                    )
                for note in v_notes:
                    findings.append({
                        "severity": "warning", "field": subject, "message": note,
                        "proposed_fix": "能力表降级，无需改剧本",
                    })
                if agnes_loop and idx == 0 and user_vids:
                    findings.append({
                        "severity": "warning",
                        "field": subject,
                        "message": "Agnes Flash 不支持 videos[]，已忽略用户参考视频",
                        "proposed_fix": "用定妆图 reference，或把该镜拆开",
                    })
                    if not v25:
                        vid_payload["prompt"] = f"{vid_payload['prompt']} {' '.join(user_vids)}".strip()
                        findings[-1]["message"] = "Agnes 2.0 无 videos[]，已把用户视频 URL 写入提示词后仍生成"
                        findings[-1]["proposed_fix"] = "回滚 2.0 以外请去掉用户视频"
                if agnes_loop and idx == 0 and rework_mode not in ("edit", "extend", "feature"):
                    identity = _identity_http_refs(
                        shot, refs, script, scene_plan,
                        skip_urls=set(),
                        cast_ref_kind=cast_ref_kind,
                        video_loop=video_loop,
                    )
                    if v25:
                        plan = shot.get("_agnes_ref_plan")
                        if not isinstance(plan, dict) or not plan:
                            plan = _agnes_flash_image_plan(identity)
                        images = list(plan["urls"])
                        # v8.2 两档时序：emit 期以本镜 resolved mode 为准
                        # （显式 agnes_mode > packet frames_mode），并按
                        # 「上镜尾帧/首帧是否真实可发」降级，不做两阶段重跑。
                        emit_mode = resolved_agnes_mode(shot, frames_mode)
                        if emit_mode == "keyframe":
                            # 真 I2V：不叠 identity 参考，只发首/尾帧；两者都缺则回落 text。
                            for key in ("images", "image_urls", "audios"):
                                vid_payload.pop(key, None)
                            if shot_audio_ref_urls(shot)[0]:
                                findings.append({
                                    "severity": "warning",
                                    "field": subject,
                                    "message": "本镜声明了音频参考，但 keyframe 模式不消费 audios，已丢弃",
                                    "proposed_fix": "改 agnes_mode=reference 保音频参考，或去 audio_ref",
                                })
                            if use_last and not use_last_url:
                                findings.append({
                                    "severity": "warning",
                                    "field": subject,
                                    "message": "尾帧是本地文件，Agnes 无法访问，已忽略尾帧链",
                                    "proposed_fix": "先上传尾帧拿公网 URL，或改用 reference_first/preview",
                                })
                            if use_first_url or use_last_url:
                                vid_payload["mode"] = "keyframe"
                                if use_first_url:
                                    vid_payload["first_frame_url"] = use_first_url
                                if use_last_url:
                                    vid_payload["last_frame_url"] = use_last_url
                            else:
                                vid_payload["mode"] = "text"
                                findings.append({
                                    "severity": "warning",
                                    "field": subject,
                                    "message": "frames_mode=keyframe 但该镜无公网首/尾帧，已回落文生",
                                    "proposed_fix": "先出首帧公网 URL，或把 frames_mode 改回 preview",
                                })
                        elif images:
                            vid_payload["images"] = images
                            vid_payload["mode"] = "reference"
                            # 音频参考叠加（v8.2 P1-audio-ref）：reference 模式
                            # images+audios 并存（官方允许）；节奏参考优先已在
                            # shot_audio_ref_urls 排序，≤3 截断同样在那边处理。
                            audio_urls, audio_notes = shot_audio_ref_urls(shot)
                            for note in audio_notes:
                                findings.append({
                                    "severity": "warning",
                                    "field": subject,
                                    "message": note,
                                    "proposed_fix": "为音频资产补公网 URL 或减少参考段数",
                                })
                            if audio_urls and audios_compatible_with_mode("reference"):
                                vid_payload["audios"] = audio_urls
                            for key in (
                                "image_url", "image_urls", "first_frame",
                                "last_frame", "last_frame_url", "extra_body",
                            ):
                                vid_payload.pop(key, None)
                        else:
                            vid_payload["mode"] = "text"
                            if shot_audio_ref_urls(shot)[0]:
                                findings.append({
                                    "severity": "warning",
                                    "field": subject,
                                    "message": "本镜声明了音频参考，但该镜无参考图回落 text，audios 已丢弃",
                                    "proposed_fix": "补参考图走 reference，或去 audio_ref",
                                })
                    else:
                        keyframes: list[str] = []
                        for url in (
                            use_first_url,
                            use_last_url,
                            *[str(r.get("url") or "") for r in identity],
                        ):
                            token = str(url).strip()
                            if token.startswith("http") and token not in keyframes:
                                keyframes.append(token)
                        if keyframes:
                            extra = dict(vid_payload.get("extra_body") or {})
                            extra["image"] = keyframes
                            extra["mode"] = "keyframes"
                            vid_payload["extra_body"] = extra
                        elif still_refs:
                            vid_payload["images"] = still_refs
                            vid_payload["mode"] = "reference"
                vid_result = run_one(
                    vid_payload,
                    dest,
                    extra=(
                        {
                            "rework_mode": vid_payload.get("rework_mode"),
                            "source": vid_payload.get("video_url") or "",
                        }
                        if vid_payload.get("rework_mode")
                        else None
                    ),
                )
                if (
                    rework_mode == "splice"
                    and vid_result.success
                    and existing_clip is not None
                    and rework.get("segment")
                ):
                    local = str(existing_clip.get("path") or "")
                    src = Path(local) if local and Path(local).is_file() else Path(project_dir) / local
                    if not src.is_file():
                        findings.append({
                            "severity": "warning",
                            "field": subject,
                            "message": "splice 找不到本地成片，已留下替换片段",
                            "proposed_fix": "整镜 --retry",
                        })
                    else:
                        try:
                            from montage.compose.ffmpeg_engine import retake_segment as _retake

                            _retake(
                                src,
                                Path(dest),
                                Path(final_path),
                                float(rework["segment"]["start_seconds"]),
                                float(rework["segment"]["duration_seconds"]),
                            )
                        except Exception as exc:  # noqa: BLE001
                            findings.append({
                                "severity": "warning",
                                "field": subject,
                                "message": f"retake_segment 失败: {exc}",
                                "proposed_fix": "整镜 --retry",
                            })
                if (
                    agnes_loop
                    and vid_result.success
                    and len(chunks) == 1
                    and _needs_agnes_refine(
                        (vid_result.meta or {}).get("quality") if isinstance(vid_result.meta, dict) else {},
                        chunk_sec,
                    )
                ):
                    if v25:
                        findings.append({
                            "severity": "warning",
                            "field": subject,
                            "message": "Flash 不能用参考视频补时长，已保留首轮成片",
                            "proposed_fix": f"把该镜拆到 ≤12s，或 shot_runner retry_ids=[{shot_id}]",
                        })
                    else:
                        refine = dict(vid_payload)
                        refine["prompt"] = f"{prompt0} {_REFINE_HINT}"
                        refined = run_one(refine, dest, extra={"refine": 1})
                        if refined.success:
                            vid_result = refined
                        else:
                            findings.append({
                                "severity": "warning",
                                "field": subject,
                                "message": refined.error or "第二刀补时长失败，保留首轮成片",
                                "proposed_fix": f"shot_runner retry_ids=[{shot_id}]",
                            })
                if not vid_result.success:
                    with state_lock:
                        if shot_id and shot_id not in retryable:
                            retryable.append(shot_id)
                        findings.append({
                            "severity": "warning",
                            "field": subject,
                            "message": vid_result.error,
                            "proposed_fix": f"shot_runner retry_ids=[{shot_id}]",
                        })
                        record_vlm(shot_id, vid_result.meta, shot)
                    return
                segment_paths.append(_media_path(vid_result, dest))
                segment_url = _media_url(vid_result)
                if seg_mode and idx < len(chunks) - 1:
                    # 供下一段桥接：抽本段尾帧（本地，图片侧可用）。
                    prev_tail_path = self._extract_tail(
                        segment_paths[-1],
                        str(img_dir / f"{shot_id}_p{idx + 1}_tail.png"),
                    ) or ""

            video_path = segment_paths[-1] if segment_paths else final_path
            video_url = segment_url
            if len(segment_paths) > 1:
                try:
                    from montage.compose.ffmpeg_engine import concat_videos

                    concat_videos([Path(p) for p in segment_paths], Path(final_path))
                    video_path = final_path
                    video_url = ""
                except Exception as exc:  # noqa: BLE001
                    findings.append({
                        "severity": "warning",
                        "field": subject,
                        "message": f"多段拼接失败，仅保留首段: {exc}",
                        "proposed_fix": "安装 ffmpeg 或在剧本层拆镜",
                    })
                    video_path = segment_paths[0]
            measured = probe_seconds(video_path) or probe_seconds(final_path)
            if measured > 0 and abs(measured - wanted) > 0.25:
                findings.append({
                    "severity": "warning",
                    "field": subject,
                    "message": (
                        f"供应商返回时长 {measured:.2f}s，与请求 {wanted:.2f}s 不符；"
                        "已按实测时长记进 asset_manifest，时间轴以实测为准"
                    ),
                    "proposed_fix": "介意误差则在剧本层改请求秒数；不要按计划时长手算字幕",
                })
            with state_lock:
                _upsert_item(items, _media_item(
                    item_id=f"{shot_id}_video",
                    kind="video",
                    path=video_path,
                    scene_id=scene_id,
                    shot_id=shot_id,
                    provider=getattr(vid_tool, "provider", "") or "",
                    url=video_url,
                    duration_seconds=measured,
                ))
                results.append({"id": f"{shot_id}_video", "ok": True, "path": video_path})
                record_vlm(shot_id, vid_result.meta, shot)
                continuity_state = update_continuity(
                    continuity_state, shot,
                    scene_plan=scene_plan, registry=registry, script=script,
                )
            # 尾帧跨 run 持久化（v8.2）：Agnes 也抽尾帧并落 asset_manifest
            # （item id {shot_id}_tail），多日 resume / 单镜 --retry 时后向锚
            # 才有源可取。与桥帧 cache_params["tail"]（路径稳定）配合，次日
            # resume 桥帧缓存命中不耗图片配额。
            tail = self._extract_tail(video_path, str(img_dir / f"{shot_id}_tail.png"))
            if tail:
                shot["_tail_path"] = tail
                with state_lock:
                    _upsert_item(items, _media_item(
                        item_id=f"{shot_id}_tail",
                        kind="image",
                        path=tail,
                        scene_id=scene_id,
                        shot_id=shot_id,
                        provider=getattr(vid_tool, "provider", "") or "",
                        url="",
                    ))
                    results.append({"id": f"{shot_id}_tail", "ok": True, "path": tail})

        prev_tail = ""
        prev_location = ""

        def _need_portrait(shot: dict[str, Any]) -> bool:
            if skip_portraits:
                return False
            if (
                agnes_loop
                and _agnes_is_v25()
                and str((shot or {}).get("agnes_mode") or "").strip().lower() == "keyframe"
            ):
                # 门禁豁免（v8.2）：显式 keyframe 镜禁 images，首帧即身份锚
                # （须来自带身份的成片尾帧或身份首帧设计稿）；不再强制定妆。
                return False
            return any(cid in registry for cid in _character_ids(shot, scene_plan))

        def _block_i2v(shot: dict[str, Any], message: str) -> None:
            sid = str(shot.get("shot_id") or "")
            field = f"{shot.get('scene_id')}/{sid}"
            blocked_i2v.append(message)
            if sid and sid not in retryable:
                retryable.append(sid)
            findings.append({
                "severity": "critical",
                "field": field,
                "message": message,
                "proposed_fix": "先生成定妆照，或换支持参考图的图模型",
            })

        def _existing_still(sid: str) -> dict[str, Any] | None:
            for item in reversed(items):
                if not isinstance(item, dict):
                    continue
                if str(item.get("kind") or "") != "image":
                    continue
                if str(item.get("shot_id") or "") != sid:
                    continue
                if "portrait" in str(item.get("id") or ""):
                    continue
                if _item_ready(project_dir, item):
                    return item
            return None

        def _may_emit_i2v(shot: dict[str, Any]) -> bool:
            if str(shot.get("shot_kind") or "video") == "image":
                return False
            sid = str(shot.get("shot_id") or "")
            if sid in skipped_video:
                return False
            if _need_portrait(shot):
                # 按本镜声明的 (cid, form_id) 校验该形态选中的身份图是否就绪；
                # 不再按 cid 泛检（那会误拦形态 A 已生成、形态 B 未生成的镜头）。
                need_http = _agnes_cast_needs_url(policy)
                missing = missing_identity_refs(
                    shot, refs, scene_plan,
                    project_dir=project_dir,
                    cast_ref_kind=str(policy.get("cast_ref_kind") or ""),
                    video_loop=str(policy.get("video_loop") or ""),
                    require_url=need_http,
                )
                if missing:
                    hint = "缺定妆/四视图公网 URL" if need_http else "缺身份图"
                    _block_i2v(shot, f"无定妆禁止 I2V（{hint}: {','.join(missing)}）")
                    return False
                if v_caps.get("first_frame") and not i_caps.get("image_reference"):
                    _block_i2v(shot, "当前图模型无 image_reference，禁止 I2V")
                    return False
            return True

        if not frames_only:
            for row in timeline_shots or shots:
                sid = str(row.get("shot_id") or "")
                existing = _existing_still(sid)
                if not existing:
                    continue
                still_by_id[sid] = {
                    "path": str(existing.get("path") or ""),
                    "url": str(existing.get("url") or ""),
                    "scene_id": str(row.get("scene_id") or ""),
                    "shot_id": sid,
                }

        for shot in shots:
            scene_id = str(shot.get("scene_id") or "")
            shot_id = str(shot.get("shot_id") or "")
            subject = f"{scene_id}/{shot_id}"
            if _clears_bridge(shot, prev_location):
                prev_tail = ""
            prev_location = str(shot.get("location_id") or "").strip()
            ref_ids = [
                r["id"]
                for r in resolve_shot_refs(
                    shot, {"reference_assets": refs}, script, scene_plan,
                )
                if r.get("id")
            ]
            shot["reference_asset_ids"] = ref_ids
            will_have_first = True
            if shot_id in skipped_first:
                will_have_first = bool(_existing_still(shot_id))
            route = _route_shot(
                shot,
                video_loop=str(policy.get("video_loop") or ""),
                vid_prov=vid_prov,
                vid_name=vid_name,
                has_first_frame=will_have_first,
                video_surface_id=str(policy.get("video_surface") or ""),
            )
            shot["api_id"] = route["api_id"]
            shot["_route_caps"] = route["caps"]
            shot["_generate_audio"] = route["generate_audio"]
            shot["_sound"] = route["sound"]
            if route["audio_source"]:
                shot["audio_source"] = route["audio_source"]
            elif agnes_loop:
                shot["audio_source"] = "agnes_prompt"
            if route["gen_strategy"]:
                shot["gen_strategy"] = route["gen_strategy"]
            for note in route["notes"]:
                findings.append({
                    "severity": "info",
                    "field": subject,
                    "message": note,
                    "proposed_fix": "按 API 面拆镜或换 video_loop",
                })

            # Agnes 2.5：提示词图例、adapter 引用、实发 images[] 共用这一张最终
            # 有序表（已公网过滤+截断+重排），编号即 <Picture N>。reference_first
            # 下首帧排首位（Picture 1 = 本镜首帧）。
            plan_first = _existing_still(shot_id) if shot_id else None
            plan_first_url = str((plan_first or {}).get("url") or "")
            agnes_plan: dict[str, Any] = {}
            if agnes_loop and _agnes_is_v25():
                identity = _identity_http_refs(
                    shot, refs, script, scene_plan,
                    cast_ref_kind=cast_ref_kind,
                    video_loop=video_loop,
                )
                reserve_first = (
                    resolved_agnes_mode(shot, frames_mode) != "keyframe"
                    and frames_mode == "reference_first"
                    and plan_first_url.startswith("http")
                )
                if reserve_first:
                    identity = [
                        {"url": plan_first_url, "kind": "first_frame", "name": "本镜首帧"},
                        *identity,
                    ]
                dp = (shot.get("_route_caps") or {}).get("duration_policy") or {}
                seg_plan: dict[str, Any] = {"mode": "single", "reason": "", "segments": []}
                if ref_overflow_mode == "segment" and frames_mode != "keyframe":
                    seg_plan = plan_reference_segments(
                        identity,
                        max_images=int(v_caps.get("max_ref_images") or _AGNES_FLASH_MAX_IMAGES),
                        wanted_seconds=float(shot.get("duration_seconds") or 0),
                        min_seconds=float(dp.get("min") or 4),
                        max_seconds=float(dp.get("max") or 12),
                        reserve_first=reserve_first,
                        max_segments=MAX_REF_SEGMENTS,
                    )
                if seg_plan.get("mode") == "segment":
                    segs = list(seg_plan.get("segments") or [])
                    for seg in segs:
                        seg["_plan"] = _agnes_flash_image_plan(list(seg.get("refs") or []))
                    shot["_agnes_segment_plan"] = {"mode": "segment", "segments": segs}
                    agnes_plan = segs[0]["_plan"] if segs else {}
                    shot["_agnes_ref_plan"] = agnes_plan
                    secs_txt = "+".join(
                        f"{float(s.get('seconds') or 0):.0f}s" for s in segs
                    )
                    findings.append({
                        "severity": "info",
                        "field": subject,
                        "message": f"参考图溢出：镜内切 {len(segs)} 段续拍（{secs_txt}）",
                        "proposed_fix": "ref_overflow_mode=single 可回旧行为（丢弃+finding）",
                    })
                else:
                    if seg_plan.get("reason"):
                        findings.append({
                            "severity": "warning",
                            "field": subject,
                            "message": f"参考图溢出但未分段：{seg_plan['reason']}",
                            "proposed_fix": "减参考图/延长时长，或改 ref_overflow_mode=single",
                        })
                    agnes_plan = _agnes_flash_image_plan(identity)
                    shot["_agnes_ref_plan"] = agnes_plan
                    findings.extend(agnes_ref_findings(agnes_plan, subject))

            first_path = ""
            first_url = ""
            if shot_id in skipped_first:
                existing = _existing_still(shot_id)
                if existing:
                    first_path = str(existing.get("path") or "")
                    first_url = str(existing.get("url") or "")
                    still_by_id[shot_id] = {
                        "path": first_path, "url": first_url,
                        "scene_id": scene_id, "shot_id": shot_id,
                    }
            else:
                try:
                    pair = prompt_builder.execute(
                        _prompt_inputs(
                            shot, scene_plan, project_dir,
                            agnes_loop=agnes_loop, vid_prov=vid_prov,
                            api_id=str(route["api_id"] or ""),
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    retryable.append(shot_id)
                    findings.append({
                        "severity": "warning",
                        "field": subject,
                        "message": f"提示词失败: {exc}",
                        "proposed_fix": "检查 visual_details",
                    })
                    continue
                first_prompt = ""
                video_prompt = ""
                builder_data = pair.data if pair.success and isinstance(pair.data, dict) else {}
                for contract_key in (
                    "prompt_contract",
                    "prompt_hash",
                    "anchor_coverage",
                    "reference_requirements",
                    "forbidden_text_hits",
                ):
                    if contract_key in builder_data:
                        shot[contract_key] = builder_data[contract_key]
                if builder_data.get("forbidden_text_hits"):
                    findings.append({
                        "severity": "warning",
                        "field": subject,
                        "message": (
                            "forbidden text 进入 prompt："
                            + "、".join(str(x) for x in builder_data["forbidden_text_hits"][:4])
                        ),
                        "proposed_fix": "修改 prompt_contract.forbidden_text 或重编提示词",
                    })
                passthrough = bool(prompt_profile(str(route["api_id"] or "")).get("passthrough"))
                if agnes_plan:
                    # 单一路径：Agnes 2.5 的 <Picture N> 只由最终有序表生成。
                    adapter_refs = agnes_plan_adapter_refs(agnes_plan)
                else:
                    adapter_refs = _adapter_refs(shot, refs)
                adapted = adapt_visual_prompt(
                    str(route["api_id"] or ""),
                    builder_data,
                    refs=adapter_refs,
                    dialogue=_shot_dialogue(shot),
                    duration_seconds=_shot_duration(shot),
                    continuity_note="" if passthrough else format_continuity_note(
                        continuity_state, shot, scene_plan=scene_plan,
                    ),
                )
                first_prompt = str(adapted.get("first_frame_prompt") or "")
                video_raw = adapted.get("video_prompt")
                video_prompt = "" if video_raw is None else str(video_raw)
                if adapted.get("notes"):
                    for note in adapted["notes"]:
                        findings.append({
                            "severity": "warning",
                            "field": subject,
                            "message": str(note),
                            "proposed_fix": "拆镜或降级 API 面，不要截断后硬发",
                        })
                if not adapted.get("valid", True):
                    shot["_prompt_invalid"] = True
                    retryable.append(shot_id)
                    findings.append({
                        "severity": "critical",
                        "field": subject,
                        "message": "提示词未点名工牌或引用非法，已停发",
                        "proposed_fix": "检查 @element_N / @image_N 后 --retry",
                    })
                if adapted.get("negative_prompt"):
                    shot["negative_prompt"] = adapted["negative_prompt"]
                shot["first_frame_prompt"] = first_prompt
                if video_prompt:
                    shot["video_prompt"] = video_prompt
                    if agnes_loop and _agnes_is_v25():
                        from lib.shot_prompt_builder import apply_agnes_prompt_limit

                        trimmed, over = apply_agnes_prompt_limit(
                            video_prompt, fallback=prompt_fallback,
                        )
                        shot["video_prompt"] = trimmed
                        if over:
                            prompt_too_long = True
                            findings.append({
                                "severity": "warning",
                                "field": subject,
                                "message": f"视频提示词 {len(video_prompt)} 字，超过 Agnes 3000 字上限",
                                "proposed_fix": "改圣经后 produce（不要 --resume）；不改则 produce --resume 走压缩兜底",
                            })
                if not first_prompt:
                    retryable.append(shot_id)
                    findings.append({
                        "severity": "warning",
                        "field": subject,
                        "message": pair.error or "首帧提示词为空",
                        "proposed_fix": "补 visual_details 后重跑",
                    })
                    continue

                if prompt_only:
                    continue

                first_path = str(img_dir / f"{shot_id}_first.png")
                img_payload = {
                    "prompt": first_prompt,
                    "output_path": first_path,
                    "project_dir": project_dir,
                }
                if img_name == "agnes_image":
                    img_payload["size"] = "2K"
                if img_name == "kling_image":
                    img_payload["result_type"] = "single"
                    img_payload["resolution"] = "2k"
                if img_name == "seedream_image":
                    img_payload["aspect_ratio"] = self._seedream_aspect(policy)
                resolved_refs = resolve_shot_refs(
                    {
                        **shot,
                        **(
                            {"_include_turnaround_refs": True}
                            if bool(policy.get("first_frame_turnaround"))
                            else {}
                        ),
                    },
                    {"reference_assets": refs}, script, scene_plan,
                    # 首帧参考位上限（agnes_image=6）：超了才挤定妆图，没超就留着。
                    max_refs=int((i_caps or {}).get("max_ref_images") or 0) or None,
                )
                # 图例要用中文名（"为宦娘基础形象的四视图"），不能写
                # turnaround_huan_niang / prop_prop_guqin 这类内部 id。
                resolved_refs = name_refs_for_prompt(
                    resolved_refs,
                    character_registry=(scene_plan or {}).get("character_registry"),
                    script=script,
                )
                notes = apply_image_refs(img_payload, resolved_refs, i_caps)
                for note in notes:
                    findings.append({
                        "severity": "warning", "field": subject, "message": note,
                        "proposed_fix": "换支持参考图的供应商或补 URL",
                    })
                if img_name == "agnes_image" and img_payload.get("operation"):
                    # 多图合成必须说明每张输入图的角色，否则模型把定妆/场景/道具
                    # 混用。图例与实发 extra_body.image 共用同一有序表。
                    from lib.shot_prompt_builder import image_ref_legend

                    entries, _ = agnes_image_ref_entries(resolved_refs, i_caps)
                    legend = image_ref_legend(entries)
                    if legend:
                        img_payload["prompt"] = f"{img_payload.get('prompt') or ''}\n{legend}"
                if img_name == "agnes_image" and not img_payload.get("operation"):
                    # 纯文生才设档案画幅；img2img 官方语义「构图保留」，不设。
                    # 判据用 operation：apply_image_refs 仅在真有条目时设 image_reference，
                    # 无条目（含 refs 被 max_ref_images 截空）会降级纯文生。
                    img_payload["ratio"] = self._profile_aspect(
                        policy,
                        env_key="AGNES_RATIO",
                        allowed=AGNES_IMAGE_RATIOS,
                        allow_21_9=True,
                    )
                elif img_name == "agnes_image":
                    # 2026-09-19 用户实测：参考模式（image_reference）不传 ratio 时
                    # Agnes 落回官方默认 1:1 → 首帧全是 2048×2048 方图，I2V 后
                    # 整片画幅与 16:9 成片不符。这里**显式传档案画幅**（默认 16:9）。
                    img_payload["size"] = img_payload.get("size") or "2K"
                    img_payload["ratio"] = self._profile_aspect(
                        policy,
                        env_key="AGNES_RATIO",
                        allowed=AGNES_IMAGE_RATIOS,
                        allow_21_9=True,
                    )
                usd = self._estimate_job({"kind": "first_frame"}, img_tool, vid_tool, project_dir)
                eid = book("image_generation", subject, payload["image_tool"], usd)
                img_result = self._generate_with_retry(
                    kind="image",
                    payload=img_payload,
                    output_path=first_path,
                    cache=cache,
                    # refs 也要进缓存键：改了参考图（例如空镜去掉人物四视图）而提示词
                    # 不变时，旧缓存会把"带人物的首帧"还回来（sc05_08 片尾空镜实测）。
                    cache_params={
                        "prompt": img_payload.get("prompt") or first_prompt,
                        "kind": "first_frame",
                        "shot_id": shot_id,
                        "ratio": img_payload.get("ratio") or img_payload.get("aspect_ratio") or "",
                        "image_tool": img_name,
                        "refs": [
                            str(r.get("url") or r.get("path") or r.get("id") or "")
                            for r in (resolved_refs or [])
                            if isinstance(r, dict)
                        ],
                    },
                    expected_duration=None,
                    skip_cache=_retry_covers(retry_ids, shot_id=shot_id) or shot_id in force_ids,
                    vlm_context={
                        **_vlm_expected(shot, registry, scene_plan, portraits),
                        "mode": "first_frame",
                    },
                )
                settle(eid, img_result.cost_usd if img_result.success else 0.0)
                if not img_result.success:
                    retryable.append(shot_id)
                    findings.append({
                        "severity": "warning",
                        "field": subject,
                        "message": img_result.error or "首帧生成失败",
                        "proposed_fix": f"shot_runner retry_ids=[{shot_id}]",
                    })
                    record_vlm(shot_id, img_result.meta, shot)
                    continue
                first_path = _media_path(img_result, first_path)
                first_url = _media_url(img_result)
                record_vlm(shot_id, img_result.meta, shot)
                continuity_state = update_continuity(
                    continuity_state, shot,
                    scene_plan=scene_plan, registry=registry, script=script,
                )
                still_by_id[shot_id] = {
                    "path": first_path, "url": first_url,
                    "scene_id": scene_id, "shot_id": shot_id,
                }
                _upsert_item(items, _media_item(
                    item_id=f"{shot_id}_first",
                    kind="image",
                    path=first_path,
                    scene_id=scene_id,
                    shot_id=shot_id,
                    provider=getattr(img_tool, "provider", "") or "",
                    url=first_url,
                ))
                results.append({"id": f"{shot_id}_first", "ok": True, "path": first_path})

            if frames_only:
                continue
            if str(shot.get("shot_kind") or "video") == "image":
                if first_path and _shot_cut(shot) != "hard":
                    prev_tail = first_path
                continue
            if agnes_loop:
                continue
            if kling_loop:
                continue
            if shot.get("_prompt_invalid"):
                continue
            if not _may_emit_i2v(shot):
                continue
            route_caps = shot.get("_route_caps") if isinstance(shot.get("_route_caps"), dict) else v_caps
            i2v_first, i2v_url = first_path, first_url
            if prev_tail and route_caps.get("first_frame"):
                i2v_first = prev_tail
                i2v_url = ""
            last_path, last_url = "", ""
            if _want_last_frame(
                shot, route_caps, kling_loop=False, sample_one=sample_one,
            ):
                last_path, last_url = _next_hero_tail(shot, timeline_shots or shots, still_by_id)
            emit_video(
                shot, i2v_first, i2v_url,
                last_path=last_path,
                last_url=last_url,
            )
            prev_tail = str(shot.get("_tail_path") or prev_tail)

        if kling_loop and not frames_only:
            prev_tail = ""
            prev_location = ""
            for shot in shots:
                shot_id = str(shot.get("shot_id") or "")
                if _clears_bridge(shot, prev_location):
                    prev_tail = ""
                prev_location = str(shot.get("location_id") or "").strip()
                still = still_by_id.get(shot_id) or {}
                first_path = str(still.get("path") or "")
                first_url = str(still.get("url") or "")
                if str(shot.get("shot_kind") or "video") == "image":
                    if first_path and _shot_cut(shot) != "hard":
                        prev_tail = first_path
                    continue
                if shot.get("_prompt_invalid"):
                    continue
                if video_ids and shot_id not in video_ids:
                    continue
                if not first_path:
                    continue
                if not _may_emit_i2v(shot):
                    continue
                route_caps = shot.get("_route_caps") if isinstance(shot.get("_route_caps"), dict) else v_caps
                i2v_first, i2v_url = first_path, first_url
                if prev_tail and route_caps.get("first_frame"):
                    i2v_first = prev_tail
                    i2v_url = ""
                last_path, last_url = "", ""
                if _want_last_frame(
                    shot, route_caps, kling_loop=True, sample_one=sample_one,
                ):
                    last_path, last_url = _next_hero_tail(
                        shot, timeline_shots or shots, still_by_id,
                    )
                emit_video(
                    shot, i2v_first, i2v_url,
                    last_path=last_path,
                    last_url=last_url,
                )
                prev_tail = str(shot.get("_tail_path") or prev_tail)

        if agnes_loop and not frames_only:
            neighbor = _next_still_urls(shots, still_by_id)

            def _parallel_eligible(shot: dict[str, Any]) -> bool:
                sid = str(shot.get('shot_id') or '')
                if (
                    str(shot.get('shot_kind') or 'video') == 'image'
                    or sid in skipped_video
                    or shot.get('_prompt_invalid')
                ):
                    return False
                if not _may_emit_i2v(shot):
                    return False
                still = still_by_id.get(sid) or {}
                first_url = str(still.get('url') or '')
                emit_mode = resolved_agnes_mode(shot, frames_mode)
                return (
                    _agnes_is_v25()
                    and emit_mode != 'keyframe'
                    and first_url.startswith('http')
                )

            video_workers = self._agnes_video_workers()
            parallel_shots = [shot for shot in shots if _parallel_eligible(shot)]
            parallel_ids = {
                str(shot.get('shot_id') or '') for shot in parallel_shots
            }
            parallel_done = self._run_dynamic_video_batch(
                parallel_shots,
                workers=video_workers,
                still_by_id=still_by_id,
                emit_video=emit_video,
                retryable=retryable,
                findings=findings,
                state_lock=state_lock,
            )
            payload['agnes_video_concurrency'] = video_workers
            payload['agnes_video_scheduling'] = 'dynamic_fifo'
            payload['agnes_video_order'] = {
                'parallel': [str(shot.get('shot_id') or '') for shot in parallel_shots],
                'sequential': [
                    str(shot.get('shot_id') or '')
                    for shot in shots
                    if str(shot.get('shot_id') or '') not in parallel_ids
                ],
            }

            back_prev_tail = ""
            back_prev_location = ""
            for shot in shots:
                shot_id = str(shot.get("shot_id") or "")
                if shot_id in parallel_done:
                    continue
                if _clears_bridge(shot, back_prev_location):
                    # 后向锚与可灵链同规则清桥：hard cut 或换 location 不接。
                    back_prev_tail = ""
                back_prev_location = str(shot.get("location_id") or "").strip()
                if str(shot.get("shot_kind") or "video") == "image":
                    continue
                if shot_id in skipped_video:
                    continue
                if shot.get("_prompt_invalid"):
                    continue
                if not _may_emit_i2v(shot):
                    continue
                still = still_by_id.get(shot_id) or {}
                first_url = str(still.get("url") or "")
                need_url = _need_portrait(shot)
                emit_mode = resolved_agnes_mode(shot, frames_mode)
                if _agnes_is_v25():
                    if need_url and emit_mode != "keyframe":
                        identity = _identity_http_refs(
                            shot, refs, script, scene_plan,
                            cast_ref_kind=cast_ref_kind,
                            video_loop=video_loop,
                        )
                        if not any(
                            str(r.get("kind") or "") in ("portrait", "turnaround")
                            and str(r.get("url") or "").startswith("http")
                            for r in identity
                        ):
                            _block_i2v(shot, "缺定妆/四视图公网 URL，禁止降级文生")
                            continue
                    if emit_mode == "keyframe" and back_prev_tail:
                        # 后向锚（v8.2，路径①）：上镜成片尾帧（本地）经桥帧
                        # 合成拿公网 URL 做 first_frame——payload_v25 只认
                        # http。仅 keyframe 意图时才物化（省图片配额）。
                        bridge_url = gen_bridge_frame(
                            back_prev_tail, 0, [],
                            owner_shot_id=shot_id,
                            owner_subject=f"{shot.get('scene_id')}/{shot_id}",
                        )
                        if bridge_url:
                            findings.append({
                                "severity": "info",
                                "field": f"{shot.get('scene_id')}/{shot_id}",
                                "message": "后向锚：上镜成片尾帧已合成桥帧做本镜 keyframe 首帧",
                                "proposed_fix": "改显式 agnes_mode=reference 可回身份图锚",
                            })
                            emit_video(
                                shot,
                                "",
                                bridge_url,
                                last_path="",
                                last_url=neighbor.get(shot_id, ""),
                            )
                            continue
                        findings.append({
                            "severity": "warning",
                            "field": f"{shot.get('scene_id')}/{shot_id}",
                            "message": "后向锚桥帧合成失败，keyframe 意图回落常规路径",
                            "proposed_fix": "检查 ffmpeg/图片侧配额；或显式 agnes_mode=reference",
                        })
                    emit_video(
                        shot,
                        str(still.get("path") or ""),
                        first_url,
                        last_path="",
                        last_url="",
                    )
                    continue
                if need_url and not first_url.startswith("http"):
                    _block_i2v(shot, "Agnes 首帧无公网 URL，禁止降级文生视频")
                    continue
                if not still.get("path"):
                    if need_url:
                        _block_i2v(shot, "Agnes 该镜静图失败，禁止降级文生视频")
                        continue
                    findings.append({
                        "severity": "warning",
                        "field": f"{shot.get('scene_id')}/{shot_id}",
                        "message": "Agnes 该镜静图失败，降级文生视频",
                        "proposed_fix": f"shot_runner retry_ids=[{shot_id}]",
                    })
                elif not first_url.startswith("http") and not need_url:
                    findings.append({
                        "severity": "warning",
                        "field": f"{shot.get('scene_id')}/{shot_id}",
                        "message": "Agnes 首帧无公网 URL，降级文生视频",
                        "proposed_fix": "确认 agnes_image 返回 url",
                    })
                emit_video(
                    shot,
                    str(still.get("path") or ""),
                    first_url,
                    last_path="",
                    last_url=neighbor.get(shot_id, ""),
                )
                back_prev_tail = str(shot.get("_tail_path") or back_prev_tail)

        lifted = lift_shot_prompts(timeline_shots or shots)
        manifest_out = {"items": items, "reference_assets": refs}
        if identity_memory is not None and project_dir:
            # 迟到定妆（shot 阶段补出）也走 canonical 保守更新；必须在
            # asset_manifest 落盘前完成，否则 canonical 标记写不进产物。
            self._update_identity_anchors(
                memory=identity_memory,
                candidates=identity_candidates,
                refs=refs,
                scene_plan=scene_plan,
                findings=findings,
            )
        if store:
            store.write("shot_prompts", lifted)
            if not prompt_only:
                store.write("asset_manifest", manifest_out, schema=None)
        if identity_memory is not None and project_dir:
            # 漂移观测落盘（P0-identity-memory）：canonical 锚由 cast/shot 阶段
            # 维护，这里更新漂移计数/retake 标记；顺带把重拍提示转成 findings。
            for row in identity_findings(identity_memory):
                if not any(
                    f.get("field") == row.get("field") and f.get("message") == row.get("message")
                    for f in findings
                ):
                    findings.append(row)
            save_identity_memory(project_dir, identity_memory)
            if store:
                store.write("identity_memory", identity_memory, schema=None)
            payload["identity_retakes"] = retake_subjects(identity_memory)
        if project_dir and not prompt_only:
            self._write_image_bindings(
                store=store,
                project_dir=project_dir,
                manifest=manifest_out,
                scene_plan=scene_plan,
                script=script,
                shots=shots,
                findings=findings,
            )
            write_continuity(project_dir, continuity_state)
            skipped = (not vlm_rows) or all(row.get("skipped") for row in vlm_rows)
            vlm_pass = (not skipped) and all(
                row.get("ok") for row in vlm_rows if not row.get("skipped")
            )
            ArtifactStore(project_dir).write("vlm_review", {
                "pass": vlm_pass,
                "skipped": skipped,
                "shots": vlm_rows,
            }, schema=None)

        payload.update({
            "results": results,
            "retryable_ids": retryable,
            "findings": findings,
            "shot_prompts": lifted,
            "asset_manifest": manifest_out,
            "blocked": bool(blocked_i2v),
            "prompt_too_long": prompt_too_long,
        })
        self._apply_event_errors(payload)
        # 档位池收尾（v8.2 P0-scheduler）：restore 回用户声明档（进程内 env
        # 切换不落盘）；记账已在 agnes_usage 按档分桶，跨天自然归零。
        if declared_tier:
            from montage.engine import scheduler as _sched

            active_now = agnes_access_tier()
            if active_now != declared_tier:
                payload["tier_switched"] = {
                    "from": declared_tier, "to": active_now,
                    "note": _sched.tier_note(active_now),
                }
            _sched.restore_tier(declared_tier)
        if prompt_too_long:
            payload["prompt_over_ids"] = [
                str(s.get("shot_id") or "")
                for s in shots
                if len(str(s.get("video_prompt") or "")) > 3000
            ]
        if blocked_i2v:
            return ToolResult(
                success=False,
                error=blocked_i2v[0],
                data=payload,
                meta={"shots": len(shots), "failed": len(retryable)},
            )
        return ToolResult(success=True, data=payload, meta={"shots": len(shots), "failed": len(retryable)})

    def _apply_event_errors(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Expose degraded event-write failures without changing tool success."""
        if self._event_context:
            payload["generation_run_id"] = str(self._event_context.get("run_id") or "")
            payload["generation_batch_id"] = str(self._event_context.get("batch_id") or "")
        payload["generation_event_errors"] = list(self._event_errors)
        return payload

    def _agnes_video_workers(self) -> int:
        raw = os.environ.get('MONTAGE_AGNES_VIDEO_CONCURRENCY')
        try:
            cap = int(raw) if raw is not None else 5
        except (TypeError, ValueError):
            cap = 5
        cap = max(1, min(cap, 5))
        rpm_workers = max(1, int(round(float(getattr(self, '_pace_video_s', 0) or 0))))
        return min(cap, rpm_workers)

    def _run_dynamic_video_batch(
        self,
        shots: list[dict[str, Any]],
        *,
        workers: int,
        still_by_id: dict[str, dict[str, Any]],
        emit_video: Callable[..., None],
        retryable: list[str],
        findings: list[dict[str, str]],
        state_lock: threading.RLock,
    ) -> set[str]:
        done: set[str] = set()
        if not shots or workers <= 0:
            return done
        pending = deque(shots)
        max_workers = min(workers, len(shots))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            in_flight: dict[Any, dict[str, Any]] = {}
            while pending or in_flight:
                while pending and len(in_flight) < workers:
                    shot = pending.popleft()
                    sid = str(shot.get('shot_id') or '')
                    still = still_by_id.get(sid) or {}
                    future = pool.submit(
                        emit_video,
                        shot,
                        str(still.get('path') or ''),
                        str(still.get('url') or ''),
                        last_path='',
                        last_url='',
                    )
                    in_flight[future] = shot
                if not in_flight:
                    break
                completed, _not_done = wait(
                    set(in_flight), return_when=FIRST_COMPLETED,
                )
                for future in completed:
                    shot = in_flight.pop(future)
                    sid = str(shot.get('shot_id') or '')
                    try:
                        future.result()
                    except Exception as exc:
                        with state_lock:
                            if sid and sid not in retryable:
                                retryable.append(sid)
                            findings.append({
                                'severity': 'warning',
                                'field': sid,
                                'message': f'并发视频任务异常: {exc}',
                                'proposed_fix': f'shot_runner retry_ids=[{sid}]',
                            })
                    done.add(sid)
        return done

    def _pace_wait(self) -> None:
        if _skip_pacing():
            return
        pacer = getattr(self, '_video_pacer', None)
        if pacer is not None:
            pacer.set_interval(float(getattr(self, '_pace_video_s', 0) or 0))
            pacer.wait()
            return
        gap = float(getattr(self, "_pace_video_s", 0) or 0)
        if gap <= 0 or _skip_pacing():
            return
        last = float(getattr(self, "_last_video_at", 0) or 0)
        if last:
            wait = gap - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_video_at = time.time()

    def _pace_image_wait(self, size: str) -> None:
        gap = 60.0 / max(agnes_image_rpm(size), 0.001)
        if gap <= 0 or _skip_pacing():
            return
        if not hasattr(self, '_image_pacers'):
            self._image_pacers = {}
        pacer = self._image_pacers
        if size not in pacer:
            pacer[size] = _IntervalPacer(gap)
        pacer[size].set_interval(gap)
        pacer[size].wait()
        return
        """图片侧 pacing：gap = 60 / 实际 RPM（按 size 档）。仅 agnes_image 生效。"""
        gap = 60.0 / max(agnes_image_rpm(size), 0.001)
        if gap <= 0 or _skip_pacing():
            return
        last = float(getattr(self, "_last_image_at", 0) or 0)
        if last:
            wait = gap - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_image_at = time.time()

    def _emit_generation_event(
        self,
        *,
        event: str,
        kind: str,
        attempt: int,
        meta: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        cache_params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        """Emit one append-only event; write failures must not stop media work."""
        emitter = self._event_emitter
        if emitter is None:
            return
        context = self._event_context
        if not context.get("run_id"):
            context = {
                "run_id": uuid.uuid4().hex,
                "batch_id": uuid.uuid4().hex,
                "stage": "",
            }
            self._event_context = context
        shot_id = str(
            (cache_params or {}).get("shot_id")
            or (payload or {}).get("shot_id")
            or context.get("shot_id")
            or ""
        )
        if not shot_id:
            return
        data = data if isinstance(data, dict) else {}
        raw = {
            "run_id": str(context.get("run_id") or ""),
            "batch_id": str(context.get("batch_id") or context.get("run_id") or ""),
            "shot_id": shot_id,
            "kind": str(kind),
            "event": str(event),
            "attempt": max(int(attempt or 1), 1),
            "provider": str((meta or {}).get("provider") or ""),
            "model": str((meta or {}).get("model") or ""),
            "provider_task_id": str((meta or {}).get("provider_task_id") or ""),
            "prompt_hash": str((cache_params or {}).get("prompt_hash") or ""),
            "ref_fingerprint": str((payload or {}).get("ref_fingerprint") or ""),
            "seed": str((payload or {}).get("seed") or ""),
            "cache_hit": bool((meta or {}).get("cache_hit")),
            "requested_seconds": (payload or {}).get("seconds"),
            "output_path": str(
                data.get("local_path") or data.get("output") or data.get("path") or ""
            ),
            "error_class": str((meta or {}).get("error_class") or ""),
            "error_message": str(error or ""),
        }
        try:
            with self._event_lock:
                emitter(raw)
        except Exception as exc:  # noqa: BLE001 - event loss is degraded, not fatal
            self._event_errors.append({
                "event": event,
                "shot_id": shot_id,
                "attempt": attempt,
                "error": str(exc),
            })

    def _generate_with_retry(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        output_path: str,
        cache,
        cache_params: dict[str, Any],
        expected_duration: float | None,
        skip_cache: bool = False,
        vlm_context: dict[str, Any] | None = None,
    ) -> ToolResult:
        last = ToolResult(success=False, error="未执行")
        generate = self._run_image if kind == "image" else self._run_video
        if kind == "image" and getattr(self, "_agnes_image_active", False):
            size = str(payload.get("size") or "2K")

            def pace() -> None:
                self._pace_image_wait(size)
        elif kind == "video":
            def pace() -> None:
                self._pace_wait()
        else:
            pace = None
        backoff = 2.0
        # seed 拦截点收窄（v8.2）：Agnes 视频且 payload 已带显式 seed（导演逐镜
        # 声明，经 overlay_plan_rework 透传）时锁种子，重试不换种；其余仍 1000+attempt。
        explicit_seed = ""
        if kind == "video" and getattr(self, "_agnes_video_active", False):
            explicit_seed = str(payload.get("seed") or "").strip()
        for attempt in range(MAX_ATTEMPTS):
            seed = int(explicit_seed) if explicit_seed.isdigit() else 1000 + attempt
            payload["seed"] = seed
            cache_params = dict(cache_params)
            cache_params["seed"] = seed
            last = self._cached_or_generate(
                cache=cache,
                params=cache_params,
                output_path=output_path,
                generate=generate,
                payload=payload,
                skip_cache=skip_cache,
                pace=pace,
            )
            if not last.success:
                meta = last.meta or {}
                decision = decide_retry(
                    state=str(meta.get("state") or "unknown"),
                    attempt=attempt + 1,
                    max_attempts=MAX_ATTEMPTS,
                    provider_task_id=str(meta.get("provider_task_id") or ""),
                    http_status=meta.get("http_status"),
                    error=last.error,
                )
                if decision.action == "poll":
                    self._emit_generation_event(
                        event="poll_timeout",
                        kind=kind,
                        attempt=attempt + 1,
                        meta=meta,
                        payload=payload,
                        cache_params=cache_params,
                        data=last.data if isinstance(last.data, dict) else {},
                        error=last.error,
                    )
                    task_id = str(meta.get("provider_task_id") or "")
                    recovery = self._run_video({
                        **payload,
                        "output_path": output_path,
                        "provider_task_id": task_id,
                        "recover": True,
                        "provider": meta.get("provider"),
                        "model": meta.get("model"),
                    })
                    if recovery.success:
                        self._emit_generation_event(
                            event="downloaded",
                            kind=kind,
                            attempt=attempt + 1,
                            meta=recovery.meta or {},
                            payload=payload,
                            cache_params=cache_params,
                            data=recovery.data if isinstance(recovery.data, dict) else {},
                        )
                        path = _media_path(recovery, output_path)
                        report = self._check_quality(
                            path, expected_duration=expected_duration
                        )
                        if not _critical_fail(report):
                            vlm = self._review_identity(path, vlm_context)
                            recovery.meta = dict(recovery.meta or {})
                            recovery.meta["quality"] = report
                            recovery.meta["vlm"] = vlm
                            recovery.meta["recovered_from_task_id"] = task_id
                            if not _critical_fail(vlm):
                                self._emit_generation_event(
                                    event="validated",
                                    kind=kind,
                                    attempt=attempt + 1,
                                    meta=recovery.meta or {},
                                    payload=payload,
                                    cache_params=cache_params,
                                    data=recovery.data if isinstance(recovery.data, dict) else {},
                                )
                                self._emit_generation_event(
                                    event="succeeded",
                                    kind=kind,
                                    attempt=attempt + 1,
                                    meta=recovery.meta or {},
                                    payload=payload,
                                    cache_params=cache_params,
                                    data=recovery.data if isinstance(recovery.data, dict) else {},
                                )
                                return recovery
                        last = ToolResult(
                            success=False,
                            error="quality/VLM gate failed after task recovery",
                            data=recovery.data,
                            meta={
                                **(recovery.meta or {}),
                                "quality": report,
                                "vlm": vlm,
                                "attempt": attempt + 1,
                            },
                        )
                        continue
                    last = recovery
                    if (last.meta or {}).get("recovery_action") in {"poll", "download"}:
                        self._emit_generation_event(
                            event="failed",
                            kind=kind,
                            attempt=attempt + 1,
                            meta=last.meta or {},
                            payload=payload,
                            cache_params=cache_params,
                            data=last.data if isinstance(last.data, dict) else {},
                            error=last.error,
                        )
                        break
                    continue
                if decision.action == "fail":
                    last.meta = dict(meta)
                    last.meta["retry_policy"] = {
                        "action": decision.action,
                        "state": decision.state,
                        "reason": decision.reason,
                        "error_class": decision.error_class,
                    }
                    self._emit_generation_event(
                        event="failed",
                        kind=kind,
                        attempt=attempt + 1,
                        meta=last.meta or {},
                        payload=payload,
                        cache_params=cache_params,
                        data=last.data if isinstance(last.data, dict) else {},
                        error=last.error,
                    )
                    break
                # 429 指数退避（对齐 _poll_video）；状态码经 ToolResult.meta 透传
                if int((last.meta or {}).get("http_status") or 0) == 429:
                    if not _skip_pacing():
                        time.sleep(decision.delay_seconds)
                    # 档位池（v8.2 P0-scheduler）：429 可能是本档日配额耗尽——
                    # 账面耗尽时确定性切池内下一档，RPM/键随 env 自动跟档；
                    # 账面未耗尽的 429 是瞬时限流，仍走退避重试。
                    if kind == "video" and getattr(self, "_agnes_video_active", False):
                        from montage.engine import scheduler as _sched

                        cur = agnes_access_tier()
                        if _sched.tier_exhausted(cur):
                            nxt = _sched.pick_next_tier(cur)
                            if nxt and _sched.activate_tier(nxt):
                                rpm_now = agnes_video_rpm()
                                self._pace_video_s = (
                                    (60.0 / rpm_now) if rpm_now > 0 else 0.0
                                )
                                self._video_pacer.set_interval(self._pace_video_s)
                                backoff = 2.0
                elif not _skip_pacing():
                    time.sleep(decision.delay_seconds)
                self._emit_generation_event(
                    event="retry_scheduled",
                    kind=kind,
                    attempt=attempt + 1,
                    meta=meta,
                    payload=payload,
                    cache_params=cache_params,
                    data=last.data if isinstance(last.data, dict) else {},
                    error=last.error,
                )
                continue
            if not (last.meta or {}).get("cache_hit"):
                self._emit_generation_event(
                    event="downloaded",
                    kind=kind,
                    attempt=attempt + 1,
                    meta=last.meta or {},
                    payload=payload,
                    cache_params=cache_params,
                    data=last.data if isinstance(last.data, dict) else {},
                )
            # Token Plan 记帐：必须在质量门禁 / VLM 之前——这两步失败会 continue
            # 再生成一次，那次也是真实配额消耗。缓存命中未真实调用 API，不计数。
            if not (last.meta or {}).get("cache_hit"):
                tier = agnes_access_tier()
                if kind == "image" and getattr(self, "_agnes_image_active", False):
                    agnes_add_images(tier, 1)
                elif kind == "video" and getattr(self, "_agnes_video_active", False):
                    # 按请求的 seconds 近似（非探测实际时长）
                    agnes_add_video_seconds(tier, float(payload.get("seconds") or 0))
            path = _media_path(last, output_path)
            report = self._check_quality(path, expected_duration=expected_duration)
            if _critical_fail(report):
                self._emit_generation_event(
                    event="failed",
                    kind=kind,
                    attempt=attempt + 1,
                    meta=last.meta or {},
                    payload=payload,
                    cache_params=cache_params,
                    data=last.data if isinstance(last.data, dict) else {},
                    error=last.error,
                )
                last = ToolResult(
                    success=False,
                    error="质量门禁 critical: " + "; ".join(
                        i.get("message", "") for i in report.get("issues") or [] if i.get("severity") == "critical"
                    ),
                    data=last.data,
                    meta={"quality": report, "attempt": attempt + 1},
                )
                continue
            self._emit_generation_event(
                event="validated",
                kind=kind,
                attempt=attempt + 1,
                meta=last.meta or {},
                payload=payload,
                cache_params=cache_params,
                data=last.data if isinstance(last.data, dict) else {},
            )
            vlm = self._review_identity(path, vlm_context)
            last.meta = dict(last.meta or {})
            last.meta["quality"] = report
            last.meta["vlm"] = vlm
            if _critical_fail(vlm):
                self._emit_generation_event(
                    event="failed",
                    kind=kind,
                    attempt=attempt + 1,
                    meta=last.meta or {},
                    payload=payload,
                    cache_params=cache_params,
                    data=last.data if isinstance(last.data, dict) else {},
                    error=last.error,
                )
                last = ToolResult(
                    success=False,
                    error="VLM critical: " + "; ".join(
                        i.get("message", "") for i in vlm.get("issues") or [] if i.get("severity") == "critical"
                    ),
                    data=last.data,
                    meta={"quality": report, "vlm": vlm, "attempt": attempt + 1},
                )
                continue
            self._emit_generation_event(
                event="succeeded",
                kind=kind,
                attempt=attempt + 1,
                meta=last.meta or {},
                payload=payload,
                cache_params=cache_params,
                data=last.data if isinstance(last.data, dict) else {},
            )
            return last
        return last

    def _review_identity(self, path: str, context: dict[str, Any] | None) -> dict[str, Any]:
        if not context:
            return {"ok": True, "skipped": True, "issues": []}
        if self._vlm_review:
            return self._vlm_review(path, context)
        from montage.tools.vlm_reviewer import review_media

        expected = context.get("expected") if isinstance(context.get("expected"), dict) else {}
        return review_media(
            media_path=path,
            expected=expected,
            mode=str(context.get("mode") or "first_frame"),
            portrait_path=str(context.get("portrait_path") or ""),
        )

    def _update_identity_anchors(
        self,
        *,
        memory: dict[str, Any] | None,
        candidates: list[dict[str, Any]],
        refs: list[dict[str, Any]],
        scene_plan: dict[str, Any] | None,
        findings: list[dict[str, Any]],
    ) -> None:
        """定妆候选 VLM 过检 → canonical 锚保守更新（cast/shot 两阶段共用）。

        只有 ``init``/``adopt`` 才在 manifest ref 上打 ``canonical`` 标记（旧锚
        同时摘标记），保证 ``_ref_index`` 的 canonical 优先真有唯一真源。
        """
        if memory is None or not candidates:
            return
        registry_map = _registry_map(scene_plan)
        for cand in candidates:
            cid = str(cand.get("character_id") or "")
            fid = str(cand.get("form_id") or "")
            key = identity_key(cid, fid)
            char = _char_for_prompt(registry_map.get(cid) or {"id": cid})
            if fid:
                form = next(
                    (
                        f for f in character_forms(registry_map.get(cid) or {})
                        if str(f.get("id") or "") == fid
                    ),
                    None,
                )
                if isinstance(form, dict):
                    char = blend_character_form(char, form)
            review = self._review_identity(str(cand.get("path") or ""), {
                "expected": {
                    "appearance": str(char.get("appearance") or ""),
                    "outfit": str(char.get("outfit_anchor") or char.get("outfit") or ""),
                },
                "portrait_path": "",
                "mode": "first_frame",
            })
            verdict = evaluate_candidate(
                memory, key,
                review=review,
                ref_id=str(cand.get("ref_id") or ""),
                path=str(cand.get("path") or ""),
                url=str(cand.get("url") or ""),
            )
            action = str(verdict.get("action") or "")
            if action in ("init", "adopt"):
                for ref in refs:
                    if str(ref.get("kind") or "") != "portrait":
                        continue
                    if str(ref.get("character_id") or "") != cid:
                        continue
                    if str(ref.get("form_id") or "") != fid:
                        continue
                    if str(ref.get("id") or "") == str(cand.get("ref_id") or ""):
                        ref["canonical"] = True
                        ref["identity_key"] = key
                    else:
                        ref.pop("canonical", None)
                mark_retaken(memory, key, ref_id=str(cand.get("ref_id") or ""))
            findings.append({
                "severity": "info",
                "field": f"identity/{identity_subject(cid, fid)}",
                "message": f"定妆身份锚判定 {action or 'hold'}：{verdict.get('reason')}",
                "proposed_fix": "漂移反复则改显式 agnes_mode=keyframe（首帧即身份锚）",
            })
