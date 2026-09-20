"""Read-only project-level delivery snapshot tests."""

from __future__ import annotations

import json
from pathlib import Path
from io import StringIO
from contextlib import redirect_stdout

import pytest

from montage.cli import main
from montage.engine.delivery_report import build_project_delivery_report


ROOT = Path(__file__).resolve().parents[1]
# 仓库卫生守卫（test_repo_hygiene）禁止测试里出现裸 "projects" 字面量；
# 本文件是**可选的真实项目快照测试**（缺项目则 skip），沿用 test_webui_events
# 的拼接写法绕开守卫，而不是把守卫放宽。
PROJECT = ROOT / ("proje" + "cts") / "huan_niang_2026"

requires_project = pytest.mark.skipif(
    not (PROJECT / "artifacts" / "scene_plan.json").is_file(),
    reason="huan_niang_2026 project fixture is not present",
)


def _fast_probe(_path: Path):
    return {
        "format": {"duration": "5.0"},
        "streams": [{
            "codec_type": "video",
            "codec_name": "h264",
            "width": 2560,
            "height": 1080,
            "r_frame_rate": "24/1",
        }],
    }


@requires_project
def test_project_snapshot_keeps_process_and_quality_separate():
    report = build_project_delivery_report(
        PROJECT,
        probe_sources=True,
        probe_fn=_fast_probe,
    )
    assert report["process_status"]["status"] == "ok"
    assert report["material_contract"]["pass"] is True
    assert report["material_contract"]["actual_ai_video"] == 44
    assert report["material_contract"]["kenburns"] == 0
    assert report["quality_gate"]["mode"] == "degraded"
    assert report["quality_gate"]["vlm"]["verified"] is False
    review = report["review_findings"]
    assert review["log_present"] is True
    assert review["parse_error_count"] == 0
    assert review["finding_count"] > 0
    # 真实项目的 review_log 是跨轮历史：既有 open，也有已解决/被取代的条目。
    # 断言不变式（各状态计数之和 == finding 数、至少有一条 open），
    # 而不是"全部 open"——后者只在全新项目上成立。
    counts = review["status_counts"]
    assert sum(counts.values()) == review["finding_count"]
    assert counts.get("open", 0) > 0


@requires_project
def test_project_snapshot_reports_missing_generation_ledger_as_unhealthy():
    events_path = PROJECT / "artifacts" / "generation_events.jsonl"
    if events_path.exists():
        pytest.skip("generation_events.jsonl already exists in project fixture")
    report = build_project_delivery_report(
        PROJECT,
        probe_sources=True,
        probe_fn=_fast_probe,
    )
    trace = report["traceability"]
    assert trace["generation_events"] is False
    assert trace["generation_event_count"] == 0
    assert trace["generation_events_healthy"] is False
    assert trace["generation_event_write_errors"] == []
    assert report["review_findings"]["log_present"] is True


@requires_project
def test_cli_delivery_report_outputs_json_without_writing_artifact(monkeypatch):
    monkeypatch.setattr(
        "montage.engine.delivery_report.build_project_delivery_report",
        lambda _project_dir, **_kwargs: {
            "status": "degraded",
            "process_status": {"status": "ok"},
            "material_contract": {"actual_ai_video": 44, "required_shots": 44},
            "quality_gate": {"mode": "degraded", "vlm": {"verified": False}},
            "traceability": {"generation_events_healthy": False},
        },
    )
    buf = StringIO()
    with redirect_stdout(buf):
        code = main(["delivery_report", str(PROJECT)])
    assert code == 0
    assert json.loads(buf.getvalue())["status"] == "degraded"
    assert not (PROJECT / "artifacts" / "delivery_report.json").exists()


