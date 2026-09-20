from montage.engine.edit_metrics import m1_prompt_relevance
from montage.engine.prompt_contract import (
    audit_forbidden_text,
    annotate_prompt_pair,
    build_prompt_contract,
    evaluate_prompt_contract,
    prompt_hash,
)
from montage.schemas import get_schema


def _shot(sid="S1", cid="c1", prop=None):
    visual = {
        "subjects": [{"id": cid, "appearance_anchor": "黑发齐老者"}],
        "environment": "雨夜石阶",
    }
    if prop:
        visual["objects"] = [{"id": prop, "appearance": "青瓷古琴"}]
    return {"shot_id": sid, "scene_id": "sc01", "visual_details": visual}


def test_prompt_hash_is_stable_and_empty_is_empty():
    assert prompt_hash("abc") == prompt_hash("abc")
    assert prompt_hash("abc") != prompt_hash("abd")
    assert prompt_hash("") == ""


def test_build_prompt_contract_derives_required_ids():
    contract = build_prompt_contract(
        _shot(prop="p1"),
        registry={"c1": {"appearance": "黑发", "outfit": "蓝衣"}},
    )
    assert contract["source"] == "derived"
    assert contract["required_characters"] == ["c1"]
    assert contract["required_outfits"] == ["c1"]
    assert contract["required_props"] == ["p1"]
    assert contract["required_locations"] == []


def test_evaluate_separates_text_reference_and_vlm():
    binding = {
        "refs": [
            {
                "id": "portrait_c1",
                "kind": "portrait",
                "character_id": "c1",
                "picture_index": 1,
                "source": "sent_plan",
            },
        ],
    }
    row = evaluate_prompt_contract(
        _shot(),
        prompt="雨夜石阶，但没有角色外貌",
        registry={"c1": {"appearance": "黑发齐老者"}},
        image_binding=binding,
    )
    assert row["text"]["coverage"] < 1.0
    assert row["reference"]["coverage"] == 1.0
    assert row["reference"]["sent_coverage"] == 1.0
    assert row["visual_verification"] == {
        "checked": False,
        "skipped": True,
        "ok": False,
        "verified": False,
    }
    assert row["forbidden_text_hits"] == []


def test_m1_compensates_missing_text_only_when_reference_was_sent():
    shots = [_shot()]
    bindings = {
        "shots": {
            "S1": {"refs": [{
                "id": "portrait_c1", "kind": "portrait", "character_id": "c1",
                "picture_index": 1, "source": "sent_plan",
            }]},
        },
    }
    result = m1_prompt_relevance(
        shots,
        prompts={"S1": "雨夜石阶"},
        registry={"c1": {"appearance": "黑发齐老者", "outfit": "蓝衣"}},
        image_bindings=bindings,
    )
    assert result["value"] == 1.0
    assert result["detail"]["reference"]["sent_coverage"] == 1.0
    assert result["detail"]["reference"]["text_misses_compensated_by_refs"]

    result_without_sent = m1_prompt_relevance(
        shots,
        prompts={"S1": "雨夜石阶"},
        registry={"c1": {"appearance": "黑发齐老者", "outfit": "蓝衣"}},
        image_bindings={"shots": {"S1": {"refs": []}}},
    )
    assert result_without_sent["value"] < 1.0
    assert any("参考缺失" in f["message"] for f in result_without_sent["findings"])


def test_m1_reports_forbidden_text_and_skipped_vlm():
    shot = _shot()
    shot["prompt_contract"] = {"forbidden_text": ["watermark"]}
    result = m1_prompt_relevance(
        [shot],
        prompts={"S1": "黑发齐老者 watermark"},
        registry={"c1": {"appearance": "黑发齐老者"}},
        vlm={"shots": [{"shot_id": "S1", "skipped": True}]},
    )
    assert result["detail"]["vlm_checked"] == 0
    assert result["detail"]["vlm_skipped"] is True
    assert any("forbidden text" in f["message"] for f in result["findings"])
    assert audit_forbidden_text(
        build_prompt_contract(shot), "clean prompt"
    ) == []


def test_annotate_prompt_pair_adds_coverage_without_replacing_fields():
    shot = _shot(prop="p1")
    shot["prompt_contract"] = {"forbidden_text": ["watermark"]}
    pair = {
        "first_frame_prompt": "黑发齐老者 雨夜石阶 青瓷古琴",
        "video_prompt": "slow dolly",
        "legacy": "keep",
    }
    annotated = annotate_prompt_pair(
        shot,
        pair,
        registry={"c1": {"appearance": "黑发齐老者"}},
    )
    assert annotated["legacy"] == "keep"
    assert annotated["anchor_coverage"]["coverage"] == 1.0
    assert annotated["prompt_contract"]["forbidden_text"] == ["watermark"]
    assert annotated["forbidden_text_hits"] == []
    assert annotated["prompt_hash"]

    bad = annotate_prompt_pair(
        shot,
        {**pair, "first_frame_prompt": "watermark"},
        registry={"c1": {"appearance": "黑发齐老者"}},
    )
    assert bad["forbidden_text_hits"] == ["watermark"]


def test_contract_fields_are_declared_in_artifact_schemas():
    assert "transition_contract" in (
        get_schema("scene_plan")["properties"]["scenes"]["items"]["properties"]
        ["shots"]["items"]["properties"]
    )
    assert "prompt_contract" in (
        get_schema("scene_plan")["properties"]["scenes"]["items"]["properties"]
        ["shots"]["items"]["properties"]
    )
    compose_props = (
        get_schema("compose_plan")["properties"]["shots"]["items"]["properties"]
    )
    assert "transition_contract" in compose_props
    assert "transition_reason" in compose_props
    assert "prompt_hash" in compose_props
    cut_props = get_schema("edit_decisions")["properties"]["cuts"]["items"]["properties"]
    assert "transition_contract" in cut_props
    assert "transition_reason" in cut_props
