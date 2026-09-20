"""Read-only delivery report projection tests."""

from __future__ import annotations

from montage.engine.delivery_report import build_delivery_report
from montage.engine.delivery_report import (
    build_duration_reconciliation,
    format_subtitle_timeline,
    format_transition_junctions,
)


def _base_inputs():
    return {
        "process_progress": {"status": "ok", "review": "director", "retry_ids": []},
        "material_contract": {
            "pass": True,
            "intent": {"all_ai_video": True},
            "summary": {
                "required_shots": 2,
                "actual_ai_video_count": 2,
                "kenburns_count": 0,
                "vfx_total_count": 2,
                "vfx_overlay_count": 1,
            },
            "fallbacks": [],
            "source_contract": {"missing_shot_ids": [], "duplicate_manifest_shot_ids": []},
        },
        "asset_manifest": {
            "items": [
                {"kind": "video", "shot_id": "sh01", "duration_seconds": 5.0},
                {"kind": "video", "shot_id": "sh02", "duration_seconds": 5.0},
            ]
        },
        "compose_plan": {
            "shots": [
                {"shot_id": "sh01", "duration_seconds": 5.0, "transition": "cut", "transition_duration": 0.0},
                {"shot_id": "sh02", "duration_seconds": 5.0, "transition": "fade", "transition_duration": 1.0},
            ]
        },
        "film_health": {
            "pass": True,
            "probe": {"duration_seconds": 9.0},
            "duration_check": {"expected": 9.0},
        },
        "edit_metrics": {
            "pass": True,
            "metrics": {
                "m1": {"value": 1.0, "threshold": 1.0, "pass": True, "skipped": False, "detail": {"vlm_checked": 1}},
                "m2": {"value": 1.0, "threshold": 1.0, "pass": True, "skipped": False},
                "m5": {"value": 0.0, "threshold": 2.0, "pass": True, "skipped": False},
                "m6": {"value": 0.5, "threshold": 0.5, "pass": True, "skipped": False},
                "drift": {"value": 0.0, "threshold": 0.0, "pass": True, "skipped": False},
            },
        },
        "title_seconds": 0.0,
        "artifact_hashes": {"compose_plan.json": "hash-cp"},
    }


def test_delivery_report_reports_ok_when_all_gates_are_verified():
    report = build_delivery_report(**_base_inputs(), human_review_decision="accepted")
    assert report["status"] == "ok"
    assert report["process_status"]["status"] == "ok"
    assert report["material_contract"]["actual_ai_video"] == 2
    assert report["quality_gate"]["vlm"]["verified"] is True
    assert report["quality_gate"]["human_review"]["decision"] == "accepted"
    assert report["traceability"]["generation_events"] is False
    assert report["duration_reconciliation"]["expected_final_seconds"] == 9.0
    assert report["duration_reconciliation"]["status"] == "explained"
    assert report["duration_reconciliation"]["formula"] == (
        "compose_total - transition_overlap + title_seconds"
    )
    assert report["duration_reconciliation"]["target_status"] == "unknown"
    assert report["duration_reconciliation"]["explained"] is True
    assert report["computed_from"] == {"compose_plan.json": "hash-cp"}


def test_delivery_report_separates_process_ok_from_unverified_quality():
    inputs = _base_inputs()
    inputs["edit_metrics"]["metrics"]["m1"]["detail"] = {"vlm_skipped": True, "vlm_checked": 0}
    report = build_delivery_report(**inputs)
    assert report["status"] == "degraded"
    assert report["process_status"]["status"] == "ok"
    assert report["material_contract"]["pass"] is True
    assert report["quality_gate"]["mode"] == "degraded"
    assert report["quality_gate"]["vlm"]["enabled"] is False
    assert report["quality_gate"]["vlm"]["skipped"] is True
    assert report["quality_gate"]["vlm"]["checked"] == 0
    assert report["quality_gate"]["vlm"]["reason"] == "DASHSCOPE_API_KEY missing"
    assert report["quality_gate"]["vlm"]["verified"] is False
    assert report["quality_gate"]["human_review"]["decision"] == "pending"


