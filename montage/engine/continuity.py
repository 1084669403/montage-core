"""continuity — 场记 sidecar（P3）。

纯 dict，禁止 LLM，禁止放进 engine/director.py。
服装/道具/地点状态；不要写进 style_context 或 Agnes 提示词。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from montage.engine.artifacts import ArtifactStore


def empty_continuity() -> dict[str, Any]:
    return {
        "characters": [],
        "props": [],
        "locations": [],
        "last_shot_id": "",
    }


def _char_row(state: dict[str, Any], cid: str) -> dict[str, Any]:
    rows = state.setdefault("characters", [])
    if not isinstance(rows, list):
        rows = []
        state["characters"] = rows
    for row in rows:
        if isinstance(row, dict) and str(row.get("id") or "") == cid:
            return row
    row = {"id": cid, "outfit": "", "location_id": "", "held_prop_ids": []}
    rows.append(row)
    return row


def _prop_row(state: dict[str, Any], pid: str) -> dict[str, Any]:
    rows = state.setdefault("props", [])
    if not isinstance(rows, list):
        rows = []
        state["props"] = rows
    for row in rows:
        if isinstance(row, dict) and str(row.get("id") or "") == pid:
            return row
    row = {"id": pid, "present": True}
    rows.append(row)
    return row


def _location_row(state: dict[str, Any], lid: str) -> dict[str, Any]:
    rows = state.setdefault("locations", [])
    if not isinstance(rows, list):
        rows = []
        state["locations"] = rows
    for row in rows:
        if isinstance(row, dict) and str(row.get("id") or "") == lid:
            return row
    row = {"id": lid}
    rows.append(row)
    return row


def _shot_location_id(shot: dict[str, Any], scene_plan: dict[str, Any] | None) -> str:
    lid = str(shot.get("location_id") or "").strip()
    if lid:
        return lid
    scene_id = str(shot.get("scene_id") or "")
    if not scene_id or not scene_plan:
        return ""
    for scene in scene_plan.get("scenes") or []:
        if isinstance(scene, dict) and str(scene.get("id") or "") == scene_id:
            return str(scene.get("location_id") or "").strip()
    return ""


def _character_ids(shot: dict[str, Any], scene_plan: dict[str, Any] | None) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        token = str(raw or "").strip()
        if token and token not in seen:
            seen.add(token)
            ids.append(token)

    for cid in shot.get("character_ids") or []:
        add(cid)
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    for sub in vd.get("subjects") or []:
        if isinstance(sub, dict):
            add(sub.get("id") or sub.get("character_id"))
        else:
            add(sub)
    ap = shot.get("audio_prompt") if isinstance(shot.get("audio_prompt"), dict) else {}
    add(ap.get("speaker_id"))
    if not ids and scene_plan:
        scene_id = str(shot.get("scene_id") or "")
        for scene in scene_plan.get("scenes") or []:
            if not isinstance(scene, dict) or str(scene.get("id") or "") != scene_id:
                continue
            for line in scene.get("lines") or []:
                if isinstance(line, dict):
                    add(line.get("speaker_id"))
    return ids


def _prop_ids(shot: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        token = str(raw or "").strip()
        if token and token not in seen:
            seen.add(token)
            ids.append(token)

    for extra in shot.get("prop_ids") or []:
        add(extra)
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
    return ids[:8]


def _outfit_for(cid: str, registry: dict[str, dict[str, Any]] | None) -> str:
    char = (registry or {}).get(cid) or {}
    return str(char.get("outfit_anchor") or char.get("outfit") or "").strip()


def update_continuity(
    state: dict[str, Any] | None,
    shot: dict[str, Any],
    *,
    scene_plan: dict[str, Any] | None = None,
    registry: dict[str, dict[str, Any]] | None = None,
    script: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """每镜成功后更新场记。原地改传入 dict 并返回。"""
    out = state if isinstance(state, dict) else empty_continuity()
    out.setdefault("characters", [])
    out.setdefault("props", [])
    out.setdefault("locations", [])
    lid = _shot_location_id(shot, scene_plan)
    if lid:
        _location_row(out, lid)
    prop_ids = _prop_ids(shot)
    for pid in prop_ids:
        row = _prop_row(out, pid)
        row["present"] = True
    held = list(prop_ids)
    for cid in _character_ids(shot, scene_plan):
        row = _char_row(out, cid)
        outfit = _outfit_for(cid, registry)
        if outfit:
            row["outfit"] = outfit
        if lid:
            row["location_id"] = lid
        row["held_prop_ids"] = held
    sid = str(shot.get("shot_id") or "").strip()
    if sid:
        out["last_shot_id"] = sid
    return out


def format_continuity_note(
    state: dict[str, Any] | None,
    shot: dict[str, Any],
    *,
    scene_plan: dict[str, Any] | None = None,
) -> str:
    """给即梦/可灵 adapter 的一行场记。禁止写入 @图片 / <<<image / {台词}。"""
    if not isinstance(state, dict):
        return ""
    wanted = set(_character_ids(shot, scene_plan))
    bits: list[str] = []
    for row in state.get("characters") or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "")
        if wanted and cid not in wanted:
            continue
        if not cid:
            continue
        parts = [cid]
        outfit = str(row.get("outfit") or "").strip()
        if outfit:
            parts.append(f"仍穿{outfit}")
        loc = str(row.get("location_id") or "").strip()
        if loc:
            parts.append(f"地点={loc}")
        held = [str(x) for x in (row.get("held_prop_ids") or []) if x]
        if held:
            parts.append("手持" + "、".join(held))
        bits.append("，".join(parts))
    text = "；".join(bits)
    for token in ("@图片", "<<<image", "{台词}", "【字幕】", "(音乐)"):
        text = text.replace(token, "")
    return text.strip(" ；，")


def _previous_episode_dir(project_dir: str | Path) -> Path | None:
    from montage.engine.episodes import (
        EPISODE_ID_RE,
        _episode_series_root,
        load_episodes_index,
    )

    located = _episode_series_root(project_dir)
    if located is None:
        return None
    series, current_eid = located
    prev: str | None = None
    for row in load_episodes_index(series):
        eid = str(row.get("episode_id") or "").strip()
        if not eid or not EPISODE_ID_RE.fullmatch(eid):
            continue
        if eid == current_eid:
            if prev:
                return series / "episodes" / prev
            return None
        prev = eid
    return None


def load_or_seed_continuity(project_dir: str | Path) -> dict[str, Any]:
    """本集已有场记则用；否则按 episodes.json 顺序抄上一集 sidecar。"""
    store = ArtifactStore(project_dir)
    existing = store.read("continuity")
    if isinstance(existing, dict) and (
        existing.get("last_shot_id") or existing.get("characters")
    ):
        return existing
    prev_dir = _previous_episode_dir(project_dir)
    if prev_dir is not None:
        seeded = ArtifactStore(prev_dir).read("continuity")
        if isinstance(seeded, dict):
            store.write("continuity", seeded, schema=None)
            return seeded
    state = empty_continuity()
    return state


def write_continuity(project_dir: str | Path, state: dict[str, Any]) -> None:
    ArtifactStore(project_dir).write("continuity", state, schema=None)
