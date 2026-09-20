from montage.engine.cut_points import (
    build_m5_parallel_audit,
    build_m6_parallel_audit,
    format_cut_point_projection,
    format_m5_parallel_audit,
    format_m6_parallel_audit,
    project_cut_points,
)


def _soundtrack(bpm=120.0):
    return {"events": [{
        "kind": "bgm",
        "bpm": bpm,
        "start_seconds": 0.0,
        "offset_seconds": 0.0,
    }]}


def test_cut_points_ignore_first_transition_and_cut_overlap():
    plan = {"shots": [
        {
            "shot_id": "s1",
            "duration_seconds": 5.0,
            "transition": "fade",
            "transition_duration": 1.0,
        },
        {
            "shot_id": "s2",
            "duration_seconds": 5.0,
            "transition": "cut",
            "transition_duration": 0.0,
        },
        {
            "shot_id": "s3",
            "duration_seconds": 5.0,
        },
    ]}
    row = project_cut_points(plan, soundtrack=None)
    assert row["cut_count"] == 2
    assert row["transition_overlap_count"] == 0
    assert row["transition_overlap_seconds"] == 0.0
    assert row["planned_duration_seconds"] == 15.0
    assert row["projected_duration_seconds"] == 15.0
    assert row["cut_rows"][0]["planned_start_seconds"] == 5.0
    assert row["cut_rows"][0]["projected_start_seconds"] == 5.0
    assert row["cut_rows"][1]["shift_seconds"] == 0.0


def test_cut_points_accumulate_xfade_shift_and_beat_comparison():
    plan = {"shots": [
        {
            "shot_id": "s1",
            "duration_seconds": 15.2,
        },
        {
            "shot_id": "s2",
            "duration_seconds": 6.0,
            "transition": "fade",
            "transition_duration": 0.2,
        },
    ]}
    row = project_cut_points(
        plan,
        soundtrack=_soundtrack(120.0),
        fps=30.0,
        tolerance_frames=1.0,
    )
    assert row["cut_count"] == 1
    assert row["transition_overlap_seconds"] == 0.2
    assert row["projected_duration_seconds"] == 21.0
    assert row["cut_rows"] == [{
        "cut_index": 1,
        "from_shot_id": "s1",
        "to_shot_id": "s2",
        "planned_start_seconds": 15.2,
        "projected_start_seconds": 15.0,
        "shift_seconds": -0.2,
        "planned_delta_frames": 6.0,
        "projected_delta_frames": 0.0,
        "planned_aligned": False,
        "projected_aligned": True,
        "improved": True,
        "regressed": False,
    }]
    assert row["planned_aligned_count"] == 0
    assert row["projected_aligned_count"] == 1
    assert row["planned_alignment_ratio"] == 0.0
    assert row["projected_alignment_ratio"] == 1.0
    assert format_cut_point_projection(row) == (
        "cuts=1 overlap=0.200s planned=0 (0.000) projected=1 (1.000) bpm=120.0"
    )


def test_cut_points_handle_missing_beat_grid():
    plan = {"shots": [
        {"shot_id": "s1", "duration_seconds": 5.0},
        {"shot_id": "s2", "duration_seconds": 5.0},
    ]}
    row = project_cut_points(plan, soundtrack=None)
    assert row["beat_grid"] is None
    assert row["cut_rows"][0]["planned_delta_frames"] is None
    assert row["cut_rows"][0]["projected_delta_frames"] is None
    assert row["planned_aligned_count"] == 0
    assert row["projected_aligned_count"] == 0
    assert row["planned_alignment_ratio"] == 0.0
    assert row["projected_alignment_ratio"] == 0.0
    assert format_cut_point_projection(row) == "cuts=1 overlap=0.000s planned=0 (0.000) projected=0 (0.000)"


def test_m5_parallel_audit_compares_without_rewriting_original_metric():
    projection = project_cut_points(
        {"shots": [
            {"shot_id": "s1", "duration_seconds": 15.2},
            {
                "shot_id": "s2",
                "duration_seconds": 5.0,
                "transition": "fade",
                "transition_duration": 0.2,
            },
        ]},
        soundtrack=_soundtrack(120.0),
        fps=30.0,
        tolerance_frames=1.0,
    )
    edit_metrics = {
        "metrics": {
            "m5": {
                "value": 0.0,
                "pass": False,
                "threshold": 0.7,
                "skipped": False,
                "circular": False,
            },
        },
    }
    audit = build_m5_parallel_audit(
        edit_metrics=edit_metrics,
        cut_points=projection,
    )
    assert audit["kind"] == "m5_parallel_audit"
    assert audit["changes_original_metric"] is False
    assert audit["original"]["value"] == 0.0
    assert audit["projected"]["value"] == 1.0
    assert audit["difference"]["value_delta"] == 1.0
    assert audit["difference"]["decision_changed"] is True
    assert audit["difference"]["threshold_crossings"] == 1
    assert format_m5_parallel_audit(audit) == (
        "m5=0.0->1.0 pass=False->True delta=+1.000 "
        "framesImproved=1 framesRegressed=0 crossings=1 (audit-only)"
    )


