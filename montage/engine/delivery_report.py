"""Read-only delivery report: a projection over already-produced artifacts.

This module deliberately does not own process state.  ``produce_progress``
remains the source of truth for pipeline status; this report adds the separate
material, quality, traceability, and duration dimensions that caused the
original ``status=ok`` misunderstanding.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from montage.engine.material_contract import build_material_contract
from montage.engine.cut_points import (
    build_m5_parallel_audit,
    build_m6_parallel_audit,
    project_cut_points,
)
from montage.engine.review_findings import (
    build_review_findings,
    load_review_log,
    review_findings_summary,
)
from montage.tools.generation_events import load_generation_events
from montage.engine.subtitle_timeline import audit_srt_sync, project_subtitle_timeline
from montage.engine.transition_contract import build_transition_contract_projection

DEFAULT_HASHED_ARTIFACTS = (
    "scene_plan.json",
    "asset_manifest.json",
    "compose_plan.json",
    "produce_progress.json",
    "film_health.json",
    "edit_metrics.json",
)

QUALITY_MODES = ("full", "strict", "degraded", "manual_only")


def normalize_quality_mode(raw: Any) -> str:
    """Validate the quality policy name without silently changing its meaning."""
    mode = str(raw or "").strip().lower()
    if mode not in QUALITY_MODES:
        allowed = ", ".join(QUALITY_MODES)
        raise ValueError(f"invalid quality_mode {raw!r}; expected one of: {allowed}")
    return mode


def _safe_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value == value else None  # rejects NaN without importing math


def _transition_junctions(
    compose_shots: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], float, list[dict[str, Any]]]:
    """Project shot-owned transition fields onto boundaries.

    ``shots[N].transition`` describes the boundary from shot ``N - 1`` to
    shot ``N``.  The first shot has no incoming boundary, so its transition
    field must not create overlap.
    """
    rows: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    overlap_seconds = 0.0
    if compose_shots:
        first = compose_shots[0]
        first_transition = str(first.get("transition") or "cut")
        first_raw_duration = _safe_float(first.get("transition_duration"))
        first_duration = first_raw_duration if first_raw_duration is not None else 0.0
        if first_transition != "cut" or first_duration != 0.0:
            anomalies.append(
                {
                    "kind": "first_shot_transition_ignored",
                    "junction_index": 0,
                    "shot_id": first.get("shot_id"),
                    "transition": first_transition,
                    "transition_duration_seconds": first_duration,
                }
            )
    for index in range(1, len(compose_shots)):
        incoming = compose_shots[index]
        outgoing = compose_shots[index - 1]
        transition = str(incoming.get("transition") or "cut")
        raw_duration = _safe_float(incoming.get("transition_duration"))
        duration = raw_duration if raw_duration is not None else 0.0
        effective_cut = transition == "cut" or duration <= 0
        overlap = 0.0 if effective_cut else duration
        overlap_seconds += overlap
        if outgoing.get("shot_id") is None or incoming.get("shot_id") is None:
            anomalies.append(
                {
                    "kind": "missing_shot_id",
                    "junction_index": index,
                    "transition": transition,
                    "transition_duration_seconds": duration,
                }
            )
        if effective_cut and transition == "cut" and duration > 0:
            anomalies.append(
                {
                    "kind": "cut_transition_duration_ignored",
                    "junction_index": index,
                    "from_shot_id": outgoing.get("shot_id"),
                    "to_shot_id": incoming.get("shot_id"),
                    "transition": transition,
                    "transition_duration_seconds": duration,
                }
            )
        if effective_cut and transition != "cut":
            anomalies.append(
                {
                    "kind": "nonpositive_transition_treated_as_cut",
                    "junction_index": index,
                    "from_shot_id": outgoing.get("shot_id"),
                    "to_shot_id": incoming.get("shot_id"),
                    "transition": transition,
                    "transition_duration_seconds": duration,
                }
            )
        if not effective_cut:
            rows.append(
                {
                    "junction_index": index,
                    "from_shot_id": outgoing.get("shot_id"),
                    "to_shot_id": incoming.get("shot_id"),
                    "transition": transition,
                    "transition_duration_seconds": duration,
                }
            )
    return rows, round(overlap_seconds, 9), anomalies


def _metric_summary(metrics: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("m1", "m2", "m5", "m6", "drift"):
        row = (metrics or {}).get(name)
        if not isinstance(row, dict):
            result[name] = None
            continue
        result[name] = {
            "value": _safe_float(row.get("value")),
            "threshold": _safe_float(row.get("threshold")),
            "pass": bool(row.get("pass")),
            "skipped": bool(row.get("skipped")),
        }
    return result


def _quality_gate(
    *,
    edit_metrics: dict[str, Any] | None,
    film_health: dict[str, Any] | None,
    mode: str,
    human_review_decision: str,
    vlm: dict[str, Any] | None,
) -> dict[str, Any]:
    mode = normalize_quality_mode(mode)
    metrics = (edit_metrics or {}).get("metrics") or {}
    objective = _metric_summary(metrics)
    objective_pass = all(
        row is None or row["pass"]
        for row in objective.values()
    )
    film_pass = bool((film_health or {}).get("pass")) if film_health else None

    m1_detail = (metrics.get("m1") or {}).get("detail") or {}
    continuity = (film_health or {}).get("continuity")
    continuity = continuity if isinstance(continuity, dict) else {}
    continuity_critical = int(continuity.get("critical") or 0)
    if vlm is not None:
        checked = int(vlm.get("checked") or 0)
        verified = bool(vlm.get("verified"))
        enabled = bool(vlm.get("enabled"))
        skipped = bool(vlm.get("skipped", not verified))
        reason = str(vlm.get("reason") or "")
        critical_count = int(
            vlm.get("critical_count", vlm.get("critical", continuity_critical)) or 0
        )
        if not verified and not reason:
            reason = "VLM verification not recorded"
    else:
        skipped = bool(m1_detail.get("vlm_skipped"))
        checked = int(m1_detail.get("vlm_checked") or 0)
        critical_count = int(m1_detail.get("vlm_critical") or continuity_critical)
        verified = not skipped and checked > 0
        enabled = not skipped and checked > 0
        if skipped:
            reason = "DASHSCOPE_API_KEY missing"
        elif checked:
            reason = ""
        else:
            reason = "VLM verification not recorded"
    vlm_state = {
        "enabled": enabled,
        "skipped": skipped,
        "checked": checked,
        "reason": reason,
        "verified": verified,
        "critical_count": critical_count,
    }
    if mode == "manual_only":
        vlm_state.update({
            "enabled": False,
            "skipped": True,
            "reason": "VLM disabled by manual_only policy",
            "verified": False,
        })
        critical_count = 0

    blocked = False
    blocked_reasons: list[str] = []
    if mode == "full" and not vlm_state["verified"]:
        blocked = True
        blocked_reasons.append("full policy requires VLM verification")
    elif mode == "strict" and (
        not vlm_state["verified"] or int(vlm_state["critical_count"] or 0) > 0
    ):
        blocked = True
        if not vlm_state["verified"]:
            blocked_reasons.append("strict policy requires VLM verification")
        if int(vlm_state["critical_count"] or 0) > 0:
            blocked_reasons.append("strict policy blocks VLM critical findings")

    return {
        "mode": mode,
        "objective_metrics": objective,
        "objective_pass": objective_pass,
        "film_health_pass": film_pass,
        "vlm": vlm_state,
        "human_review": {
            "required": True,
            "decision": human_review_decision,
        },
        "blocked": blocked,
        "blocked_reasons": blocked_reasons,
    }


def _traceability(
    *,
    asset_manifest: dict[str, Any] | None,
    generation_events: list[dict[str, Any]] | None,
    generation_event_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    video_items = [
        row
        for row in (asset_manifest or {}).get("items") or []
        if isinstance(row, dict) and str(row.get("kind") or "") == "video"
    ]

    def all_have(field: str) -> bool:
        return bool(video_items) and all(str(row.get(field) or "") for row in video_items)

    return {
        "generation_events": generation_events is not None,
        "generation_event_count": len(generation_events or []),
        "generation_event_write_errors": list(generation_event_errors or []),
        "generation_event_write_error_count": len(generation_event_errors or []),
        "generation_events_healthy": bool(
            generation_events is not None and not generation_event_errors
        ),
        "all_video_items_have_event_id": all_have("generation_event_id"),
        "provider_task_ids": all_have("provider_task_id"),
        "file_hashes": all_have("file_sha256"),
    }


def build_duration_reconciliation(
    *,
    asset_manifest: dict[str, Any] | None,
    compose_plan: dict[str, Any] | None,
    film_health: dict[str, Any] | None,
    process_progress: dict[str, Any] | None,
    title_seconds: float | None,
) -> dict[str, Any]:
    """Build the explainable final-duration model from real clip/cut facts."""
    compose_shots = [
        row
        for row in (compose_plan or {}).get("shots") or []
        if isinstance(row, dict)
    ]
    compose_total = sum(_safe_float(row.get("duration_seconds")) or 0 for row in compose_shots)
    transition_rows, transition_overlap, transition_anomalies = _transition_junctions(compose_shots)
    junction_count = max(len(compose_shots) - 1, 0)
    transition_cut_count = junction_count - len(transition_rows)
    manifest_total = sum(
        _safe_float(row.get("duration_seconds")) or 0
        for row in (asset_manifest or {}).get("items") or []
        if isinstance(row, dict) and str(row.get("kind") or "") == "video"
    )

    if title_seconds is None:
        finish = ((process_progress or {}).get("steps") or {}).get("finish") or {}
        title_seconds = _safe_float(finish.get("title_dur")) or 0.0
    expected_composed = compose_total - transition_overlap
    expected_final = expected_composed + (title_seconds or 0)
    final_reported = _safe_float(((film_health or {}).get("probe") or {}).get("duration_seconds"))
    expected_delta = (
        final_reported - expected_final
        if final_reported is not None else None
    )
    target = _safe_float(((film_health or {}).get("duration_check") or {}).get("expected"))
    target_delta = final_reported - target if final_reported is not None and target is not None else None

    explained = expected_delta is not None and abs(expected_delta) <= 0.25
    if compose_shots and final_reported is None:
        status = "missing_probe"
    elif compose_shots and explained:
        status = "explained"
    else:
        status = "unexplained"
    check = (film_health or {}).get("duration_check")
    check = check if isinstance(check, dict) else {}
    target_over = check.get("over")
    target_status = (
        "unknown" if target is None
        else "within_tolerance" if target_over is False
        else "over_tolerance" if target_over is True
        else "unknown"
    )
    return {
        "formula": "compose_total - transition_overlap + title_seconds",
        "status": status,
        "manifest_total_seconds": manifest_total,
        "compose_plan_total_seconds": compose_total,
        "compose_shot_count": len(compose_shots),
        "transition_junction_count": junction_count,
        "transition_cut_count": transition_cut_count,
        "transition_overlap_count": len(transition_rows),
        "expected_composed_seconds": expected_composed if compose_shots else None,
        "transition_overlap_seconds": transition_overlap,
        "transition_overlap_model": "xfade",
        "transition_rows": transition_rows,
        "transition_anomaly_count": len(transition_anomalies),
        "transition_anomalies": transition_anomalies,
        "title_seconds": title_seconds,
        "final_reported_seconds": final_reported,
        "expected_final_seconds": expected_final if compose_shots else None,
        "expected_delta_seconds": expected_delta,
        "reconciliation_tolerance_seconds": 0.25,
        "target_seconds": target,
        "target_delta_seconds": target_delta,
        "target_delta_ratio": (
            abs(target_delta) / target
            if target_delta is not None and target else None
        ),
        "explained": explained,
        "target_status": target_status,
    }


def _duration_reconciliation(
    *,
    asset_manifest: dict[str, Any] | None,
    compose_plan: dict[str, Any] | None,
    film_health: dict[str, Any] | None,
    process_progress: dict[str, Any] | None,
    title_seconds: float | None,
) -> dict[str, Any]:
    return build_duration_reconciliation(
        asset_manifest=asset_manifest,
        compose_plan=compose_plan,
        film_health=film_health,
        process_progress=process_progress,
        title_seconds=title_seconds,
    )


def format_duration_reconciliation(reconciliation: dict[str, Any] | None) -> str:
    """Render a compact line for stop-point cards and operator summaries."""
    row = reconciliation or {}
    if not row.get("compose_shot_count"):
        return "compose_plan 未生成"
    def seconds(raw: Any) -> str:
        return f"{float(raw):.3f}s" if raw is not None else "missing"

    if row.get("status") == "missing_probe":
        return (
            f"final probe missing / expected {seconds(row.get('expected_final_seconds'))} / "
            f"status missing_probe"
        )
    value = (
        f"实测 {seconds(row.get('final_reported_seconds'))} / "
        f"expected {seconds(row.get('expected_final_seconds'))} / "
        f"delta {seconds(row.get('expected_delta_seconds'))} / {row.get('status')}"
    )
    target = row.get("target_seconds")
    if target is not None:
        value += (
            f" / target {seconds(target)} delta {seconds(row.get('target_delta_seconds'))} "
            f"({row.get('target_status')})"
        )
    return value


def format_transition_junctions(reconciliation: dict[str, Any] | None) -> str:
    """Render compact junction counts for stop-point cards and CLI summaries."""
    row = reconciliation or {}
    if not row.get("compose_shot_count"):
        return "compose_plan 未生成"
    value = (
        f"junctions={row.get('transition_junction_count', 0)} "
        f"cuts={row.get('transition_cut_count', 0)} "
        f"overlap={row.get('transition_overlap_count', 0)} "
        f"total={float(row.get('transition_overlap_seconds') or 0):.3f}s "
        f"model={row.get('transition_overlap_model') or 'unknown'} "
        f"anomalies={row.get('transition_anomaly_count', 0)}"
    )
    anomaly_counts = Counter(
        str(item.get("kind") or "unknown")
        for item in row.get("transition_anomalies") or []
        if isinstance(item, dict)
    )
    if anomaly_counts:
        kinds = ",".join(f"{kind}={count}" for kind, count in sorted(anomaly_counts.items()))
        value += f"（{kinds}）"
    return value


def format_subtitle_timeline(projection: dict[str, Any] | None) -> str:
    """Render compact subtitle correction facts for operators."""
    row = projection or {}
    if not row:
        return "compose_plan 未生成"
    return (
        f"cues={row.get('cue_count', 0)} "
        f"shifted={row.get('shifted_shot_count', 0)} "
        f"maxShift={float(row.get('max_abs_shift_seconds') or 0):.3f}s "
        f"planned={float(row.get('planned_duration_seconds') or 0):.3f}s "
        f"projected={float(row.get('projected_duration_seconds') or 0):.3f}s "
        f"anomalies={row.get('anomaly_count', 0)}"
    )


def _subtitle_timeline_summary(compose_plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """Compact subtitle projection; full cues remain in the dedicated module."""
    if not isinstance(compose_plan, dict):
        return None
    projection = project_subtitle_timeline(compose_plan)
    return {
        "model": projection["model"],
        "tolerance_seconds": projection["tolerance_seconds"],
        "shot_count": projection["shot_count"],
        "cue_count": projection["cue_count"],
        "shifted_shot_count": projection["shifted_shot_count"],
        "max_abs_shift_seconds": projection["max_abs_shift_seconds"],
        "planned_duration_seconds": projection["planned_duration_seconds"],
        "projected_duration_seconds": projection["projected_duration_seconds"],
        "transition_overlap_count": projection["transition_overlap_count"],
        "transition_overlap_seconds": projection["transition_overlap_seconds"],
        "anomaly_count": projection["anomaly_count"],
        "anomalies": projection["anomalies"],
        "cue_rows": projection["cue_rows"],
    }


def _cut_points_summary(
    compose_plan: dict[str, Any] | None,
    soundtrack: dict[str, Any] | None,
    *,
    fps: float,
    projection: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(compose_plan, dict):
        return None
    if projection is None:
        projection = project_cut_points(compose_plan, soundtrack=soundtrack, fps=fps)
    grid = projection.get("beat_grid") if isinstance(projection.get("beat_grid"), dict) else {}
    rows = projection.get("cut_rows") or []
    return {
        "model": projection["model"],
        "fps": projection["fps"],
        "cut_count": projection["cut_count"],
        "tolerance_frames": projection["tolerance_frames"],
        "bpm": grid.get("bpm"),
        "beat_grid_source": grid.get("source"),
        "transition_overlap_count": projection["transition_overlap_count"],
        "transition_overlap_seconds": projection["transition_overlap_seconds"],
        "planned_aligned_count": projection["planned_aligned_count"],
        "projected_aligned_count": projection["projected_aligned_count"],
        "planned_alignment_ratio": projection["planned_alignment_ratio"],
        "projected_alignment_ratio": projection["projected_alignment_ratio"],
        "max_projected_delta_frames": projection["max_projected_delta_frames"],
        "improved_count": sum(1 for row in rows if row.get("improved")),
        "regressed_count": sum(1 for row in rows if row.get("regressed")),
    }


def build_delivery_report(
    *,
    process_progress: dict[str, Any] | None = None,
    material_contract: dict[str, Any] | None = None,
    asset_manifest: dict[str, Any] | None = None,
    compose_plan: dict[str, Any] | None = None,
    film_health: dict[str, Any] | None = None,
    edit_metrics: dict[str, Any] | None = None,
    quality_mode: str = "degraded",
    human_review_decision: str = "pending",
    vlm: dict[str, Any] | None = None,
    generation_events: list[dict[str, Any]] | None = None,
    generation_event_errors: list[dict[str, Any]] | None = None,
    title_seconds: float | None = None,
    artifact_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a derived, read-only delivery snapshot.

    ``artifact_hashes`` keeps the projection connected to exact input versions.
    The caller may inject hashes for pure tests; production callers can use
    ``collect_artifact_hashes``.
    """
    progress_status = str((process_progress or {}).get("status") or "")
    contract = material_contract or {}
    summary = contract.get("summary") or {}
    source = contract.get("source_contract") or {}
    quality = _quality_gate(
        edit_metrics=edit_metrics,
        film_health=film_health,
        mode=quality_mode,
        human_review_decision=human_review_decision,
        vlm=vlm,
    )
    traceability = _traceability(
        asset_manifest=asset_manifest,
        generation_events=generation_events,
        generation_event_errors=generation_event_errors,
    )

    process_ok = progress_status == "ok"
    material_pass = bool(contract.get("pass"))
    full_quality_pass = (
        quality["objective_pass"]
        and quality["film_health_pass"] is True
        and quality["vlm"]["verified"]
        and quality["human_review"]["decision"] == "accepted"
    )
    if not process_ok or not material_pass:
        status = "blocked"
    elif quality.get("blocked"):
        status = "blocked"
    elif full_quality_pass:
        status = "ok"
    else:
        status = "degraded"

    intent = contract.get("intent") or {}
    return {
        "schema_version": 1,
        "status": status,
        "process_status": {
            "status": progress_status,
            "review": (process_progress or {}).get("review") or "",
            "retry_ids": list((process_progress or {}).get("retry_ids") or []),
        },
        "material_contract": {
            "shot_contract": (
                "all_ai_video" if intent.get("all_ai_video") else "shot_kind"
            ),
            "required_shots": summary.get("required_shots"),
            "actual_ai_video": summary.get("actual_ai_video_count"),
            "kenburns": summary.get("kenburns_count"),
            "vfx_total": summary.get("vfx_total_count"),
            "vfx_overlay": summary.get("vfx_overlay_count"),
            "fallback": list(contract.get("fallbacks") or []),
            "missing": list(source.get("missing_shot_ids") or []),
            "duplicate": list(source.get("duplicate_manifest_shot_ids") or []),
            "pass": material_pass,
        },
        "quality_gate": quality,
        "traceability": traceability,
        "duration_reconciliation": _duration_reconciliation(
            asset_manifest=asset_manifest,
            compose_plan=compose_plan,
            film_health=film_health,
            process_progress=process_progress,
            title_seconds=title_seconds,
        ),
        "subtitle_timeline": _subtitle_timeline_summary(compose_plan),
        "computed_from": dict(artifact_hashes or {}),
    }


