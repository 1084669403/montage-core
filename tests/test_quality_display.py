from __future__ import annotations

import pytest

from montage.cli import main
from montage.schemas import PROPOSAL_PACKET_SCHEMA
from montage.engine.delivery_report import (
    build_vlm_state,
    format_vlm_state,
)
from montage.engine.director import _attach_quality_summary, render_review_md
from montage.engine.delivery_report import format_duration_reconciliation


METRICS = {
    "metrics": {
        "m1": {
            "value": 1.0,
            "threshold": 1.0,
            "pass": True,
            "skipped": False,
            "detail": {"vlm_checked": 0, "vlm_skipped": True},
        }
    }
}


def test_build_vlm_state_and_compact_format_separate_skip_from_verify():
    state = build_vlm_state(edit_metrics=METRICS, quality_mode="degraded")
    assert state == {
        "enabled": False,
        "skipped": True,
        "checked": 0,
        "reason": "DASHSCOPE_API_KEY missing",
        "verified": False,
        "critical_count": 0,
    }
    assert format_vlm_state(state) == "skipped(DASHSCOPE_API_KEY_missing)"
    assert format_vlm_state({"verified": True}) == "verified"
    assert format_vlm_state({"verified": False}) == "unverified"


def test_cli_rejects_unknown_quality_mode_without_calling_report(monkeypatch):
    monkeypatch.setattr(
        "montage.engine.delivery_report.build_project_delivery_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not be called")),
    )
    with pytest.raises(SystemExit) as exc:
        main(["delivery_report", "fake", "--quality-mode", "optional"])
    assert exc.value.code == 2


def test_stop_card_attaches_compact_quality_summary(monkeypatch):
    monkeypatch.setattr(
        "montage.engine.director._quality_summary_line",
        lambda _dir: {"label": "质量验证", "value": "degraded / VLM skipped"},
    )
    card = _attach_quality_summary("fake", {"summary": []})
    labels = [row["label"] for row in card["summary"]]
    assert "质量验证" in labels


def test_review_md_renders_quality_verification_line():
    card = {
        "status": "await_clips",
        "heading": "clips",
        "summary": [{
            "label": "质量验证",
            "value": "degraded / VLM skipped(DASHSCOPE_API_KEY_missing)",
        }],
        "fields": [],
        "choices": {},
        "findings": [],
    }
    rendered = render_review_md(card)
    assert "**质量验证**：degraded / VLM skipped(DASHSCOPE_API_KEY_missing)" in rendered


def test_proposal_packet_schema_declares_quality_policy():
    assert PROPOSAL_PACKET_SCHEMA["properties"]["quality_mode"]["enum"] == [
        "full", "strict", "degraded", "manual_only",
    ]


def test_stop_card_quality_line_exposes_blocked_reason(monkeypatch):
    from pathlib import Path

    from montage.engine.director import _quality_summary_line

    monkeypatch.setattr(
        "montage.engine.policy.load_loop_policy",
        lambda _dir: {"quality_mode": "strict"},
    )
    monkeypatch.setattr(
        "montage.engine.delivery_report.build_quality_gate",
        lambda **_kwargs: {
            "mode": "strict",
            "blocked": True,
            "blocked_reasons": ["strict policy requires VLM verification"],
            "vlm": {
                "enabled": False,
                "skipped": True,
                "checked": 0,
                "reason": "DASHSCOPE_API_KEY missing",
                "verified": False,
                "critical_count": 0,
            },
        },
    )
    # 该函数只读 store 里被 monkeypatch 掉的数据，路径本身不做真 IO；
    # 这里用 tmp 目录，避免碰仓库 projects/（test_repo_hygiene 守卫）。
    line = _quality_summary_line(Path("proj_demo"))
    assert line is not None
    assert line["value"] == (
        "strict / VLM skipped(DASHSCOPE_API_KEY_missing) "
        "/ BLOCKED：strict policy requires VLM verification"
    )


