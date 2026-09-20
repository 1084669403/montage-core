"""Read-only review finding projection tests."""

from __future__ import annotations

from pathlib import Path

from montage.engine.review_findings import (
    build_review_findings,
    load_review_log,
    parse_review_log,
    review_findings_summary,
)


def _row(**overrides):
    base = {
        "timestamp": "2026-09-18T01:00:00+00:00",
        "role": "art_director",
        "subject": "cast",
        "decision": "REVISE",
        "findings": [{
            "severity": "critical",
            "field": "elder_xu.outfit",
            "message": "visible text on costume",
            "proposed_fix": "remove text and regenerate",
        }],
    }
    base.update(overrides)
    return base


def test_build_review_findings_assigns_stable_legacy_ids():
    first = build_review_findings([_row()])
    second = build_review_findings([_row()])
    assert first["finding_count"] == 1
    finding = first["findings"][0]
    assert finding["finding_id"].startswith("F-")
    assert finding["status"] == "open"
    assert finding["first_seen_review_id"].startswith("R-")
    assert finding["finding_id"] == second["findings"][0]["finding_id"]


def test_build_review_findings_groups_repeated_finding_and_keeps_first_raiser():
    rows = [
        _row(timestamp="2026-09-18T01:00:00+00:00"),
        _row(timestamp="2026-09-18T02:00:00+00:00"),
    ]
    report = build_review_findings(rows)
    assert report["record_count"] == 2
    assert report["finding_count"] == 1
    assert report["occurrence_count"] == 2
    finding = report["findings"][0]
    assert finding["occurrence_count"] == 2
    assert finding["last_seen_at"] == "2026-09-18T02:00:00+00:00"
    assert finding["first_seen_review_id"] != finding["last_seen_review_id"]


def test_build_review_findings_honors_explicit_status_and_evidence():
    rows = [
        _row(
            timestamp="2026-09-18T01:10:00+00:00",
            findings=[{
                "finding_id": "F-explicit",
                "severity": "critical",
                "field": "elder_xu.outfit",
                "message": "visible text on costume",
                "status": "fixed",
            }],
        ),
        _row(
            timestamp="2026-09-18T02:00:00+00:00",
            findings=[{
                "finding_id": "F-explicit",
                "severity": "critical",
                "field": "elder_xu.outfit",
                "message": "visible text on costume",
                "status": "verified",
                "evidence": ["assets/videos/sc01_02.mp4"],
            }],
        ),
    ]
    report = build_review_findings(rows)
    assert report["finding_count"] == 1
    finding = report["findings"][0]
    assert finding["finding_id"] == "F-explicit"
    assert finding["status"] == "verified"
    assert finding["evidence"] == ["assets/videos/sc01_02.mp4"]
    assert report["status_counts"]["verified"] == 1


def test_parse_review_log_keeps_valid_rows_and_error_positions():
    rows, errors = parse_review_log([
        '{"role":"director"}',
        '{bad}',
        '[]',
        '',
    ])
    assert rows == [{"role": "director"}]
    assert [error["line_number"] for error in errors] == [2, 3]


def test_load_review_log_distinguishes_missing_from_empty():
    missing, missing_errors = load_review_log(Path("does-not-exist/review_log.jsonl"))
    assert missing is None
    assert missing_errors == []
    rows, errors = parse_review_log([])
    assert rows == []
    assert errors == []


def test_review_findings_summary_counts_open_critical_and_parse_errors():
    projection = build_review_findings([
        _row(),
        _row(timestamp="2026-09-18T02:00:00+00:00", findings=[{
            "severity": "critical",
            "field": "material_mix",
            "message": "mixed material",
            "status": "verified",
            "evidence": ["assets/videos/sc01_01.mp4"],
        }]),
    ])
    summary = review_findings_summary(
        projection,
        log_present=True,
        parse_errors=[{"line_number": 2, "error": "bad json"}],
    )
    assert summary["log_present"] is True
    assert summary["finding_count"] == 2
    assert summary["open_critical_count"] == 1  # legacy fixture finding is open/critical
    assert summary["status_counts"]["verified"] == 1
    assert summary["parse_error_count"] == 1
