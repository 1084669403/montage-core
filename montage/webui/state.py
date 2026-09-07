"""state — 看板数据层（纯函数，不依赖 FastAPI，可直接测试）。

所有数据都从磁盘读取（项目目录是唯一事实来源）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from montage.engine.budget import BudgetLedger
from montage.engine.decisions import DecisionLog
from montage.engine.episodes import EPISODE_ID_RE, is_series_root
from montage.engine.stages import CheckpointStore, STAGE_ORDER

_STATUS_STYLE = {
    "completed": "done",
    "in_progress": "running",
    "awaiting_human": "waiting",
    "failed": "error",
    "pending": "idle",
}


def _card_from_meta(meta_path: Path, meta: dict[str, Any], *, kind: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    store = CheckpointStore(meta_path.parent)
    card = {
        "kind": kind,
        "project_id": meta.get("project_id", meta_path.parent.name),
        "title": meta.get("title", "(未命名)"),
        "pipeline_type": meta.get("pipeline_type", ""),
        "created_at": meta.get("created_at", ""),
        "progress": {
            stage: _status_style(store.read(stage).status if store.read(stage) else "pending")
            for stage in STAGE_ORDER
        },
        "next_stage": store.next_stage(),
    }
    if extra:
        card.update(extra)
    return card


def collect_projects(root: str | Path) -> list[dict[str, Any]]:
    """扫描 projects/ 下的项目列表（含系列子集）。"""
    root = Path(root)
    projects_dir = root / "projects"
    if not projects_dir.exists():
        return []
    result: list[dict[str, Any]] = []
    for meta_path in sorted(projects_dir.glob("*/project.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(meta, dict):
            continue
        kind = "series" if is_series_root(meta_path.parent) else "project"
        result.append(_card_from_meta(meta_path, meta, kind=kind))
    for meta_path in sorted(projects_dir.glob("*/episodes/*/project.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(meta, dict):
            continue
        parent_id = str(meta.get("parent_id") or meta_path.parent.parent.parent.name)
        episode_id = str(meta.get("episode_id") or meta_path.parent.name)
        if not EPISODE_ID_RE.fullmatch(episode_id):
            continue
        result.append(_card_from_meta(
            meta_path,
            meta,
            kind="episode",
            extra={
                "project_id": f"{parent_id}::{episode_id}",
                "parent_id": parent_id,
                "episode_id": episode_id,
                "detail_path": f"/api/projects/{parent_id}/episodes/{episode_id}",
            },
        ))
    return result


def project_detail(
    root: str | Path,
    project_id: str,
    episode_id: str | None = None,
) -> dict[str, Any] | None:
    """项目详情：元数据 / 阶段状态 / 产物 / 成本 / 决策。"""
    if not _safe_id(project_id):
        return None
    if episode_id is not None:
        if not EPISODE_ID_RE.fullmatch(str(episode_id)):
            return None
        project_dir = Path(root) / "projects" / project_id / "episodes" / episode_id
        display_id = f"{project_id}::{episode_id}"
        kind = "episode"
        readonly = False
    else:
        project_dir = Path(root) / "projects" / project_id
        display_id = project_id
        kind = "series" if is_series_root(project_dir) else "project"
        readonly = False
    meta_path = project_dir / "project.json"
    if not meta_path.exists():
        return None

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    store = CheckpointStore(project_dir)
    artifacts: dict[str, Any] = {}
    artifacts_dir = project_dir / "artifacts"
    if artifacts_dir.exists():
        for path in sorted(artifacts_dir.glob("*.json")):
            try:
                artifacts[path.stem] = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                artifacts[path.stem] = {"_error": "无法解析"}

    ledger = BudgetLedger(project_dir / "cost.jsonl")
    log = DecisionLog(project_dir / "decisions.jsonl")

    nxt = store.next_stage()
    gate: dict[str, Any] = {"stage": nxt, "missing": [], "invalid": [], "ok": True}
    if nxt:
        try:
            from montage.engine.gates import validate_completion

            g = validate_completion(project_dir, nxt, strict=False)
            gate = {
                "stage": nxt,
                "missing": g.get("missing") or [],
                "invalid": g.get("invalid") or [],
                "ok": not (g.get("missing") or g.get("invalid")),
            }
        except Exception:  # noqa: BLE001
            gate = {"stage": nxt, "missing": [], "invalid": [], "ok": True}

    packet = artifacts.get("proposal_packet") if isinstance(artifacts.get("proposal_packet"), dict) else {}
    ceiling = packet.get("budget_ceiling_usd") if isinstance(packet, dict) else None
    budget = ledger.totals()
    budget["ceiling_usd"] = ceiling

    raw_progress = artifacts.get("produce_progress") if isinstance(artifacts.get("produce_progress"), dict) else {}
    nxt_progress = raw_progress.get("next") if isinstance(raw_progress.get("next"), dict) else {}
    produce_progress = {
        "status": str(raw_progress.get("status") or ""),
        "next": {
            "argv": _produce_cli_argv(nxt_progress.get("argv")),
            "note": str(nxt_progress.get("note") or ""),
        },
    }

    return {
        "project_id": display_id,
        "kind": kind,
        "readonly": readonly,
        "meta": meta,
        "stages": [
            {
                "stage": stage,
                "status": _status_style(store.read(stage).status if store.read(stage) else "pending"),
                "human_approved": bool(store.read(stage).human_approved if store.read(stage) else False),
                "artifact": store.read(stage).artifact if store.read(stage) else None,
                "blocked": bool(
                    nxt == stage and (gate.get("missing") or gate.get("invalid"))
                ),
            }
            for stage in STAGE_ORDER
        ],
        "next_stage": nxt,
        "gate": gate,
        "artifacts": artifacts,
        "scenes": scene_strip(project_dir, artifacts),
        "shots": shot_strip(artifacts),
        "auto_edit": auto_edit_status(project_dir),
        "produce_media": produce_media_status(
            project_dir,
            project_id=project_id,
            episode_id=episode_id,
        ),
        "budget": budget,
        "decisions": [
            {"category": k[0], "subject": k[1], **v.to_dict()}
            for k, v in log.current().items()
        ],
        "review": review_board(project_dir),
        "produce_progress": produce_progress,
        "project_dir": str(project_dir),
        "parent_id": project_id if episode_id is not None else None,
        "episode_id": episode_id,
    }


ALLOWED_MEDIA = frozenset({"final.mp4", "plan.json", "report.json"})
ALLOWED_PRODUCE_MEDIA = frozenset({"final.mp4", "cover.jpg", "season.mp4"})


def _produce_cli_argv(raw: Any) -> list[str]:
    argv = [str(x) for x in (raw or []) if str(x)]
    if "produce" in argv:
        return argv[argv.index("produce"):]
    return argv


def review_board(project_dir: str | Path) -> dict[str, Any] | None:
    """导演确认卡 + 可复制的 produce argv（不含解释器）。无卡则 None。"""
    root = Path(project_dir)
    art = root / "artifacts"
    card_path = art / "review_card.json"
    progress_path = art / "produce_progress.json"
    card: dict[str, Any] | None = None
    if card_path.is_file():
        try:
            loaded = json.loads(card_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            loaded = None
        if isinstance(loaded, dict) and loaded.get("summary") is not None:
            card = loaded
    progress: dict[str, Any] = {}
    if progress_path.is_file():
        try:
            loaded = json.loads(progress_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            loaded = None
        if isinstance(loaded, dict):
            progress = loaded
    if card is None:
        return None
    nxt = progress.get("next") if isinstance(progress.get("next"), dict) else {}
    return {
        "card": card,
        "status": str(progress.get("status") or card.get("status") or ""),
        "next": {
            "argv": _produce_cli_argv(nxt.get("argv")),
            "note": str(nxt.get("note") or ""),
        },
    }


def _safe_id(value: str) -> bool:
    raw = str(value or "")
    if not raw or "::" in raw or ".." in raw or "/" in raw or "\\" in raw:
        return False
    return True


def produce_media_status(
    project_dir: str | Path,
    *,
    project_id: str,
    episode_id: str | None = None,
) -> dict[str, Any]:
    """七阶段成片：renders/final.mp4、cover.jpg、可选 season.mp4 的 exists+url。"""
    if episode_id:
        base_url = f"/media/{project_id}/episodes/{episode_id}/renders"
    else:
        base_url = f"/media/{project_id}/renders"
    renders = Path(project_dir) / "renders"
    out: dict[str, Any] = {}
    for key, name in (("final", "final.mp4"), ("cover", "cover.jpg"), ("season", "season.mp4")):
        path = renders / name
        out[key] = {
            "exists": path.is_file(),
            "url": f"{base_url}/{name}",
        }
    return out


def resolve_produce_media(
    root: str | Path,
    project_id: str,
    filename: str,
    *,
    episode_id: str | None = None,
) -> Path | None:
    """七阶段成片回看：只允许 renders 下 final.mp4 / cover.jpg / season.mp4。``::`` 不能当目录。"""
    if filename not in ALLOWED_PRODUCE_MEDIA:
        return None
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    if not _safe_id(project_id):
        return None
    root = Path(root)
    if episode_id is not None:
        if not EPISODE_ID_RE.fullmatch(str(episode_id)):
            return None
        base = (root / "projects" / project_id / "episodes" / episode_id / "renders").resolve()
    else:
        base = (root / "projects" / project_id / "renders").resolve()
    path = (base / filename).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path if path.is_file() else None


def auto_edit_status(project_dir: str | Path) -> dict[str, Any]:
    """独立 auto_edit 键：plan / report / final 各 {exists, path}。"""
    auto = Path(project_dir) / "auto_edit"
    result: dict[str, Any] = {}
    for key, name in (("plan", "plan.json"), ("report", "report.json"), ("final", "final.mp4")):
        path = auto / name
        exists = path.is_file()
        result[key] = {"exists": exists, "path": str(path) if exists else None}
    return result


def resolve_media_file(root: str | Path, project_id: str, filename: str) -> Path | None:
    """看板成片回看：只允许 auto_edit 下白名单文件，拒绝路径穿越。"""
    if filename not in ALLOWED_MEDIA:
        return None
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    if not _safe_id(project_id):
        return None
    base = (Path(root) / "projects" / project_id / "auto_edit").resolve()
    path = (base / filename).resolve()
    try:
        path.relative_to(base)
    except ValueError:
        return None
    return path if path.is_file() else None


def _status_style(status: str) -> str:
    return _STATUS_STYLE.get(status, "idle")


def scene_strip(project_dir: str | Path, artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    """分镜条：scene_plan + 可选缩略图（缺失不报错）。"""
    plan = artifacts.get("scene_plan")
    if not isinstance(plan, dict):
        return []
    thumbs = Path(project_dir) / ".webui_thumbs"
    out: list[dict[str, Any]] = []
    for sc in plan.get("scenes") or []:
        if not isinstance(sc, dict):
            continue
        sid = str(sc.get("id") or "")
        name = f"{sid}.jpg" if sid else ""
        exists = bool(name) and (thumbs / name).is_file()
        out.append({
            "id": sid,
            "description": sc.get("description") or "",
            "thumb": name if exists else None,
        })
    return out


def shot_strip(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    """retry 用的镜头 id，与 produce match_retry_ids / collect_shots 同源。"""
    from montage.tools.shot_runner import collect_shots

    plan = artifacts.get("scene_plan") if isinstance(artifacts.get("scene_plan"), dict) else None
    prompts = artifacts.get("shot_prompts") if isinstance(artifacts.get("shot_prompts"), dict) else None
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for shot in collect_shots(plan, prompts):
        sid = str(shot.get("shot_id") or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        out.append({
            "id": sid,
            "scene_id": str(shot.get("scene_id") or ""),
        })
    return out


def resolve_thumb(
    root: str | Path,
    project_id: str,
    name: str,
    *,
    episode_id: str | None = None,
) -> Path | None:
    """只允许 projects/<id>/.webui_thumbs/ 下的 basename 图片。"""
    base_name = Path(name).name
    if base_name != name or name in {".", ".."}:
        return None
    if "/" in name or "\\" in name or ".." in name:
        return None
    if not _safe_id(project_id):
        return None
    suffix = Path(name).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        return None
    if episode_id is not None:
        if not EPISODE_ID_RE.fullmatch(str(episode_id)):
            return None
        project_dir = Path(root) / "projects" / project_id / "episodes" / episode_id
    else:
        project_dir = Path(root) / "projects" / project_id
    try:
        ensure_thumb(project_dir, base_name)
    except Exception:  # noqa: BLE001
        pass
    thumbs = (project_dir / ".webui_thumbs").resolve()
    path = (thumbs / base_name).resolve()
    try:
        path.relative_to(thumbs)
    except ValueError:
        return None
    return path if path.is_file() else None


def ensure_thumb(project_dir: str | Path, name: str) -> Path | None:
    """按 scene_id 从 asset_manifest 抽/拷第一帧。ffmpeg 缺失则跳过，不抛错。"""
    dest = Path(project_dir) / ".webui_thumbs" / Path(name).name
    if dest.is_file():
        return dest
    scene_id = dest.stem
    manifest_path = Path(project_dir) / "artifacts" / "asset_manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    src_path: Path | None = None
    kind = ""
    for item in manifest.get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("scene_id") or "") != scene_id:
            continue
        candidate = Path(str(item.get("path") or ""))
        if candidate.is_file():
            src_path = candidate
            kind = str(item.get("kind") or "")
            break
    if src_path is None:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    image_ext = {".jpg", ".jpeg", ".png", ".webp"}
    if kind == "image" or src_path.suffix.lower() in image_ext:
        try:
            dest.write_bytes(src_path.read_bytes())
        except OSError:
            return None
        return dest if dest.is_file() else None
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [ffmpeg, "-y", "-ss", "0", "-i", str(src_path), "-frames:v", "1", "-q:v", "2", str(dest)],
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return dest if dest.is_file() else None
