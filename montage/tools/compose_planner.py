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
from montage.tools._shot_refs import probe_seconds, probe_size
from lib.shot_prompt_builder import dialogue_line_role, dialogue_line_text

_STILL_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}


def is_still_image(path: str) -> bool:
    return Path(path).suffix.lower() in _STILL_EXTS


def _shot_vfx(shot: dict[str, Any]) -> list[dict[str, Any]]:
    """P0-8：透传 shot.vfx[]（特效指导制定的观感特效）。

    只收 dict 条目；层过滤留给 assemble 端（prompt 层在 assemble 无意义但
    保留透传完整性，便于 edit_decisions 审计）。
    """
    return [v for v in (shot.get("vfx") or []) if isinstance(v, dict)]


def _probe_image_size(path: Path) -> tuple[int, int] | None:
    """源图实测尺寸；失败 None（ken_burns 交给其默认，但不再由本层假设 1080p）。"""
    return probe_size(path)


def _profile_target_size(store: ArtifactStore | None) -> tuple[int, int] | None:
    """proposal_packet.output_profile → 成片画布；未知则 None（不猜 1080p）。"""
    if store is None:
        return None
    packet = store.read("proposal_packet") or {}
    name = str(packet.get("output_profile") or "").strip()
    if not name:
        return None
    from montage.compose.profiles import get_profile

    profile = get_profile(name)
    return (profile.width, profile.height) if profile else None


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
    target_size: tuple[int, int] | None = None,
    findings: list[dict[str, str]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """仅对静图 clip 跑 ken_burns，原地改 cuts[].clip_path。

    ``target_size``：成片画布（按 output_profile 推导）。不给则用源图实测尺寸，
    避免默认 1920x1080 把竖屏项目的静图镜拉成横屏。
    """
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
        size = target_size or _probe_image_size(src)
        kwargs: dict[str, Any] = {"zoom": zoom, "pan": pan}
        if size:
            kwargs["width"], kwargs["height"] = int(size[0]), int(size[1])
        try:
            ken_burns_fn(src, dest, dur, **kwargs)
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


VALID_TRANSITION_CONTRACT_DECISIONS = {
    "fade",
    "xfade",
    "match_cut",
    "audio_bridge",
    "shared_element",
    "establishing_shot",
    "user_accepted_hard_cut",
}


def _valid_transition_contract(shot: dict[str, Any]) -> dict[str, Any] | None:
    """Return an explicit, complete contract attached to the scene shot."""
    contract = shot.get("transition_contract")
    if not isinstance(contract, dict):
        return None
    decision = str(contract.get("decision") or "").strip()
    reason = str(contract.get("reason") or "").strip()
    if decision not in VALID_TRANSITION_CONTRACT_DECISIONS or not reason:
        return None
    return {**contract, "decision": decision, "reason": reason}


def _transition_for_contract(
    contract: dict[str, Any], shot: dict[str, Any], pack_tdur: float
) -> tuple[str, float]:
    """Map an editorial contract onto a compose-executable transition."""
    decision = str(contract.get("decision"))
    requested = str(shot.get("transition") or "").strip()
    raw_duration = shot.get("transition_duration")
    try:
        requested_duration = float(raw_duration or 0)
    except (TypeError, ValueError):
        requested_duration = 0.0
    duration = requested_duration if requested_duration > 0 else pack_tdur
    if decision == "user_accepted_hard_cut":
        return "cut", 0.0
    if decision == "fade":
        return ("fade" if requested not in {"fade", "crossfade"} else requested), duration
    if decision == "xfade":
        return ("crossfade" if requested in {"", "cut"} else requested), duration
    # Narrative bridges can be a hard cut; preserve any explicitly selected
    # compose transition, but never invent a visual wipe from the contract name.
    return (requested or "cut"), (duration if requested not in {"", "cut"} else 0.0)


def _measured_durations(manifest: dict[str, Any] | None) -> dict[str, float]:
    """asset_manifest.items[] 里 kind=video 的实测时长 → {shot_id: seconds}。

    优先用 shot_runner 写回的 ``duration_seconds``；老产物没有该字段时回落到
    直接 probe 磁盘上的 clip（本机既有项目就是这种情况），保证 compose_plan
    的时间轴始终以"成片里真实存在的片段"为准。
    """
    out: dict[str, float] = {}
    for item in (manifest or {}).get("items") or []:
        if not isinstance(item, dict) or str(item.get("kind") or "") != "video":
            continue
        sid = str(item.get("shot_id") or "")
        if not sid:
            continue
        try:
            seconds = float(item.get("duration_seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        if seconds <= 0 and item.get("path"):
            seconds = probe_seconds(str(item["path"]))
        if seconds > 0:
            out[sid] = seconds  # 后写覆盖：取最新一次生成
    return out


def _scene_plan_with_measured(
    scene_plan: dict[str, Any] | None,
    measured: dict[str, float],
) -> dict[str, Any] | None:
    """把实测时长盖到 scene_plan，并**按实测重算每场的起止时间**。

    ``shots_with_timeline`` 的规则是"每场以本人 ``start_seconds`` 为起点，场内
    逐镜累加"。只改镜长不动 ``scene.start_seconds`` 会让下一场从旧起点开始
    （如 sc01 三镜实回 6.59s → 场内累加到 19.78，而 sc02 仍从计划 18.0 开始，
    字幕反而重叠）。所以这里同时按实测把场起点改成累计值。

    残余误差：非 cut 转场会让相邻片段真实重叠 ``transition_duration``，本函数
    未扣除（cut 无重叠、纯硬切成片精确对齐）。跨场字幕误差上限 = 各转场重叠之和。
    """
    if not measured or not isinstance(scene_plan, dict):
        return scene_plan
    scenes: list[Any] = []
    cursor: float | None = None
    for scene in scene_plan.get("scenes") or []:
        if not isinstance(scene, dict):
            scenes.append(scene)
            continue
        shots: list[Any] = []
        total = 0.0
        has_shots = False
        for shot in scene.get("shots") or []:
            if not isinstance(shot, dict):
                shots.append(shot)
                continue
            has_shots = True
            real = measured.get(str(shot.get("shot_id") or ""))
            if real:
                shots.append({**shot, "duration_seconds": real})
                total += real
            else:
                shots.append(shot)
                try:
                    total += float(shot.get("duration_seconds") or 0)
                except (TypeError, ValueError):
                    pass
        new_scene = {**scene, "shots": shots}
        if has_shots:
            if cursor is None:
                cursor = float(scene.get("start_seconds") or 0)
            new_scene["start_seconds"] = round(cursor, 3)
            new_scene["end_seconds"] = round(cursor + total, 3)
            cursor += total
        scenes.append(new_scene)
    return {**scene_plan, "scenes": scenes}


def _planned_seconds(scene_plan: dict[str, Any] | None, shot_id: str) -> float:
    for scene in (scene_plan or {}).get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for shot in scene.get("shots") or []:
            if isinstance(shot, dict) and str(shot.get("shot_id") or "") == shot_id:
                try:
                    return float(shot.get("duration_seconds") or 0)
                except (TypeError, ValueError):
                    return 0.0
    return 0.0


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
        contract = _valid_transition_contract(shot)
        if contract:
            cut["transition_contract"] = contract
            cut["transition_reason"] = str(contract.get("reason"))
        gap = shot.get("negative_gap_seconds")
        if gap not in (None, ""):
            cut["negative_gap_seconds"] = float(gap)
        vfx = _shot_vfx(shot)
        if vfx:
            cut["vfx"] = vfx
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
    measured = _measured_durations(asset_manifest)
    timed = shots_with_timeline(_scene_plan_with_measured(scene_plan, measured))
    if not timed and shot_prompts:
        timed = [s for s in (shot_prompts.get("shots") or []) if isinstance(s, dict)]
    if not timed:
        findings.append({
            "severity": "warning",
            "field": "shots",
            "message": "没有镜头可编进 compose_plan",
            "proposed_fix": "先跑 script_to_scene_plan 或提供 shot_prompts",
        })
    drift = [
        (sid, real, _planned_seconds(scene_plan, sid))
        for sid, real in measured.items()
    ]
    drift = [(sid, real, planned) for sid, real, planned in drift if planned and abs(real - planned) > 0.25]
    if drift:
        real_total = sum(real for _sid, real, _p in drift)
        plan_total = sum(planned for _sid, _r, planned in drift)
        findings.append({
            "severity": "warning",
            "field": "timeline",
            "message": (
                f"{len(drift)} 个镜头实测时长≠计划（合计 {real_total:.2f}s vs 计划 {plan_total:.2f}s）；"
                "已按实测重算时间轴，字幕/转场以此为准"
            ),
            "proposed_fix": "若要严格贴合计划时长，改剧本请求秒数后重生成该镜",
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
        contract = _valid_transition_contract(shot)
        if shot.get("transition_contract") is not None and contract is None:
            findings.append({
                "severity": "warning",
                "field": shot_id,
                "message": "transition_contract 缺少合法 decision 或 reason，已忽略",
                "proposed_fix": "补 decision/reason；不要静默保留不完整契约",
            })
        if i == 0 or cut_only:
            transition = "cut"
            gap = 0.0
            tdur = 0.0
        elif contract:
            transition, tdur = _transition_for_contract(
                shot=shot, contract=contract, pack_tdur=pack_tdur
            )
            gap = float(shot.get("negative_gap_seconds") or 0)
        else:
            transition = str(junction.get("suggested_transition") or "cut")
            gap = float(junction.get("negative_gap_seconds") or 0)
            tdur = (gap if gap > 0 else pack_tdur) if transition != "cut" else 0.0
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
            # schema 里 transition_contract 是 object；无合法契约时**省略键**
            # 而不是写 null（null 会被 schema 校验拒，实测 test_compose_planner
            # 的 build_and_compile_valid 就是被这条卡住）。
            **({"transition_contract": contract} if contract else {}),
            "transition_reason": str((contract or {}).get("reason") or ""),
            "lut": lut,
            "subtitle_cues": cues,
            "audio_events": audio_events,
            "effects": effects,
            "vfx": _shot_vfx(shot),
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
            plan, decisions, out_dir=out_dir, ken_burns_fn=fn,
            target_size=_profile_target_size(store), findings=findings,
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
