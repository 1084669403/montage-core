"""bible — series_bible sidecar 编译为 script / scene_plan（W1）。

真源仍是编译后的 script.json / scene_plan.json。本模块只做确定性转换：
bible → script → convert_script_to_scene_plan → 用 bible 覆盖 visual_details。
不改 script_to_scene_plan 的 stub 动作函数。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from montage.engine.artifacts import ArtifactStore
from montage.tools.script_to_scene_plan import convert_script_to_scene_plan

# 与 shot_runner._MAX_PROPS 保持一致（W1 不改 cap 数字）
MAX_PROPS = 3

_X_MAP = {
    "左": "left", "left": "left",
    "中": "center", "center": "center",
    "右": "right", "right": "right",
}
_Z_MAP = {
    "近": "near", "near": "near",
    "中": "mid", "mid": "mid",
    "远": "far", "far": "far",
}


def normalize_blocking(raw: Any) -> dict[str, str] | None:
    """把 左/中/右、近/中/远 收成 {x, z}。"""
    if not isinstance(raw, dict):
        return None
    x = str(raw.get("x") or raw.get("lr") or "").strip()
    z = str(raw.get("z") or raw.get("depth") or "").strip()
    x = _X_MAP.get(x, x)
    z = _Z_MAP.get(z, z)
    if x not in ("left", "center", "right"):
        x = ""
    if z not in ("near", "mid", "far"):
        z = ""
    if not x and not z:
        return None
    return {"x": x or "center", "z": z or "mid"}


def blocking_position(blocking: dict[str, str] | None) -> str:
    if not blocking:
        return ""
    return f"{blocking.get('x') or 'center'}/{blocking.get('z') or 'mid'}"


def _as_objects(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in raw or []:
        if isinstance(item, dict):
            appearance = str(item.get("appearance") or item.get("name") or item.get("id") or "").strip()
            oid = str(item.get("id") or item.get("name") or appearance).strip()
            if not appearance and not oid:
                continue
            row = {"id": oid or appearance, "appearance": appearance or oid}
            pos = str(item.get("position") or "").strip()
            if pos:
                row["position"] = pos
            out.append(row)
        elif isinstance(item, str) and item.strip():
            out.append({"id": item.strip(), "appearance": item.strip()})
    return out


def cap_props(props: list[Any], findings: list[dict[str, str]]) -> list[Any]:
    """全片道具上限；超出截断并 findings。"""
    usable = [p for p in props if isinstance(p, (dict, str))]
    if len(usable) <= MAX_PROPS:
        return usable
    dropped = usable[MAX_PROPS:]
    labels = []
    for item in dropped:
        if isinstance(item, dict):
            labels.append(str(item.get("id") or item.get("name") or item.get("appearance") or "?"))
        else:
            labels.append(str(item))
    findings.append({
        "severity": "warning",
        "stage": "bible",
        "field": "props",
        "message": f"道具超过 {MAX_PROPS} 个，已截断：{', '.join(labels[:6])}",
        "proposed_fix": f"每片最多 {MAX_PROPS} 个关键道具，其余从 bible.props 删除",
    })
    return usable[:MAX_PROPS]


def _filter_episode(
    bible: dict[str, Any],
    episode_plan: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Any]]:
    scenes = [s for s in (bible.get("scenes") or []) if isinstance(s, dict)]
    chars = [c for c in (bible.get("characters") or []) if isinstance(c, dict)]
    props = list(bible.get("props") or [])
    if not isinstance(episode_plan, dict):
        return scenes, chars, props
    scene_ids = {str(x) for x in (episode_plan.get("scene_ids") or []) if x}
    char_ids = {str(x) for x in (episode_plan.get("character_ids") or []) if x}
    if scene_ids:
        scenes = [s for s in scenes if str(s.get("id") or "") in scene_ids]
    if char_ids:
        chars = [c for c in chars if str(c.get("id") or "") in char_ids]
    return scenes, chars, props


def _section_from_scene(scene: dict[str, Any]) -> dict[str, Any]:
    lines = [ln for ln in (scene.get("lines") or []) if isinstance(ln, dict)]
    narration = str(scene.get("narration") or "").strip()
    if not narration and lines:
        narration = "".join(str(ln.get("text") or "") for ln in lines)
    if not narration:
        narration = str(scene.get("id") or "scene")
    row: dict[str, Any] = {
        "id": str(scene.get("id") or "sc01"),
        "narration": narration,
        "lines": lines,
    }
    env = scene.get("environment")
    if isinstance(env, dict) and env:
        row["environment"] = env
    elif isinstance(env, str) and env.strip():
        row["environment"] = {"location": env.strip()}
    duration = float(scene.get("duration_seconds") or 0)
    if duration > 0:
        row["duration_seconds"] = duration
    return row


def _shot_skeletons_by_scene(scenes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """只把完整显式镜头骨架交给转换器。

    条件是：每镜都有非空且不重复的 shot_id。作者只写了一半骨架时，
    仍回退到旧逻辑，避免半自动分镜造成不可预期的逐位覆盖。
    """
    out: dict[str, list[dict[str, Any]]] = {}
    for scene in scenes:
        sid = str(scene.get("id") or "").strip()
        shots = [s for s in (scene.get("shots") or []) if isinstance(s, dict)]
        ids = [str(s.get("shot_id") or "").strip() for s in shots]
        if not sid or not shots or not all(ids) or len(set(ids)) != len(ids):
            continue
        out[sid] = shots
    return out


def _merge_action(base: Any, overlay: Any) -> dict[str, str]:
    out = dict(base) if isinstance(base, dict) else {}
    if isinstance(overlay, dict):
        for key, val in overlay.items():
            if val:
                out[str(key)] = val
    return out


def _overlay_vfx(raw: Any) -> list[dict[str, Any]]:
    """P0-8：bible 逐镜 vfx[] 校验合并。

    只收 dict 条目且带非空 layer/kind 的行；脏条目静默丢弃（compile 自审
    会另行对保留条目出 warning，见 _audit_vfx）。
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        layer = str(item.get("layer") or "").strip().lower()
        kind = str(item.get("kind") or "").strip()
        if layer not in ("prompt", "post") or not kind:
            continue
        row: dict[str, Any] = {"layer": layer, "kind": kind}
        if item.get("onset") is not None:
            try:
                row["onset"] = float(item["onset"])
            except (TypeError, ValueError):
                pass
        if item.get("duration") is not None:
            try:
                row["duration"] = float(item["duration"])
            except (TypeError, ValueError):
                pass
        if item.get("intensity") is not None:
            try:
                row["intensity"] = float(item["intensity"])
            except (TypeError, ValueError):
                pass
        note = str(item.get("note") or "").strip()
        if note:
            row["note"] = note
        out.append(row)
    return out


