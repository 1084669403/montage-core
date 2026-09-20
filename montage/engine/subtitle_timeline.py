"""Read-only subtitle timeline projection.

``compose_plan.subtitle_cues`` records absolute times on the measured
shot-accumulation axis.  Non-cut xfade junctions overlap that axis, so final
subtitle times must be shifted by the cumulative overlap before each shot.
This module only projects the correction; it does not rewrite cues or SRT.
"""

from __future__ import annotations

import re
from typing import Any


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


def _parse_srt_timestamp(raw: str) -> float | None:
    match = re.fullmatch(
        r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})",
        raw.strip(),
    )
    if not match:
        return None
    hours, minutes, seconds, milliseconds = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000


def parse_srt_cues(srt_text: str) -> tuple[list[dict[str, Any]], int]:
    """Parse SRT into ordered cues and a malformed-block count."""
    cues: list[dict[str, Any]] = []
    parse_errors = 0
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    for block in blocks:
        if not block.strip():
            continue
        lines = block.strip().splitlines()
        timestamp_index = next(
            (index for index, line in enumerate(lines) if "-->" in line),
            None,
        )
        if timestamp_index is None:
            parse_errors += 1
            continue
        parts = re.split(r"-->", lines[timestamp_index], maxsplit=1)
        start = _parse_srt_timestamp(parts[0]) if parts else None
        end = _parse_srt_timestamp(parts[1]) if len(parts) > 1 else None
        text = "\n".join(lines[timestamp_index + 1:]).strip()
        if start is None or end is None or not text:
            parse_errors += 1
            continue
        cues.append({
            "start_seconds": start,
            "end_seconds": end,
            "text": text,
        })
    return cues, parse_errors


def _normalized_text(raw: Any) -> str:
    return "".join(str(raw or "").split())


def compare_srt_to_projection(
    *,
    srt_text: str | None,
    projection: dict[str, Any],
    title_offset_seconds: float = 0.0,
    tolerance_seconds: float = 0.0015,
) -> dict[str, Any]:
    """Compare an existing SRT against projected main-video cues.

    ``title_offset_seconds`` is added to projected cues before comparison so
    the audit models the same offset that ``finish`` applies to final SRT.
    """
    projected = [
        row for row in projection.get("cues") or [] if isinstance(row, dict)
    ]
    if srt_text is None:
        srt_cues: list[dict[str, Any]] = []
        parse_errors = 0
    else:
        srt_cues, parse_errors = parse_srt_cues(srt_text)
    compared = min(len(srt_cues), len(projected))
    start_deltas: list[float] = []
    end_deltas: list[float] = []
    text_match_count = 0
    drift_rows: list[dict[str, Any]] = []
    for index in range(compared):
        source = projected[index]
        srt = srt_cues[index]
        expected_start = float(source.get("start_seconds") or 0) + title_offset_seconds
        expected_end = float(source.get("end_seconds") or 0) + title_offset_seconds
        start_delta = float(srt.get("start_seconds") or 0) - expected_start
        end_delta = float(srt.get("end_seconds") or 0) - expected_end
        start_deltas.append(start_delta)
        end_deltas.append(end_delta)
        text_match = _normalized_text(source.get("text")) == _normalized_text(srt.get("text"))
        if text_match:
            text_match_count += 1
        if (
            abs(start_delta) > tolerance_seconds
            or abs(end_delta) > tolerance_seconds
            or not text_match
        ):
            drift_rows.append({
                "index": index + 1,
                "srt_start_seconds": srt.get("start_seconds"),
                "srt_end_seconds": srt.get("end_seconds"),
                "expected_start_seconds": expected_start,
                "expected_end_seconds": expected_end,
                "start_delta_seconds": round(start_delta, 9),
                "end_delta_seconds": round(end_delta, 9),
                "text_match": text_match,
            })
    max_start_delta = max((abs(value) for value in start_deltas), default=0.0)
    max_end_delta = max((abs(value) for value in end_deltas), default=0.0)
    max_abs_delta = max(max_start_delta, max_end_delta)
    all_text_match = compared == len(projected) and text_match_count == compared
    if srt_text is None:
        status = "not_needed" if not projected else "missing_srt"
    elif parse_errors:
        status = "malformed_srt"
    elif len(srt_cues) != len(projected):
        status = "cue_count_mismatch"
    elif max_abs_delta > tolerance_seconds or not all_text_match:
        status = "drift"
    else:
        status = "aligned"

    if status == "aligned":
        action = "keep"
    elif status in ("drift", "missing_srt"):
        action = "regenerate_srt"
    elif status == "not_needed":
        action = "none"
    else:
        action = "inspect_srt"

    srt_end = max(
        (float(row.get("end_seconds") or 0) for row in srt_cues),
        default=0.0,
    )
    expected_end = max(
        (float(row.get("end_seconds") or 0) for row in projected),
        default=0.0,
    ) + title_offset_seconds
    return {
        "status": status,
        "recommended_action": action,
        "srt_present": srt_text is not None,
        "srt_cue_count": len(srt_cues),
        "projected_cue_count": len(projected),
        "compared_cue_count": compared,
        "parse_error_count": parse_errors,
        "text_match_count": text_match_count,
        "title_offset_seconds": title_offset_seconds,
        "tolerance_seconds": tolerance_seconds,
        "max_abs_start_delta_seconds": round(max_start_delta, 9),
        "max_abs_end_delta_seconds": round(max_end_delta, 9),
        "max_abs_delta_seconds": round(max_abs_delta, 9),
        "srt_end_seconds": srt_end,
        "projected_end_with_title_seconds": expected_end,
        "drift_row_count": len(drift_rows),
        "drift_rows": drift_rows,
    }


