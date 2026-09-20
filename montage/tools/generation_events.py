"""Append-only generation event records.

The JSONL file is the durable fact source.  A later task state file may be
rebuilt from it, but must never replace it.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Iterable


EVENT_NAMES = {
    "queued",
    "submitted",
    "poll_started",
    "poll_timeout",
    "failed",
    "retry_scheduled",
    "downloaded",
    "validated",
    "succeeded",
    "manifest_updated",
}

REQUIRED_FIELDS = ("run_id", "shot_id", "kind", "event", "attempt")


class GenerationEventError(ValueError):
    """Raised when an event cannot become a durable fact."""


def normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate one event and add its schema version and stable identity."""
    if not isinstance(raw, dict):
        raise GenerationEventError("generation event must be an object")

    event = dict(raw)
    event["schema_version"] = 1
    event["event_id"] = str(event.get("event_id") or uuid.uuid4().hex)
    event["event"] = str(event.get("event") or "")

    missing = [
        field
        for field in REQUIRED_FIELDS
        if field != "event_id" and event.get(field) in ("", None)
    ]
    if missing:
        raise GenerationEventError(f"missing required fields: {', '.join(missing)}")
    if event["event"] not in EVENT_NAMES:
        raise GenerationEventError(f"unknown event: {event['event']}")
    try:
        attempt = int(event["attempt"])
    except (TypeError, ValueError) as exc:
        raise GenerationEventError("attempt must be an integer") from exc
    if attempt < 1:
        raise GenerationEventError("attempt must be >= 1")
    event["attempt"] = attempt

    return event


def write_event(stream: Any, raw: dict[str, Any]) -> dict[str, Any]:
    """Serialize one normalized event as one JSONL line."""
    event = normalize_event(raw)
    stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
    return event


def append_event(path: str | Path, raw: dict[str, Any]) -> dict[str, Any]:
    """Append one normalized event atomically at the JSONL-record level."""
    event = normalize_event(raw)
    with Path(path).open("a", encoding="utf-8", newline="\n") as stream:
        write_event(stream, event)
    return event


def parse_events(lines: Iterable[str]) -> list[dict[str, Any]]:
    """Parse in-memory JSONL lines without rewriting a canonical file."""
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise GenerationEventError(f"invalid JSON on line {line_number}") from exc
        if not isinstance(row, dict):
            raise GenerationEventError(f"line {line_number} is not an object")
        rows.append(row)
    return rows


def parse_events_with_errors(lines: Iterable[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse in-memory JSONL lines while preserving malformed-line facts."""
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append({
                "line_number": line_number,
                "error": str(exc),
            })
            continue
        if not isinstance(row, dict):
            errors.append({
                "line_number": line_number,
                "error": "line is not a JSON object",
            })
            continue
        rows.append(row)
    return rows, errors


def read_events(path: str | Path) -> list[dict[str, Any]]:
    """Read events without rewriting or validating a canonical file."""
    file_path = Path(path)
    if not file_path.exists():
        return []
    return parse_events(file_path.read_text(encoding="utf-8").splitlines())


def load_generation_events(path: str | Path) -> tuple[list[dict[str, Any]] | None, list[dict[str, Any]]]:
    """Load the append-only ledger without rewriting it.

    A missing ledger is reported as ``None`` so projections can distinguish
    “no generations yet” from “an empty but healthy ledger”.
    """
    file_path = Path(path)
    if not file_path.exists():
        return None, []
    return parse_events_with_errors(file_path.read_text(encoding="utf-8").splitlines())