def test_duration_reconciliation_compact_line_distinguishes_target(monkeypatch):
    assert format_duration_reconciliation({
        "compose_shot_count": 2,
        "final_reported_seconds": 9.092667,
        "expected_final_seconds": 9.0,
        "expected_delta_seconds": 0.092667,
        "status": "explained",
        "target_seconds": 10.0,
        "target_delta_seconds": -0.907333,
        "target_status": "within_tolerance",
    }) == (
        "实测 9.093s / expected 9.000s / delta 0.093s / explained "
        "/ target 10.000s delta -0.907s (within_tolerance)"
    )


def test_stop_card_attaches_duration_summary(monkeypatch):
    from montage.engine import director

    class _FakeStore:
        def read(self, name):
            return {
                "compose_plan": {"shots": [{"shot_id": "sh01"}]},
                "asset_manifest": {},
                "film_health": {},
                "produce_progress": {},
            }.get(name)

    monkeypatch.setattr(director, "ArtifactStore", lambda _root: _FakeStore())
    card = director._attach_duration_summary("fake", {"summary": []})
    labels = [row["label"] for row in card["summary"]]
    assert "时长对账" in labels
    assert "missing_probe" in card["summary"][-1]["value"]


def test_stop_card_attaches_transition_junction_summary(monkeypatch):
    from montage.engine import director

    class _FakeStore:
        def read(self, name):
            return {
                "compose_plan": {
                    "shots": [
                        {"shot_id": "sh01", "duration_seconds": 5.0},
                        {
                            "shot_id": "sh02",
                            "duration_seconds": 5.0,
                            "transition": "fade",
                            "transition_duration": 1.0,
                        },
                    ],
                },
                "asset_manifest": {},
                "film_health": {},
                "produce_progress": {},
            }.get(name)

    monkeypatch.setattr(director, "ArtifactStore", lambda _root: _FakeStore())
    card = director._attach_transition_summary("fake", {"summary": []})
    assert card["summary"] == [{
        "label": "转场接缝",
        "value": (
            "junctions=1 cuts=0 overlap=1 total=1.000s model=xfade anomalies=0"
        ),
        "anomaly_count": "0",
    }]


def test_stop_card_attaches_subtitle_timeline_summary(monkeypatch):
    from montage.engine import director

    class _FakeStore:
        def read(self, name):
            return {
                "compose_plan": {
                    "shots": [
                        {"shot_id": "sh01", "duration_seconds": 5.0},
                        {
                            "shot_id": "sh02",
                            "duration_seconds": 5.0,
                            "transition": "fade",
                            "transition_duration": 1.0,
                            "subtitle_cues": [{
                                "text": "late",
                                "start_seconds": 5.0,
                                "end_seconds": 10.0,
                            }],
                        },
                    ],
                },
            }.get(name)

    monkeypatch.setattr(director, "ArtifactStore", lambda _root: _FakeStore())
    card = director._attach_subtitle_summary("fake", {"summary": []})
    assert card["summary"] == [{
        "label": "字幕时间轴",
        "value": (
            "cues=1 shifted=1 maxShift=1.000s planned=10.000s "
            "projected=9.000s anomalies=0"
        ),
        "anomaly_count": "0",
    }]


def test_stop_card_attaches_cut_points_summary(monkeypatch):
    from montage.engine import director

    class _FakeStore:
        def read(self, name):
            return {
                "compose_plan": {
                    "shots": [
                        {"shot_id": "sh01", "duration_seconds": 15.2},
                        {
                            "shot_id": "sh02",
                            "duration_seconds": 5.0,
                            "transition": "fade",
                            "transition_duration": 0.2,
                        },
                    ],
                },
                "soundtrack": {"events": [{"kind": "bgm", "bpm": 120}]},
                "film_health": {"probe": {"fps": 30.0}},
            }.get(name)

    monkeypatch.setattr(director, "ArtifactStore", lambda _root: _FakeStore())
    card = director._attach_cut_points_summary("fake", {"summary": []})
    assert card["summary"] == [{
        "label": "切点吸拍",
        "value": (
            "cuts=1 overlap=0.200s planned=0 (0.000) "
            "projected=1 (1.000) bpm=120.0"
        ),
    }]


