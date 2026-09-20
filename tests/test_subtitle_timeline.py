from montage.engine.subtitle_timeline import (
    audit_srt_sync,
    format_srt_audit,
    project_subtitle_timeline,
)


def _plan(shots):
    return {"shots": shots}


def test_subtitle_timeline_ignores_first_row_transition_and_cut_overlap():
    plan = _plan([
        {
            "shot_id": "s1",
            "duration_seconds": 5.0,
            "transition": "fade",
            "transition_duration": 1.0,
            "subtitle_cues": [{
                "text": "first",
                "start_seconds": 0.0,
                "end_seconds": 5.0,
            }],
        },
        {
            "shot_id": "s2",
            "duration_seconds": 5.0,
            "transition": "cut",
            "transition_duration": 0.0,
            "subtitle_cues": [{
                "text": "second",
                "start_seconds": 5.0,
                "end_seconds": 10.0,
            }],
        },
    ])
    row = project_subtitle_timeline(plan)
    assert row["cue_count"] == 2
    assert row["shifted_shot_count"] == 0
    assert row["transition_overlap_count"] == 0
    assert row["transition_overlap_seconds"] == 0.0
    assert row["projected_duration_seconds"] == 10.0
    assert [cue["start_seconds"] for cue in row["cues"]] == [0.0, 5.0]
    assert row["anomaly_count"] == 0


def test_subtitle_timeline_shifts_by_cumulative_xfade_overlap():
    plan = _plan([
        {
            "shot_id": "s1",
            "duration_seconds": 5.0,
            "subtitle_cues": [{
                "text": "one",
                "start_seconds": 0.0,
                "end_seconds": 5.0,
            }],
        },
        {
            "shot_id": "s2",
            "duration_seconds": 5.0,
            "transition": "cut",
            "subtitle_cues": [{
                "text": "two",
                "start_seconds": 5.0,
                "end_seconds": 10.0,
            }],
        },
        {
            "shot_id": "s3",
            "duration_seconds": 4.0,
            "transition": "fade",
            "transition_duration": 1.0,
            "subtitle_cues": [{
                "text": "three",
                "start_seconds": 10.0,
                "end_seconds": 14.0,
                "speaker_id": "narrator",
            }],
        },
        {
            "shot_id": "s4",
            "duration_seconds": 6.0,
            "transition": "dissolve",
            "transition_duration": 0.5,
            "subtitle_cues": [{
                "text": "four",
                "start_seconds": 14.0,
                "end_seconds": 20.0,
            }],
        },
    ])
    row = project_subtitle_timeline(plan)
    assert row["model"] == "measured_accumulation_minus_xfade"
    assert row["cue_count"] == 4
    assert row["shifted_shot_count"] == 2
    assert row["max_abs_shift_seconds"] == 1.5
    assert row["planned_duration_seconds"] == 20.0
    assert row["transition_overlap_count"] == 2
    assert row["transition_overlap_seconds"] == 1.5
    assert row["projected_duration_seconds"] == 18.5
    assert row["cue_rows"] == [
        {
            "shot_id": "s1",
            "planned_start_seconds": 0.0,
            "projected_start_seconds": 0.0,
            "shift_seconds": 0.0,
            "cue_count": 1,
        },
        {
            "shot_id": "s2",
            "planned_start_seconds": 5.0,
            "projected_start_seconds": 5.0,
            "shift_seconds": 0.0,
            "cue_count": 1,
        },
        {
            "shot_id": "s3",
            "planned_start_seconds": 10.0,
            "projected_start_seconds": 9.0,
            "shift_seconds": -1.0,
            "cue_count": 1,
        },
        {
            "shot_id": "s4",
            "planned_start_seconds": 14.0,
            "projected_start_seconds": 12.5,
            "shift_seconds": -1.5,
            "cue_count": 1,
        },
    ]
    assert row["cues"] == [
        {"text": "one", "start_seconds": 0.0, "end_seconds": 5.0},
        {"text": "two", "start_seconds": 5.0, "end_seconds": 10.0},
        {
            "text": "three",
            "start_seconds": 9.0,
            "end_seconds": 13.0,
            "speaker_id": "narrator",
        },
        {"text": "four", "start_seconds": 12.5, "end_seconds": 18.5},
    ]
    assert row["anomaly_count"] == 0


def test_subtitle_timeline_reports_invalid_and_outside_cues_without_shifting():
    plan = _plan([
        {
            "shot_id": "s1",
            "duration_seconds": 5.0,
            "subtitle_cues": [
                {"text": "", "start_seconds": 0.0, "end_seconds": 1.0},
                {"text": "bad", "start_seconds": "x", "end_seconds": 1.0},
                {"text": "outside", "start_seconds": 5.2, "end_seconds": 6.0},
            ],
        },
    ])
    row = project_subtitle_timeline(plan)
    assert row["cue_count"] == 1
    assert row["anomaly_count"] == 3
    assert row["anomalies"][0]["kind"] == "invalid_cue_skipped"
    assert row["anomalies"][1]["kind"] == "invalid_cue_skipped"
    assert row["anomalies"][2]["kind"] == "cue_outside_shot"
    assert row["cues"] == [{
        "text": "outside",
        "start_seconds": 5.2,
        "end_seconds": 6.0,
    }]


