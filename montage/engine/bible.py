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


def _merge_action(base: Any, overlay: Any) -> dict[str, str]:
    out = dict(base) if isinstance(base, dict) else {}
    if isinstance(overlay, dict):
        for key, val in overlay.items():
            if val:
                out[str(key)] = val
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
            base["action"] = _merge_action(base.get("action"), sub.get("action"))
            if sub.get("position"):
                base["position"] = str(sub["position"])
            elif pos:
                base["position"] = pos
            merged_subjects.append(base)
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


def overlay_location_sensory(bible: dict[str, Any], scene_plan: dict[str, Any]) -> None:
    """把 locations[].sensory 抄进每镜；环境句按景别裁切，不编造地标。"""
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
    for plan_scene in scene_plan.get("scenes") or []:
        if not isinstance(plan_scene, dict):
            continue
        scene_lid = str(plan_scene.get("location_id") or "").strip()
        for plan_shot in plan_scene.get("shots") or []:
            if not isinstance(plan_shot, dict):
                continue
            lid = str(plan_shot.get("location_id") or scene_lid or "").strip()
            loc = by_id.get(lid) or {}
            sensory = str(loc.get("sensory") or "").strip()
            if not sensory:
                continue
            plan_shot["location_sensory"] = sensory
            vd = plan_shot.get("visual_details")
            if not isinstance(vd, dict):
                vd = {}
                plan_shot["visual_details"] = vd
            sl = plan_shot.get("shot_language") if isinstance(plan_shot.get("shot_language"), dict) else {}
            cropped = crop_location_sensory(sensory, str(sl.get("shot_size") or ""))
            # 始终用 location.sensory 覆盖 vd.environment：它是该镜头的权威
            # 机位环境描述。convert_script_to_scene_plan 会把 script.environment
            # （全局/首场）拍扁进每镜，若不覆盖，室内镜会被全局室外环境污染。
            if cropped:
                vd["environment"] = cropped.rstrip("。")


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
    converted = convert_script_to_scene_plan(
        script,
        pb if isinstance(pb, dict) else None,
        duration_policy=policy,
        fill_shot_language=fill_on,
        defer_camera_fill=True,
    )
    findings.extend(converted.get("findings") or [])
    scene_plan = converted.get("scene_plan") or {"scenes": []}
    overlay_visuals(scene_plan, scenes, _as_objects(script.get("props")))
    align_location_ids(bible, scene_plan, findings)
    overlay_location_sensory(bible, scene_plan)
    from montage.engine.shot_budget import fill_shot_budget_class
    from montage.engine.shot_language import fill_shot_language

    findings.extend(fill_shot_budget_class(scene_plan))
    findings.extend(fill_shot_language(scene_plan, pb if isinstance(pb, dict) else None, enabled=fill_on))
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