def test_stop_card_attaches_m5_parallel_audit(monkeypatch):
    from montage.engine import director

    class _FakeStore:
        def read(self, name):
            return {
                "edit_metrics": {
                    "metrics": {
                        "m5": {
                            "value": 0.0,
                            "pass": False,
                            "threshold": 0.7,
                            "skipped": False,
                        },
                    },
                },
                "compose_plan": {
                    "shots": [
                        {"shot_id": "sh01", "duration_seconds": 15.2},
                        {
                            "shot_id": "sh02",
                            "duration_seconds": 5.0,
                            "transition": "fade",
                            "transition_duration": 0.2,
                        },
                    ],
                },
                "soundtrack": {"events": [{"kind": "bgm", "bpm": 120}]},
                "film_health": {"probe": {"fps": 30.0}},
            }.get(name)

    monkeypatch.setattr(director, "ArtifactStore", lambda _root: _FakeStore())
    card = director._attach_m5_parallel_audit("fake", {"summary": []})
    assert card["summary"] == [{
        "label": "M5 并行审计",
        "value": (
            "m5=0.0->1.0 pass=False->True delta=+1.000 "
            "framesImproved=1 framesRegressed=0 crossings=1 (audit-only)"
        ),
    }]


def test_stop_card_attaches_m6_parallel_audit(monkeypatch):
    from montage.engine import director

    class _FakeStore:
        def read(self, name):
            return {
                "edit_metrics": {
                    "metrics": {
                        "m6": {
                            "value": -0.5,
                            "pass": False,
                            "threshold": 0.5,
                            "detail": {
                                "rows": [
                                    {"shot_id": "sh01", "audio_energy": 1.0},
                                    {"shot_id": "sh02", "audio_energy": 0.0},
                                    {"shot_id": "sh03", "audio_energy": 0.0},
                                ],
                            },
                        },
                    },
                },
                "compose_plan": {
                    "shots": [
                        {"shot_id": "sh01", "duration_seconds": 5.0},
                        {
                            "shot_id": "sh02",
                            "duration_seconds": 5.0,
                            "transition": "fade",
                            "transition_duration": 0.5,
                        },
                        {"shot_id": "sh03", "duration_seconds": 6.0},
                    ],
                },
                "soundtrack": None,
                "film_health": {"probe": {"fps": 30.0}},
            }.get(name)

    monkeypatch.setattr(director, "ArtifactStore", lambda _root: _FakeStore())
    monkeypatch.setattr(
        "montage.engine.delivery_report._timeline_shots",
        lambda _root: [],
    )
    monkeypatch.setattr(
        "montage.engine.delivery_report._beat_map_contour",
        lambda _root: None,
    )
    card = director._attach_m6_parallel_audit("fake", {"summary": []})
    assert card["summary"] == [{
        "label": "M6 并行审计",
        "value": (
            "m6=-0.5->0.803 pass=False->True delta=+1.303 "
            "intervalsChanged=1 (audit-only)"
        ),
    }]


def test_stop_card_attaches_transition_contract_summary(monkeypatch):
    from montage.engine import director

    class _FakeStore:
        def read(self, name):
            return {
                "compose_plan": {
                    "shots": [
                        {"shot_id": "a1", "scene_id": "sc01"},
                        {"shot_id": "b1", "scene_id": "sc02", "transition": "cut"},
                    ],
                },
                "scene_plan": None,
            }.get(name)

    monkeypatch.setattr(director, "ArtifactStore", lambda _root: _FakeStore())
    card = director._attach_transition_contract_summary("fake", {"summary": []})
    assert card["summary"] == [{
        "label": "跨场转场契约",
        "value": (
            "contracts=1 missing=1 invalid=0 mismatched=0 "
            "contracted=0 acceptedHard=0"
        ),
        "anomaly_count": "1",
    }]
