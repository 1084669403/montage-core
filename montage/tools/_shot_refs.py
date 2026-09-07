"""shot_runner 引用解析 + 就绪判定 + 定妆收集 纯函数。

从 shot_runner.py 抽出，原文件保留显式重导出 shim。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from montage.toolbase import ToolResult

from montage.tools._shot_constants import (
    _AGNES_FLASH_IMAGE_RANK,
    _AGNES_FLASH_MAX_IMAGES,
    _MAX_PROPS,
    _REFINE_HINT,
)
from montage.tools._shot_route import _agnes_is_v25, collect_shots


def _http_video_urls(shot: dict[str, Any], refs: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for key in ("video_url", "reference_video_url"):
        raw = str(shot.get(key) or "").strip()
        if raw.startswith("http") and raw not in urls:
            urls.append(raw)
    for ref in refs:
        if str(ref.get("kind") or "") != "video":
            continue
        raw = str(ref.get("url") or "").strip()
        if raw.startswith("http") and raw not in urls:
            urls.append(raw)
    return urls


def _http_still_urls(refs: list[dict[str, Any]]) -> list[str]:
    kinds = {"portrait", "turnaround", "scene_ref", "style_anchor", "prop"}
    urls: list[str] = []
    for ref in refs:
        if str(ref.get("kind") or "") not in kinds:
            continue
        raw = str(ref.get("url") or "").strip()
        if raw.startswith("http") and raw not in urls:
            urls.append(raw)
    return urls


def _identity_http_refs(
    shot: dict[str, Any],
    refs: list[dict[str, Any]],
    script: dict[str, Any] | None,
    scene_plan: dict[str, Any] | None,
    *,
    skip_urls: set[str] | frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """本镜定妆/四视图/场景/道具的公网 URL，供 Omni subject / Agnes extra_body。"""
    skip = {str(u).strip() for u in (skip_urls or []) if str(u).strip()}
    out: list[dict[str, Any]] = []
    seen: set[str] = set(skip)
    for ref in resolve_shot_refs(shot, {"reference_assets": refs}, script, scene_plan):
        url = str(ref.get("url") or "").strip()
        if not url.startswith("http") or url in seen:
            continue
        seen.add(url)
        out.append({"url": url, "type": "subject", "kind": ref.get("kind")})
    return out


def _agnes_flash_images(identity: list[dict[str, Any]]) -> list[str]:
    """Flash reference：定妆优先，最多 5 张 https。切段与首段必须同一套。"""
    ordered: list[str] = []
    for ref in sorted(
        identity,
        key=lambda r: _AGNES_FLASH_IMAGE_RANK.get(str(r.get("kind") or ""), 9),
    ):
        url = str(ref.get("url") or "").strip()
        if url.startswith("http") and url not in ordered:
            ordered.append(url)
    return ordered[:_AGNES_FLASH_MAX_IMAGES]


def _vlm_expected(
    shot: dict[str, Any],
    registry: dict[str, dict[str, Any]],
    scene_plan: dict[str, Any] | None,
    portraits: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cids = _character_ids(shot, scene_plan)
    appearances: list[str] = []
    outfits: list[str] = []
    portrait_path = ""
    for cid in cids:
        char = registry.get(cid) or {}
        app = str(char.get("appearance") or "").strip()
        if app:
            appearances.append(app)
        outfit = str(char.get("outfit_anchor") or char.get("outfit") or "").strip()
        if outfit:
            outfits.append(outfit)
        if not portrait_path and portraits:
            hit = portraits.get(cid) or {}
            portrait_path = str(hit.get("path") or "")
    return {
        "expected": {
            "appearance": "；".join(appearances),
            "outfit": "；".join(outfits),
            "location": str(shot.get("location_id") or ""),
            "props": collect_prop_ids([shot], None, cap=8),
        },
        "portrait_path": portrait_path,
    }


def _needs_agnes_refine(report: dict[str, Any] | None, expected: float) -> bool:
    data = report if isinstance(report, dict) else {}
    if _critical_fail(data):
        return True
    try:
        duration = float(data.get("duration_seconds"))
    except (TypeError, ValueError):
        return False
    return expected > 0 and duration < expected * 0.8


def _media_item(
    *,
    item_id: str,
    kind: str,
    path: str,
    scene_id: str,
    shot_id: str,
    provider: str,
    url: str = "",
) -> dict[str, Any]:
    row = {
        "id": item_id,
        "kind": kind,
        "path": path,
        "scene_id": scene_id,
        "shot_id": shot_id,
        "provider": provider,
    }
    if url:
        row["url"] = url
    return row


def _character_ids(shot: dict[str, Any], scene_plan: dict[str, Any] | None) -> list[str]:
    ids: list[str] = []
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    for sub in vd.get("subjects") or []:
        if isinstance(sub, dict) and sub.get("id") and sub["id"] not in ids:
            ids.append(str(sub["id"]))
    if ids:
        return ids
    scene_id = str(shot.get("scene_id") or "")
    for scene in (scene_plan or {}).get("scenes") or []:
        if str(scene.get("id") or "") == scene_id:
            for cid in scene.get("character_ids") or []:
                if cid and cid not in ids:
                    ids.append(str(cid))
            break
    return ids


def _registry_map(scene_plan: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for char in (scene_plan or {}).get("character_registry") or []:
        if isinstance(char, dict) and char.get("id"):
            out[str(char["id"])] = char
    return out


def _media_exists(project_dir: str, raw: str) -> bool:
    text = str(raw or "").strip()
    if not text:
        return False
    path = Path(text)
    candidates = [path] if path.is_absolute() else ([Path(project_dir) / path, path] if project_dir else [path])
    return any(cand.is_file() for cand in candidates)


def _item_ready(project_dir: str, item: dict[str, Any] | None) -> bool:
    if not isinstance(item, dict):
        return False
    if _media_exists(project_dir, str(item.get("path") or "")):
        return True
    return str(item.get("url") or "").startswith("http")


def _ref_ready(project_dir: str, item: dict[str, Any] | None, *, require_url: bool = False) -> bool:
    """Agnes 2.5 参考图必须是 https；可灵认 https 或可装箱本地文件。"""
    if require_url:
        return isinstance(item, dict) and str(item.get("url") or "").startswith("http")
    return _item_ready(project_dir, item)


def _agnes_cast_needs_url(policy: dict[str, Any] | None = None) -> bool:
    if not _agnes_is_v25():
        return False
    loop = str((policy or {}).get("video_loop") or "").strip().lower()
    return loop in ("", "agnes")


def _cast_needs_url(policy: dict[str, Any] | None = None) -> bool:
    loop = str((policy or {}).get("video_loop") or "").strip().lower()
    if loop == "kling":
        return False
    return _agnes_cast_needs_url(policy)


def _items_of(
    manifest: dict[str, Any] | None,
    *,
    kind: str,
    shot_id: str = "",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in (manifest or {}).get("items") or []:
        if not isinstance(item, dict) or str(item.get("kind") or "") != kind:
            continue
        if shot_id and str(item.get("shot_id") or "") != shot_id:
            continue
        if kind == "image" and "portrait" in str(item.get("id") or ""):
            continue
        out.append(item)
    return out


def shot_final_ready(
    shot: dict[str, Any],
    manifest: dict[str, Any] | None,
    project_dir: str = "",
) -> bool:
    """能否进 W0：视频镜要有 video，或非 first_frame 的静图 clip（Ken Burns）。"""
    sid = str(shot.get("shot_id") or "")
    if not sid:
        return False
    if str(shot.get("shot_kind") or "video") == "image":
        return any(_item_ready(project_dir, item) for item in _items_of(manifest, kind="image", shot_id=sid))
    if any(_item_ready(project_dir, item) for item in _items_of(manifest, kind="video", shot_id=sid)):
        return True
    for item in _items_of(manifest, kind="image", shot_id=sid):
        if "_first" in str(item.get("id") or ""):
            continue
        if _item_ready(project_dir, item):
            return True
    return False


def clips_compose_ready(
    scene_plan: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    project_dir: str = "",
) -> bool:
    shots = collect_shots(scene_plan, None)
    if not shots:
        return False
    return all(shot_final_ready(shot, manifest, project_dir) for shot in shots)


def _retry_covers(
    retry_ids: set[str],
    *,
    shot_id: str = "",
    portrait_id: str = "",
    prop_id: str = "",
    turnaround_id: str = "",
    location_id: str = "",
) -> bool:
    if not retry_ids:
        return False
    if shot_id and shot_id in retry_ids:
        return True
    if portrait_id and (portrait_id in retry_ids or f"portrait/{portrait_id}" in retry_ids):
        return True
    if turnaround_id and (
        turnaround_id in retry_ids or f"turnaround/{turnaround_id}" in retry_ids
    ):
        return True
    if location_id and (
        location_id in retry_ids
        or f"scene_ref/{location_id}" in retry_ids
        or f"location/{location_id}" in retry_ids
    ):
        return True
    if prop_id and (prop_id in retry_ids or f"prop/{prop_id}" in retry_ids):
        return True
    return False


def _job_already_done(
    job: dict[str, Any],
    *,
    manifest: dict[str, Any] | None,
    project_dir: str,
    retry_ids: set[str],
    portraits: dict[str, dict[str, Any]],
    props: dict[str, dict[str, Any]],
    turnarounds: dict[str, dict[str, Any]] | None = None,
    scene_refs: dict[str, dict[str, Any]] | None = None,
    frames_only: bool = False,
    require_url: bool = False,
    force_ids: set[str] | None = None,
) -> bool:
    kind = str(job.get("kind") or "")
    if kind == "portrait":
        cid = str(job.get("character_id") or "")
        if _retry_covers(retry_ids, portrait_id=cid):
            return False
        return _ref_ready(project_dir, portraits.get(cid), require_url=require_url)
    if kind == "turnaround":
        cid = str(job.get("character_id") or "")
        if _retry_covers(retry_ids, turnaround_id=cid):
            return False
        return _ref_ready(project_dir, (turnarounds or {}).get(cid), require_url=require_url)
    if kind == "scene_ref":
        lid = str(job.get("location_id") or "")
        if _retry_covers(retry_ids, location_id=lid):
            return False
        return _ref_ready(project_dir, (scene_refs or {}).get(lid), require_url=require_url)
    if kind == "prop":
        pid = str(job.get("prop_id") or "")
        if _retry_covers(retry_ids, prop_id=pid):
            return False
        return _ref_ready(project_dir, props.get(pid), require_url=require_url)
    sid = str(job.get("shot_id") or "")
    force = {str(x) for x in (force_ids or []) if x}
    if kind == "video":
        if _retry_covers(retry_ids, shot_id=sid) or sid in force:
            return False
        return any(_item_ready(project_dir, item) for item in _items_of(manifest, kind="video", shot_id=sid))
    if kind == "first_frame":
        if (frames_only and _retry_covers(retry_ids, shot_id=sid)) or sid in force:
            return False
        return any(_item_ready(project_dir, item) for item in _items_of(manifest, kind="image", shot_id=sid))
    if _retry_covers(retry_ids, shot_id=sid) or sid in force:
        return False
    return False


def _spoken_skip_portraits(
    scene_plan: dict[str, Any] | None,
    script: dict[str, Any] | None,
    format_card: dict[str, Any] | None,
    bible: dict[str, Any] | None = None,
) -> bool:
    from montage.tools.script_validator import is_spoken_mode

    doc = dict(bible or script or {})
    if not doc.get("characters"):
        doc["characters"] = list((scene_plan or {}).get("character_registry") or [])
    if not doc.get("playbook"):
        doc["playbook"] = str(
            (bible or {}).get("playbook")
            or (scene_plan or {}).get("playbook")
            or ""
        )
    return is_spoken_mode(bible=doc, format_card=format_card)


def _ref_index(
    manifest: dict[str, Any] | None,
    kind: str,
    id_key: str,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ref in (manifest or {}).get("reference_assets") or []:
        if not isinstance(ref, dict) or str(ref.get("kind") or "") != kind:
            continue
        kid = str(ref.get(id_key) or "")
        if kid and (ref.get("path") or ref.get("url")):
            out[kid] = ref
    return out


def _portrait_index(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return _ref_index(manifest, "portrait", "character_id")


def _turnaround_index(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return _ref_index(manifest, "turnaround", "character_id")


def _scene_ref_index(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    by_loc = _ref_index(manifest, "scene_ref", "location_id")
    if by_loc:
        return by_loc
    return _ref_index(manifest, "scene_ref", "scene_id")


def _prop_index(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return _ref_index(manifest, "prop", "prop_id")


def _char_for_prompt(char: dict[str, Any]) -> dict[str, Any]:
    row = dict(char)
    if not str(row.get("outfit_anchor") or "").strip():
        row["outfit_anchor"] = str(row.get("outfit") or "")
    return row


def _append_note(prompt: str, note: str) -> str:
    text = str(note or "").strip()
    if not text or not prompt:
        return prompt
    return f"{prompt}。修正：{text}"


def _location_scene(loc: dict[str, Any]) -> dict[str, Any]:
    lid = str(loc.get("id") or loc.get("name") or "").strip()
    desc = str(
        loc.get("sensory") or loc.get("appearance") or loc.get("name") or lid
    ).strip()
    note = str(loc.get("cast_note") or "").strip()
    parts = [p for p in (desc, "无人空镜，不锁机位", note) if p]
    return {"id": lid, "description": "，".join(parts)}


def collect_cast_jobs(
    bible: dict[str, Any] | None,
    *,
    scene_plan: dict[str, Any] | None = None,
    video_loop: str = "",
    sample_shot_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """根据 bible 列出定妆作业：全身照、四视图、空镜、道具白底。

    可灵环：忽略 skip_turnaround（一张拼板含四视图），有 scene_plan 时按出场过滤。
    """
    loop = str(video_loop or "").strip().lower()
    kling = loop == "kling"
    allow_chars: set[str] | None = None
    allow_locs: set[str] | None = None
    allow_props: set[str] | None = None
    if kling and isinstance(scene_plan, dict):
        shots = collect_shots(scene_plan, None)
        if shots:
            wanted: set[str] | None = None
            if sample_shot_ids:
                origins = [str(x) for x in sample_shot_ids if x]
                wanted = set(origins)
                ordered = [str(s.get("shot_id") or "") for s in shots]
                # 只从最早点名镜并入下一镜；调用方已传入窗口时不再扩到第三镜。
                for sid in origins[:1]:
                    if sid in ordered:
                        idx = ordered.index(sid)
                        if idx + 1 < len(ordered) and ordered[idx + 1]:
                            wanted.add(ordered[idx + 1])
                shots = [s for s in shots if str(s.get("shot_id") or "") in wanted]
            allow_chars = set()
            allow_locs = set()
            allow_props = set(collect_prop_ids(shots, bible, cap=_MAX_PROPS))
            for shot in shots:
                for cid in _character_ids(shot, scene_plan):
                    allow_chars.add(cid)
                speaker = str(shot.get("speaker_id") or "").strip()
                if speaker:
                    allow_chars.add(speaker)
                lid = str(shot.get("location_id") or "").strip()
                if lid:
                    allow_locs.add(lid)
    jobs: list[dict[str, Any]] = []
    for char in (bible or {}).get("characters") or []:
        if not isinstance(char, dict):
            continue
        cid = str(char.get("id") or "").strip()
        if not cid:
            continue
        if allow_chars is not None and cid not in allow_chars:
            continue
        jobs.append({
            "kind": "portrait",
            "subject": f"portrait/{cid}",
            "character_id": cid,
            "category": "image_generation",
        })
        if kling:
            continue
        if not char.get("skip_turnaround"):
            jobs.append({
                "kind": "turnaround",
                "subject": f"turnaround/{cid}",
                "character_id": cid,
                "category": "image_generation",
            })
    for loc in (bible or {}).get("locations") or []:
        if not isinstance(loc, dict):
            continue
        lid = str(loc.get("id") or loc.get("name") or "").strip()
        if not lid:
            continue
        if allow_locs is not None and lid not in allow_locs:
            continue
        jobs.append({
            "kind": "scene_ref",
            "subject": f"scene_ref/{lid}",
            "location_id": lid,
            "category": "image_generation",
        })
    n_props = 0
    for prop in (bible or {}).get("props") or []:
        pid = ""
        if isinstance(prop, dict):
            pid = str(prop.get("id") or prop.get("name") or "").strip()
        elif isinstance(prop, str):
            pid = prop.strip()
        if not pid:
            continue
        if allow_props is not None and pid not in allow_props:
            continue
        jobs.append({
            "kind": "prop",
            "subject": f"prop/{pid}",
            "prop_id": pid,
            "category": "image_generation",
        })
        n_props += 1
        if n_props >= _MAX_PROPS:
            break
    return jobs


def _cast_ref_for_job(job: dict[str, Any], manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    kind = str(job.get("kind") or "")
    if kind == "portrait":
        return _portrait_index(manifest).get(str(job.get("character_id") or ""))
    if kind == "turnaround":
        return _turnaround_index(manifest).get(str(job.get("character_id") or ""))
    if kind == "scene_ref":
        return _scene_ref_index(manifest).get(str(job.get("location_id") or ""))
    if kind == "prop":
        return _prop_index(manifest).get(str(job.get("prop_id") or ""))
    return None


def first_frame_item(
    shot: dict[str, Any],
    manifest: dict[str, Any] | None,
    project_dir: str = "",
) -> dict[str, Any] | None:
    """该镜已落盘的首帧静图（任意 kind=image + shot_id，含 *_first）。"""
    sid = str(shot.get("shot_id") or "")
    if not sid:
        return None
    for item in reversed(_items_of(manifest, kind="image", shot_id=sid)):
        if _item_ready(project_dir, item):
            return item
    return None


def frames_missing(
    scene_plan: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    project_dir: str,
    retry_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """尚未就绪（或被 retry 点名）的首帧。"""
    retry_ids = retry_ids or set()
    missing: list[dict[str, Any]] = []
    for shot in collect_shots(scene_plan, None):
        sid = str(shot.get("shot_id") or "")
        if not sid:
            continue
        if _retry_covers(retry_ids, shot_id=sid) or not first_frame_item(shot, manifest, project_dir):
            missing.append(shot)
    return missing


def cast_missing(
    bible: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    project_dir: str,
    retry_ids: set[str] | None = None,
    *,
    require_url: bool = False,
    scene_plan: dict[str, Any] | None = None,
    video_loop: str = "",
    sample_shot_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """尚未就绪（或被 retry 点名）的定妆作业。"""
    retry_ids = retry_ids or set()
    missing: list[dict[str, Any]] = []
    for job in collect_cast_jobs(
        bible,
        scene_plan=scene_plan,
        video_loop=video_loop,
        sample_shot_ids=sample_shot_ids,
    ):
        kind = str(job.get("kind") or "")
        force = False
        if kind == "portrait":
            force = _retry_covers(retry_ids, portrait_id=str(job.get("character_id") or ""))
        elif kind == "turnaround":
            force = _retry_covers(retry_ids, turnaround_id=str(job.get("character_id") or ""))
        elif kind == "scene_ref":
            force = _retry_covers(retry_ids, location_id=str(job.get("location_id") or ""))
        elif kind == "prop":
            force = _retry_covers(retry_ids, prop_id=str(job.get("prop_id") or ""))
        if force or not _ref_ready(
            project_dir, _cast_ref_for_job(job, manifest), require_url=require_url,
        ):
            missing.append(job)
    return missing


def _upsert_ref(
    refs: list[dict[str, Any]],
    new_ref: dict[str, Any],
    *,
    kind: str,
    id_key: str,
    id_val: str,
) -> None:
    for idx, ref in enumerate(refs):
        if not isinstance(ref, dict):
            continue
        if str(ref.get("kind") or "") == kind and str(ref.get(id_key) or "") == id_val:
            refs[idx] = new_ref
            return
    refs.append(new_ref)


def _upsert_item(items: list[dict[str, Any]], row: dict[str, Any]) -> None:
    rid = str(row.get("id") or "")
    if rid:
        for idx, item in enumerate(items):
            if isinstance(item, dict) and str(item.get("id") or "") == rid:
                items[idx] = row
                return
    items.append(row)


def _script_prop_map(script: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for prop in (script or {}).get("props") or []:
        if isinstance(prop, dict) and (prop.get("id") or prop.get("name")):
            out[str(prop.get("id") or prop.get("name"))] = prop
        elif isinstance(prop, str) and prop.strip():
            out[prop.strip()] = {"id": prop.strip(), "name": prop.strip(), "appearance": prop.strip()}
    return out


def collect_prop_ids(
    shots: list[dict[str, Any]],
    script: dict[str, Any] | None = None,
    *,
    cap: int = _MAX_PROPS,
) -> list[str]:
    """镜头 objects / audio_prompt 引用到的 prop_id，去重后全片上限 cap。"""
    known = _script_prop_map(script)
    ids: list[str] = []

    def add(raw: Any) -> None:
        pid = str(raw or "").strip()
        if not pid or pid in ids:
            return
        if known and pid not in known:
            return
        ids.append(pid)

    for shot in shots:
        vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
        for obj in vd.get("objects") or []:
            if isinstance(obj, dict):
                add(obj.get("id") or obj.get("prop_id") or obj.get("name"))
            else:
                add(obj)
        ap = shot.get("audio_prompt") if isinstance(shot.get("audio_prompt"), dict) else {}
        for key in ("prop_ids", "props"):
            for item in ap.get(key) or []:
                if isinstance(item, dict):
                    add(item.get("id") or item.get("prop_id"))
                else:
                    add(item)
        for extra in shot.get("prop_ids") or []:
            add(extra)
    return ids[:cap]


def resolve_shot_refs(
    shot: dict[str, Any],
    manifest: dict[str, Any] | None,
    script: dict[str, Any] | None = None,
    scene_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按 character_ids / scene_id / prop 引用收集 reference_assets。"""
    refs: list[dict[str, Any]] = []
    portraits = _portrait_index(manifest)
    turnarounds = _turnaround_index(manifest)
    props = _prop_index(manifest)
    seen: set[str] = set()
    for cid in _character_ids(shot, scene_plan):
        for hit in (turnarounds.get(cid), portraits.get(cid)):
            if hit and hit.get("id") not in seen:
                refs.append(hit)
                seen.add(str(hit.get("id") or cid))
    scene_id = str(shot.get("scene_id") or "")
    location_id = str(shot.get("location_id") or "")
    if not location_id and scene_plan:
        for scene in scene_plan.get("scenes") or []:
            if isinstance(scene, dict) and str(scene.get("id") or "") == scene_id:
                location_id = str(scene.get("location_id") or "").strip()
                break
    for ref in (manifest or {}).get("reference_assets") or []:
        if not isinstance(ref, dict) or ref.get("kind") != "scene_ref":
            continue
        rid = str(ref.get("id") or "")
        if rid in seen:
            continue
        if location_id and str(ref.get("location_id") or "") == location_id:
            refs.append(ref)
            seen.add(rid)
            continue
        if scene_id and str(ref.get("scene_id") or "") == scene_id:
            refs.append(ref)
            seen.add(rid)
    for pid in collect_prop_ids([shot], script, cap=_MAX_PROPS):
        hit = props.get(pid)
        if hit and str(hit.get("id") or pid) not in seen:
            refs.append(hit)
            seen.add(str(hit.get("id") or pid))
    return refs


