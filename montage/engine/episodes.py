"""episodes — 多集物化（仅 artifacts/episodes.json length>=2）。

禁止 init_project(..., "show/episodes/ep01")。子集写在已知绝对路径
series_dir/episodes/<episode_id>/ 。空 scene_ids 不 compile 全集。
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from montage.engine.artifacts import ArtifactStore
from montage.engine.bible import compile_bible, write_compiled
from montage.engine.policy import load_loop_policy
from montage.providers.capabilities import policy_for_loop

EPISODE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finding(
    *,
    severity: str,
    field: str,
    message: str,
    proposed_fix: str,
    stage: str = "episodes",
) -> dict[str, str]:
    return {
        "severity": severity,
        "stage": stage,
        "field": field,
        "message": message,
        "proposed_fix": proposed_fix,
    }


def load_episodes_index(series_dir: str | Path) -> list[dict[str, Any]]:
    data = ArtifactStore(series_dir).read("episodes")
    if not isinstance(data, dict):
        return []
    rows = data.get("episodes")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def is_series_root(project_dir: str | Path) -> bool:
    """index length>=2 且 project.json 没有 parent_id / episode_id。"""
    root = Path(project_dir)
    meta_path = root / "project.json"
    if not meta_path.is_file():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(meta, dict):
        return False
    if str(meta.get("parent_id") or "").strip() or str(meta.get("episode_id") or "").strip():
        return False
    return len(load_episodes_index(root)) >= 2


def duration_policy_for(project_dir: str | Path) -> dict[str, Any]:
    """有 proposal.video_loop 跟供应商表；无则 {kind:none}。"""
    loop = load_loop_policy(project_dir).get("video_loop")
    return policy_for_loop(loop)


def write_episode_skeleton(
    ep_dir: Path,
    *,
    parent_id: str,
    episode_id: str,
    title: str,
    pipeline_type: str,
) -> None:
    """在已知绝对路径写与 init_project 相同的子目录，不经 workspace/projects/。"""
    for sub in ("artifacts", "renders", "history", "scratch"):
        (ep_dir / sub).mkdir(parents=True, exist_ok=True)
    for sub in ("images", "video", "videos", "audio", "music", "placed", "kenburns"):
        (ep_dir / "assets" / sub).mkdir(parents=True, exist_ok=True)
    meta_path = ep_dir / "project.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta.setdefault("version", "1.0")
        meta.setdefault("created_at", _now())
        meta["parent_id"] = parent_id
        meta["episode_id"] = episode_id
        meta.setdefault("project_id", episode_id)
        meta.setdefault("title", title)
        meta.setdefault("pipeline_type", pipeline_type)
    else:
        meta = {
            "version": "1.0",
            "created_at": _now(),
            "project_id": episode_id,
            "title": title,
            "pipeline_type": pipeline_type,
            "parent_id": parent_id,
            "episode_id": episode_id,
        }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    review = ep_dir / "REVIEW.md"
    if not review.is_file():
        review.write_text(
            "# REVIEW\n\n"
            f"本目录是系列 `{parent_id}` 的子集 `{episode_id}`。"
            "对本集跑 `python -m montage produce .` ；默认不在系列根拼季，全集完成后才可 `--season-concat` 写出 `renders/season.mp4`。\n",
            encoding="utf-8",
        )


def materialize_episodes(series_dir: str | Path) -> dict[str, Any]:
    """按 episodes.json 物化子集。空 scene_ids 跳过 compile。已有 scene_plan 不覆盖。"""
    root = Path(series_dir)
    findings: list[dict[str, str]] = []
    index = load_episodes_index(root)
    try:
        meta = json.loads((root / "project.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    parent_id = str(meta.get("project_id") or root.name)
    pipeline_type = str(meta.get("pipeline_type") or "cinematic")
    parent_title = str(meta.get("title") or parent_id)
    bible = ArtifactStore(root).read("series_bible")
    policy = duration_policy_for(root)

    seen_scenes: dict[str, str] = {}
    for row in index:
        eid = str(row.get("episode_id") or "").strip()
        for sid in row.get("scene_ids") or []:
            key = str(sid or "").strip()
            if not key:
                continue
            if key in seen_scenes and seen_scenes[key] != eid:
                findings.append(_finding(
                    severity="warning",
                    field="scene_ids",
                    message=f"场景 id 重叠: {key}（{seen_scenes[key]} 与 {eid or '?'}）",
                    proposed_fix="每场只划进一集；仍会物化，不自动重切",
                ))
            else:
                seen_scenes[key] = eid or seen_scenes.get(key, "")

    runnable: list[dict[str, Any]] = []
    materialized: list[dict[str, Any]] = []
    for row in index:
        eid = str(row.get("episode_id") or "").strip()
        info: dict[str, Any] = {"episode_id": eid, "path": None, "skipped": False}
        if not EPISODE_ID_RE.fullmatch(eid):
            findings.append(_finding(
                severity="critical",
                field="episode_id",
                message=f"非法 episode_id: {eid!r}",
                proposed_fix="只用字母数字、-、_（如 ep01）",
            ))
            info["skipped"] = True
            materialized.append(info)
            continue
        scene_ids = [str(x) for x in (row.get("scene_ids") or []) if str(x).strip()]
        ep_dir = root / "episodes" / eid
        write_episode_skeleton(
            ep_dir,
            parent_id=parent_id,
            episode_id=eid,
            title=f"{parent_title} / {eid}",
            pipeline_type=pipeline_type,
        )
        ArtifactStore(ep_dir).write("episode_plan", {
            "episode_id": eid,
            "scene_ids": scene_ids,
            "character_ids": [str(x) for x in (row.get("character_ids") or []) if x],
        })
        info["path"] = ep_dir
        if not scene_ids:
            findings.append(_finding(
                severity="critical",
                field="scene_ids",
                message=f"{eid} scene_ids 为空，已跳过（禁止把全集写入该集）",
                proposed_fix="在 artifacts/episodes.json 填写该集 scene_ids[]",
            ))
            info["skipped"] = True
            materialized.append(info)
            continue
        if not isinstance(bible, dict):
            findings.append(_finding(
                severity="critical",
                field="series_bible",
                message=f"{eid} 无法 compile：系列根缺少 series_bible.json",
                proposed_fix="先写 artifacts/series_bible.json 再 produce",
            ))
            info["skipped"] = True
            materialized.append(info)
            continue
        scene_plan_path = ep_dir / "artifacts" / "scene_plan.json"
        if scene_plan_path.is_file():
            info["skipped"] = False
            runnable.append(info)
            materialized.append(info)
            continue
        compiled = compile_bible(
            bible,
            episode_plan=row,
            duration_policy=policy,
            project_dir=root,
        )
        findings.extend(compiled.get("findings") or [])
        write_compiled(ep_dir, compiled["script"], compiled["scene_plan"])
        runnable.append(info)
        materialized.append(info)
    return {
        "episodes": materialized,
        "runnable": runnable,
        "findings": findings,
    }


def _episode_series_root(project_dir: str | Path) -> tuple[Path, str] | None:
    """仅当本集位于 <series>/episodes/<id>/ 且上两级是系列根。"""
    root = Path(project_dir)
    if root.parent.name != "episodes":
        return None
    series = root.parent.parent
    if not is_series_root(series):
        return None
    try:
        meta = json.loads((root / "project.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    eid = str(meta.get("episode_id") or root.name).strip()
    if not EPISODE_ID_RE.fullmatch(eid):
        return None
    return series, eid


def _still_retry_covers(
    retry_ids: set[str],
    *,
    portrait_id: str = "",
    prop_id: str = "",
    turnaround_id: str = "",
) -> bool:
    if not retry_ids:
        return False
    if portrait_id and (portrait_id in retry_ids or f"portrait/{portrait_id}" in retry_ids):
        return True
    if prop_id and (prop_id in retry_ids or f"prop/{prop_id}" in retry_ids):
        return True
    if turnaround_id and (
        turnaround_id in retry_ids or f"turnaround/{turnaround_id}" in retry_ids
    ):
        return True
    return False


def _resolve_media_file(project_dir: Path, raw: str) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    path = Path(text)
    candidates = [path] if path.is_absolute() else [project_dir / path, path]
    for cand in candidates:
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def _item_ready_here(project_dir: Path, item: dict[str, Any] | None) -> bool:
    if not isinstance(item, dict):
        return False
    if _resolve_media_file(project_dir, str(item.get("path") or "")) is not None:
        return True
    return str(item.get("url") or "").startswith("http")


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except (ValueError, OSError):
        return False


def _still_indexes(
    manifest: dict[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    portraits: dict[str, dict[str, Any]] = {}
    props: dict[str, dict[str, Any]] = {}
    turnarounds: dict[str, dict[str, Any]] = {}
    for ref in (manifest or {}).get("reference_assets") or []:
        if not isinstance(ref, dict):
            continue
        kind = str(ref.get("kind") or "")
        if kind == "portrait":
            cid = str(ref.get("character_id") or "")
            if cid:
                portraits[cid] = ref
        elif kind == "turnaround":
            cid = str(ref.get("character_id") or "")
            if cid:
                turnarounds[cid] = ref
        elif kind == "prop":
            pid = str(ref.get("prop_id") or "")
            if pid:
                props[pid] = ref
    return portraits, props, turnarounds


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


def _find_sibling_still(
    series: Path,
    current_eid: str,
    *,
    kind: str,
    id_key: str,
    asset_id: str,
) -> tuple[Path, dict[str, Any]] | None:
    """episodes.json 顺序、跳过本集，第一条文件或 http url ready 的同 id 静图。"""
    for row in load_episodes_index(series):
        eid = str(row.get("episode_id") or "").strip()
        if not eid or eid == current_eid or not EPISODE_ID_RE.fullmatch(eid):
            continue
        ep_dir = series / "episodes" / eid
        portraits, props, turnarounds = _still_indexes(ArtifactStore(ep_dir).read("asset_manifest"))
        if kind == "portrait":
            hit = portraits.get(asset_id)
        elif kind == "turnaround":
            hit = turnarounds.get(asset_id)
        else:
            hit = props.get(asset_id)
        if hit is not None and str(hit.get(id_key) or "") == asset_id and _item_ready_here(ep_dir, hit):
            return ep_dir, hit
    return None


def copy_sibling_still_refs(
    project_dir: str | Path,
    *,
    portrait_ids: list[str] | None = None,
    prop_ids: list[str] | None = None,
    retry_ids: set[str] | frozenset[str] | None = None,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把兄弟集已 ready 的 portrait/prop 拷进本集 assets/ 或抄 url。扁平项目 no-op。

    有本地文件则 copy2（禁止 symlink、禁止 ../../ 相对路径）；仅 http url 则抄进
    本集 manifest、不下载。retry 点名的 id 不复用。同时更新传入的 manifest。
    """
    out = manifest if isinstance(manifest, dict) else {"items": [], "reference_assets": []}
    out.setdefault("items", [])
    out.setdefault("reference_assets", [])
    located = _episode_series_root(project_dir)
    if located is None:
        return out
    series, current_eid = located
    root = Path(project_dir)
    retry = {str(x) for x in (retry_ids or []) if x}
    refs = out["reference_assets"]
    if not isinstance(refs, list):
        refs = []
        out["reference_assets"] = refs
    items = out["items"]
    if not isinstance(items, list):
        items = []
        out["items"] = items
    current_portraits, current_props, current_turnarounds = _still_indexes(out)
    copied = False
    still_jobs: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for cid in portrait_ids or []:
        token = str(cid or "").strip()
        if not token or not EPISODE_ID_RE.fullmatch(token) or ("portrait", token) in seen:
            continue
        seen.add(("portrait", token))
        still_jobs.append(("portrait", "character_id", token))
        if ("turnaround", token) not in seen:
            seen.add(("turnaround", token))
            still_jobs.append(("turnaround", "character_id", token))
    for pid in prop_ids or []:
        token = str(pid or "").strip()
        if not token or not EPISODE_ID_RE.fullmatch(token) or ("prop", token) in seen:
            continue
        seen.add(("prop", token))
        still_jobs.append(("prop", "prop_id", token))

    assets_root = root / "assets"
    img_dir = assets_root / "images"
    for kind, id_key, asset_id in still_jobs:
        if kind == "portrait" and _still_retry_covers(retry, portrait_id=asset_id):
            continue
        if kind == "turnaround" and _still_retry_covers(retry, turnaround_id=asset_id):
            continue
        if kind == "prop" and _still_retry_covers(retry, prop_id=asset_id):
            continue
        if kind == "portrait":
            current_hit = current_portraits.get(asset_id)
        elif kind == "turnaround":
            current_hit = current_turnarounds.get(asset_id)
        else:
            current_hit = current_props.get(asset_id)
        if _item_ready_here(root, current_hit):
            continue
        found = _find_sibling_still(series, current_eid, kind=kind, id_key=id_key, asset_id=asset_id)
        if found is None:
            continue
        sib_dir, sib_ref = found
        src = _resolve_media_file(sib_dir, str(sib_ref.get("path") or ""))
        provider = str(sib_ref.get("provider") or "")
        url = str(sib_ref.get("url") or "").strip()
        new_ref: dict[str, Any] = {
            "id": f"{kind}_{asset_id}",
            "kind": kind,
            id_key: asset_id,
        }
        if provider:
            new_ref["provider"] = provider
        row: dict[str, Any] = {"id": new_ref["id"], "kind": "image"}
        if src is not None:
            suffix = src.suffix if src.suffix else ".png"
            dest = img_dir / f"{kind}_{asset_id}{suffix}"
            try:
                img_dir.mkdir(parents=True, exist_ok=True)
                if dest.exists() or dest.is_symlink():
                    dest.unlink()
                shutil.copy2(src, dest)
            except OSError:
                continue
            if dest.is_symlink() or not dest.is_file() or not _is_under(dest, assets_root):
                try:
                    dest.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            rel = dest.relative_to(root).as_posix()
            new_ref["path"] = rel
            row["path"] = rel
            if provider:
                row["provider"] = provider
        elif url.startswith("http"):
            new_ref["url"] = url
            row["url"] = url
            if provider:
                row["provider"] = provider
        else:
            continue
        _upsert_ref(refs, new_ref, kind=kind, id_key=id_key, id_val=asset_id)
        _upsert_item(items, row)
        if kind == "portrait":
            current_portraits[asset_id] = new_ref
        elif kind == "turnaround":
            current_turnarounds[asset_id] = new_ref
        else:
            current_props[asset_id] = new_ref
        copied = True

    if copied:
        ArtifactStore(root).write("asset_manifest", out)
    return out
