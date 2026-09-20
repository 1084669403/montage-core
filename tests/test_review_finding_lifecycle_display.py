from __future__ import annotations

from montage.engine.director import render_review_md
from montage.engine.review_findings import (
    build_finding_id_index,
    build_review_findings,
    review_findings_summary,
)


def _projection() -> dict:
    rows = [
        {
            "timestamp": "2026-09-18T01:00:00+00:00",
            "role": "art_director",
            "subject": "cast",
            "phase": "revise",
            "decision": "REVISE",
            "findings": [{
                "finding_id": "F-1",
                "severity": "critical",
                "field": "elder_xu.outfit",
                "message": "visible text on costume",
                "status": "fixed",
                "evidence": ["assets/videos/sc01_01.mp4"],
            }],
        },
        {
            "timestamp": "2026-09-18T02:00:00+00:00",
            "role": "user",
            "subject": "finding_status",
            "phase": "status_update",
            "decision": "STATUS_UPDATE",
            "findings": [{
                "finding_id": "F-1",
                "status": "verified",
                "evidence": ["assets/videos/sc01_01.mp4"],
            }],
        },
    ]
    return build_review_findings(rows)


def test_finding_id_index_is_read_only_and_compact():
    projection = _projection()
    index = build_finding_id_index(projection)
    assert index == [{
        "finding_id": "F-1",
        "status": "verified",
        "severity": "critical",
        "target": "elder_xu.outfit",
        "message": "visible text on costume",
        "first_seen_at": "2026-09-18T01:00:00+00:00",
        "last_seen_at": "2026-09-18T02:00:00+00:00",
        "first_seen_review_id": projection["findings"][0]["first_seen_review_id"],
        "last_seen_review_id": projection["findings"][0]["last_seen_review_id"],
        "occurrence_count": 2,
    }]
    assert build_finding_id_index(projection, limit=0) == []


def test_review_md_renders_lifecycle_index_without_making_it_editable():
    projection = _projection()
    summary = review_findings_summary(
        projection,
        log_present=True,
        parse_errors=[{"line_number": 2, "error": "bad json"}],
    )
    card = {
        "status": "await_setup",
        "heading": "setup",
        "summary": [{"label": "标题", "value": "demo"}],
        "fields": [],
        "choices": {},
        "findings": [],
        "finding_lifecycle": {
            "summary": summary,
            "parse_errors": [{"line_number": 2, "error": "bad json"}],
            "index": build_finding_id_index(projection),
        },
    }
    rendered = render_review_md(card)
    assert "## Finding 生命周期" in rendered
    assert "Findings**：1 个" in rendered
    assert "Open critical" not in rendered
    assert "日志坏行**：1 行" in rendered
    assert "`F-1` `verified/critical` · elder_xu.outfit" in rendered
