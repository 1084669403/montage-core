from __future__ import annotations

import json

from montage.cli import main
from montage.engine.review_findings import (
    build_review_findings,
    query_finding_id_index,
)


ROWS = [
    {
        "timestamp": "2026-09-18T01:00:00+00:00",
        "role": "art_director",
        "subject": "cast",
        "phase": "revise",
        "decision": "REVISE",
        "findings": [
            {
                "finding_id": "F-open",
                "severity": "critical",
                "field": "material_mix",
                "message": "mixed material",
            },
            {
                "finding_id": "F-verified",
                "severity": "warning",
                "field": "m1/sc01_01",
                "message": "weak anchor",
                "status": "fixed",
            },
        ],
    },
    {
        "timestamp": "2026-09-18T02:00:00+00:00",
        "role": "user",
        "subject": "finding_status",
        "phase": "status_update",
        "decision": "STATUS_UPDATE",
        "findings": [{
            "finding_id": "F-verified",
            "status": "verified",
            "evidence": ["assets/videos/sc01_01.mp4"],
        }],
    },
]


def test_query_finding_id_index_filters_without_mutation():
    projection = build_review_findings(ROWS)
    before = list(projection["findings"])
    rows = query_finding_id_index(
        projection,
        status="Verified",
        severity="WARNING",
        target="M1",
    )
    assert [row["finding_id"] for row in rows] == ["F-verified"]
    assert rows[0]["occurrence_count"] == 2
    assert projection["findings"] == before


def test_cli_review_findings_outputs_readonly_json(monkeypatch, capsys):
    from montage.engine import review_findings

    monkeypatch.setattr(
        review_findings,
        "load_review_log",
        lambda _path: (ROWS, [{"line_number": 3, "error": "bad json"}]),
    )
    code = main(["review_findings", "fake-project", "--finding-id", "f-open"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["log_present"] is True
    assert payload["filters"]["finding_id"] == "f-open"
    assert payload["total_findings"] == 2
    assert payload["matched_findings"] == 1
    assert payload["matched_occurrences"] == 1
    assert payload["findings"][0]["finding_id"] == "F-open"
    assert payload["parse_error_count"] == 1


def test_cli_review_findings_summary(monkeypatch, capsys):
    from montage.engine import review_findings

    monkeypatch.setattr(
        review_findings,
        "load_review_log",
        lambda _path: (ROWS, []),
    )
    code = main(["review_findings", "fake-project", "--status", "open", "--summary"])
    out = capsys.readouterr().out.strip()
    assert code == 0
    assert "log_present=True" in out
    assert "total=2" in out
    assert "matched=1" in out
    assert "occurrences=1" in out
    assert "parse_errors=0" in out