def _prop_prompt(prop: dict[str, Any]) -> str:
    name = str(prop.get("name") or prop.get("id") or "prop")
    appearance = str(prop.get("appearance") or name)
    return (
        f"cinematic product still of {name}, {appearance}, "
        "white background, isolated object, still life, studio lighting, "
        "no people, no hands, not handheld, sharp detail"
    )


def _media_path(result: ToolResult, fallback: str) -> str:
    data = result.data if isinstance(result.data, dict) else {}
    for key in ("output", "local_path", "path", "cached_path"):
        val = data.get(key)
        if val:
            return str(val)
    if fallback and Path(fallback).exists():
        return fallback
    return fallback


def _media_url(result: ToolResult) -> str:
    data = result.data if isinstance(result.data, dict) else {}
    for key in ("url", "image_url", "video_url"):
        val = data.get(key)
        if val:
            return str(val)
    urls = data.get("urls")
    if isinstance(urls, list) and urls:
        return str(urls[0])
    return ""


def _skip_pacing() -> bool:
    if os.environ.get("MONTAGE_SKIP_PACING") == "1":
        return True
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _critical_fail(report: dict[str, Any]) -> bool:
    if report.get("skipped") or report.get("ok"):
        return False
    return any(i.get("severity") == "critical" for i in (report.get("issues") or []))