def collect_artifact_hashes(
    project_dir: str | Path,
    names: tuple[str, ...] = DEFAULT_HASHED_ARTIFACTS,
) -> dict[str, str]:
    """Hash only the artifacts used by the report."""
    artifacts = Path(project_dir) / "artifacts"
    result: dict[str, str] = {}
    for name in names:
        path = artifacts / name
        if path.is_file():
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _timeline_shots(project_dir: Path) -> list[dict[str, Any]]:
    """Read the same measured timeline shots used by objective metrics."""
    from montage.engine.artifacts import ArtifactStore
    from montage.tools.review_logger import _timeline_shots as read_timeline_shots

    return read_timeline_shots(ArtifactStore(project_dir))


def _beat_map_contour(project_dir: Path) -> list[dict[str, Any]] | None:
    """Read the optional measured energy contour without changing m6 semantics."""
    from montage.tools.auto_edit import beat_map_contour, load_beat_map

    beat_map = load_beat_map(project_dir)
    return beat_map_contour(beat_map) or None


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def build_project_delivery_report(
    project_dir: str | Path,
    *,
    probe_sources: bool = True,
    probe_fn: Any = None,
    exists_fn: Any = None,
    quality_mode: str = "degraded",
    human_review_decision: str | None = None,
) -> dict[str, Any]:
    """Build a delivery report from files in a project, without writing artifacts.

    ``probe_fn``/``exists_fn`` are injection points for tests; production callers
    should leave them unset so the snapshot uses the real filesystem and ffprobe.
    """
    root = Path(project_dir)
    artifacts = root / "artifacts"

    def read(name: str) -> dict[str, Any] | None:
        return _read_json(artifacts / name)

    process_progress = read("produce_progress.json")
    scene_plan = read("scene_plan.json")
    asset_manifest = read("asset_manifest.json")
    compose_plan = read("compose_plan.json")
    film_health = read("film_health.json")
    edit_metrics = read("edit_metrics.json")
    recorded_review = process_progress.get("human_review") if isinstance(
        process_progress.get("human_review"), dict
    ) else {}
    recorded_decision = str(recorded_review.get("decision") or "pending")
    if human_review_decision is None:
        human_review_decision = recorded_decision

    material = None
    if scene_plan is not None and asset_manifest is not None:
        material = build_material_contract(
            scene_plan=scene_plan,
            asset_manifest=asset_manifest,
            compose_plan=compose_plan,
            project_dir=root,
            all_ai_video=True,
            probe_fn=probe_fn,
            probe_sources=probe_sources,
            exists_fn=exists_fn,
        )

    generation_events, generation_event_errors = load_generation_events(
        artifacts / "generation_events.jsonl"
    )
    review_rows, review_parse_errors = load_review_log(
        artifacts / "review_log.jsonl"
    )
    review_projection = build_review_findings(review_rows)
    review_summary = review_findings_summary(
        review_projection,
        log_present=review_rows is not None,
        parse_errors=review_parse_errors,
    )

    report = build_delivery_report(
        process_progress=process_progress,
        material_contract=material,
        asset_manifest=asset_manifest,
        compose_plan=compose_plan,
        film_health=film_health,
        edit_metrics=edit_metrics,
        quality_mode=quality_mode,
        human_review_decision=human_review_decision,
        generation_events=generation_events,
        generation_event_errors=generation_event_errors,
        artifact_hashes=collect_artifact_hashes(root),
    )
    report["transition_contracts"] = build_transition_contract_projection(
        compose_plan=compose_plan,
        scene_plan=scene_plan,
    )
    report["review_findings"] = review_summary
    if isinstance(compose_plan, dict):
        srt_path = root / "renders" / "final.srt"
        srt_text = srt_path.read_text(encoding="utf-8") if srt_path.is_file() else None
        duration = report.get("duration_reconciliation") or {}
        report["subtitle_srt_audit"] = audit_srt_sync(
            compose_plan=compose_plan,
            srt_text=srt_text,
            title_offset_seconds=float(duration.get("title_seconds") or 0),
            final_duration_seconds=duration.get("final_reported_seconds"),
        )
    soundtrack = read("soundtrack.json")
    fps = ((film_health.get("probe") or {}).get("fps") if isinstance(film_health, dict) else None)
    cut_projection = project_cut_points(
        compose_plan,
        soundtrack=soundtrack,
        fps=float(fps or 30.0),
    )
    report["cut_points"] = _cut_points_summary(
        compose_plan,
        soundtrack,
        fps=float(fps or 30.0),
        projection=cut_projection,
    )
    report["m5_parallel_audit"] = build_m5_parallel_audit(
        edit_metrics=edit_metrics,
        cut_points=cut_projection,
    )
    report["m6_parallel_audit"] = build_m6_parallel_audit(
        edit_metrics=edit_metrics,
        cut_points=cut_projection,
        shots=_timeline_shots(root),
        soundtrack=soundtrack,
        contour=_beat_map_contour(root),
    )
    return report


def build_vlm_state(
    *,
    edit_metrics: dict[str, Any] | None = None,
    quality_mode: str = "degraded",
    vlm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the VLM half of the quality gate for lightweight projections."""
    quality = _quality_gate(
        edit_metrics=edit_metrics,
        film_health=None,
        mode=quality_mode,
        human_review_decision="pending",
        vlm=vlm,
    )
    return quality["vlm"]


def build_quality_gate(
    *,
    edit_metrics: dict[str, Any] | None = None,
    film_health: dict[str, Any] | None = None,
    quality_mode: str = "degraded",
    human_review_decision: str = "pending",
    vlm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Public pure projection for callers that need only the quality gate."""
    return _quality_gate(
        edit_metrics=edit_metrics,
        film_health=film_health,
        mode=quality_mode,
        human_review_decision=human_review_decision,
        vlm=vlm,
    )


def format_vlm_state(state: dict[str, Any] | None) -> str:
    """Render a compact state that never mistakes “skipped” for “verified”."""
    row = state or {}
    if row.get("verified"):
        return "verified"
    if row.get("skipped"):
        reason = str(row.get("reason") or "").replace(" ", "_")
        return f"skipped({reason})" if reason else "skipped"
    return "unverified"