# P0-8 post 层特效白名单（与 montage.compose.effects.POST_VFX_KINDS 同源口径；
# 此处独立常量避免 compose 模块在纯函数层被意外引入）。
_POST_VFX_WHITELIST = ("impact_flash", "zoom_punch", "camera_shake")


def _audit_vfx(scene_plan: dict[str, Any]) -> list[dict[str, str]]:
    """P0-8 compile 确定性 vfx 自审（轮询「自审不占额度」的地基，V21 体系）。

    全部 severity=warning 不阻塞（特效师在点位一制定后，重编译即自动清账；
    真正的美学判断留给 LLM 轮）。规则：
    - layer 枚举 / post 层 kind 白名单（脏条目在 _overlay_vfx 已被丢弃，这里
      主要防 scene_plan 直改与 schema 校验旁路）；
    - onset ∈ [0, 镜时长]、duration > 0、intensity ∈ [0,1]；
    - 密度红线：非 hero 镜 post 层全片 ≤3 处（hero 镜才允许组合特效）；
    - sfx 同步存在性：视觉特效镜无 audio_prompt.sfx 时提示（不做时间戳
      对齐——sfx.onset 是 string，vfx.onset 是 number，语义不同）。
    """
    out: list[dict[str, str]] = []

    def _warn(field: str, message: str, proposed_fix: str) -> None:
        out.append({
            "severity": "warning",
            "stage": "bible",
            "field": field,
            "message": message,
            "proposed_fix": proposed_fix,
        })

    post_total = 0
    non_hero_post_total = 0
    for scene in scene_plan.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        scene_id = str(scene.get("id") or "")
        for shot in scene.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            sid = str(shot.get("shot_id") or f"{scene_id}")
            vfx_list = shot.get("vfx")
            if not isinstance(vfx_list, list):
                continue
            dur = float(shot.get("duration_seconds") or 0)
            for v in vfx_list:
                if not isinstance(v, dict):
                    continue
                layer = str(v.get("layer") or "")
                kind = str(v.get("kind") or "")
                onset = v.get("onset")
                intensity = v.get("intensity")
                vduration = v.get("duration")
                if layer not in ("prompt", "post"):
                    _warn(
                        f"scenes[{scene_id}].{sid}.vfx",
                        f"layer={layer!r} 不是 prompt/post，该条特效会被管线忽略",
                        "layer 写 prompt（画面内 AI 生成）或 post（后期 ffmpeg）",
                    )
                    continue
                if layer == "post" and kind not in _POST_VFX_WHITELIST:
                    _warn(
                        f"scenes[{scene_id}].{sid}.vfx",
                        f"post 层 kind={kind!r} 不在白名单（{', '.join(_POST_VFX_WHITELIST)}）",
                        "post 层用 impact_flash/zoom_punch/camera_shake；其余走 prompt 层",
                    )
                    continue
                if onset is not None and dur > 0 and not 0.0 <= float(onset) <= dur:
                    _warn(
                        f"scenes[{scene_id}].{sid}.vfx",
                        f"onset={float(onset):.2f} 超出镜时长 {dur:.2f}s，特效将被截断或不出现",
                        "onset 是镜内相对秒（0=镜头起点），范围 [0, duration_seconds]",
                    )
                if vduration is not None and float(vduration) <= 0:
                    _warn(
                        f"scenes[{scene_id}].{sid}.vfx",
                        f"duration={float(vduration):.2f} ≤ 0，该特效无效",
                        "duration 写正数秒（如闪白 0.12）",
                    )
                if intensity is not None and not 0.0 <= float(intensity) <= 1.0:
                    _warn(
                        f"scenes[{scene_id}].{sid}.vfx",
                        f"intensity={float(intensity):.2f} 超出 [0,1]，将按边界截断",
                        "intensity 写 0-1 小数",
                    )
                if layer == "post":
                    post_total += 1
                    if not shot.get("hero_moment"):
                        non_hero_post_total += 1
            # sfx 同步存在性（只查有无，不对时间戳）
            if vfx_list and not (shot.get("audio_prompt") or {}).get("sfx"):
                _warn(
                    f"scenes[{scene_id}].{sid}",
                    "镜头有 vfx 特效但 audio_prompt.sfx 为空",
                    "特效与声音同步写（sfx 补冲击音/能量音），节奏感成倍提升",
                )
    if non_hero_post_total > 3:
        _warn(
            "scene_plan.vfx",
            f"非 hero 镜 post 层特效 {non_hero_post_total} 处，超过全片 ≤3 的密度红线",
            "post 特效留给 hero 镜（hero_moment=true）；过渡镜特效走 prompt 层",
        )
    return out


