from montage.engine.transition_contract import (
    build_transition_contract_projection,
    format_transition_contract_projection,
)
from montage.engine.edit_metrics import m2_semantic_coherence
from montage.tools.compose_planner import build_compose_plan, compile_compose_plan


def _plan(shots, contracts=None):
    plan = {"shots": shots}
    if contracts is not None:
        plan["transition_contracts"] = contracts
    return plan


def test_cross_scene_boundary_without_contract_is_reported_readonly():
    row = build_transition_contract_projection(
        compose_plan=_plan([
            {"shot_id": "a1", "scene_id": "sc01"},
            {"shot_id": "b1", "scene_id": "sc02", "transition": "cut"},
            {"shot_id": "b2", "scene_id": "sc02"},
        ]),
    )
    assert row["changes_compose_plan"] is False
    assert len(row["boundaries"]) == 1
    boundary = row["boundaries"][0]
    assert boundary["from_shot_id"] == "a1"
    assert boundary["to_shot_id"] == "b1"
    assert boundary["status"] == "missing_contract"
    assert boundary["contract"] is None
    assert row["summary"] == {
        "cross_scene_boundaries": 1,
        "missing_contract": 1,
        "invalid_contract": 0,
        "mismatched_transition": 0,
        "contracted": 0,
        "accepted_hard_cut": 0,
    }
    assert format_transition_contract_projection(row) == (
        "contracts=1 missing=1 invalid=0 mismatched=0 contracted=0 acceptedHard=0"
    )


def test_user_accepted_hard_cut_is_explicit_not_missing():
    row = build_transition_contract_projection(
        compose_plan=_plan([
            {"shot_id": "a1", "scene_id": "sc01"},
            {
                "shot_id": "b1",
                "scene_id": "sc02",
                "transition": "cut",
                "transition_contract": {
                    "decision": "user_accepted_hard_cut",
                    "reason": "operator accepted the semantic break",
                    "accepted_by": "user",
                },
            },
        ])
    )
    assert row["boundaries"][0]["status"] == "accepted_hard_cut"
    assert row["summary"]["missing_contract"] == 0
    assert row["summary"]["accepted_hard_cut"] == 1


def test_fade_contract_validates_current_execution():
    row = build_transition_contract_projection(
        compose_plan=_plan([
            {"shot_id": "a1", "scene_id": "sc01"},
            {
                "shot_id": "b1",
                "scene_id": "sc02",
                "transition": "fade",
                "transition_duration": 0.5,
                "transition_contract": {
                    "decision": "fade",
                    "reason": "time and location change",
                },
            },
        ])
    )
    assert row["boundaries"][0]["status"] == "contracted"

    mismatched = build_transition_contract_projection(
        compose_plan=_plan([
            {"shot_id": "a1", "scene_id": "sc01"},
            {
                "shot_id": "b1",
                "scene_id": "sc02",
                "transition": "cut",
                "transition_contract": {
                    "decision": "fade",
                    "reason": "time and location change",
                },
            },
        ])
    )
    assert mismatched["boundaries"][0]["status"] == "mismatched_transition"


def test_invalid_contract_requires_decision_and_reason():
    row = build_transition_contract_projection(
        compose_plan=_plan([
            {"shot_id": "a1", "scene_id": "sc01"},
            {
                "shot_id": "b1",
                "scene_id": "sc02",
                "transition": "cut",
                "transition_contract": {"decision": "magic_wipe"},
            },
        ])
    )
    boundary = row["boundaries"][0]
    assert boundary["status"] == "invalid_contract"
    assert boundary["contract"]["errors"] == [
        "invalid_decision",
        "missing_reason",
    ]
    assert row["summary"]["invalid_contract"] == 1


def test_scene_plan_chapter_and_same_scene_boundaries_are_projected():
    row = build_transition_contract_projection(
        compose_plan=_plan([
            {"shot_id": "a1", "scene_id": "sc01"},
            {"shot_id": "a2", "scene_id": "sc01"},
            {"shot_id": "b1", "scene_id": "sc02"},
        ]),
        scene_plan={"scenes": [
            {"id": "sc01", "chapter_id": "ch01"},
            {"id": "sc02", "chapter_id": "ch01"},
        ]},
    )
    assert len(row["boundaries"]) == 1
    assert row["boundaries"][0]["same_chapter"] is True


