"""shot_runner — 定妆照 → 首帧 → 质检 → 图生视频 编排器。

- 默认 dry_run=true：按镜调用供应商 estimate_cost 求和，超 budget_ceiling_usd 则停，不打 API。
- dry_run=false：写磁盘（媒体 + asset_manifest.reference_assets + 把嵌套 shots lift 成 shot_prompts）。
- 单镜失败不中断，返回 retryable_ids。默认串行。
- 复用 generation_cache / asset_quality_gate；成片后抽尾帧给下一镜 I2V 首帧（不填本镜 last_frame）。
"""

from __future__ import annotations

import os
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
from montage.engine.policy import load_loop_policy, resolve_allowed_providers
from montage.engine.rework import pick_rework_mode, rework_prompt
from montage.providers.capabilities import (
    apply_image_refs,
    apply_video_frames,
    image_caps,
    snap_duration_seconds,
    video_caps,
    video_surface,
    VIDEO_META,
    VIDEO_SURFACES,
)
from montage.providers.prompt_adapter import adapt_visual_prompt
from montage.providers.selectors import ImageSelector, VideoSelector
from montage.providers.video_prompts import prompt_profile
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

from montage.tools._shot_constants import (
    MAX_ATTEMPTS,
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
)
from montage.tools._shot_refs import (
    _http_video_urls,
    _http_still_urls,
    _identity_http_refs,
    _agnes_flash_images,
    _vlm_expected,
    _needs_agnes_refine,
    _media_item,
    _character_ids,
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
    _script_prop_map,
    collect_prop_ids,
    resolve_shot_refs,
    _prop_prompt,
    _media_path,
    _media_url,
    _skip_pacing,
    _critical_fail,
)

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
    ) -> None:
        self._image_execute = image_execute
        self._video_execute = video_execute
        self._image_estimate = image_estimate
        self._video_estimate = video_estimate
        self._quality_check = quality_check
        self._extract_last_frame = extract_last_frame
        self._vlm_review = vlm_review
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
        rpm = float((VIDEO_META.get(vid_name) or {}).get("rpm") or 0)
        self._pace_video_s = (60.0 / rpm) if (agnes_loop and rpm > 0) else 0.0
        self._last_video_at = 0.0

        portraits = _portrait_index(manifest)
        registry = _registry_map(scene_plan)
        existing_props = _prop_index(manifest)
        record_ledger = inputs.get("record_ledger")
        if record_ledger is None:
            record_ledger = True
        record_ledger = bool(record_ledger)
        format_card = store.read("format_card") if store else None
        skip_portraits = _spoken_skip_portraits(scene_plan, script, format_card)

        used_portrait_ids: list[str] = []
        for shot in timeline_shots:
            for cid in _character_ids(shot, scene_plan):
                if cid not in used_portrait_ids and cid in registry:
                    used_portrait_ids.append(cid)
        used_prop_ids = collect_prop_ids(timeline_shots, script)
        if project_dir:
            if not isinstance(manifest, dict):
                manifest = {"items": [], "reference_assets": []}
            else:
                manifest.setdefault("items", [])
                manifest.setdefault("reference_assets", [])
            copy_sibling_still_refs(
                project_dir,
                portrait_ids=used_portrait_ids,
                prop_ids=used_prop_ids,
                retry_ids=retry_ids,
                manifest=manifest,
            )
            portraits = _portrait_index(manifest)
            existing_props = _prop_index(manifest)

        needed_portraits: list[str] = []
        need_http = _agnes_cast_needs_url(policy)
        for shot in shots:
            for cid in _character_ids(shot, scene_plan):
                if cid not in needed_portraits and cid in registry:
                    if _retry_covers(retry_ids, portrait_id=cid) or not _ref_ready(
                        project_dir, portraits.get(cid), require_url=need_http,
                    ):
                        needed_portraits.append(cid)
        for token in retry_ids:
            cid = ""
            text = str(token)
            if text.startswith("portrait/"):
                cid = text.split("/", 1)[1]
            elif text in registry:
                cid = text
            if (
                cid
                and cid in registry
                and cid not in needed_portraits
                and (
                    _retry_covers(retry_ids, portrait_id=cid)
                    or not _ref_ready(project_dir, portraits.get(cid), require_url=need_http)
                )
            ):
                needed_portraits.append(cid)
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
        for cid in needed_portraits:
            jobs.append({
                "kind": "portrait",
                "subject": f"portrait/{cid}",
                "character_id": cid,
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
                portraits=portraits,
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
        findings: list[dict[str, str]] = []
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
                n_vid = sum(1 for j in jobs if j.get("kind") == "video")
                payload["pacing_note"] = (
                    f"Agnes 视频默认 1 RPM，{n_vid} 段至少约 {n_vid} 分钟"
                    "（pytest / MONTAGE_SKIP_PACING=1 关闭等待）"
                )
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
            needed_portraits=needed_portraits,
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
            char = None
            for item in bible.get("characters") or []:
                if isinstance(item, dict) and str(item.get("id") or "") == cid:
                    char = _char_for_prompt(item)
                    break
            char = char or _char_for_prompt({"id": cid})
            if kling_loop and kind == "portrait":
                prompt = build_kling_look_sheet_prompt(char)
                if not prompt:
                    return "", {
                        "severity": "critical",
                        "field": f"{kind}/{cid}",
                        "message": "拼板提示词为空",
                        "proposed_fix": "补 characters[].appearance",
                    }
                return _append_note(prompt, str(char.get("cast_note") or "")), None
            try:
                built = builder.execute({
                    "purpose": kind,
                    "character": char,
                    "project_dir": project_dir,
                })
            except ValueError as exc:
                return "", {
                    "severity": "critical",
                    "field": f"{kind}/{cid}",
                    "message": str(exc),
                    "proposed_fix": "补 characters[].appearance",
                }
            prompt = ""
            if built.success and isinstance(built.data, dict):
                prompt = str(built.data.get("first_frame_prompt") or "")
            if not prompt:
                return "", {
                    "severity": "critical",
                    "field": f"{kind}/{cid}",
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

        portraits = _portrait_index(manifest)
        turnarounds = _turnaround_index(manifest)
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
                out_path = str(img_dir / (
                    f"look_sheet_{cid}.png" if kling_loop else f"portrait_{cid}.png"
                ))
                id_key, id_val = "character_id", cid
                ref_id = f"portrait_{cid}"
            elif kind == "turnaround":
                cid = str(job.get("character_id") or "")
                out_path = str(img_dir / f"turnaround_{cid}.png")
                id_key, id_val = "character_id", cid
                ref_id = f"turnaround_{cid}"
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
                cache_params={"prompt": prompt, "kind": kind, "subject": subject, "image_tool": img_name},
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
            if kind == "turnaround":
                ref["views"] = ["front", "side", "back", "three_quarter"]
            _upsert_ref(refs, ref, kind=kind, id_key=id_key, id_val=id_val)
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

        manifest_out = {"items": items, "reference_assets": refs}
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

    def _seedream_aspect(self, policy: dict[str, Any]) -> str:
        """Seedream 首帧/定妆画幅取值链：env > output_profile > 默认 16:9。"""
        env_val = str(os.environ.get("SEEDREAM_ASPECT") or "").strip()
        if env_val:
            return env_val
        profile = str(policy.get("output_profile") or "").strip().lower()
        if profile.endswith("vertical"):
            return "9:16"
        return "16:9"

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
        needed_portraits: list[str],
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
        continuity_state = load_or_seed_continuity(project_dir) if project_dir else {}
        skipped_first = skipped_first or set()
        skipped_video = skipped_video or set()
        retry_ids = retry_ids or set()
        force_ids = force_ids or set()
        video_ids = video_ids or set()
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
        rpm = float((VIDEO_META.get(vid_name) or {}).get("rpm") or 0)
        self._pace_video_s = (60.0 / rpm) if (agnes_loop and rpm > 0) else 0.0
        self._last_video_at = float(getattr(self, "_last_video_at", 0) or 0)

        def book(category: str, subject: str, item: str, usd: float) -> str:
            if ledger is None or usd <= 0:
                return ""
            eid, _ok = ledger.estimate_checked(category, subject, item, usd)
            return eid

        def settle(eid: str, actual: float) -> None:
            if ledger is None or not eid:
                return
            ledger.settle(eid, actual)

        def record_vlm(shot_id: str, meta: dict[str, Any] | None) -> None:
            vlm = meta.get("vlm") if isinstance(meta, dict) else None
            if not isinstance(vlm, dict):
                return
            vlm_rows.append({
                "shot_id": shot_id,
                "ok": bool(vlm.get("ok", True)),
                "skipped": bool(vlm.get("skipped")),
                "issues": list(vlm.get("issues") or []),
            })
            if vlm.get("skipped") and not any(f.get("field") == "vlm" for f in findings):
                findings.append({
                    "severity": "warning",
                    "field": "vlm",
                    "message": "vlm_skipped：未配置 DASHSCOPE_API_KEY",
                    "proposed_fix": "配置 DASHSCOPE_API_KEY 后才做语义质检",
                })

        # -- portraits -------------------------------------------------------
        for cid in needed_portraits:
            char = registry.get(cid) or {"id": cid}
            out_path = str(img_dir / (f"look_sheet_{cid}.png" if kling_loop else f"portrait_{cid}.png"))
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
                    "field": f"portrait/{cid}",
                    "message": str(exc),
                    "proposed_fix": "补 character_registry.appearance",
                })
                retryable.append(f"portrait/{cid}")
                continue
            if not prompt:
                findings.append({
                    "severity": "critical" if not skip_portraits else "warning",
                    "field": f"portrait/{cid}",
                    "message": (built.error if built else "") or "定妆照提示词为空",
                    "proposed_fix": "检查人物卡 appearance",
                })
                retryable.append(f"portrait/{cid}")
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
                subject = f"portrait/{cid}"
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
                    results.append({
                        "id": f"portrait_{cid}",
                        "ok": True,
                        "path": path,
                        "cached": bool((result.meta or {}).get("cache_hit")),
                    })
                    sheet_ok = True
                    break
                if not sheet_ok and subject not in retryable:
                    retryable.append(subject)
                continue
            eid = book("image_generation", f"portrait/{cid}", payload["image_tool"], usd)
            result = self._generate_with_retry(
                kind="image",
                payload=img_payload,
                output_path=out_path,
                cache=cache,
                cache_params={"prompt": prompt, "kind": "portrait", "character_id": cid, "image_tool": img_name},
                expected_duration=None,
            )
            settle(eid, result.cost_usd if result.success else 0.0)
            if not result.success:
                retryable.append(f"portrait/{cid}")
                findings.append({
                    "severity": "critical" if not skip_portraits else "warning",
                    "field": f"portrait/{cid}",
                    "message": result.error or "定妆照生成失败",
                    "proposed_fix": "检查密钥或重跑 retry_ids",
                })
                continue
            path = _media_path(result, out_path)
            url = _media_url(result)
            ref = {
                "id": f"portrait_{cid}",
                "kind": "portrait",
                "character_id": cid,
                "path": path,
                "url": url,
                "provider": getattr(img_tool, "provider", "") or "volcengine",
            }
            portraits[cid] = ref
            refs.append(ref)
            row = {
                "id": ref["id"], "kind": "image", "path": path,
                "provider": ref["provider"],
            }
            if url:
                row["url"] = url
            items.append(row)
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
                cache_params={"prompt": prompt, "kind": "prop", "prop_id": pid, "image_tool": img_name},
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
            refs.append(ref)
            row = {
                "id": ref["id"], "kind": "image", "path": path,
                "provider": ref["provider"],
            }
            if url:
                row["url"] = url
            items.append(row)
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
            if agnes_loop:
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
                    "first_frame": bool(
                        vid_payload.get("image_url") or vid_payload.get("first_frame_url")
                    ),
                    "last_frame": bool(vid_payload.get("last_frame_url")),
                    "element_id": ",".join(
                        str(r.get("element_id") or "")
                        for r in (vid_payload.get("refs") or [])
                        if isinstance(r, dict) and r.get("element_id")
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

            for idx, chunk_sec in enumerate(chunks):
                dest = final_path if len(chunks) == 1 else str(vid_dir / f"{shot_id}_p{idx + 1}.mp4")
                if rework_mode == "splice" and rework.get("segment"):
                    dest = str(vid_dir / f"{shot_id}_retake.mp4")
                    chunk_sec = float(rework["segment"]["duration_seconds"])
                vid_payload: dict[str, Any] = {
                    "prompt": prompt0,
                    "output_path": dest,
                    "project_dir": project_dir,
                    "seconds": chunk_sec,
                    "duration": str(int(chunk_sec)),
                    "prompt_fallback": prompt_fallback,
                }
                if api_id:
                    vid_payload["api_id"] = api_id
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
                    vid_payload["prompt"] = f"{prompt0} {_REFINE_HINT}"
                    ident = _identity_http_refs(shot, refs, script, scene_plan)
                    imgs = _agnes_flash_images(ident)
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
                    )
                    if api_id == "kling_omni_30" and rework_mode not in ("edit", "extend", "feature"):
                        vid_payload["refs"] = identity
                    if api_id in _SEEDANCE_APIS and not use_first_url and not use_last_url:
                        vid_payload["reference_urls"] = [r["url"] for r in identity]
                    v_notes: list[str] = []
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
                    )
                    if v25:
                        images = _agnes_flash_images(identity)
                        if images:
                            vid_payload["images"] = images
                            vid_payload["mode"] = "reference"
                            for key in (
                                "image_url", "image_urls", "first_frame",
                                "last_frame", "last_frame_url", "extra_body",
                            ):
                                vid_payload.pop(key, None)
                        else:
                            vid_payload["mode"] = "text"
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
                    retryable.append(shot_id)
                    findings.append({
                        "severity": "warning",
                        "field": subject,
                        "message": vid_result.error or "视频生成失败",
                        "proposed_fix": f"shot_runner retry_ids=[{shot_id}]",
                    })
                    record_vlm(shot_id, vid_result.meta)
                    return
                segment_paths.append(_media_path(vid_result, dest))
                segment_url = _media_url(vid_result)

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
            items.append(_media_item(
                item_id=f"{shot_id}_video",
                kind="video",
                path=video_path,
                scene_id=scene_id,
                shot_id=shot_id,
                provider=getattr(vid_tool, "provider", "") or "",
                url=video_url,
            ))
            results.append({"id": f"{shot_id}_video", "ok": True, "path": video_path})
            record_vlm(shot_id, vid_result.meta)
            continuity_state = update_continuity(
                continuity_state, shot,
                scene_plan=scene_plan, registry=registry, script=script,
            )
            if not agnes_loop:
                tail = self._extract_tail(video_path, str(img_dir / f"{shot_id}_tail.png"))
                if tail:
                    shot["_tail_path"] = tail

        prev_tail = ""
        prev_location = ""

        def _need_portrait(shot: dict[str, Any]) -> bool:
            if skip_portraits:
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
                missing = [
                    cid for cid in _character_ids(shot, scene_plan)
                    if cid in registry and cid not in portraits
                ]
                if missing:
                    _block_i2v(shot, f"无定妆禁止 I2V（缺 portrait: {','.join(missing)}）")
                    return False
                if agnes_loop and _agnes_is_v25():
                    turns = _turnaround_index({"reference_assets": refs})
                    for cid in _character_ids(shot, scene_plan):
                        if cid not in registry:
                            continue
                        hit = turns.get(cid) or portraits.get(cid) or {}
                        if not str(hit.get("url") or "").startswith("http"):
                            _block_i2v(
                                shot,
                                f"缺定妆/四视图公网 URL（{cid}），禁止降级文生",
                            )
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
                passthrough = bool(prompt_profile(str(route["api_id"] or "")).get("passthrough"))
                adapted = adapt_visual_prompt(
                    str(route["api_id"] or ""),
                    builder_data,
                    refs=_adapter_refs(shot, refs),
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
                    img_payload["ratio"] = "16:9"
                if img_name == "kling_image":
                    img_payload["result_type"] = "single"
                    img_payload["resolution"] = "2k"
                if img_name == "seedream_image":
                    img_payload["aspect_ratio"] = self._seedream_aspect(policy)
                notes = apply_image_refs(
                    img_payload,
                    resolve_shot_refs(
                        shot, {"reference_assets": refs}, script, scene_plan,
                    ),
                    i_caps,
                )
                for note in notes:
                    findings.append({
                        "severity": "warning", "field": subject, "message": note,
                        "proposed_fix": "换支持参考图的供应商或补 URL",
                    })
                usd = self._estimate_job({"kind": "first_frame"}, img_tool, vid_tool, project_dir)
                eid = book("image_generation", subject, payload["image_tool"], usd)
                img_result = self._generate_with_retry(
                    kind="image",
                    payload=img_payload,
                    output_path=first_path,
                    cache=cache,
                    cache_params={"prompt": first_prompt, "kind": "first_frame", "shot_id": shot_id, "image_tool": img_name},
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
                    record_vlm(shot_id, img_result.meta)
                    continue
                first_path = _media_path(img_result, first_path)
                first_url = _media_url(img_result)
                record_vlm(shot_id, img_result.meta)
                continuity_state = update_continuity(
                    continuity_state, shot,
                    scene_plan=scene_plan, registry=registry, script=script,
                )
                still_by_id[shot_id] = {
                    "path": first_path, "url": first_url,
                    "scene_id": scene_id, "shot_id": shot_id,
                }
                items.append(_media_item(
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
            for shot in shots:
                if str(shot.get("shot_kind") or "video") == "image":
                    continue
                shot_id = str(shot.get("shot_id") or "")
                if shot_id in skipped_video:
                    continue
                if shot.get("_prompt_invalid"):
                    continue
                if not _may_emit_i2v(shot):
                    continue
                still = still_by_id.get(shot_id) or {}
                first_url = str(still.get("url") or "")
                need_url = _need_portrait(shot)
                if _agnes_is_v25():
                    if need_url:
                        identity = _identity_http_refs(shot, refs, script, scene_plan)
                        if not any(
                            str(r.get("kind") or "") in ("portrait", "turnaround")
                            and str(r.get("url") or "").startswith("http")
                            for r in identity
                        ):
                            _block_i2v(shot, "缺定妆/四视图公网 URL，禁止降级文生")
                            continue
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

        lifted = lift_shot_prompts(timeline_shots or shots)
        manifest_out = {"items": items, "reference_assets": refs}
        if store:
            store.write("shot_prompts", lifted)
            if not prompt_only:
                store.write("asset_manifest", manifest_out, schema=None)
        if project_dir and not prompt_only:
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

    def _pace_wait(self) -> None:
        gap = float(getattr(self, "_pace_video_s", 0) or 0)
        if gap <= 0 or _skip_pacing():
            return
        last = float(getattr(self, "_last_video_at", 0) or 0)
        if last:
            wait = gap - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_video_at = time.time()

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
        for attempt in range(MAX_ATTEMPTS):
            if kind == "video":
                self._pace_wait()
            seed = 1000 + attempt
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
            )
            if not last.success:
                continue
            path = _media_path(last, output_path)
            report = self._check_quality(path, expected_duration=expected_duration)
            if _critical_fail(report):
                last = ToolResult(
                    success=False,
                    error="质量门禁 critical: " + "; ".join(
                        i.get("message", "") for i in report.get("issues") or [] if i.get("severity") == "critical"
                    ),
                    data=last.data,
                    meta={"quality": report, "attempt": attempt + 1},
                )
                continue
            vlm = self._review_identity(path, vlm_context)
            last.meta = dict(last.meta or {})
            last.meta["quality"] = report
            last.meta["vlm"] = vlm
            if _critical_fail(vlm):
                last = ToolResult(
                    success=False,
                    error="VLM critical: " + "; ".join(
                        i.get("message", "") for i in vlm.get("issues") or [] if i.get("severity") == "critical"
                    ),
                    data=last.data,
                    meta={"quality": report, "vlm": vlm, "attempt": attempt + 1},
                )
                continue
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