def _overlay_shot(
    plan_shot: dict[str, Any],
    bible_shot: dict[str, Any] | None,
    *,
    scene_blocking: dict[str, str] | None,
    fallback_objects: list[dict[str, str]],
) -> None:
    bshot = bible_shot if isinstance(bible_shot, dict) else {}
    vd = dict(plan_shot.get("visual_details") or {})
    blocking = normalize_blocking(bshot.get("blocking")) or scene_blocking
    pos = blocking_position(blocking)
    plan_subjects = [s for s in (vd.get("subjects") or []) if isinstance(s, dict)]
    bible_subjects = [s for s in (bshot.get("subjects") or []) if isinstance(s, dict)]
    merged_subjects: list[dict[str, Any]] = []
    if bible_subjects:
        by_id = {str(s.get("id") or ""): s for s in plan_subjects if s.get("id")}
        for idx, sub in enumerate(bible_subjects):
            sid = str(sub.get("id") or "")
            base = dict(by_id.get(sid) or (plan_subjects[idx] if idx < len(plan_subjects) else {}))
            if sid:
                base["id"] = sid
            if sub.get("appearance_anchor"):
                base["appearance_anchor"] = sub["appearance_anchor"]
            # 每镜出场形态：bible 显式声明优先，未声明保留 plan 原值（缺省=默认形态）。
            if sub.get("form_id"):
                base["form_id"] = str(sub["form_id"])
            base["action"] = _merge_action(base.get("action"), sub.get("action"))
            if sub.get("position"):
                base["position"] = str(sub["position"])
            elif pos:
                base["position"] = pos
            merged_subjects.append(base)
    else:
        # 2026-09-20：**显式空镜**要保住空——bible 写了 subjects: [] 且给了
        # presence.empty_reason（人已退场/纯环境）时，不能让转换器的场景级 stub
        # 主体把人物加回来（实测片尾空镜被画回两个角色 + 碑上刻字）。
        explicit_empty = (
            "subjects" in bshot
            and not bible_subjects
            and isinstance(bshot.get("subjects"), list)
            and not bshot.get("subjects")
        )
        empty_reason = str((bshot.get("presence") or {}).get("empty_reason") or "").strip() \
            if isinstance(bshot.get("presence"), dict) else ""
        if explicit_empty and empty_reason:
            merged_subjects = []
        else:
            for sub in plan_subjects:
                row = dict(sub)
                if pos and not row.get("position"):
                    row["position"] = pos
                merged_subjects.append(row)
    vd["subjects"] = merged_subjects
    shot_objects = _as_objects(bshot.get("objects"))
    if shot_objects:
        vd["objects"] = shot_objects[:MAX_PROPS]
    elif fallback_objects:
        vd["objects"] = fallback_objects[:MAX_PROPS]
    plan_shot["visual_details"] = vd
    if blocking:
        plan_shot["blocking"] = blocking
    raw_sl = bshot.get("shot_language")
    if isinstance(raw_sl, dict):
        merged = dict(plan_shot.get("shot_language") or {}) if isinstance(plan_shot.get("shot_language"), dict) else {}
        for key, val in raw_sl.items():
            if val is None:
                continue
            if isinstance(val, str) and not str(val).strip():
                continue
            merged[str(key)] = val
        if merged:
            plan_shot["shot_language"] = merged
    klass = str(bshot.get("shot_budget_class") or "").strip()
    if klass:
        plan_shot["shot_budget_class"] = klass
    cut = str(bshot.get("cut") or "").strip().lower()
    if cut in ("hard", "bridge"):
        plan_shot["cut"] = cut
    loc = str(bshot.get("location_id") or "").strip()
    if loc:
        plan_shot["location_id"] = loc
    # P0-8：bible 逐镜 hero_moment（密度红线/视觉分层成本策略的事实源）。
    # bible 未声明时保留 plan 原值（如 playbook/四拍推导出的标记）。
    if isinstance(bshot.get("hero_moment"), bool):
        plan_shot["hero_moment"] = bshot["hero_moment"]
    # P0-8：bible 逐镜 vfx[]（特效指导制定的观感特效，唯一事实源在 bible——D15）。
    # 白名单合并模式同 shot_language：list 校验 + 非空过滤 + item dict 校验。
    bible_vfx = _overlay_vfx(bshot.get("vfx"))
    if bible_vfx:
        plan_shot["vfx"] = bible_vfx
    # 作者可在 bible 里逐镜指定时长；未给则沿用 scene_plan 的权重分配结果。
    bible_dur = bshot.get("duration_seconds")
    if bible_dur is not None:
        try:
            dur_value = float(bible_dur)
        except (TypeError, ValueError):
            dur_value = 0.0
        if dur_value > 0:
            plan_shot["duration_seconds"] = dur_value


