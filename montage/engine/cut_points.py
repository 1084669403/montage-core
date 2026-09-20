"""Read-only cut-point and beat-grid projection.

The legacy m5 metric consumes shot-accumulation times.  This module adds a
parallel xfade projection without changing metric semantics or media files.
"""

from __future__ import annotations

from typing import Any

from montage.engine.edit_metrics import (
    DEFAULT_BEAT_TOLERANCE_FRAMES,
    _shot_audio_energy,
    beat_grid,
)


def _safe_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value == value else None


def _effective_overlap(row: dict[str, Any]) -> float:
    transition = str(row.get("transition") or "cut")
    duration = _safe_float(row.get("transition_duration"))
    if transition == "cut" or duration is None or duration <= 0:
        return 0.0
    return duration


def project_cut_points(
    compose_plan: dict[str, Any] | None,
    *,
    soundtrack: dict[str, Any] | None = None,
    fps: float = 30.0,
    beat_map: dict[str, Any] | None = None,
    tolerance_frames: int = DEFAULT_BEAT_TOLERANCE_FRAMES,
) -> dict[str, Any]:
    """Project planned and xfade-adjusted cut points onto a beat grid."""
    shots = [
        row
        for row in (compose_plan or {}).get("shots") or []
        if isinstance(row, dict)
    ]
    grid = beat_grid(soundtrack, fps=fps, beat_map=beat_map)
    planned_duration = 0.0
    projected_start = 0.0
    overlap_seconds = 0.0
    overlap_count = 0
    rows: list[dict[str, Any]] = []
    planned_aligned = 0
    projected_aligned = 0
    max_projected_delta = 0.0

    for index, shot in enumerate(shots):
        duration = _safe_float(shot.get("duration_seconds")) or 0.0
        planned_start = planned_duration
        if index > 0:
            overlap = _effective_overlap(shot)
            overlap_seconds += overlap
            overlap_count += 1 if overlap > 0 else 0
            projected_start -= overlap

        if index > 0:
            planned_cut = planned_start
            projected_cut = projected_start
            planned_delta = _beat_delta_frames(
                planned_cut,
                grid=grid,
                fps=fps,
            )
            projected_delta = _beat_delta_frames(
                projected_cut,
                grid=grid,
                fps=fps,
            )
            planned_ok = (
                planned_delta is not None and planned_delta <= tolerance_frames
            )
            projected_ok = (
                projected_delta is not None
                and projected_delta <= tolerance_frames
            )
            if planned_delta is not None and planned_ok:
                planned_aligned += 1
            if projected_delta is not None and projected_ok:
                projected_aligned += 1
            if projected_delta is not None:
                max_projected_delta = max(max_projected_delta, projected_delta)
            rows.append({
                "cut_index": index,
                "from_shot_id": shots[index - 1].get("shot_id"),
                "to_shot_id": shot.get("shot_id"),
                "planned_start_seconds": round(planned_cut, 9),
                "projected_start_seconds": round(projected_cut, 9),
                "shift_seconds": round(projected_cut - planned_cut, 9),
                "planned_delta_frames": (
                    round(planned_delta, 3) if planned_delta is not None else None
                ),
                "projected_delta_frames": (
                    round(projected_delta, 3) if projected_delta is not None else None
                ),
                "planned_aligned": planned_ok,
                "projected_aligned": projected_ok,
                "improved": (
                    projected_delta is not None
                    and planned_delta is not None
                    and projected_delta < planned_delta
                ),
                "regressed": (
                    projected_delta is not None
                    and planned_delta is not None
                    and projected_delta > planned_delta
                ),
            })

        planned_duration += duration
        projected_start += duration

    cuts = max(len(shots) - 1, 0)
    shot_rows: list[dict[str, Any]] = []
    for index, shot in enumerate(shots):
        duration = _safe_float(shot.get("duration_seconds")) or 0.0
        outgoing_overlap = (
            _effective_overlap(shots[index + 1])
            if index + 1 < len(shots) else 0.0
        )
        shot_rows.append({
            "shot_id": shot.get("shot_id"),
            "planned_duration_seconds": round(duration, 9),
            "projected_duration_seconds": round(duration - outgoing_overlap, 9),
            "outgoing_overlap_seconds": round(outgoing_overlap, 9),
        })
    return {
        "model": "measured_accumulation_minus_xfade",
        "fps": fps,
        "shot_count": len(shots),
        "cut_count": cuts,
        "beat_grid": grid,
        "tolerance_frames": tolerance_frames,
        "transition_overlap_count": overlap_count,
        "transition_overlap_seconds": round(overlap_seconds, 9),
        "planned_duration_seconds": round(planned_duration, 9),
        "projected_duration_seconds": round(planned_duration - overlap_seconds, 9),
        "planned_aligned_count": planned_aligned,
        "projected_aligned_count": projected_aligned,
        "planned_alignment_ratio": round(planned_aligned / cuts, 3) if cuts else None,
        "projected_alignment_ratio": (
            round(projected_aligned / cuts, 3) if cuts else None
        ),
        "max_projected_delta_frames": round(max_projected_delta, 3),
        "cut_rows": rows,
        "shot_rows": shot_rows,
    }