def test_m2_treats_valid_transition_contract_as_anchor():
    shots = [
        {"shot_id": "a1", "scene_id": "sc01"},
        {"shot_id": "b1", "scene_id": "sc02", "transition": "cut"},
    ]
    projection = build_transition_contract_projection(
        compose_plan={"shots": shots}
    )
    missing = m2_semantic_coherence(
        shots,
        transition_contract_projection=projection,
    )
    assert missing["value"] == 0.0
    assert missing["findings"][0]["contract_status"] == "missing_contract"
    assert "transition contract" in missing["findings"][0]["proposed_fix"]

    shots[1]["transition_contract"] = {
        "decision": "user_accepted_hard_cut",
        "reason": "operator accepted the semantic break",
    }
    projection = build_transition_contract_projection(
        compose_plan={"shots": shots}
    )
    accepted = m2_semantic_coherence(
        shots,
        transition_contract_projection=projection,
    )
    assert accepted["value"] == 1.0
    assert accepted["findings"] == []
    assert accepted["detail"]["rows"][0]["anchors"] == [
        "transition_contract:user_accepted_hard_cut"
    ]


def test_m2_reports_invalid_transition_contract_without_suppression():
    shots = [
        {"shot_id": "a1", "scene_id": "sc01"},
        {
            "shot_id": "b1",
            "scene_id": "sc02",
            "transition": "cut",
            "transition_contract": {"decision": "magic_wipe"},
        },
    ]
    projection = build_transition_contract_projection(
        compose_plan={"shots": shots}
    )
    result = m2_semantic_coherence(
        shots,
        transition_contract_projection=projection,
    )
    assert result["value"] == 0.0
    assert result["findings"][0]["contract_status"] == "invalid_contract"
    assert "invalid_decision" in result["findings"][0]["message"]


def _scene_plan(shot):
    return {
        "scenes": [
            {"id": "sc01", "shots": [
                {"shot_id": "a1", "shot_kind": "video", "duration_seconds": 5.0},
            ]},
            {"id": "sc02", "shots": [shot]},
        ],
    }


def test_compose_plan_propagates_valid_contract_and_reason():
    shot = {
        "shot_id": "b1",
        "shot_kind": "video",
        "duration_seconds": 5.0,
        "transition": "fade",
        "transition_duration": 0.4,
        "transition_contract": {
            "decision": "fade",
            "reason": "time and location change",
        },
    }
    built = build_compose_plan(
        _scene_plan(shot),
        asset_manifest={"items": []},
        edit_style="documentary",
        transition_policy="cinematic_xfade",
    )
    compose = built["compose_plan"]
    composed = next(s for s in compose["shots"] if s["shot_id"] == "b1")
    assert composed["transition"] == "fade"
    assert composed["transition_duration"] == 0.4
    assert composed["transition_reason"] == "time and location change"
    decision = compile_compose_plan(compose)["cuts"][1]
    assert decision["transition_reason"] == "time and location change"
    assert decision["transition_contract"]["decision"] == "fade"


def test_compose_plan_user_accepted_hard_cut_overrides_advisor():
    shot = {
        "shot_id": "b1",
        "shot_kind": "video",
        "duration_seconds": 5.0,
        "transition_contract": {
            "decision": "user_accepted_hard_cut",
            "reason": "operator accepted the semantic break",
        },
    }
    built = build_compose_plan(
        _scene_plan(shot),
        asset_manifest={"items": []},
        edit_style="cinematic",
        transition_policy="cinematic_xfade",
    )
    composed = next(
        s for s in built["compose_plan"]["shots"] if s["shot_id"] == "b1"
    )
    assert composed["transition"] == "cut"
    assert composed["transition_duration"] == 0.0
    assert composed["transition_reason"] == "operator accepted the semantic break"


def test_compose_plan_reports_incomplete_contract_without_silence():
    shot = {
        "shot_id": "b1",
        "shot_kind": "video",
        "duration_seconds": 5.0,
        "transition_contract": {"decision": "magic_wipe"},
    }
    built = build_compose_plan(
        _scene_plan(shot),
        asset_manifest={"items": []},
        edit_style="documentary",
        transition_policy="cut_only",
    )
    assert any(
        f["field"] == "b1" and "transition_contract" in f["message"]
        for f in built["findings"]
    )
    composed = next(
        s for s in built["compose_plan"]["shots"] if s["shot_id"] == "b1"
    )
    assert composed["transition"] == "cut"
    assert composed["transition_reason"] == ""
