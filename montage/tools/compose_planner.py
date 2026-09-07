"""compose_planner — 每镜合成方案 → edit_decisions（assemble 仍只读后者）。

读取 playbook.motion + style pack，调用 edit_advisor 填转场。
默认写出 edit_decisions；compose_plan 可选落盘，但**不进** compose produces。
render_kind 只写契约（默认 ai_clip），本轮不实现第二运行时。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from montage.engine.artifacts import ArtifactStore
from montage.engine.policy import load_pipeline_settings, keep_embedded_audio, shot_keeps_embedded_audio
from montage.playbooks import get_playbook
from montage.style_packs import STYLE_PACKS, get_style_pack
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus
from montage.tools.edit_advisor import suggest_transitions
from montage.tools.voice_director import shots_with_timeline
from lib.shot_prompt_builder import dialogue_line_role, dialogue_line_text

_STILL_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def is_still_image(path: str) -> bool:
    return Path(path).suffix.lower() in _STILL_EXTS


def resolve_lut_file(lut_id: str) -> str:
    """catalog id → .cube 路径；找不到则原样返回。"""
    from montage.compose.ffmpeg_engine import resolve_lut_file as _resolve

    found = _resolve(lut_id)
    return str(found) if found is not None else str(lut_id or "")


def realize_ken_burns(
    plan: dict[str, Any],
    decisions: dict[str, Any],
    *,
    out_dir: Path | str,
    ken_burns_fn: Callable[..., Any],
    findings: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """仅对静图 clip 跑 ken_burns，原地改 cuts[].clip_path。"""
    notes = findings if findings is not None else []
    dest_root = Path(out_dir)
    dest_root.mkdir(parents=True, exist_ok=True)
    shots = {
        str(s.get("shot_id") or ""): s
        for s in (plan.get("shots") or [])
        if isinstance(s, dict)
    }
    cuts = [dict(c) for c in (decisions.get("cuts") or []) if isinstance(c, dict)]
    realized: list[str] = []
    for cut in cuts:
        sid = str(cut.get("shot_id") or cut.get("to_scene") or "")
        shot = shots.get(sid) or {}
        effects = [e for e in (shot.get("effects") or []) if isinstance(e, dict)]
        wants = any(e.get("operation") == "ken_burns" for e in effects)
        clip = str(cut.get("clip_path") or shot.get("clip_path") or "")
        if not wants or not clip or not is_still_image(clip):
            continue
        src = Path(clip)
        if not src.exists():
            notes.append({
                "severity": "warning",
                "field": sid or clip,
                "message": f"ken_burns 源图不存在: {src}",
                "proposed_fix": "先跑 shot_runner 或填 clip_path",
            })
            continue
        zoom = "in"
        pan = "center"
        for e in effects:
            if e.get("operation") == "ken_burns":
                zoom = str(e.get("zoom") or "in")
                pan = str(e.get("pan") or "center")
        dest = dest_root / f"{sid or src.stem}_kb.mp4"
        dur = float(shot.get("duration_seconds") or 5) or 5.0
        try:
            ken_burns_fn(src, dest, dur, zoom=zoom, pan=pan)
        except TypeError:
            ken_burns_fn(src, dest, dur)
        except Exception as exc:  # noqa: BLE001
            notes.append({
                "severity": "warning",
                "field": sid,
                "message": f"ken_burns 失败: {exc}",
                "proposed_fix": "安装 ffmpeg，或确认源图可读",
            })
            continue
        cut["clip_path"] = str(dest)
        if shot:
            shot["clip_path"] = str(dest)
        realized.append(sid)
    out_decisions = dict(decisions)
    out_decisions["cuts"] = cuts
    return out_decisions, plan, realized


RENDER_KINDS = (
    "ai_clip",
    "title_card",
    "kinetic_caption",
    "manga_panel",
    "chart",
)
_GRAPHIC = frozenset(k for k in RENDER_KINDS if k != "ai_clip")


def _playbook_dict(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict) and raw.get("id"):
        return raw
    if isinstance(raw, str) and raw.strip():
        return get_playbook(raw.strip())
    return None


def pack_for_playbook(playbook_id: str) -> dict[str, Any] | None:
    if not playbook_id:
        return None
    for pack in STYLE_PACKS.values():
        if pack.get("bind_playbook") == playbook_id:
            return dict(pack)
    return None


def clip_path_for_shot(shot_id: str, scene_id: str, manifest: dict[str, Any] | None) -> str:
    """asset_manifest.items[] → 该镜 clip 路径（video 优先，其次非 portrait 静图）。"""
    return _clip_path(shot_id, scene_id, manifest)


def _clip_path(shot_id: str, scene_id: str, manifest: dict[str, Any] | None) -> str:
    items = (manifest or {}).get("items") or []
    videos = [
        i for i in items
        if isinstance(i, dict) and i.get("kind") == "video" and str(i.get("shot_id") or "") == shot_id
    ]
    if videos:
        return str(videos[-1].get("path") or "")
    images = [
        i for i in items
        if isinstance(i, dict) and i.get("kind") == "image"
        and str(i.get("shot_id") or "") == shot_id
        and "portrait" not in str(i.get("id") or "")
    ]
    if images:
        return str(images[-1].get("path") or "")
    if scene_id:
        scene_vids = [
            i for i in items
            if isinstance(i, dict) and i.get("kind") == "video" and str(i.get("scene_id") or "") == scene_id
        ]
        if len(scene_vids) == 1:
            return str(scene_vids[0].get("path") or "")
    return ""


def _normalize_kind(raw: Any) -> str:
    kind = str(raw or "ai_clip").strip() or "ai_clip"
    if kind not in RENDER_KINDS:
        return "ai_clip"
    return kind


def compile_compose_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """compose_plan → edit_decisions（assemble 的唯一运行时输入）。"""
    shots = [s for s in (plan.get("shots") or []) if isinstance(s, dict)]
    cuts: list[dict[str, Any]] = []
    prev_id = ""
    for shot in shots:
        sid = str(shot.get("shot_id") or shot.get("scene_id") or "")
        tname = str(shot.get("transition") or "cut")
        cut: dict[str, Any] = {
            "from_scene": prev_id or sid,
            "to_scene": sid,
            "clip_path": str(shot.get("clip_path") or ""),
            "shot_id": sid,
            "scene_id": str(shot.get("scene_id") or ""),
            "transition": tname,
            "transition_in": tname,
            "transition_duration": float(shot.get("transition_duration") or 0),
        }
        gap = shot.get("negative_gap_seconds")
        if gap not in (None, ""):
            cut["negative_gap_seconds"] = float(gap)
        cuts.append(cut)
        prev_id = sid
    return {
        "cuts": cuts,
        "render_runtime": "ffmpeg",
        "allow_non_cut": bool(plan.get("allow_non_cut")),
    }


def build_compose_plan(
    scene_plan: dict[str, Any] | None,
    *,
    shot_prompts: dict[str, Any] | None = None,
    asset_manifest: dict[str, Any] | None = None,
    soundtrack: dict[str, Any] | None = None,
    playbook: dict[str, Any] | None = None,
    style_pack: dict[str, Any] | None = None,
    edit_style: str = "cinematic",
    transition_policy: str = "",
    keep_audio: bool = False,
    mix_source_audio: bool | None = None,
    ducking: bool | None = None,
) -> dict[str, Any]:
    """纯函数：镜头骨架 + 顾问 + 风格包 → {compose_plan, findings}。"""
    findings: list[dict[str, str]] = []
    timed = shots_with_timeline(scene_plan)
    if not timed and shot_prompts:
        timed = [s for s in (shot_prompts.get("shots") or []) if isinstance(s, dict)]
    if not timed:
        findings.append({
            "severity": "warning",
            "field": "shots",
            "message": "没有镜头可编进 compose_plan",
            "proposed_fix": "先跑 script_to_scene_plan 或提供 shot_prompts",
        })

    pb = playbook if isinstance(playbook, dict) else None
    pack = style_pack if isinstance(style_pack, dict) else None
    lut = str((pack or {}).get("lut") or "")
    pack_tdur = float(((pack or {}).get("pacing") or {}).get("transition_duration") or 0)
    cut_only = transition_policy == "cut_only"
    style = "documentary" if edit_style == "documentary" or cut_only else "cinematic"

    scenes_by_id = {
        str(s.get("id") or ""): s
        for s in ((scene_plan or {}).get("scenes") or [])
        if isinstance(s, dict)
    }
    pseudo: list[dict[str, Any]] = []
    for shot in timed:
        parent = scenes_by_id.get(str(shot.get("scene_id") or "")) or {}
        pseudo.append({
            "id": str(shot.get("shot_id") or shot.get("scene_id") or ""),
            "narrative_role": parent.get("narrative_role") or "",
            "hero_moment": bool(shot.get("hero_moment") or parent.get("hero_moment")),
            "shot_language": shot.get("shot_language") or parent.get("shot_language") or {},
            "type": parent.get("type") or "",
        })
    junctions = {
        str(j.get("to_scene") or ""): j
        for j in (suggest_transitions(pseudo, style=style) if len(pseudo) >= 2 else [])
    }

    events = list((soundtrack or {}).get("events") or []) if isinstance(soundtrack, dict) else []
    if keep_audio:
        events = []
    plan_shots: list[dict[str, Any]] = []
    for i, shot in enumerate(timed):
        shot_id = str(shot.get("shot_id") or f"sh{i + 1:02d}")
        scene_id = str(shot.get("scene_id") or "")
        kind = _normalize_kind(shot.get("render_kind"))
        if shot.get("render_kind") and str(shot.get("render_kind")) not in RENDER_KINDS:
            findings.append({
                "severity": "warning",
                "field": shot_id,
                "message": f"未知 render_kind={shot.get('render_kind')!r}，按 ai_clip",
                "proposed_fix": f"使用 {', '.join(RENDER_KINDS)}",
            })
        if kind in _GRAPHIC:
            findings.append({
                "severity": "warning",
                "field": shot_id,
                "message": f"render_kind={kind} 本轮不渲染图形镜，按 ai_clip 进 FFmpeg",
                "proposed_fix": "P6 再按 render_kind 路由覆盖层",
            })
            kind = "ai_clip"
        clip = _clip_path(shot_id, scene_id, asset_manifest)
        if not clip:
            findings.append({
                "severity": "warning",
                "field": shot_id,
                "message": "缺少 clip_path（asset_manifest 无对应 video/image）",
                "proposed_fix": "先跑 shot_runner，或手填 clip_path",
            })
        junction = junctions.get(shot_id) or {}
        transition = "cut" if (i == 0 or cut_only) else str(junction.get("suggested_transition") or "cut")
        gap = 0.0 if (i == 0 or cut_only) else float(junction.get("negative_gap_seconds") or 0)
        tdur = 0.0
        if transition != "cut":
            tdur = gap if gap > 0 else pack_tdur
        shot_kind = str(shot.get("shot_kind") or "video")
        effects: list[dict[str, Any]] = []
        if shot_kind == "image":
            effects.append({"operation": "ken_burns", "zoom": "in"})
            findings.append({
                "severity": "warning",
                "field": shot_id,
                "message": "空镜是静图，assemble 前需 ken_burns 转视频",
                "proposed_fix": "ffmpeg_compose operation=ken_burns",
            })
        cues: list[dict[str, Any]] = []
        ap = shot.get("audio_prompt") if isinstance(shot.get("audio_prompt"), dict) else {}
        start = float(shot.get("start_seconds") or 0)
        end = float(shot.get("end_seconds") or start + float(shot.get("duration_seconds") or 0))
        for dlg in ap.get("dialogue") or []:
            if not isinstance(dlg, dict):
                continue
            text = dialogue_line_text(dlg)
            if not text:
                continue
            cues.append({
                "text": text,
                "start_seconds": start,
                "end_seconds": end,
                "speaker_id": dialogue_line_role(dlg),
            })
        audio_events = [
            e for e in events
            if isinstance(e, dict) and (
                str(e.get("shot_id") or "") == shot_id
                or (e.get("kind") == "bgm" and str(e.get("scene_id") or "") == scene_id and not e.get("shot_id"))
            )
        ]
        plan_shots.append({
            "shot_id": shot_id,
            "scene_id": scene_id,
            "clip_path": clip,
            "duration_seconds": float(shot.get("duration_seconds") or 0),
            "transition": transition,
            "transition_duration": tdur,
            "negative_gap_seconds": gap,
            "lut": lut,
            "subtitle_cues": cues,
            "audio_events": audio_events,
            "effects": effects,
            "render_kind": kind,
            "hero_moment": bool(shot.get("hero_moment")),
        })

    plan = {
        "version": "1",
        "render_runtime": "ffmpeg",
        "playbook": str((pb or {}).get("id") or ""),
        "style_pack": str((pack or {}).get("id") or ""),
        "lut": lut,
        "allow_non_cut": (not cut_only) and style == "cinematic",
        "shots": plan_shots,
        "assemble_hints": {
            "ducking": (not keep_audio) if ducking is None else ducking,
            "loudnorm": True,
            "lut": lut,
            "mix_source_audio": keep_audio if mix_source_audio is None else mix_source_audio,
        },
    }
    return {"compose_plan": plan, "findings": findings}


class ComposePlanner(BaseTool):
    name = "compose_planner"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "properties": {
            "scene_plan": {"type": "object"},
            "shot_prompts": {"type": "object"},
            "asset_manifest": {"type": "object"},
            "soundtrack": {"type": "object"},
            "playbook": {"description": "playbook id 或 dict"},
            "style_pack": {"description": "style pack id 或 dict"},
            "project_dir": {"type": "string"},
            "overwrite": {"type": "boolean", "default": False},
            "write_plan": {
                "type": "boolean",
                "default": True,
                "description": "有 project_dir 时是否写入 artifacts/compose_plan.json（不进 produces）",
            },
            "realize": {
                "type": "boolean",
                "default": False,
                "description": "对静图 clip 跑 ken_burns 并原地改 cuts[].clip_path（不走 overwrite 拒绝）",
            },
        },
    }

    def __init__(self, *, ken_burns_fn: Callable[..., Any] | None = None) -> None:
        self._ken_burns_fn = ken_burns_fn

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        project_dir = str(inputs.get("project_dir") or "")
        store = ArtifactStore(project_dir) if project_dir else None
        scene_plan = inputs.get("scene_plan") if isinstance(inputs.get("scene_plan"), dict) else None
        shot_prompts = inputs.get("shot_prompts") if isinstance(inputs.get("shot_prompts"), dict) else None
        manifest = inputs.get("asset_manifest") if isinstance(inputs.get("asset_manifest"), dict) else None
        soundtrack = inputs.get("soundtrack") if isinstance(inputs.get("soundtrack"), dict) else None
        if store:
            scene_plan = scene_plan or store.read("scene_plan")
            shot_prompts = shot_prompts or store.read("shot_prompts")
            manifest = manifest or store.read("asset_manifest")
            if soundtrack is None:
                soundtrack = store.read("soundtrack")
        existing = store.read("edit_decisions") if store else None
        realize = bool(inputs.get("realize"))
        if existing and (existing.get("cuts") or []) and not inputs.get("overwrite") and not realize:
            return ToolResult(
                success=False,
                error="已有 edit_decisions，拒绝覆盖精修（overwrite=true 才重编）",
                data={"refused": True, "existing_cuts": len(existing.get("cuts") or [])},
            )
        if realize and existing and (existing.get("cuts") or []) and not inputs.get("overwrite"):
            plan = store.read("compose_plan") if store else None
            if not isinstance(plan, dict) or not (plan.get("shots") or []):
                return ToolResult(
                    success=False,
                    error="realize=true 需要已有 compose_plan（先跑 compose_planner 编译）",
                )
            return self._realize_result(plan, existing, project_dir, store, inputs)
        if not scene_plan and not shot_prompts:
            return ToolResult(success=False, error="需要 scene_plan 或 shot_prompts")

        settings = load_pipeline_settings(project_dir) if project_dir else {}
        packet = (store.read("proposal_packet") or {}) if store else {}
        pb = _playbook_dict(inputs.get("playbook")) or _playbook_dict(packet.get("playbook"))
        if pb is None and settings.get("default_playbook"):
            pb = _playbook_dict(settings.get("default_playbook"))
        pack_raw = inputs.get("style_pack")
        pack: dict[str, Any] | None = pack_raw if isinstance(pack_raw, dict) else None
        if pack is None and isinstance(pack_raw, str) and pack_raw.strip():
            pack = get_style_pack(pack_raw.strip())
        if pack is None and pb:
            pack = pack_for_playbook(str(pb.get("id") or ""))
        if pack is None and packet.get("style_pack"):
            pack = get_style_pack(str(packet.get("style_pack")))

        whole = keep_embedded_audio(shot_prompts, project_dir)
        native_any = any(
            shot_keeps_embedded_audio(s)
            for s in (shot_prompts or {}).get("shots") or []
            if isinstance(s, dict)
        )
        built = build_compose_plan(
            scene_plan,
            shot_prompts=shot_prompts,
            asset_manifest=manifest,
            soundtrack=soundtrack,
            playbook=pb,
            style_pack=pack,
            edit_style=str(settings.get("edit_style") or "cinematic"),
            transition_policy=str(settings.get("transition_policy") or ""),
            keep_audio=whole,
            mix_source_audio=whole or native_any,
            ducking=not whole,
        )
        plan = built["compose_plan"]
        decisions = compile_compose_plan(plan)
        if realize:
            return self._realize_result(
                plan, decisions, project_dir, store, inputs,
                extra_findings=built["findings"],
            )
        if store:
            store.write("edit_decisions", decisions)
            if inputs.get("write_plan", True):
                store.write("compose_plan", plan)
        return ToolResult(
            success=True,
            data={
                "compose_plan": plan,
                "edit_decisions": decisions,
                "findings": built["findings"],
            },
        )

    def _realize_result(
        self,
        plan: dict[str, Any],
        decisions: dict[str, Any],
        project_dir: str,
        store: ArtifactStore | None,
        inputs: dict[str, Any],
        extra_findings: list[dict[str, str]] | None = None,
    ) -> ToolResult:
        fn = self._ken_burns_fn
        if fn is None:
            from montage.compose.ffmpeg_engine import ken_burns as fn
        out_dir = Path(project_dir) / "assets" / "kenburns" if project_dir else Path("assets/kenburns")
        findings: list[dict[str, str]] = list(extra_findings or [])
        decisions, plan, realized = realize_ken_burns(
            plan, decisions, out_dir=out_dir, ken_burns_fn=fn, findings=findings,
        )
        if store:
            store.write("edit_decisions", decisions)
            if inputs.get("write_plan", True):
                store.write("compose_plan", plan)
        return ToolResult(
            success=True,
            data={
                "compose_plan": plan,
                "edit_decisions": decisions,
                "findings": findings,
                "realized": realized,
                "lut_path": resolve_lut_file(str(plan.get("lut") or "")),
            },
        )
