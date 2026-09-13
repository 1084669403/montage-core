"""shot_runner 路由 + 提示词输入 + 时长 纯函数。

从 shot_runner.py 抽出，原文件保留显式重导出 shim。
"""

from __future__ import annotations

import os
from typing import Any

from montage.providers.capabilities import VIDEO_SURFACES, video_caps, video_surface
from montage.providers.video_prompts import prompt_profile

from montage.tools._shot_constants import (
    _FAMILY_APIS,
    _REF_ROLES,
    _SEEDANCE_APIS,
)


def _nested_plan_shots(scene_plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    shots: list[dict[str, Any]] = []
    for scene in (scene_plan or {}).get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        sid = str(scene.get("id") or "")
        nested = scene.get("shots") or []
        if not nested:
            continue
        for idx, raw in enumerate(nested):
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["scene_id"] = str(item.get("scene_id") or sid)
            item.setdefault("shot_id", item.get("shot_id") or f"{sid}_{idx + 1:02d}")
            item.setdefault("shot_kind", "video")
            scene_loc = str(scene.get("location_id") or "").strip()
            if scene_loc and not str(item.get("location_id") or "").strip():
                item["location_id"] = scene_loc
            shots.append(item)
    return shots


def collect_shots(
    scene_plan: dict[str, Any] | None,
    shot_prompts: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """shot_prompts 覆盖 scene_plan 全部 shot_id 时用它；否则用嵌套时间线。"""
    nested = _nested_plan_shots(scene_plan)
    existing = (shot_prompts or {}).get("shots") if isinstance(shot_prompts, dict) else None
    if isinstance(existing, list) and existing:
        out: list[dict[str, Any]] = []
        for raw in existing:
            if isinstance(raw, dict) and raw.get("shot_kind"):
                item = dict(raw)
                item.setdefault("shot_id", item.get("shot_id") or item.get("id") or "")
                out.append(item)
        if out:
            if not nested:
                return out
            nested_ids = {str(s.get("shot_id") or "") for s in nested}
            prompt_ids = {str(s.get("shot_id") or "") for s in out}
            if nested_ids <= prompt_ids:
                return out
            return nested
    return nested


_REWORK_KEYS = ("rework_mode", "revision_note", "retake_segment")


def overlay_plan_rework(
    shots: list[dict[str, Any]],
    scene_plan: dict[str, Any] | None,
) -> None:
    """scene_plan 上的返工字段盖过已 lift 的 shot_prompts（确认卡只写 scene_plan）。

    时长同样以 scene_plan 为准：shot_prompts 是 lift 时的快照，改 bible/scene_plan
    后重抽若不覆盖，会拿旧时长静默重发（huapi sc01_01 踩过：改 9s 仍按 6s 请求）。
    """
    nested = {
        str(s.get("shot_id") or ""): s
        for s in _nested_plan_shots(scene_plan)
        if str(s.get("shot_id") or "")
    }
    for shot in shots:
        src = nested.get(str(shot.get("shot_id") or ""))
        if not isinstance(src, dict):
            continue
        for key in _REWORK_KEYS:
            if key in src:
                shot[key] = src[key]
        try:
            dur = float(src.get("duration_seconds") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        if dur > 0:
            shot["duration_seconds"] = dur


def lift_shot_prompts(shots: list[dict[str, Any]]) -> dict[str, Any]:
    lifted: list[dict[str, Any]] = []
    for shot in shots:
        row = {
            "scene_id": str(shot.get("scene_id") or ""),
            "shot_id": str(shot.get("shot_id") or ""),
            "shot_kind": str(shot.get("shot_kind") or "video"),
            "duration_seconds": float(shot.get("duration_seconds") or 5),
        }
        for key in (
            "visual_details", "audio_prompt", "reference_asset_ids",
            "render_kind", "hero_moment", "shot_language",
            "first_frame_prompt", "video_prompt", "audio_source",
            "cut", "location_id", "shot_budget_class", "blocking",
            "dialogue_audio_mode", "gen_strategy", "api_id", "negative_prompt",
            "rework_mode", "revision_note", "retake_segment",
        ):
            if key in shot and shot[key] not in (None, "", []):
                row[key] = shot[key]
        lifted.append(row)
    return {"version": "1", "shots": lifted}


def _shot_cut(shot: dict[str, Any]) -> str:
    val = str(shot.get("cut") or "bridge").strip().lower()
    return "hard" if val == "hard" else "bridge"


def _clears_bridge(shot: dict[str, Any], prev_location_id: str) -> bool:
    """清桥只认 cut=hard，或双方都有且不等的 location_id。缺 cut 当 bridge。"""
    if _shot_cut(shot) == "hard":
        return True
    loc = str(shot.get("location_id") or "").strip()
    prev = str(prev_location_id or "").strip()
    return bool(loc and prev and loc != prev)


def _next_hero_tail(
    shot: dict[str, Any],
    timeline: list[dict[str, Any]] | None,
    still_by_id: dict[str, dict[str, Any]],
) -> tuple[str, str]:
    """本镜 last_frame = 下一镜设计首帧。缺镜 / 清桥（hard 或换 location_id）/ 无静图则空。"""
    sid = str(shot.get("shot_id") or "")
    rows = timeline or []
    idx = -1
    for i, row in enumerate(rows):
        if str(row.get("shot_id") or "") == sid:
            idx = i
            break
    if idx < 0 or idx + 1 >= len(rows):
        return "", ""
    nxt = rows[idx + 1]
    if _clears_bridge(nxt, str(shot.get("location_id") or "")):
        return "", ""
    still = still_by_id.get(str(nxt.get("shot_id") or "")) or {}
    return str(still.get("path") or ""), str(still.get("url") or "")


def _want_last_frame(
    shot: dict[str, Any],
    route_caps: dict[str, Any] | None,
    *,
    kling_loop: bool,
    sample_one: bool,
) -> bool:
    """可灵双桥凡有 last_frame 能力就填；即梦仍只给 hero 且非单镜样品。"""
    caps = route_caps or {}
    if not caps.get("last_frame"):
        return False
    if kling_loop:
        return True
    if sample_one:
        return False
    return str(shot.get("shot_budget_class") or "").strip().lower() == "hero"


def _is_agnes_loop(policy: dict[str, Any], vid_prov: str) -> bool:
    return str(policy.get("video_loop") or "") == "agnes" or vid_prov == "agnes"


def loop_family(video_loop: str = "", vid_prov: str = "") -> str:
    loop = str(video_loop or "").strip().lower()
    aliases = {"seedance": "ark", "jimeng": "volcengine"}
    loop = aliases.get(loop, loop)
    if loop in _FAMILY_APIS:
        return loop
    prov = aliases.get(str(vid_prov or "").strip().lower(), str(vid_prov or "").strip().lower())
    if prov in _FAMILY_APIS:
        return prov
    return loop or prov or "volcengine"


def _env_model(*names: str) -> str:
    for name in names:
        val = str(os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


def _env_flag(*names: str) -> bool:
    for name in names:
        val = str(os.environ.get(name) or "").strip().lower()
        if val in ("1", "true", "yes", "on"):
            return True
    return False


def _shot_speaker_ids(shot: dict[str, Any] | None) -> list[str]:
    data = shot if isinstance(shot, dict) else {}
    ids: list[str] = []

    def add(raw: Any) -> None:
        sid = str(raw or "").strip()
        if sid and sid not in ids:
            ids.append(sid)

    add(data.get("speaker_id"))
    audio = data.get("audio_prompt") if isinstance(data.get("audio_prompt"), dict) else {}
    add(audio.get("speaker_id"))
    raw = audio.get("dialogue")
    if isinstance(raw, list):
        for line in raw:
            if isinstance(line, dict):
                add(line.get("speaker_id") or line.get("role") or line.get("name"))
    for line in data.get("lines") or []:
        if isinstance(line, dict):
            add(line.get("speaker_id") or line.get("role") or line.get("name"))
    return ids


def _kling_native_audio(shot: dict[str, Any] | None) -> bool:
    """Omni 仅单说话人对白才 native；多说话人 / 无对白 / TTS 走 off。"""
    data = shot if isinstance(shot, dict) else {}
    mode = str(data.get("dialogue_audio_mode") or "").strip().lower()
    rework = str(data.get("rework_mode") or "").strip().lower()
    if rework in ("feature", "edit", "base"):
        return False
    if mode in ("tts", "off"):
        return False
    if len(_shot_speaker_ids(data)) > 1:
        return False
    if mode == "native":
        return True
    return _shot_has_dialogue(data)


def _shot_duration(shot: dict[str, Any] | None) -> float:
    try:
        return float((shot or {}).get("duration_seconds") or 5)
    except (TypeError, ValueError):
        return 5.0


def _shot_is_hero(shot: dict[str, Any] | None) -> bool:
    return str((shot or {}).get("shot_budget_class") or "").strip().lower() == "hero"


def _shot_dialogue(shot: dict[str, Any] | None) -> str:
    from lib.shot_prompt_builder import dialogue_line_text

    data = shot if isinstance(shot, dict) else {}
    audio = data.get("audio_prompt") if isinstance(data.get("audio_prompt"), dict) else {}
    rows: list[Any] = []
    raw = audio.get("dialogue")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(raw, list):
        rows.extend(raw)
    for line in data.get("lines") or []:
        rows.append(line)
    parts: list[str] = []
    for line in rows:
        piece = dialogue_line_text(line)
        if piece and piece not in parts:
            parts.append(piece)
    return " ".join(parts)


def _shot_has_dialogue(shot: dict[str, Any] | None) -> bool:
    data = shot if isinstance(shot, dict) else {}
    if str(data.get("dialogue_audio_mode") or "").strip().lower() == "native":
        return True
    return bool(_shot_dialogue(data))


def _adapter_refs(shot: dict[str, Any], refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = {str(x) for x in (shot.get("reference_asset_ids") or []) if x}
    out: list[dict[str, Any]] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        rid = str(ref.get("id") or "")
        if ids and rid not in ids:
            continue
        if not ids:
            continue
        kind = str(ref.get("kind") or "")
        row: dict[str, Any] = {
            "role": _REF_ROLES.get(kind, "参考"),
            "url": ref.get("url"),
            "kind": kind,
        }
        if kind in ("portrait", "turnaround"):
            row["type"] = "element"
            if ref.get("element_id") is not None:
                row["element_id"] = ref.get("element_id")
        elif kind in ("scene_ref", "prop"):
            row["type"] = "refer_image"
        out.append(row)
    return out


def _route_caps(api_id: str, vid_name: str, vid_prov: str) -> dict[str, Any]:
    tool_caps = video_caps(tool=vid_name, provider=vid_prov)
    surface = video_surface(api_id) if api_id else {}
    raw = VIDEO_SURFACES.get(str(api_id or "").strip()) or {}
    out = dict(tool_caps)
    for key in (
        "api_id", "native_audio", "multi_shot", "multi_shot_max",
        "first_frame", "last_frame", "requires_first_frame",
        "last_frame_requires_first", "max_duration", "duration_policy",
        "edit_clip", "extend_clip", "passthrough", "citation_syntax",
        "ratio_adaptive_when_frames_locked", "negative_prompt",
        "watermark_default",
    ):
        if key in surface:
            out[key] = dict(surface[key]) if isinstance(surface[key], dict) else surface[key]
    for key in (
        "first_url_fields", "last_url_fields",
        "first_path_fields", "last_path_fields",
    ):
        if key in raw:
            out[key] = raw[key]
    if api_id:
        out["api_id"] = api_id
    return out


def _pick_seedance(shot: dict[str, Any], wanted: str, notes: list[str]) -> str:
    allowed = _FAMILY_APIS["ark"]
    if wanted in allowed:
        return wanted
    if wanted:
        notes.append(f"ark 环忽略 api_id={wanted}")
    dur = _shot_duration(shot)
    if dur > 15:
        return "seedance_25"
    if _shot_is_hero(shot) and dur <= 15:
        return "seedance_20_pro"
    return "seedance_25"


def _pick_kling(
    shot: dict[str, Any],
    wanted: str,
    has_first_frame: bool,
    notes: list[str],
) -> tuple[str, bool]:
    """默认 Omni。不因缺 KLING_OMNI_MODEL / HTTP 403 降 v1。"""
    allowed = _FAMILY_APIS["kling"]
    data = shot if isinstance(shot, dict) else {}

    if _env_flag("KLING_FORCE_V1"):
        notes.append("KLING_FORCE_V1：锁定 kling_v1")
        return "kling_v1", False
    if _env_flag("KLING_FORCE_21"):
        if has_first_frame and _env_model("KLING_I2V_MODEL"):
            notes.append("KLING_FORCE_21：锁定 2.1 Pro")
            return "kling_i2v_21_pro", False
        notes.append("KLING_FORCE_21 无首帧或缺 KLING_I2V_MODEL，已改 Omni")
        return "kling_omni_30", True

    if wanted in allowed:
        if wanted == "kling_i2v_21_pro" and not has_first_frame:
            notes.append("2.1 Pro 无首帧，已改 Omni")
            return "kling_omni_30", True
        if wanted == "kling_i2v_21_pro" and not _env_model("KLING_I2V_MODEL"):
            notes.append("缺 KLING_I2V_MODEL，已改 Omni")
            return "kling_omni_30", True
        if wanted == "kling_i2v_30" and not has_first_frame:
            notes.append("i2v 3.0 无首帧，已改 Omni")
            return "kling_omni_30", True
        if wanted == "kling_motion_30" and not str(
            data.get("motion_video_url") or data.get("video_url") or ""
        ).strip():
            notes.append("motion 无 motion_video_url，已改 Omni")
            return "kling_omni_30", True
        return wanted, False
    if wanted:
        notes.append(f"kling 环忽略 api_id={wanted}")

    if _env_flag("KLING_DISABLE_OMNI"):
        return ("kling_i2v_30" if has_first_frame else "kling_t2v_30"), False

    if str(data.get("motion_video_url") or "").strip():
        return "kling_motion_30", False

    last = str(data.get("last_frame_url") or data.get("last_frame_path") or "").strip()
    true_i2v = (
        has_first_frame
        and bool(last)
        and not _shot_has_dialogue(data)
        and not data.get("clears_bridge")
        and str(data.get("gen_strategy") or "") != "single_call_multi_shot"
    )
    if true_i2v:
        return "kling_i2v_30", False
    return "kling_omni_30", False


def _route_shot(
    shot: dict[str, Any] | None = None,
    *,
    video_loop: str = "",
    vid_prov: str = "",
    vid_name: str = "",
    has_first_frame: bool = False,
    video_surface_id: str = "",
) -> dict[str, Any]:
    """本环家族内挑 api_id。禁止把 volcengine 升级成 Seedance。"""
    data = shot if isinstance(shot, dict) else {}
    family = loop_family(video_loop, vid_prov)
    notes: list[str] = []
    degraded = False
    wanted = str(data.get("api_id") or video_surface_id or "").strip()
    if family == "volcengine":
        api_id = "jimeng_v30"
        if wanted and wanted != api_id:
            notes.append(f"volcengine 环忽略 api_id={wanted}，保持 jimeng_v30")
    elif family == "agnes":
        from montage.providers.agnes import want_video_v20

        if wanted == "agnes_v20" or want_video_v20():
            api_id = "agnes_v20"
        else:
            api_id = "agnes_v25"
            if wanted and wanted != "agnes_v25":
                notes.append(f"Agnes 环默认 agnes_v25，忽略 api_id={wanted}")
    elif family == "ark":
        api_id = _pick_seedance(data, wanted, notes)
    elif family == "kling":
        api_id, degraded = _pick_kling(data, wanted, has_first_frame, notes)
    else:
        api_id = str(video_caps(tool=vid_name, provider=vid_prov).get("api_id") or "")

    caps = _route_caps(api_id, vid_name, vid_prov)
    mode = str(data.get("dialogue_audio_mode") or "").strip().lower()
    dialogue = _shot_has_dialogue(data)
    generate_audio = False
    sound = "off"
    audio_source = ""
    if api_id in ("agnes_v20", "agnes_v25"):
        audio_source = "agnes_prompt"
        generate_audio = True
    elif api_id in _SEEDANCE_APIS:
        generate_audio = mode != "tts"
        audio_source = "jimeng_prompt" if generate_audio else ""
    elif api_id == "kling_omni_30":
        native = _kling_native_audio(data)
        sound = "on" if native else "off"
        audio_source = "kling_prompt" if native else ""
        generate_audio = native

    strategy = str(data.get("gen_strategy") or "shot_by_shot") or "shot_by_shot"
    if strategy == "single_call_multi_shot":
        if not caps.get("multi_shot"):
            notes.append("当前面无 multi_shot，已降级 shot_by_shot")
            strategy = "shot_by_shot"
            degraded = True
        else:
            notes.append("P2 仍逐镜执行 single_call_multi_shot（不发一场多镜 HTTP）")

    return {
        "api_id": api_id,
        "family": family,
        "caps": caps,
        "notes": notes,
        "degraded": degraded,
        "generate_audio": generate_audio,
        "sound": sound,
        "audio_source": audio_source,
        "gen_strategy": strategy,
    }


def _prompt_inputs(
    shot: dict[str, Any],
    scene_plan: dict[str, Any] | None,
    project_dir: str,
    *,
    agnes_loop: bool,
    vid_prov: str,
    api_id: str = "",
) -> dict[str, Any]:
    if not api_id:
        if agnes_loop or vid_prov == "agnes":
            from montage.providers.agnes import want_video_v20

            api_id = "agnes_v20" if want_video_v20() else "agnes_v25"
        elif vid_prov == "ark":
            api_id = "seedance_25"
        elif vid_prov == "kling":
            api_id = "kling_omni_30"
        elif vid_prov == "volcengine":
            api_id = "jimeng_v30"
    profile = prompt_profile(api_id) if api_id else {}
    agnes_v20 = api_id == "agnes_v20"
    agnes_any = api_id in ("agnes_v20", "agnes_v25") or (not api_id and agnes_loop)
    jimeng_v30 = api_id == "jimeng_v30"
    max_chars = profile.get("provider_max_chars") or profile.get("max_chars")
    if agnes_any:
        max_chars = 3000
    elif jimeng_v30:
        max_chars = 800
    _KLING_PROMPT_APIS = {
        "kling_omni_30": "omni",
        "kling_t2v_30": "none",
        "kling_i2v_30": "i2v",
        "kling_motion_30": "none",
    }
    kling_cite = _KLING_PROMPT_APIS.get(api_id, "")
    if kling_cite:
        max_chars = int(max_chars or 2500)

    # 母带注入：复用 visual_prompt_builder 的统一 style_context 解析，
    # 避免与 _resolve_style_context 双读 proposal_packet。
    master_pattern = ""
    master_prompt = ""
    if kling_cite:
        try:
            from montage.tools.visual_prompt_builder import _resolve_style_context

            ctx = _resolve_style_context({"project_dir": project_dir})
            if isinstance(ctx, dict):
                master_pattern = str(ctx.get("master_pattern") or "").strip()
                master_prompt = str(ctx.get("master_prompt") or "").strip()
        except (OSError, TypeError, ValueError):
            pass

    agnes_plan = shot.get("_agnes_ref_plan") if isinstance(shot.get("_agnes_ref_plan"), dict) else {}
    refs_in = list(agnes_plan.get("entries") or []) if api_id == "agnes_v25" else []
    return {
        "purpose": "shot",
        "shot": shot,
        "character_registry": (scene_plan or {}).get("character_registry"),
        "project_dir": project_dir,
        "agnes_audio": agnes_v20,
        "agnes_v25": api_id == "agnes_v25",
        "kling_prompt": bool(kling_cite),
        "kling_cite": kling_cite or "omni",
        "english_visual": False,
        "jimeng_prompt": jimeng_v30,
        "provider_max_chars": max_chars if max_chars else None,
        "locations": (scene_plan or {}).get("locations"),
        # 单一来源：Agnes 2.5 图例只认已过公网过滤+截断的最终有序表，
        # 编号即 images[] 下标；非 Agnes 路线不传，避免造出第二个生产者。
        "refs": refs_in or None,
        "master_pattern": master_pattern or None,
        "master_prompt": master_prompt or None,
    }


def _next_still_urls(
    shots: list[dict[str, Any]],
    stills: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """同场下一镜已生成的静图 URL，供 Agnes 关键帧尾帧。"""
    by_scene: dict[str, list[str]] = {}
    for shot in shots:
        sid = str(shot.get("shot_id") or "")
        if not sid:
            continue
        by_scene.setdefault(str(shot.get("scene_id") or ""), []).append(sid)
    out: dict[str, str] = {}
    for ids in by_scene.values():
        for i, sid in enumerate(ids):
            for later in ids[i + 1:]:
                url = str((stills.get(later) or {}).get("url") or "")
                if url.startswith("http"):
                    out[sid] = url
                    break
    return out


def _agnes_is_v25() -> bool:
    """非 2.0 回滚即按 Flash 编排，与 resolve_video_model 对齐。"""
    from montage.providers.agnes import want_video_v20

    return not want_video_v20()


def agnes_duration_chunks(seconds: float, *, v25: bool | None = None) -> list[float]:
    """按当前 Agnes 模型上限切段；过短余量丢弃（由 validator 建议在剧本层拆镜）。"""
    if v25 is None:
        v25 = _agnes_is_v25()
    cap = 12.0 if v25 else 18.0
    floor = 4.0 if v25 else 3.0
    sec = float(seconds or 5)
    if sec <= cap:
        return [sec]
    chunks: list[float] = []
    remain = sec
    while remain > cap:
        chunks.append(cap)
        remain -= cap
    if remain >= floor:
        chunks.append(remain)
    return chunks or [min(sec, cap)]
