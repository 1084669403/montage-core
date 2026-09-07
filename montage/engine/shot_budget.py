"""shot_budget — 镜头成本分层（hero / talk / establishing）。

W3 波次 1：缺 class 一律 talk；GEN 路径才改 shot_kind；30% 只计尚未有
video 文件的 hero。不读 bible，不猜 establishing。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

LEGAL_CLASS = frozenset({"hero", "talk", "establishing"})
HERO_CAP = 0.30


def normalize_class(raw: Any) -> str:
    val = str(raw or "").strip().lower()
    return val if val in LEGAL_CLASS else ""


def plan_shots(scene_plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    """嵌套时间线里的镜（可变引用）。"""
    out: list[dict[str, Any]] = []
    for scene in (scene_plan or {}).get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for shot in scene.get("shots") or []:
            if isinstance(shot, dict):
                out.append(shot)
    return out


def shot_duration(shot: dict[str, Any]) -> float:
    try:
        return max(0.0, float(shot.get("duration_seconds") or 0))
    except (TypeError, ValueError):
        return 0.0


def _path_exists(project_dir: str, raw: str) -> bool:
    text = str(raw or "").strip()
    if not text:
        return False
    path = Path(text)
    cands = [path] if path.is_absolute() else ([Path(project_dir) / path, path] if project_dir else [path])
    return any(cand.is_file() for cand in cands)


def has_video_file(
    shot: dict[str, Any],
    manifest: dict[str, Any] | None,
    project_dir: str = "",
) -> bool:
    """W2 表：items kind=video 且文件在。禁止用 shot_final_ready。"""
    sid = str(shot.get("shot_id") or "")
    if not sid:
        return False
    for item in (manifest or {}).get("items") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("kind") or "") != "video":
            continue
        if str(item.get("shot_id") or "") != sid:
            continue
        if _path_exists(project_dir, str(item.get("path") or "")):
            return True
    return False


def ready_video_ids(
    scene_plan: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    project_dir: str = "",
) -> set[str]:
    out: set[str] = set()
    for shot in plan_shots(scene_plan):
        sid = str(shot.get("shot_id") or "")
        if sid and has_video_file(shot, manifest, project_dir):
            out.add(sid)
    return out


def fill_shot_budget_class(scene_plan: dict[str, Any] | None) -> list[dict[str, str]]:
    """缺省 / 非法值 → talk。显式 hero|talk|establishing 不改。永不自动 hero。"""
    findings: list[dict[str, str]] = []
    for shot in plan_shots(scene_plan):
        current = normalize_class(shot.get("shot_budget_class"))
        if current:
            shot["shot_budget_class"] = current
            continue
        shot["shot_budget_class"] = "talk"
        sid = str(shot.get("shot_id") or "")
        findings.append({
            "severity": "info",
            "stage": "shot_budget",
            "field": sid or "shot",
            "message": "缺 shot_budget_class，已填 talk",
            "proposed_fix": "叙事高潮镜显式标 hero；空镜标 establishing",
        })
    return findings


def apply_kind_from_class(
    scene_plan: dict[str, Any] | None,
    *,
    all_video: bool = False,
    ready_video_ids: set[str] | None = None,
) -> None:
    ready = ready_video_ids or set()
    for shot in plan_shots(scene_plan):
        sid = str(shot.get("shot_id") or "")
        if all_video:
            shot["shot_kind"] = "video"
            continue
        if sid and sid in ready:
            shot["shot_kind"] = "video"
            continue
        klass = normalize_class(shot.get("shot_budget_class")) or "talk"
        shot["shot_kind"] = "video" if klass == "hero" else "image"


def pending_hero_ratio(
    scene_plan: dict[str, Any] | None,
    manifest: dict[str, Any] | None = None,
    project_dir: str = "",
) -> float:
    shots = plan_shots(scene_plan)
    total = sum(shot_duration(s) for s in shots)
    if total <= 0:
        return 0.0
    pending = 0.0
    for shot in shots:
        if normalize_class(shot.get("shot_budget_class")) != "hero":
            continue
        if has_video_file(shot, manifest, project_dir):
            continue
        pending += shot_duration(shot)
    return pending / total


def trim_pending_hero(
    scene_plan: dict[str, Any] | None,
    manifest: dict[str, Any] | None = None,
    project_dir: str = "",
) -> list[dict[str, str]]:
    """从左保留 pending hero，再加会 >30% 则该镜及之后 pending hero → talk。"""
    findings: list[dict[str, str]] = []
    shots = plan_shots(scene_plan)
    total = sum(shot_duration(s) for s in shots)
    if total <= 0:
        return findings
    kept = 0.0
    dropping = False
    for shot in shots:
        if normalize_class(shot.get("shot_budget_class")) != "hero":
            continue
        if has_video_file(shot, manifest, project_dir):
            continue
        dur = shot_duration(shot)
        if dropping or (kept + dur) / total > HERO_CAP:
            shot["shot_budget_class"] = "talk"
            dropping = True
            findings.append({
                "severity": "warning",
                "stage": "shot_budget",
                "field": str(shot.get("shot_id") or ""),
                "message": "pending hero 超 30%，--trim-hero 已降为 talk",
                "proposed_fix": "需要贵镜时减少其它 hero 或使用 --all-video",
            })
        else:
            kept += dur
    return findings


def enforce_hero_cap(
    scene_plan: dict[str, Any] | None,
    manifest: dict[str, Any] | None = None,
    project_dir: str = "",
) -> dict[str, Any]:
    ratio = pending_hero_ratio(scene_plan, manifest, project_dir)
    findings: list[dict[str, str]] = []
    ok = ratio <= HERO_CAP
    if not ok:
        findings.append({
            "severity": "critical",
            "stage": "shot_budget",
            "field": "shot_budget_class",
            "message": f"pending hero 时长占比 {ratio:.0%} 超过 30%",
            "proposed_fix": "标更少 hero，或 produce --trim-hero / --all-video",
        })
    return {"ok": ok, "ratio": ratio, "findings": findings}


def _patch_shot_prompts(prompts: dict[str, Any] | None, scene_plan: dict[str, Any]) -> None:
    if not isinstance(prompts, dict):
        return
    by_id = {str(s.get("shot_id") or ""): s for s in plan_shots(scene_plan) if s.get("shot_id")}
    for row in prompts.get("shots") or []:
        if not isinstance(row, dict):
            continue
        src = by_id.get(str(row.get("shot_id") or ""))
        if not src:
            continue
        if src.get("shot_budget_class"):
            row["shot_budget_class"] = src["shot_budget_class"]
        if src.get("shot_kind"):
            row["shot_kind"] = src["shot_kind"]


def apply_produce_budget(
    project_dir: str | Path,
    *,
    trim_hero: bool = False,
    all_video: bool = False,
) -> dict[str, Any]:
    """GEN 路径：fill → 可选 trim → 改 kind → 写回。all_video 跳过 30%。"""
    from montage.engine.artifacts import ArtifactStore

    root = Path(project_dir)
    store = ArtifactStore(root)
    plan = store.read("scene_plan")
    if not isinstance(plan, dict):
        return {"ok": True, "ratio": 0.0, "findings": []}
    manifest = store.read("asset_manifest")
    if not isinstance(manifest, dict):
        manifest = {}
    proj = str(root)
    findings = fill_shot_budget_class(plan)
    ready = ready_video_ids(plan, manifest, proj)
    if all_video:
        apply_kind_from_class(plan, all_video=True, ready_video_ids=ready)
        _write_plan(store, plan)
        return {"ok": True, "ratio": 0.0, "findings": findings, "skipped_cap": True}
    if trim_hero:
        findings.extend(trim_pending_hero(plan, manifest, proj))
    apply_kind_from_class(plan, all_video=False, ready_video_ids=ready)
    cap = enforce_hero_cap(plan, manifest, proj)
    findings.extend(cap.get("findings") or [])
    _write_plan(store, plan)
    return {
        "ok": bool(cap.get("ok")),
        "ratio": float(cap.get("ratio") or 0),
        "findings": findings,
    }


def _write_plan(store: Any, plan: dict[str, Any]) -> None:
    prompts = store.read("shot_prompts")
    if isinstance(prompts, dict):
        _patch_shot_prompts(prompts, plan)
        store.write("shot_prompts", prompts)
    store.write("scene_plan", plan)