def test_delivery_report_keeps_degraded_vlm_acceptance_degraded():
    inputs = _base_inputs()
    inputs["edit_metrics"]["metrics"]["m1"]["detail"] = {
        "vlm_skipped": True,
        "vlm_checked": 0,
    }
    report = build_delivery_report(
        **inputs,
        human_review_decision="accepted_with_degraded_vlm",
    )
    assert report["status"] == "degraded"
    assert report["quality_gate"]["vlm"]["verified"] is False
    assert report["quality_gate"]["human_review"]["decision"] == (
        "accepted_with_degraded_vlm"
    )


def test_delivery_report_quality_mode_rejects_unknown_policy():
    import pytest

    with pytest.raises(ValueError, match="invalid quality_mode"):
        build_delivery_report(**_base_inputs(), quality_mode="optional")


def test_delivery_report_manual_only_distinguishes_policy_from_missing_key():
    inputs = _base_inputs()
    inputs["edit_metrics"]["metrics"]["m1"]["detail"] = {"vlm_checked": 0}
    report = build_delivery_report(**inputs, quality_mode="manual_only")
    vlm = report["quality_gate"]["vlm"]
    assert report["quality_gate"]["mode"] == "manual_only"
    assert vlm["verified"] is False
    assert vlm["skipped"] is True
    assert vlm["reason"] == "VLM disabled by manual_only policy"


def test_delivery_report_full_policy_blocks_when_vlm_is_skipped():
    inputs = _base_inputs()
    inputs["edit_metrics"]["metrics"]["m1"]["detail"] = {
        "vlm_skipped": True,
        "vlm_checked": 0,
    }
    report = build_delivery_report(
        **inputs,
        quality_mode="full",
        human_review_decision="accepted",
    )
    assert report["status"] == "blocked"
    quality = report["quality_gate"]
    assert quality["blocked"] is True
    assert quality["blocked_reasons"] == ["full policy requires VLM verification"]


def test_delivery_report_strict_policy_blocks_unverified_and_vlm_critical():
    inputs = _base_inputs()
    inputs["edit_metrics"]["metrics"]["m1"]["detail"] = {
        "vlm_skipped": True,
        "vlm_checked": 0,
    }
    unverified = build_delivery_report(**inputs, quality_mode="strict")
    assert unverified["status"] == "blocked"
    assert "strict policy requires VLM verification" in (
        unverified["quality_gate"]["blocked_reasons"]
    )

    inputs["edit_metrics"]["metrics"]["m1"]["detail"] = {
        "vlm_checked": 2,
        "vlm_critical": 1,
    }
    critical = build_delivery_report(**inputs, quality_mode="strict")
    assert critical["status"] == "blocked"
    assert critical["quality_gate"]["vlm"]["critical_count"] == 1
    assert "strict policy blocks VLM critical findings" in (
        critical["quality_gate"]["blocked_reasons"]
    )


def test_delivery_report_degraded_policy_does_not_block_unverified_vlm():
    inputs = _base_inputs()
    inputs["edit_metrics"]["metrics"]["m1"]["detail"] = {
        "vlm_skipped": True,
        "vlm_checked": 0,
    }
    report = build_delivery_report(**inputs, quality_mode="degraded")
    assert report["status"] == "degraded"
    assert report["quality_gate"]["blocked"] is False
    assert report["quality_gate"]["blocked_reasons"] == []


def test_delivery_report_strict_reads_film_health_continuity_critical():
    inputs = _base_inputs()
    inputs["film_health"]["continuity"] = {"critical": 2}
    report = build_delivery_report(**inputs, quality_mode="strict")
    assert report["quality_gate"]["vlm"]["critical_count"] == 2
    assert report["status"] == "blocked"


def test_delivery_report_honors_explicit_vlm_state():
    report = build_delivery_report(
        **_base_inputs(),
        quality_mode="full",
        vlm={"enabled": True, "checked": 3, "verified": True},
        human_review_decision="accepted",
    )
    vlm = report["quality_gate"]["vlm"]
    assert report["quality_gate"]["mode"] == "full"
    assert vlm == {
        "enabled": True,
        "skipped": False,
        "checked": 3,
        "reason": "",
        "verified": True,
        "critical_count": 0,
    }


