"""Append-only generation event writer tests."""

from __future__ import annotations

import io
import json

import pytest

from montage.tools.generation_events import (
    GenerationEventError,
    normalize_event,
    parse_events_with_errors,
    parse_events,
    read_events,
    write_event,
)


def _event(**overrides):
    return {
        "event_id": "evt-1",
        "run_id": "run-1",
        "batch_id": "batch-1",
        "shot_id": "sh01",
        "attempt": 1,
        "provider": "agnes",
        "model": "agnes-video-2.5-flash",
        "kind": "video",
        "event": "submitted",
        **overrides,
    }


def test_write_event_preserves_append_only_jsonl_line():
    stream = io.StringIO()
    first = write_event(stream, _event())
    second = write_event(stream, _event(event_id="evt-2", event="succeeded"))
    rows = [json.loads(row) for row in stream.getvalue().splitlines()]
    assert first["event_id"] == "evt-1"
    assert second["event_id"] == "evt-2"
    assert len(rows) == 2
    assert rows[0]["event"] == "submitted"
    assert rows[1]["event"] == "succeeded"
    assert all(row["schema_version"] == 1 for row in rows)


def test_normalize_event_generates_id_and_rejects_unknown_kind():
    event = normalize_event(_event(event_id=""))
    assert event["event_id"]
    with pytest.raises(GenerationEventError, match="unknown event"):
        normalize_event(_event(event="maybe_succeeded"))


def test_normalize_event_requires_core_fields_and_valid_attempt():
    with pytest.raises(GenerationEventError, match="missing required fields"):
        normalize_event(_event(run_id="", shot_id=""))
    with pytest.raises(GenerationEventError, match="attempt"):
        normalize_event(_event(attempt=0))
    assert normalize_event(_event(attempt="2"))["attempt"] == 2


def test_parse_events_reports_corrupt_jsonl_position():
    rows = parse_events(['{"event_id":"evt-1"}'])
    assert rows == [{"event_id": "evt-1"}]
    with pytest.raises(GenerationEventError, match="line 2"):
        parse_events(['{"event_id":"evt-1"}', "{bad}"])


def test_parse_events_with_errors_keeps_valid_rows_and_malformed_lines():
    rows, errors = parse_events_with_errors([
        '{"event_id":"evt-1"}',
        '{bad}',
        '[]',
        '',
    ])
    assert rows == [{"event_id": "evt-1"}]
    assert errors == [
        {"line_number": 2, "error": errors[0]["error"]},
        {"line_number": 3, "error": "line is not a JSON object"},
    ]