def audit_srt_sync(
    *,
    compose_plan: dict[str, Any] | None,
    srt_text: str | None,
    title_offset_seconds: float = 0.0,
    final_duration_seconds: float | None = None,
    tolerance_seconds: float = 0.0015,
) -> dict[str, Any] | None:
    """Read-only SRT sync audit against the xfade projection."""
    if compose_plan is None:
        return None
    projection = project_subtitle_timeline(compose_plan)
    audit = compare_srt_to_projection(
        srt_text=srt_text,
        projection=projection,
        title_offset_seconds=title_offset_seconds,
        tolerance_seconds=tolerance_seconds,
    )
    srt_end = float(audit.get("srt_end_seconds") or 0)
    expected_end = float(audit.get("projected_end_with_title_seconds") or 0)
    if final_duration_seconds is None:
        within_final = None
        margin = None
    else:
        margin = final_duration_seconds - srt_end
        within_final = margin >= -tolerance_seconds
    audit.update({
        "final_duration_seconds": final_duration_seconds,
        "srt_end_margin_seconds": round(margin, 9) if margin is not None else None,
        "srt_end_within_final": within_final,
        "projected_duration_seconds": projection.get("projected_duration_seconds"),
        "subtitle_anomaly_count": projection.get("anomaly_count"),
        "subtitle_anomalies": projection.get("anomalies"),
    })
    if within_final is False:
        audit["status"] = "outside_final"
        audit["recommended_action"] = "regenerate_srt"
    return audit


def format_srt_audit(audit: dict[str, Any] | None) -> str:
    """Render compact SRT sync facts for reports and operator summaries."""
    row = audit or {}
    if not row:
        return "compose_plan 未生成"
    margin = row.get("srt_end_margin_seconds")
    value = (
        f"status={row.get('status') or 'unknown'} "
        f"action={row.get('recommended_action') or 'unknown'} "
        f"compared={row.get('compared_cue_count', 0)}/{row.get('projected_cue_count', 0)} "
        f"maxDelta={float(row.get('max_abs_delta_seconds') or 0):.3f}s "
        f"end={float(row.get('srt_end_seconds') or 0):.3f}s "
    )
    if margin is not None:
        value = value.rstrip()
        value += f" margin={float(margin):.3f}s"
    return value.rstrip()


def build_regenerated_srt(
    compose_plan: dict[str, Any] | None,
    *,
    title_offset_seconds: float = 0.0,
    max_chars_per_line: int = 20,
) -> dict[str, Any]:
    """Build corrected final-SRT content from projected xfade cues.

    This is a pure content builder.  Callers must expose an explicit write
    action; the read-only delivery audit never invokes it.
    """
    if compose_plan is None:
        raise ValueError("compose_plan missing")
    projection = project_subtitle_timeline(compose_plan)
    source_cues = [
        row for row in projection.get("cues") or [] if isinstance(row, dict)
    ]
    if not source_cues:
        raise ValueError("compose_plan has no subtitle cues")
    cues = []
    for cue in source_cues:
        rebuilt = {
            **cue,
            "start_seconds": float(cue.get("start_seconds") or 0) + title_offset_seconds,
            "end_seconds": float(cue.get("end_seconds") or 0) + title_offset_seconds,
        }
        cues.append(rebuilt)
    from montage.tools.subtitle_builder import timestamps_to_srt

    return {
        "cues": cues,
        "srt": timestamps_to_srt(
            cues,
            max_chars_per_line=max_chars_per_line,
        ),
        "title_offset_seconds": title_offset_seconds,
        "cue_count": len(cues),
        "projected_duration_seconds": projection.get("projected_duration_seconds"),
        "projection": projection,
    }