def test_delivery_report_tracks_partial_traceability_and_duration_deltas():
    inputs = _base_inputs()
    inputs["film_health"]["probe"]["duration_seconds"] = 9.2
    inputs["generation_events"] = [{"event_id": "evt-1"}]
    report = build_delivery_report(**inputs)
    assert report["traceability"]["generation_events"] is True
    assert report["traceability"]["all_video_items_have_event_id"] is False
    assert report["traceability"]["provider_task_ids"] is False
    assert report["traceability"]["file_hashes"] is False
    duration = report["duration_reconciliation"]
    assert duration["manifest_total_seconds"] == 10.0
    assert duration["transition_overlap_seconds"] == 1.0
    assert duration["expected_final_seconds"] == 9.0
    assert abs(duration["expected_delta_seconds"] - 0.2) < 1e-9
    assert abs(duration["target_delta_seconds"] - 0.2) < 1e-9
    assert abs(duration["target_delta_ratio"] - 0.02222222222222225) < 1e-12
    assert duration["explained"] is True


def test_delivery_report_includes_compact_subtitle_timeline():
    inputs = _base_inputs()
    inputs["compose_plan"]["shots"][0]["subtitle_cues"] = [{
        "text": "one",
        "start_seconds": 0.0,
        "end_seconds": 5.0,
    }]
    inputs["compose_plan"]["shots"][1]["subtitle_cues"] = [{
        "text": "two",
        "start_seconds": 5.0,
        "end_seconds": 10.0,
    }]
    report = build_delivery_report(**inputs)
    subtitle = report["subtitle_timeline"]
    assert subtitle["model"] == "measured_accumulation_minus_xfade"
    assert subtitle["shot_count"] == 2
    assert subtitle["cue_count"] == 2
    assert subtitle["shifted_shot_count"] == 1
    assert subtitle["max_abs_shift_seconds"] == 1.0
    assert subtitle["anomaly_count"] == 0
    assert "cues" not in subtitle
    assert [row["shot_id"] for row in subtitle["cue_rows"]] == ["sh01", "sh02"]
    assert format_subtitle_timeline(subtitle) == (
        "cues=2 shifted=1 maxShift=1.000s planned=10.000s "
        "projected=9.000s anomalies=0"
    )


def test_delivery_report_flags_unexplained_and_missing_probe():
    inputs = _base_inputs()
    inputs["film_health"]["probe"]["duration_seconds"] = 12.0
    unexplained = build_delivery_report(**inputs)
    assert unexplained["duration_reconciliation"]["status"] == "unexplained"
    assert unexplained["duration_reconciliation"]["explained"] is False

    inputs["film_health"].pop("probe")
    missing = build_delivery_report(**inputs)
    assert missing["duration_reconciliation"]["status"] == "missing_probe"


def test_duration_reconciliation_projects_shot_transition_onto_junctions():
    shots = [
        {
            "shot_id": "s1",
            "duration_seconds": 5.0,
            "transition": "fade",
            "transition_duration": 0.5,
        },
        {
            "shot_id": "s2",
            "duration_seconds": 4.0,
            "transition": "fade",
            "transition_duration": 1.0,
        },
        {
            "shot_id": "s3",
            "duration_seconds": 6.0,
            "transition": "cut",
            "transition_duration": 0.3,
        },
        {
            "shot_id": "s4",
            "duration_seconds": 5.0,
            "transition": "dissolve",
            "transition_duration": -1.0,
        },
        {
            "shot_id": "s5",
            "duration_seconds": 5.0,
            "transition": "dissolve",
            "transition_duration": 0.5,
        },
    ]
    duration = build_duration_reconciliation(
        asset_manifest=None,
        compose_plan={"shots": shots},
        film_health=None,
        process_progress=None,
        title_seconds=0.0,
    )
    assert duration["compose_shot_count"] == 5
    assert duration["transition_junction_count"] == 4
    assert duration["transition_cut_count"] == 2
    assert duration["transition_overlap_count"] == 2
    assert duration["transition_overlap_seconds"] == 1.5
    assert duration["transition_overlap_model"] == "xfade"
    assert duration["transition_rows"] == [
        {
            "junction_index": 1,
            "from_shot_id": "s1",
            "to_shot_id": "s2",
            "transition": "fade",
            "transition_duration_seconds": 1.0,
        },
        {
            "junction_index": 4,
            "from_shot_id": "s4",
            "to_shot_id": "s5",
            "transition": "dissolve",
            "transition_duration_seconds": 0.5,
        }
    ]
    assert duration["expected_composed_seconds"] == 23.5


