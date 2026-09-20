"""Tests for append-only review finding status updates."""

from __future__ import annotations

import pytest

from montage.schemas import REVIEW_LOG_SCHEMA
from montage.tools import review_logger as rl
from montage.engine.review_findings import build_review_findings


def test_review_log_schema_allows_additive_lifecycle_fields():
    finding = REVIEW_LOG_SCHEMA["properties"]["findings"]["items"]["properties"]
    assert {"finding_id", "status", "evidence"} <= set(finding)
    assert "status_update" in REVIEW_LOG_SCHEMA["properties"]["phase"]["enum"]


def test_status_update_phase_is_not_a_revise_round():
    rows = rl._auto_rounds([{
        "timestamp": "t1", "role": "user", "subject": "cast",
        "phase": rl.PHASE_STATUS_UPDATE, "decision": "STATUS_UPDATE",
        "findings": [{"finding_id": "F-1", "status": "fixed"}],
    }])
    assert rows[0]["phase_eff"] == rl.PHASE_STATUS_UPDATE
    assert rows[0]["auto_round"] == 0


def test_finding_status_operation_appends_status_update_row(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(rl, "record_review", lambda _dir, row: recorded.append(row))
    result = rl.ReviewLogger().execute({
        "operation": "finding_status",
        "project_dir": "fake-project",
        "finding_id": "F-123",
        "status": "verified",
        "evidence": ["assets/videos/sc01_01.mp4"],
        "note": "verified after regeneration",
    })
    assert result.success is True
    assert result.meta["operation"] == "finding_status"
    row = result.data["row"]
    assert row["phase"] == rl.PHASE_STATUS_UPDATE
    assert row["decision"] == "STATUS_UPDATE"
    assert row["subject"] == "finding_status"
    assert row["findings"][0]["finding_id"] == "F-123"
    assert row["findings"][0]["status"] == "verified"
    assert row["findings"][0]["evidence"] == ["assets/videos/sc01_01.mp4"]
    assert recorded == [row]


def test_finding_status_operation_requires_valid_status(monkeypatch):
    recorded: list[dict] = []
    monkeypatch.setattr(rl, "record_review", lambda _dir, row: recorded.append(row))
    missing_status = rl.ReviewLogger().execute({
        "operation": "finding_status", "project_dir": "fake", "finding_id": "F-1",
    })
    missing_id = rl.ReviewLogger().execute({
        "operation": "finding_status", "project_dir": "fake", "status": "fixed",
    })
    invalid = rl.ReviewLogger().execute({
        "operation": "finding_status", "project_dir": "fake",
        "finding_id": "F-1", "status": "closed",
    })
    assert missing_status.success is False and "status" in (missing_status.error or "")
    assert missing_id.success is False and "finding_id" in (missing_id.error or "")
    assert invalid.success is False and "status" in (invalid.error or "")
    assert recorded == []


def test_summary_exposes_lifecycle_and_does_not_count_status_updates(monkeypatch):
    rows = [
        {
            "timestamp": "2026-09-18T01:00:00+00:00", "role": "art_director",
            "subject": "cast", "phase": rl.PHASE_REVISE, "decision": "REVISE",
            "findings": [{
                "finding_id": "F-1", "severity": "critical",
                "field": "elder_xu.outfit", "message": "text on costume",
            }],
        },
        {
            "timestamp": "2026-09-18T02:00:00+00:00", "role": "user",
            "subject": "finding_status", "phase": rl.PHASE_STATUS_UPDATE,
            "decision": "STATUS_UPDATE",
            "findings": [{
                "finding_id": "F-1", "status": "verified",
                "evidence": ["assets/videos/sc01_02.mp4"],
            }],
        },
    ]
    monkeypatch.setattr(rl, "load_reviews", lambda _dir: rows)
    summary = rl.summarize_reviews("fake-project")
    assert summary["rows"] == 2
    cast = next(row for row in summary["subjects"] if row["subject"] == "cast")
    assert cast["revise_rounds"] == 1
    status = next(row for row in summary["subjects"] if row["subject"] == "finding_status")
    assert status["revise_rounds"] == 0
    assert summary["finding_lifecycle"]["status_counts"]["verified"] == 1
    assert summary["finding_lifecycle"]["open_critical_count"] == 0
    projection = build_review_findings(rows)
    assert projection["finding_count"] == 1