def _env_location_text(raw: Any) -> str:
    if isinstance(raw, dict):
        return str(raw.get("location") or "").strip()
    if isinstance(raw, str):
        return raw.strip()
    return ""


def _location_catalog(bible: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    ids: list[str] = []
    by_name: dict[str, str] = {}
    for loc in bible.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        lid = str(loc.get("id") or "").strip()
        if not lid:
            continue
        if lid not in ids:
            ids.append(lid)
        name = str(loc.get("name") or "").strip()
        if name and name not in by_name:
            by_name[name] = lid
    return ids, by_name


def _pick_location_id(
    *,
    explicit: str,
    env_text: str,
    loc_ids: list[str],
    by_name: dict[str, str],
) -> str:
    text = str(explicit or "").strip()
    if text:
        return text
    if env_text and env_text in by_name:
        return by_name[env_text]
    if len(loc_ids) == 1:
        return loc_ids[0]
    return ""


def align_location_ids(
    bible: dict[str, Any],
    scene_plan: dict[str, Any],
    findings: list[dict[str, str]],
) -> None:
    """compile 后把幕/镜头 location_id 对齐 bible.locations[]。"""
    loc_ids, by_name = _location_catalog(bible)
    known = set(loc_ids)
    bible_scenes = {
        str(sc.get("id") or ""): sc
        for sc in (bible.get("scenes") or [])
        if isinstance(sc, dict)
    }
    for plan_scene in scene_plan.get("scenes") or []:
        if not isinstance(plan_scene, dict):
            continue
        bible_scene = bible_scenes.get(str(plan_scene.get("id") or "")) or {}
        scene_lid = _pick_location_id(
            explicit=str(bible_scene.get("location_id") or plan_scene.get("location_id") or ""),
            env_text=_env_location_text(bible_scene.get("environment")),
            loc_ids=loc_ids,
            by_name=by_name,
        )
        if scene_lid and scene_lid not in known:
            findings.append({
                "severity": "warning",
                "stage": "bible",
                "field": f"scenes[{plan_scene.get('id') or ''}].location_id",
                "message": f"location_id={scene_lid} 不在 locations[]",
                "proposed_fix": "改成 bible.locations[].id，或补地点卡",
            })
            scene_lid = loc_ids[0] if len(loc_ids) == 1 else ""
        if scene_lid:
            plan_scene["location_id"] = scene_lid
        bible_shots = [s for s in (bible_scene.get("shots") or []) if isinstance(s, dict)]
        for idx, plan_shot in enumerate(plan_scene.get("shots") or []):
            if not isinstance(plan_shot, dict):
                continue
            match = None
            sid = str(plan_shot.get("shot_id") or "")
            for cand in bible_shots:
                if sid and str(cand.get("shot_id") or "") == sid:
                    match = cand
                    break
            if match is None and idx < len(bible_shots):
                match = bible_shots[idx]
            shot_lid = str((match or {}).get("location_id") or plan_shot.get("location_id") or "").strip()
            if not shot_lid:
                shot_lid = scene_lid
            if shot_lid and shot_lid not in known:
                findings.append({
                    "severity": "warning",
                    "stage": "bible",
                    "field": f"shots[{sid or idx}].location_id",
                    "message": f"location_id={shot_lid} 不在 locations[]",
                    "proposed_fix": "改成 bible.locations[].id",
                })
                shot_lid = scene_lid if scene_lid in known else (loc_ids[0] if len(loc_ids) == 1 else "")
            if shot_lid:
                plan_shot["location_id"] = shot_lid


def _environment_time_keys(env: Any) -> list[str]:
    """从环境对象提取 time/lighting 选择键（time 优先）。"""
    keys: list[str] = []
    if isinstance(env, dict):
        for field in ("time", "lighting"):
            val = str(env.get(field) or "").strip()
            if val and val not in keys:
                keys.append(val)
    return keys


def _sensory_for_time(loc: dict[str, Any], keys: list[str]) -> str:
    """按 time/lighting 从 loc.sensory_by_time 选句；支持 dict 与 list 两种写法。"""
    by_time = loc.get("sensory_by_time")
    if not by_time or not keys:
        return ""
    if isinstance(by_time, dict):
        for key in keys:
            hit = str(by_time.get(key) or "").strip()
            if hit:
                return hit
        return ""
    if isinstance(by_time, list):
        for key in keys:
            for item in by_time:
                if not isinstance(item, dict):
                    continue
                labels = (
                    str(item.get("time") or "").strip(),
                    str(item.get("lighting") or "").strip(),
                )
                if key in labels:
                    hit = str(item.get("sensory") or "").strip()
                    if hit:
                        return hit
    return ""


def overlay_location_sensory(bible: dict[str, Any], scene_plan: dict[str, Any]) -> None:
    """把 locations[].sensory 抄进每镜；按 scene.environment.time 选句并按景别裁切。"""
    from lib.shot_prompt_builder import crop_location_sensory

    catalog: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for loc in bible.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        lid = str(loc.get("id") or "").strip()
        if not lid:
            continue
        catalog.append(loc)
        by_id[lid] = loc
    if catalog:
        scene_plan["locations"] = catalog
    bible_scenes = {
        str(s.get("id") or ""): s
        for s in (bible.get("scenes") or [])
        if isinstance(s, dict)
    }
    for plan_scene in scene_plan.get("scenes") or []:
        if not isinstance(plan_scene, dict):
            continue
        scene_lid = str(plan_scene.get("location_id") or "").strip()
        # scene_plan 自带的 environment.time 优先；旧产物没有则回落 bible 场景环境。
        scene_env = plan_scene.get("environment")
        if not _environment_time_keys(scene_env):
            bscene = bible_scenes.get(str(plan_scene.get("id") or "")) or {}
            scene_env = bscene.get("environment")
        time_keys = _environment_time_keys(scene_env)
        for plan_shot in plan_scene.get("shots") or []:
            if not isinstance(plan_shot, dict):
                continue
            lid = str(plan_shot.get("location_id") or scene_lid or "").strip()
            loc = by_id.get(lid) or {}
            sensory = _sensory_for_time(loc, time_keys) or str(loc.get("sensory") or "").strip()
            if not sensory:
                continue
            vd = plan_shot.get("visual_details")
            if not isinstance(vd, dict):
                vd = {}
                plan_shot["visual_details"] = vd
            sl = plan_shot.get("shot_language") if isinstance(plan_shot.get("shot_language"), dict) else {}
            # 以裁切结果为准，且 location_sensory 与 vd.environment 写同一值：
            # 后者是 location 权威环境（覆盖 converter 塞进来的全局/首场环境），
            # 前者是 _location_sensory_text 的第一优先来源。过去写"原始未裁句 +
            # 裁切句"两份，导致裁切永远被绕过（crop_location_sensory 成死代码）。
            cropped = crop_location_sensory(sensory, str(sl.get("shot_size") or "")).rstrip("。")
            authoritative = cropped or sensory
            plan_shot["location_sensory"] = authoritative
            vd["environment"] = authoritative


def _attach_presence_and_continuity(
    scene_plan: dict[str, Any],
    script: dict[str, Any],
    findings: list[dict[str, Any]],
) -> None:
    """B2.5：把逐镜在场清单（presence）与承接表（continuity）写进 scene_plan。

    - presence：编剧写在 ``bible.scenes[].shots[].presence``，由 shot_skeletons
      透传进来；缺省时按 ``visual_details`` 派生（并记 ``presence 派生`` warning）。
    - continuity：**编译器生成、只读**（must_keep/changed/missing）；上一镜在场、
      未标 ``exits``、本镜没写 → ``missing``，进 findings 并列缺，不静默。
    """
    ordered: list[dict[str, Any]] = []
    scenes_by_id: dict[str, dict[str, Any]] = {}
    for plan_scene in scene_plan.get("scenes") or []:
        if not isinstance(plan_scene, dict):
            continue
        sid = str(plan_scene.get("id") or "")
        scenes_by_id[sid] = plan_scene
        for shot in plan_scene.get("shots") or []:
            if isinstance(shot, dict):
                shot.setdefault("scene_id", sid)
                ordered.append(shot)
    if not ordered:
        return
    from lib.shot_presence import build_ledger

    registry_rows = scene_plan.get("character_registry")
    if not isinstance(registry_rows, list):
        registry_rows = script.get("characters") or []
    ledger = build_ledger(
        ordered,
        scenes_by_id=scenes_by_id,
        registry={
            str(row.get("id")): row
            for row in registry_rows
            if isinstance(row, dict) and row.get("id")
        },
    )
    for shot in ordered:
        row = ledger["shots"].get(str(shot.get("shot_id"))) or {}
        if row.get("presence"):
            shot["presence"] = row["presence"]
        if row.get("continuity"):
            shot["continuity"] = row["continuity"]
    findings.extend(ledger["findings"])


def overlay_visuals(
    scene_plan: dict[str, Any],
    scenes: list[dict[str, Any]],
    global_objects: list[dict[str, str]],
) -> None:
    """用 bible 镜头覆盖转换器留下的「说话/站立」stub。"""
    bible_by_id = {str(s.get("id") or ""): s for s in scenes}
    for plan_scene in scene_plan.get("scenes") or []:
        if not isinstance(plan_scene, dict):
            continue
        bible_scene = bible_by_id.get(str(plan_scene.get("id") or "")) or {}
        scene_blocking = normalize_blocking(bible_scene.get("blocking"))
        scene_objects = _as_objects(bible_scene.get("objects")) or global_objects
        bible_shots = [s for s in (bible_scene.get("shots") or []) if isinstance(s, dict)]
        plan_shots = [s for s in (plan_scene.get("shots") or []) if isinstance(s, dict)]
        # V27 镜数守卫：bible 镜数 ≠ plan 镜数时，shot_id 精确匹配之外的索引/兜底
        # 取首镜逻辑会把错误视觉覆盖到未匹配镜（拆镜/加镜/删镜后最常见）。
        # 不阻断 compile，但必须显式 warning 提示"整场对齐或显式 shot_id"。
        if bible_shots and plan_shots and len(bible_shots) != len(plan_shots):
            plan_ids = [str(s.get("shot_id") or "") for s in plan_shots]
            bible_ids = [str(s.get("shot_id") or "") for s in bible_shots]
            unmatched = [pid for pid in plan_ids if pid and pid not in bible_ids]
            findings_holder = plan_scene.setdefault("_overlay_warnings", [])
            findings_holder.append({
                "scene_id": str(plan_scene.get("id") or ""),
                "bible_shots": len(bible_shots),
                "plan_shots": len(plan_shots),
                "unmatched_plan_shot_ids": unmatched,
                "message": (
                    f"bible 镜数 {len(bible_shots)} ≠ scene_plan 镜数 {len(plan_shots)}"
                    + (f"；未匹配 plan 镜 {unmatched} 将按索引/首镜兜底" if unmatched else "")
                ),
                "proposed_fix": "拆镜/加镜/删镜后整场对齐 bible.scenes[].shots[]，且每镜显式 shot_id",
            })
        for idx, plan_shot in enumerate(plan_shots):
            match = None
            sid = str(plan_shot.get("shot_id") or "")
            for cand in bible_shots:
                if sid and str(cand.get("shot_id") or "") == sid:
                    match = cand
                    break
            if match is None and idx < len(bible_shots):
                match = bible_shots[idx]
            elif match is None and bible_shots:
                match = bible_shots[0]
            _overlay_shot(
                plan_shot,
                match,
                scene_blocking=scene_blocking,
                fallback_objects=scene_objects,
            )
            scene_loc = str(bible_scene.get("location_id") or "").strip()
            if scene_loc and not str(plan_shot.get("location_id") or "").strip():
                plan_shot["location_id"] = scene_loc
        bgm_id = str(bible_scene.get("bgm_id") or "").strip()
        if bgm_id:
            plan_scene["bgm_id"] = bgm_id
        role = str(bible_scene.get("narrative_role") or "").strip()
        if role:
            plan_scene["narrative_role"] = role
        raw_sl = bible_scene.get("shot_language")
        if isinstance(raw_sl, dict):
            merged = dict(plan_scene.get("shot_language") or {}) if isinstance(plan_scene.get("shot_language"), dict) else {}
            for key, val in raw_sl.items():
                if val is None:
                    continue
                if isinstance(val, str) and not str(val).strip():
                    continue
                merged[str(key)] = val
            if merged:
                plan_scene["shot_language"] = merged


def compile_bible(
    bible: dict[str, Any],
    episode_plan: dict[str, Any] | None = None,
    playbook: dict[str, Any] | None = None,
    *,
    duration_policy: dict[str, Any] | None = None,
    project_dir: str | Path | None = None,
) -> dict[str, Any]:
    """纯函数：bible → {script, scene_plan, findings}。"""
    findings: list[dict[str, str]] = []
    if not isinstance(bible, dict):
        return {
            "script": {"title": "untitled", "sections": [{"id": "sc01", "narration": "empty"}]},
            "scene_plan": {"scenes": []},
            "findings": [{
                "severity": "critical",
                "stage": "bible",
                "field": "series_bible",
                "message": "bible 不是对象",
                "proposed_fix": "写入 artifacts/series_bible.json",
            }],
        }
    scenes, chars, props = _filter_episode(bible, episode_plan)
    props = cap_props(props, findings)
    sections = [_section_from_scene(sc) for sc in scenes]
    if not sections:
        sections = [{"id": "sc01", "narration": str(bible.get("logline") or "empty")}]
        findings.append({
            "severity": "warning",
            "stage": "bible",
            "field": "scenes",
            "message": "bible 无 scenes[]，已写占位场次",
            "proposed_fix": "按场填写 scenes[].id / lines / shots",
        })
    env = bible.get("environment")
    if env is None and scenes:
        env = scenes[0].get("environment")
    title = str(bible.get("title") or bible.get("logline") or "untitled").strip() or "untitled"
    script: dict[str, Any] = {
        "title": title,
        "sections": sections,
        "characters": chars,
        "props": [p if isinstance(p, dict) else {"id": str(p), "appearance": str(p)} for p in props],
    }
    if env is not None:
        script["environment"] = env
    if bible.get("tone"):
        script["tone"] = bible["tone"]
    structure = bible.get("structure")
    if isinstance(structure, dict) and any(str(v or "").strip() for v in structure.values()):
        script["structure"] = dict(structure)
    pb = playbook
    if pb is None:
        name = str(bible.get("playbook") or "").strip()
        if name:
            from montage.playbooks import get_playbook

            pb = get_playbook(name)
    from montage.providers.capabilities import policy_for_loop

    policy = duration_policy
    if policy is None:
        loop = None
        if project_dir:
            from montage.engine.policy import load_loop_policy

            loop = load_loop_policy(project_dir).get("video_loop")
        policy = policy_for_loop(loop)
    fill_on = bible.get("fill_shot_language", True) is not False
    # 章节归属（v8.2 P0-0）：显式 chapters 按 start_scene 区间归属；长片
    # （≥12 场）未声明时自动等距分章，供"按日拆批次=章节粒度"。
    # P0-6 分层锚沿层传递：归属后只保留**有场次归属**的章进 scene_plan 快照
    # ——子集（episode）物化时集边界与章边界不齐的话，不含本集场次的章
    # 不进子集，防止分层合成（chapter_layers）把空章当段。
    from montage.engine.story_outline import build_chapter_plan, chapter_bgm_fallback

    chapter_plan = build_chapter_plan(
        bible,
        [str(s.get("id") or "") for s in sections if isinstance(s, dict)],
        auto=True,
    )
    findings.extend(chapter_plan.get("findings") or [])
    scene_chapter_map = chapter_plan.get("scene_chapter") or {}
    owned_chapter_ids = {str(cid) for cid in scene_chapter_map.values() if cid}
    chapters_owned = [
        ch for ch in (chapter_plan.get("chapters") or [])
        if isinstance(ch, dict) and str(ch.get("id") or "") in owned_chapter_ids
    ]
    converted = convert_script_to_scene_plan(
        script,
        pb if isinstance(pb, dict) else None,
        duration_policy=policy,
        fill_shot_language=fill_on,
        defer_camera_fill=True,
        scene_chapter=scene_chapter_map,
        chapter_plan=chapters_owned,
        shot_skeletons=_shot_skeletons_by_scene(scenes),
    )
    findings.extend(converted.get("findings") or [])
    scene_plan = converted.get("scene_plan") or {"scenes": []}
    overlay_visuals(scene_plan, scenes, _as_objects(script.get("props")))
    # B2.5 逐镜在场清单 + 承接表：**必须在 overlay 之后**算——此时 scene_plan
    # 的 visual_details/blocking 已是 bible 的最终值，派生出的方位/道具才准。
    # presence 由编剧写在 bible.scenes[].shots[]，continuity 由编译器生成（只读）。
    _attach_presence_and_continuity(scene_plan, script, findings)
    # V27：把 overlay_visuals 的镜数不齐守卫落成正式 findings（从 scene_plan 摘除）
    for plan_scene in scene_plan.get("scenes") or []:
        if not isinstance(plan_scene, dict):
            continue
        for warn in plan_scene.pop("_overlay_warnings", []) or []:
            findings.append({
                "severity": "warning",
                "stage": "bible",
                "field": f"scenes[{warn.get('scene_id')}].shots",
                "message": str(warn.get("message") or ""),
                "proposed_fix": str(warn.get("proposed_fix") or ""),
            })
    align_location_ids(bible, scene_plan, findings)
    overlay_location_sensory(bible, scene_plan)
    findings.extend(_audit_vfx(scene_plan))
    from montage.engine.shot_budget import fill_shot_budget_class
    from montage.engine.shot_language import fill_shot_language

    findings.extend(fill_shot_budget_class(scene_plan))
    findings.extend(fill_shot_language(scene_plan, pb if isinstance(pb, dict) else None, enabled=fill_on))
    # 音乐锚降章节内辅助（v8.2 P0-0）：chapter.bgm_id 只兜底**无显式**
    # bgm_id 的场；overlay_visuals 已把显式 scene.bgm_id 抄进 plan，先查缺再补。
    if scene_chapter_map and chapters_owned:
        bgm_fallback = chapter_bgm_fallback(
            chapters_owned, scene_chapter_map,
        )
        for plan_scene in scene_plan.get("scenes") or []:
            if not isinstance(plan_scene, dict):
                continue
            if str(plan_scene.get("bgm_id") or "").strip():
                continue
            sid = str(plan_scene.get("id") or "")
            bgm = bgm_fallback.get(sid, "")
            if bgm:
                plan_scene["bgm_id"] = bgm
    hits = bible.get("library_hit_ids")
    if isinstance(hits, list) and hits:
        script["library_hit_ids"] = [str(x) for x in hits if x]
    return {"script": script, "scene_plan": scene_plan, "findings": findings}


def write_bible(project_dir: str | Path, bible: dict[str, Any]) -> Path:
    """写入 series_bible.json；已有文件则先备份为 series_bible.prev.json。"""
    art = Path(project_dir) / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    dest = art / "series_bible.json"
    prev = art / "series_bible.prev.json"
    if dest.is_file():
        shutil.copy2(dest, prev)
    dest.write_text(json.dumps(bible, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def write_compiled(
    project_dir: str | Path,
    script: dict[str, Any],
    scene_plan: dict[str, Any],
) -> None:
    store = ArtifactStore(project_dir)
    store.write("script", script)
    store.write("scene_plan", scene_plan)
