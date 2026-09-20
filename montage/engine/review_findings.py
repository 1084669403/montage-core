"""Read-only projections over the append-only review finding log.

``review_log.jsonl`` remains the source of truth.  This module never rewrites
it; it derives stable finding identities and lifecycle summaries for reports
and future status updates.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


REVIEW_FINDING_STATUSES = {
    "open",
    "in_progress",
    "fixed",
    "verified",
    "waived",
    "invalid",
}


def _digest(raw: Any) -> str:
    payload = json.dumps(
        raw,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _review_id(row: dict[str, Any], record_index: int) -> str:
    explicit = str(row.get("review_id") or "").strip()
    if explicit:
        return explicit
    identity = {
        "timestamp": row.get("timestamp"),
        "role": row.get("role"),
        "subject": row.get("subject"),
        "phase": row.get("phase"),
        "decision": row.get("decision"),
        "record_index": record_index,
    }
    return f"R-{_digest(identity)}"


def _finding_identity(
    *,
    finding: dict[str, Any],
    row: dict[str, Any],
) -> str:
    explicit = str(finding.get("finding_id") or "").strip()
    if explicit:
        return explicit
    identity = {
        "role": str(row.get("role") or ""),
        "subject": str(row.get("subject") or ""),
        "field": str(finding.get("field") or ""),
        "message": str(finding.get("message") or ""),
    }
    return f"F-{_digest(identity)}"


def _valid_status(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return value if value in REVIEW_FINDING_STATUSES else "open"


def parse_review_log(lines: Iterable[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse JSONL without rewriting it and keep malformed-line facts."""
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append({"line_number": line_number, "error": str(exc)})
            continue
        if not isinstance(row, dict):
            errors.append({
                "line_number": line_number,
                "error": "line is not a JSON object",
            })
            continue
        rows.append(row)
    return rows, errors


def load_review_log(path: str | Path) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """Load the append-only log; missing is None to distinguish empty."""
    file_path = Path(path)
    if not file_path.is_file():
        return None, []
    return parse_review_log(file_path.read_text(encoding="utf-8").splitlines())


def build_review_findings(
    rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Project findings into stable, additive lifecycle records."""
    grouped: dict[str, dict[str, Any]] = {}
    for record_index, row in enumerate(rows or []):
        review_id = _review_id(row, record_index)
        for finding_index, finding in enumerate(
            row.get("findings") if isinstance(row.get("findings"), list) else [],
            1,
        ):
            if not isinstance(finding, dict):
                continue
            finding_id = _finding_identity(finding=finding, row=row)
            status = _valid_status(finding.get("status"))
            evidence = [
                str(item)
                for item in (finding.get("evidence") if isinstance(finding.get("evidence"), list) else [])
                if str(item)
            ]
            seen_at = str(row.get("timestamp") or "")
            current = grouped.get(finding_id)
            if current is None:
                current = {
                    "finding_id": finding_id,
                    "status": status,
                    "target": str(finding.get("field") or ""),
                    "severity": str(finding.get("severity") or ""),
                    "message": str(finding.get("message") or ""),
                    "proposed_fix": str(finding.get("proposed_fix") or ""),
                    "evidence": evidence,
                    "first_seen_at": seen_at,
                    "last_seen_at": seen_at,
                    "first_seen_review_id": review_id,
                    "last_seen_review_id": review_id,
                    "occurrence_count": 0,
                    "explicit_status": bool(finding.get("status")),
                }
                grouped[finding_id] = current

            current["occurrence_count"] += 1
            current["last_seen_at"] = seen_at
            current["last_seen_review_id"] = review_id
            if finding.get("status"):
                current["status"] = status
                current["explicit_status"] = True
            if evidence:
                current["evidence"] = evidence

    findings = list(grouped.values())
    status_counts = {
        status: sum(row["status"] == status for row in findings)
        for status in sorted(REVIEW_FINDING_STATUSES)
    }
    severity_counts = {
        severity: sum(row["severity"] == severity for row in findings)
        for severity in sorted({row["severity"] for row in findings})
        if severity
    }
    return {
        "schema_version": 1,
        "record_count": len(rows or []),
        "finding_count": len(findings),
        "occurrence_count": sum(row["occurrence_count"] for row in findings),
        "status_counts": status_counts,
        "severity_counts": severity_counts,
        "findings": findings,
    }


def review_findings_summary(
    projection: dict[str, Any],
    *,
    log_present: bool,
    parse_errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Small projection for delivery reports; omit full findings by default."""
    status_counts = projection.get("status_counts") or {}
    findings = projection.get("findings") or []
    return {
        "log_present": bool(log_present),
        "record_count": int(projection.get("record_count") or 0),
        "finding_count": int(projection.get("finding_count") or 0),
        "occurrence_count": int(projection.get("occurrence_count") or 0),
        "status_counts": status_counts,
        "severity_counts": projection.get("severity_counts") or {},
        "open_critical_count": sum(
            row.get("status") == "open" and row.get("severity") == "critical"
            for row in findings
            if isinstance(row, dict)
        ),
        "parse_error_count": len(parse_errors or []),
    }


def build_finding_id_index(
    projection: dict[str, Any],
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return a compact, read-only index for review finding IDs.

    The full projection stays the source of truth.  This view is intended for
    review cards and human-facing lookups, so each row keeps enough context to
    trace an ``F-*`` id back to its first and last review records.
    """
    findings = [
        row for row in (projection.get("findings") or [])
        if isinstance(row, dict)
    ]
    if limit is not None and limit >= 0:
        findings = findings[:limit]
    return [
        {
            "finding_id": row.get("finding_id") or "",
            "status": row.get("status") or "open",
            "severity": row.get("severity") or "",
            "target": row.get("target") or "",
            "message": row.get("message") or "",
            "first_seen_at": row.get("first_seen_at") or "",
            "last_seen_at": row.get("last_seen_at") or "",
            "first_seen_review_id": row.get("first_seen_review_id") or "",
            "last_seen_review_id": row.get("last_seen_review_id") or "",
            "occurrence_count": int(row.get("occurrence_count") or 0),
        }
        for row in findings
    ]


def query_finding_id_index(
    projection: dict[str, Any],
    *,
    finding_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    target: str | None = None,
) -> list[dict[str, Any]]:
    """Filter the read-only finding index without mutating the projection."""
    needle_id = str(finding_id or "").strip().lower()
    needle_status = str(status or "").strip().lower()
    needle_severity = str(severity or "").strip().lower()
    needle_target = str(target or "").strip().lower()
    rows = build_finding_id_index(projection)
    return [
        row for row in rows
        if (not needle_id or str(row["finding_id"]).lower() == needle_id)
        and (not needle_status or str(row["status"]).lower() == needle_status)
        and (not needle_severity or str(row["severity"]).lower() == needle_severity)
        and (not needle_target or needle_target in str(row["target"]).lower())
    ]