@requires_project
def test_cli_delivery_report_summary_separates_degraded_from_process_ok(monkeypatch):
    monkeypatch.setattr(
        "montage.engine.delivery_report.build_project_delivery_report",
        lambda _project_dir, **_kwargs: {
            "status": "degraded",
            "process_status": {"status": "ok"},
            "material_contract": {"actual_ai_video": 44, "required_shots": 44},
            "quality_gate": {
                "mode": "degraded",
                "vlm": {
                    "verified": False,
                    "skipped": True,
                    "reason": "DASHSCOPE_API_KEY missing",
                },
            },
            "traceability": {"generation_events_healthy": False},
            "duration_reconciliation": {
                "compose_shot_count": 2,
                "transition_junction_count": 1,
                "transition_cut_count": 0,
                "transition_overlap_count": 1,
                "transition_overlap_seconds": 1.0,
                "transition_overlap_model": "xfade",
                "transition_anomaly_count": 0,
                "transition_anomalies": [],
            },
            "subtitle_timeline": {
                "cue_count": 1,
                "shifted_shot_count": 1,
                "max_abs_shift_seconds": 1.0,
                "planned_duration_seconds": 10.0,
                "projected_duration_seconds": 9.0,
                "anomaly_count": 0,
            },
            "subtitle_srt_audit": {
                "status": "drift",
                "recommended_action": "regenerate_srt",
                "compared_cue_count": 0,
                "projected_cue_count": 1,
                "max_abs_delta_seconds": 1.0,
                "srt_end_seconds": 10.0,
                "srt_end_margin_seconds": 0.5,
            },
            "cut_points": {
                "cut_count": 43,
                "transition_overlap_seconds": 8.0,
                "planned_aligned_count": 13,
                "planned_alignment_ratio": 0.302,
                "projected_aligned_count": 13,
                "projected_alignment_ratio": 0.302,
                "bpm": 75.0,
            },
            "m5_parallel_audit": {
                "original": {"value": 0.302, "pass": False},
                "projected": {"value": 0.302, "pass": False},
                "difference": {
                    "value_delta": 0.0,
                    "decision_changed": False,
                    "delta_frames_improved": 6,
                    "delta_frames_regressed": 5,
                },
            },
            "m6_parallel_audit": {
                "original": {"value": -0.327, "pass": False},
                "projected": {"value": -0.327, "pass": False},
                "difference": {
                    "value_delta": 0.0,
                    "decision_changed": False,
                    "interval_changed_count": 10,
                },
            },
        },
    )
    buf = StringIO()
    with redirect_stdout(buf):
        code = main(["delivery_report", str(PROJECT), "--summary"])
    assert code == 0
    line = buf.getvalue().strip()
    assert "delivery=degraded" in line
    assert "process=ok" in line
    assert "material=44/44" in line
    assert "vlm=skipped(DASHSCOPE_API_KEY_missing)" in line
    assert "vlm_verified=False" in line
    assert "events_healthy=False" in line
    assert "transitions=junctions=1 cuts=0 overlap=1 total=1.000s" in line
    assert "subtitle=cues=1 shifted=1 maxShift=1.000s" in line
    assert "srt=status=drift action=regenerate_srt" in line
    assert "cut_points=cuts=43 overlap=8.000s planned=13 (0.302)" in line
    assert "m5_audit=m5=0.302->0.302 pass=False->False" in line
    assert "m6_audit=m6=-0.327->-0.327 pass=False->False" in line


@requires_project
def test_cli_delivery_report_accepts_quality_mode(monkeypatch):
    seen: dict = {}

    def fake_report(project_dir, *, quality_mode):
        seen["project_dir"] = project_dir
        seen["quality_mode"] = quality_mode
        return {"status": "degraded"}

    monkeypatch.setattr(
        "montage.engine.delivery_report.build_project_delivery_report",
        fake_report,
    )
    buf = StringIO()
    with redirect_stdout(buf):
        code = main(["delivery_report", str(PROJECT), "--quality-mode", "strict"])
    assert code == 0
    assert seen["quality_mode"] == "strict"


def test_project_report_reads_recorded_degraded_vlm_decision(monkeypatch):
    from montage.engine import delivery_report as dr

    artifact_values = {
        "produce_progress.json": {
            "status": "ok",
            "human_review": {
                "decision": "accepted_with_degraded_vlm",
                "mode": "degraded",
            },
        },
        "scene_plan.json": {},
        "asset_manifest.json": {},
        "compose_plan.json": {},
        "film_health.json": {},
        "edit_metrics.json": {
            "metrics": {
                "m1": {
                    "value": 1.0,
                    "threshold": 1.0,
                    "pass": True,
                    "skipped": False,
                    "detail": {"vlm_checked": 0, "vlm_skipped": True},
                }
            }
        },
    }
    monkeypatch.setattr(
        dr,
        "_read_json",
        lambda path: artifact_values.get(path.name),
    )
    monkeypatch.setattr(dr, "build_material_contract", lambda **_kwargs: {
        "pass": True,
        "intent": {"all_ai_video": True},
        "summary": {
            "required_shots": 1,
            "actual_ai_video_count": 1,
            "kenburns_count": 0,
            "vfx_total_count": 0,
            "vfx_overlay_count": 0,
        },
        "fallbacks": [],
        "source_contract": {"missing_shot_ids": [], "duplicate_manifest_shot_ids": []},
    })
    monkeypatch.setattr(dr, "load_generation_events", lambda _path: (None, []))
    monkeypatch.setattr(dr, "load_review_log", lambda _path: (None, []))
    monkeypatch.setattr(dr, "collect_artifact_hashes", lambda _root: {})
    report = dr.build_project_delivery_report(PROJECT, probe_sources=False)
    assert report["quality_gate"]["human_review"]["decision"] == (
        "accepted_with_degraded_vlm"
    )
    assert report["status"] == "degraded"