def test_duration_reconciliation_counts_single_shot_as_zero_junctions():
    duration = build_duration_reconciliation(
        asset_manifest=None,
        compose_plan={"shots": [{
            "shot_id": "s1",
            "duration_seconds": 5.0,
            "transition": "fade",
            "transition_duration": 1.0,
        }]},
        film_health=None,
        process_progress=None,
        title_seconds=0.0,
    )
    assert duration["transition_junction_count"] == 0
    assert duration["transition_cut_count"] == 0
    assert duration["transition_overlap_count"] == 0
    assert duration["transition_overlap_seconds"] == 0.0
    assert duration["transition_rows"] == []


def test_duration_reconciliation_stabilizes_decimal_transition_sum():
    shots = [
        {
            "shot_id": "s1",
            "duration_seconds": 1.0,
            "transition": "cut",
            "transition_duration": 0.0,
        },
        *[
            {
                "shot_id": f"s{index + 2}",
                "duration_seconds": 1.0,
                "transition": "zoom_punch",
                "transition_duration": 0.8,
            }
            for index in range(10)
        ],
    ]
    duration = build_duration_reconciliation(
        asset_manifest=None,
        compose_plan={"shots": shots},
        film_health=None,
        process_progress=None,
        title_seconds=0.0,
    )
    assert duration["transition_junction_count"] == 10
    assert duration["transition_overlap_count"] == 10
    assert duration["transition_overlap_seconds"] == 8.0


def test_duration_reconciliation_reports_transition_anomalies_without_counting_overlap():
    shots = [
        {
            "shot_id": "s1",
            "duration_seconds": 5.0,
            "transition": "fade",
            "transition_duration": 0.5,
        },
        {
            "shot_id": "s2",
            "duration_seconds": 5.0,
            "transition": "cut",
            "transition_duration": 0.4,
        },
        {
            "shot_id": "s3",
            "duration_seconds": 5.0,
            "transition": "fade",
            "transition_duration": -0.2,
        },
        {
            "shot_id": "s4",
            "duration_seconds": 5.0,
            "transition": "fade",
            "transition_duration": 0.2,
        },
    ]
    duration = build_duration_reconciliation(
        asset_manifest=None,
        compose_plan={"shots": shots},
        film_health=None,
        process_progress=None,
        title_seconds=0.0,
    )
    assert duration["transition_junction_count"] == 3
    assert duration["transition_cut_count"] == 2
    assert duration["transition_overlap_count"] == 1
    assert duration["transition_overlap_seconds"] == 0.2
    assert duration["transition_anomaly_count"] == 3
    assert [item["kind"] for item in duration["transition_anomalies"]] == [
        "first_shot_transition_ignored",
        "cut_transition_duration_ignored",
        "nonpositive_transition_treated_as_cut",
    ]
    assert format_transition_junctions(duration) == (
        "junctions=3 cuts=2 overlap=1 total=0.200s model=xfade anomalies=3"
        "（cut_transition_duration_ignored=1,first_shot_transition_ignored=1,"
        "nonpositive_transition_treated_as_cut=1）"
    )


def test_delivery_report_exposes_unhealthy_generation_event_writer():
    inputs = _base_inputs()
    inputs["generation_events"] = [{"event_id": "evt-1"}]
    inputs["generation_event_errors"] = [{"event": "succeeded", "error": "disk unavailable"}]
    report = build_delivery_report(**inputs)
    trace = report["traceability"]
    assert trace["generation_events"] is True
    assert trace["generation_event_write_error_count"] == 1
    assert trace["generation_event_write_errors"][0]["error"] == "disk unavailable"
    assert trace["generation_events_healthy"] is False


def test_delivery_report_blocks_when_material_contract_fails():
    inputs = _base_inputs()
    inputs["material_contract"]["pass"] = False
    report = build_delivery_report(**inputs, human_review_decision="accepted")
    assert report["status"] == "blocked"
    assert report["quality_gate"]["human_review"]["decision"] == "accepted"