def test_m5_parallel_audit_skips_without_grid_and_preserves_no_change_case():
    edit_metrics = {
        "metrics": {
            "m5": {
                "value": 1.0,
                "pass": True,
                "threshold": 0.7,
                "skipped": False,
            },
        },
    }
    no_grid = build_m5_parallel_audit(
        edit_metrics=edit_metrics,
        cut_points=project_cut_points(
            {"shots": [
                {"shot_id": "s1", "duration_seconds": 5.0},
                {"shot_id": "s2", "duration_seconds": 5.0},
            ]},
            soundtrack=None,
        ),
    )
    assert no_grid["projected"]["skipped"] is True
    assert no_grid["projected"]["pass"] is False
    assert format_m5_parallel_audit(no_grid) == (
        "m5=parallel audit skipped (no beat grid)"
    )

    unchanged = build_m5_parallel_audit(
        edit_metrics=edit_metrics,
        cut_points=project_cut_points(
            {"shots": [
                {"shot_id": "s1", "duration_seconds": 5.0},
                {"shot_id": "s2", "duration_seconds": 5.0},
            ]},
            soundtrack=_soundtrack(120.0),
            fps=30.0,
        ),
    )
    assert unchanged["changes_original_metric"] is False
    assert unchanged["difference"]["value_delta"] == 0.0
    assert unchanged["difference"]["decision_changed"] is False
    assert unchanged["difference"]["threshold_crossings"] == 0


def test_cut_points_project_output_intervals_for_each_shot():
    projection = project_cut_points(
        {"shots": [
            {"shot_id": "s1", "duration_seconds": 5.0},
            {
                "shot_id": "s2",
                "duration_seconds": 5.0,
                "transition": "fade",
                "transition_duration": 0.5,
            },
            {"shot_id": "s3", "duration_seconds": 6.0},
        ]},
        soundtrack=None,
    )
    assert projection["shot_rows"] == [
        {
            "shot_id": "s1",
            "planned_duration_seconds": 5.0,
            "projected_duration_seconds": 4.5,
            "outgoing_overlap_seconds": 0.5,
        },
        {
            "shot_id": "s2",
            "planned_duration_seconds": 5.0,
            "projected_duration_seconds": 5.0,
            "outgoing_overlap_seconds": 0.0,
        },
        {
            "shot_id": "s3",
            "planned_duration_seconds": 6.0,
            "projected_duration_seconds": 6.0,
            "outgoing_overlap_seconds": 0.0,
        },
    ]


def test_m6_parallel_audit_compares_intervals_without_rewriting_original_metric():
    projection = project_cut_points(
        {"shots": [
            {"shot_id": "s1", "duration_seconds": 5.0},
            {
                "shot_id": "s2",
                "duration_seconds": 5.0,
                "transition": "fade",
                "transition_duration": 0.5,
            },
            {"shot_id": "s3", "duration_seconds": 6.0},
        ]},
        soundtrack=None,
    )
    edit_metrics = {
        "metrics": {
            "m6": {
                "value": -0.5,
                "pass": False,
                "threshold": 0.5,
                "skipped": False,
                "detail": {
                    "rows": [
                        {"shot_id": "s1", "audio_energy": 1.0},
                        {"shot_id": "s2", "audio_energy": 0.0},
                        {"shot_id": "s3", "audio_energy": 0.0},
                    ],
                },
            },
        },
    }
    audit = build_m6_parallel_audit(
        edit_metrics=edit_metrics,
        cut_points=projection,
    )
    assert audit["kind"] == "m6_parallel_audit"
    assert audit["changes_original_metric"] is False
    assert audit["original"]["value"] == -0.5
    assert audit["projected"]["value"] == 0.803
    assert audit["projected"]["pass"] is True
    assert audit["difference"]["value_delta"] == 1.303
    assert audit["difference"]["decision_changed"] is True
    assert audit["difference"]["interval_changed_count"] == 1
    assert format_m6_parallel_audit(audit) == (
        "m6=-0.5->0.803 pass=False->True delta=+1.303 "
        "intervalsChanged=1 (audit-only)"
    )


def test_m6_parallel_audit_skips_when_energy_is_missing():
    projection = project_cut_points(
        {"shots": [
            {"shot_id": "s1", "duration_seconds": 5.0},
            {"shot_id": "s2", "duration_seconds": 5.0},
        ]},
        soundtrack=None,
    )
    audit = build_m6_parallel_audit(
        edit_metrics={
            "metrics": {
                "m6": {
                    "value": 0.0,
                    "pass": False,
                    "threshold": 0.5,
                    "detail": {"rows": [{"shot_id": "s1", "audio_energy": 1.0}]},
                },
            },
        },
        cut_points=projection,
    )
    assert audit["projected"]["skipped"] is True
    assert audit["projected"]["pass"] is False
    assert format_m6_parallel_audit(audit) == (
        "m6=parallel audit skipped (no usable energy/variance)"
    )


def test_m6_parallel_audit_prefers_complete_timeline_energy_over_truncated_detail():
    projection = project_cut_points(
        {"shots": [
            {"shot_id": "s1", "duration_seconds": 5.0},
            {
                "shot_id": "s2",
                "duration_seconds": 5.0,
                "transition": "fade",
                "transition_duration": 0.5,
            },
        ]},
        soundtrack=None,
    )
    audit = build_m6_parallel_audit(
        edit_metrics={
            "metrics": {
                "m6": {
                    "value": 0.0,
                    "pass": False,
                    "threshold": 0.5,
                    "detail": {"rows": [{"shot_id": "s1", "audio_energy": 1.0}]},
                },
            },
        },
        cut_points=projection,
        shots=[
            {"shot_id": "s1", "duration_seconds": 5.0, "audio_prompt": {"sfx": [{}] * 5}},
            {"shot_id": "s2", "duration_seconds": 5.0},
        ],
        soundtrack={},
    )
    assert audit["projected"]["skipped"] is False
    assert audit["projected"]["shots"] == 2
    assert audit["changes_original_metric"] is False