def _beat_delta_frames(
    seconds: float,
    *,
    grid: dict[str, Any] | None,
    fps: float,
) -> float | None:
    if not isinstance(grid, dict):
        return None
    bpm = _safe_float(grid.get("bpm"))
    frames_per_beat = _safe_float(grid.get("frames_per_beat"))
    if bpm is None or bpm <= 0 or frames_per_beat is None or frames_per_beat <= 0:
        return None
    offset = _safe_float(grid.get("offset_seconds")) or 0.0
    beat_position = ((seconds - offset) * fps) / frames_per_beat
    return abs(beat_position - round(beat_position)) * frames_per_beat


def format_cut_point_projection(projection: dict[str, Any] | None) -> str:
    """Render compact planned-vs-projected beat alignment facts."""
    row = projection or {}
    if not row.get("cut_count"):
        return "cuts=0"
    planned_ratio = row.get("planned_alignment_ratio")
    projected_ratio = row.get("projected_alignment_ratio")
    value = (
        f"cuts={row.get('cut_count', 0)} "
        f"overlap={row.get('transition_overlap_seconds', 0):.3f}s "
        f"planned={row.get('planned_aligned_count', 0)} "
        f"({float(planned_ratio):.3f}) "
        f"projected={row.get('projected_aligned_count', 0)} "
        f"({float(projected_ratio):.3f})"
    )
    grid = row.get("beat_grid") if isinstance(row.get("beat_grid"), dict) else {}
    bpm = grid.get("bpm") if grid else row.get("bpm")
    if bpm is not None:
        value += f" bpm={bpm}"
    return value