def test_srt_audit_matches_projection_plus_title_offset():
    plan = _plan([
        {
            "shot_id": "s1",
            "duration_seconds": 5.0,
            "subtitle_cues": [{
                "text": "first line",
                "start_seconds": 0.0,
                "end_seconds": 5.0,
            }],
        },
        {
            "shot_id": "s2",
            "duration_seconds": 5.0,
            "transition": "cut",
            "subtitle_cues": [{
                "text": "second",
                "start_seconds": 5.0,
                "end_seconds": 10.0,
            }],
        },
    ])
    srt = """1
00:00:02,000 --> 00:00:07,000
first
line

2
00:00:07,000 --> 00:00:12,000
second
"""
    audit = audit_srt_sync(
        compose_plan=plan,
        srt_text=srt,
        title_offset_seconds=2.0,
        final_duration_seconds=12.0,
    )
    assert audit["status"] == "aligned"
    assert audit["recommended_action"] == "keep"
    assert audit["srt_cue_count"] == audit["projected_cue_count"] == 2
    assert audit["max_abs_delta_seconds"] == 0.0
    assert audit["text_match_count"] == 2
    assert audit["srt_end_seconds"] == 12.0
    assert audit["srt_end_within_final"] is True
    assert audit["srt_end_margin_seconds"] == 0.0
    assert audit["drift_rows"] == []


def test_srt_audit_reports_overlap_drift_and_recommends_regeneration():
    plan = _plan([
        {
            "shot_id": "s1",
            "duration_seconds": 5.0,
            "subtitle_cues": [{
                "text": "one",
                "start_seconds": 0.0,
                "end_seconds": 5.0,
            }],
        },
        {
            "shot_id": "s2",
            "duration_seconds": 5.0,
            "transition": "fade",
            "transition_duration": 1.0,
            "subtitle_cues": [{
                "text": "two",
                "start_seconds": 5.0,
                "end_seconds": 10.0,
            }],
        },
    ])
    old_srt = """1
00:00:02,000 --> 00:00:07,000
one

2
00:00:07,000 --> 00:00:12,000
two
"""
    audit = audit_srt_sync(
        compose_plan=plan,
        srt_text=old_srt,
        title_offset_seconds=2.0,
        final_duration_seconds=12.0,
    )
    assert audit["status"] == "drift"
    assert audit["recommended_action"] == "regenerate_srt"
    assert audit["max_abs_start_delta_seconds"] == 1.0
    assert audit["max_abs_end_delta_seconds"] == 1.0
    assert audit["drift_row_count"] == 1
    assert audit["drift_rows"][0]["start_delta_seconds"] == 1.0
    assert audit["srt_end_within_final"] is True


def test_srt_audit_distinguishes_missing_mismatched_and_outside_final():
    plan = _plan([
        {
            "shot_id": "s1",
            "duration_seconds": 5.0,
            "subtitle_cues": [{
                "text": "one",
                "start_seconds": 0.0,
                "end_seconds": 5.0,
            }],
        },
    ])
    missing = audit_srt_sync(compose_plan=plan, srt_text=None)
    assert missing["status"] == "missing_srt"
    assert missing["recommended_action"] == "regenerate_srt"

    mismatch = audit_srt_sync(
        compose_plan=plan,
        srt_text=(
            "1\n00:00:02,000 --> 00:00:07,000\none\n\n"
            "2\n00:00:07,000 --> 00:00:08,000\ntwo\n"
        ),
    )
    assert mismatch["status"] == "cue_count_mismatch"
    assert mismatch["recommended_action"] == "inspect_srt"

    outside = audit_srt_sync(
        compose_plan=plan,
        srt_text="1\n00:00:02,000 --> 00:00:07,000\none\n",
        title_offset_seconds=2.0,
        final_duration_seconds=6.5,
    )
    assert outside["srt_end_within_final"] is False
    assert outside["status"] == "outside_final"
    assert outside["recommended_action"] == "regenerate_srt"


def test_srt_audit_format_handles_present_and_missing_margin():
    assert format_srt_audit({
        "status": "drift",
        "recommended_action": "regenerate_srt",
        "compared_cue_count": 23,
        "projected_cue_count": 24,
        "max_abs_delta_seconds": 8.0,
        "srt_end_seconds": 423.824,
        "srt_end_margin_seconds": 0.092667,
    }) == (
        "status=drift action=regenerate_srt compared=23/24 "
        "maxDelta=8.000s end=423.824s margin=0.093s"
    )
    assert format_srt_audit({
        "status": "missing_srt",
        "recommended_action": "regenerate_srt",
        "compared_cue_count": 0,
        "projected_cue_count": 1,
        "max_abs_delta_seconds": 0.0,
        "srt_end_seconds": 0.0,
        "srt_end_margin_seconds": None,
    }) == (
        "status=missing_srt action=regenerate_srt compared=0/1 "
        "maxDelta=0.000s end=0.000s"
    )