def _subtitle_from_cue(cue: dict[str, Any], shift: float) -> dict[str, Any] | None:
    text = str(cue.get("text") or "").strip()
    start = _safe_float(cue.get("start_seconds"))
    if not text or start is None:
        return None
    end = _safe_float(cue.get("end_seconds"))
    if end is None:
        end = start
    subtitle: dict[str, Any] = {
        "text": text,
        "start_seconds": start + shift,
        "end_seconds": end + shift,
    }
    speaker = cue.get("speaker_id")
    if speaker:
        subtitle["speaker_id"] = speaker
    return subtitle


def project_subtitle_timeline(
    compose_plan: dict[str, Any] | None,
    *,
    tolerance_seconds: float = 0.001,
) -> dict[str, Any]:
    """Project cues from shot accumulation onto the xfade output timeline.

    The projected value is relative to the assembled main video.  Title-card
    offset is intentionally excluded and remains a separate finish concern.
    """
    shots = [
        row
        for row in (compose_plan or {}).get("shots") or []
        if isinstance(row, dict)
    ]
    cue_rows: list[dict[str, Any]] = []
    cues: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    planned_duration = 0.0
    projected_start = 0.0
    overlap_count = 0
    overlap_seconds = 0.0

    for index, shot in enumerate(shots):
        duration = _safe_float(shot.get("duration_seconds")) or 0.0
        planned_start = planned_duration
        if index > 0:
            overlap = _effective_overlap(shot)
            overlap_seconds += overlap
            overlap_count += 1 if overlap > 0 else 0
            projected_start -= overlap
        shift = projected_start - planned_start

        raw_cues = [
            row
            for row in shot.get("subtitle_cues") or []
            if isinstance(row, dict)
        ]
        shot_cues: list[dict[str, Any]] = []
        for source_index, cue in enumerate(raw_cues):
            projected = _subtitle_from_cue(cue, shift)
            if projected is None:
                anomalies.append({
                    "kind": "invalid_cue_skipped",
                    "junction_index": index,
                    "source_index": source_index,
                    "shot_id": shot.get("shot_id"),
                })
                continue
            source_start = _safe_float(cue.get("start_seconds")) or 0.0
            source_end = _safe_float(cue.get("end_seconds"))
            if source_end is None:
                source_end = source_start
            if (
                source_start < planned_start - tolerance_seconds
                or source_end > planned_start + duration + tolerance_seconds
            ):
                anomalies.append({
                    "kind": "cue_outside_shot",
                    "junction_index": index,
                    "source_index": source_index,
                    "shot_id": shot.get("shot_id"),
                    "cue_start_seconds": source_start,
                    "cue_end_seconds": source_end,
                    "shot_start_seconds": planned_start,
                    "shot_end_seconds": planned_start + duration,
                })
            shot_cues.append(projected)

        if shot_cues:
            cue_rows.append({
                "shot_id": shot.get("shot_id"),
                "planned_start_seconds": round(planned_start, 9),
                "projected_start_seconds": round(projected_start, 9),
                "shift_seconds": round(shift, 9),
                "cue_count": len(shot_cues),
            })
        cues.extend(shot_cues)
        planned_duration += duration
        projected_start += duration

    shifted = sum(
        1
        for row in cue_rows
        if abs(float(row.get("shift_seconds") or 0)) > tolerance_seconds
    )
    max_abs_shift = max(
        (abs(float(row.get("shift_seconds") or 0)) for row in cue_rows),
        default=0.0,
    )
    return {
        "model": "measured_accumulation_minus_xfade",
        "tolerance_seconds": tolerance_seconds,
        "shot_count": len(shots),
        "cue_count": len(cues),
        "shifted_shot_count": shifted,
        "max_abs_shift_seconds": round(max_abs_shift, 9),
        "planned_duration_seconds": round(planned_duration, 9),
        "projected_duration_seconds": round(planned_duration - overlap_seconds, 9),
        "transition_overlap_count": overlap_count,
        "transition_overlap_seconds": round(overlap_seconds, 9),
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
        "cue_rows": cue_rows,
        "cues": cues,
    }