def build_m5_parallel_audit(
    *,
    edit_metrics: dict[str, Any] | None,
    cut_points: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Compare the recorded m5 with an xfade-projected view, read-only.

    This is deliberately parallel evidence: it never rewrites ``edit_metrics``,
    objective gates, or the director decision.
    """
    original = ((edit_metrics or {}).get("metrics") or {}).get("m5")
    projection = cut_points or {}
    if not isinstance(original, dict) or not projection:
        return None

    threshold = float(original.get("threshold") or 0.0)
    cuts = int(projection.get("cut_count") or 0)
    rows = [row for row in projection.get("cut_rows") or [] if isinstance(row, dict)]
    has_grid = projection.get("beat_grid") is not None
    projected_value = projection.get("projected_alignment_ratio")
    projected_aligned = int(projection.get("projected_aligned_count") or 0)
    projected_pass = (
        not original.get("skipped")
        and has_grid
        and projected_value is not None
        and float(projected_value) >= threshold
    )
    improved_frames = sum(1 for row in rows if row.get("improved"))
    regressed_frames = sum(1 for row in rows if row.get("regressed"))
    threshold_crossings = sum(
        1
        for row in rows
        if bool(row.get("planned_aligned")) != bool(row.get("projected_aligned"))
    )
    skipped = bool(original.get("skipped") or not has_grid)
    projected_pass = projected_pass and not skipped
    original_value = original.get("value")
    value_delta = (
        float(projected_value) - float(original_value)
        if projected_value is not None and original_value is not None
        else None
    )
    return {
        "kind": "m5_parallel_audit",
        "changes_original_metric": False,
        "original": {
            "value": original_value,
            "pass": original.get("pass"),
            "threshold": threshold,
            "skipped": original.get("skipped"),
            "circular": original.get("circular"),
        },
        "projected": {
            "value": projected_value,
            "pass": projected_pass,
            "threshold": threshold,
            "skipped": skipped,
            "cuts": cuts,
            "aligned": projected_aligned,
            "tolerance_frames": projection.get("tolerance_frames"),
        },
        "difference": {
            "value_delta": value_delta,
            "decision_changed": bool(original.get("pass")) != projected_pass,
            "threshold_crossings": threshold_crossings,
            "delta_frames_improved": improved_frames,
            "delta_frames_regressed": regressed_frames,
        },
    }


def format_m5_parallel_audit(audit: dict[str, Any] | None) -> str:
    """Render compact parallel-audit facts without suggesting a gate rewrite."""
    row = audit or {}
    if not row:
        return "m5=missing"
    original = row.get("original") or {}
    projected = row.get("projected") or {}
    difference = row.get("difference") or {}
    if original.get("skipped") or projected.get("skipped"):
        return "m5=parallel audit skipped (no beat grid)"
    value_delta = difference.get("value_delta")
    delta_text = f"{float(value_delta):+.3f}" if value_delta is not None else "missing"
    return (
        f"m5={original.get('value')}->{projected.get('value')} "
        f"pass={original.get('pass')}->{projected.get('pass')} "
        f"delta={delta_text} "
        f"framesImproved={difference.get('delta_frames_improved', 0)} "
        f"framesRegressed={difference.get('delta_frames_regressed', 0)} "
        f"crossings={difference.get('threshold_crossings', 0)} "
        f"(audit-only)"
    )


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    import math

    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    variance_x = sum((x - mean_x) ** 2 for x in xs)
    variance_y = sum((y - mean_y) ** 2 for y in ys)
    if variance_x <= 1e-9 or variance_y <= 1e-9:
        return None
    return numerator / math.sqrt(variance_x * variance_y)


def build_m6_parallel_audit(
    *,
    edit_metrics: dict[str, Any] | None,
    cut_points: dict[str, Any] | None,
    shots: list[dict[str, Any]] | None = None,
    soundtrack: dict[str, Any] | None = None,
    contour: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Compare recorded m6 with projected output intervals, read-only.

    Energy values stay exactly as recorded by the original metric.  Only the
    denominator of cut density changes from clip duration to the output
    interval between transition onsets.
    """
    original = ((edit_metrics or {}).get("metrics") or {}).get("m6")
    projection = cut_points or {}
    if not isinstance(original, dict) or not projection:
        return None

    threshold = float(original.get("threshold") or 0.0)
    original_rows = [
        row
        for row in (original.get("detail") or {}).get("rows") or []
        if isinstance(row, dict)
    ]
    if shots:
        energy_by_shot = {}
        for shot in shots:
            if not isinstance(shot, dict) or float(shot.get("duration_seconds") or 0) <= 0:
                continue
            shot_id = str(shot.get("shot_id") or "")
            energy, _source = _shot_audio_energy(shot, soundtrack, contour)
            energy_by_shot[shot_id] = float(energy)
    else:
        energy_by_shot = {
            str(row.get("shot_id") or ""): float(row.get("audio_energy") or 0)
            for row in original_rows
        }
    shot_rows = [
        row for row in projection.get("shot_rows") or [] if isinstance(row, dict)
    ]
    planned_densities: list[float] = []
    projected_densities: list[float] = []
    energies: list[float] = []
    interval_changes: list[dict[str, Any]] = []
    missing_energy = 0
    invalid_interval = 0

    for shot in shot_rows:
        shot_id = str(shot.get("shot_id") or "")
        planned_duration = float(shot.get("planned_duration_seconds") or 0)
        projected_duration = float(shot.get("projected_duration_seconds") or 0)
        if planned_duration <= 0:
            continue
        if shot_id not in energy_by_shot:
            missing_energy += 1
            continue
        if projected_duration <= 0:
            invalid_interval += 1
            continue
        energy = energy_by_shot[shot_id]
        planned_densities.append(1.0 / planned_duration)
        projected_densities.append(1.0 / projected_duration)
        energies.append(energy)
        if abs(projected_duration - planned_duration) > 1e-9:
            interval_changes.append({
                "shot_id": shot_id,
                "planned_duration_seconds": round(planned_duration, 3),
                "projected_duration_seconds": round(projected_duration, 3),
                "delta_seconds": round(projected_duration - planned_duration, 3),
            })

    projected_correlation = _pearson(projected_densities, energies)
    skipped = (
        bool(original.get("skipped"))
        or missing_energy > 0
        or invalid_interval > 0
        or projected_correlation is None
    )
    projected_value = (
        round(projected_correlation, 3) if projected_correlation is not None else None
    )
    projected_pass = (
        not skipped
        and projected_value is not None
        and projected_value >= threshold
    )
    original_value = original.get("value")
    value_delta = (
        projected_value - float(original_value)
        if projected_value is not None and original_value is not None
        else None
    )
    return {
        "kind": "m6_parallel_audit",
        "changes_original_metric": False,
        "original": {
            "value": original_value,
            "pass": original.get("pass"),
            "threshold": threshold,
            "skipped": original.get("skipped"),
            "circular": original.get("circular"),
            "shots": len(original_rows),
        },
        "projected": {
            "value": projected_value,
            "pass": projected_pass,
            "threshold": threshold,
            "skipped": skipped,
            "shots": len(projected_densities),
        },
        "difference": {
            "value_delta": value_delta,
            "decision_changed": bool(original.get("pass")) != projected_pass,
            "interval_changed_count": len(interval_changes),
            "interval_changes": interval_changes[:8],
        },
    }


def format_m6_parallel_audit(audit: dict[str, Any] | None) -> str:
    """Render compact m6 parallel-audit facts."""
    row = audit or {}
    if not row:
        return "m6=missing"
    original = row.get("original") or {}
    projected = row.get("projected") or {}
    difference = row.get("difference") or {}
    if projected.get("skipped"):
        return "m6=parallel audit skipped (no usable energy/variance)"
    value_delta = difference.get("value_delta")
    delta_text = f"{float(value_delta):+.3f}" if value_delta is not None else "missing"
    return (
        f"m6={original.get('value')}->{projected.get('value')} "
        f"pass={original.get('pass')}->{projected.get('pass')} "
        f"delta={delta_text} "
        f"intervalsChanged={difference.get('interval_changed_count', 0)} "
        f"(audit-only)"
    )
