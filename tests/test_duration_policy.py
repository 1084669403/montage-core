"""W2.2a：duration_policy 与转换器/校验器跟同一张表。"""

from montage.providers.capabilities import (
    policy_for_loop,
    snap_duration_seconds,
    video_caps,
)
from montage.tools.script_to_scene_plan import convert_script_to_scene_plan
from montage.tools.script_validator import AGNES_GRID, JIMENG_GRID, check_dialogue_budget


def _script(*, duration=None, lines=None, narration="雨还在下。你看清楚没有。"):
    section = {"id": "sc01", "narration": narration, "lines": lines or [
        {"speaker_id": "a", "text": "雨还在下。"},
        {"speaker_id": "a", "text": "你看清楚没有。"},
    ]}
    if duration is not None:
        section["duration_seconds"] = duration
    return {
        "title": "t",
        "characters": [{"id": "a", "appearance": "黑发", "outfit": "风衣"}],
        "sections": [section],
    }


def test_policy_for_loop_maps_aliases():
    jimeng = policy_for_loop("jimeng")
    volc = policy_for_loop("volcengine")
    assert jimeng == volc
    assert jimeng["kind"] == "enum"
    assert jimeng["values"] == [5, 10]
    agnes = policy_for_loop("agnes")
    assert agnes["kind"] == "range"
    assert agnes["min"] == 4
    assert agnes["max"] == 12
    assert policy_for_loop("none") == {"kind": "none"}
    assert policy_for_loop(None) == {"kind": "none"}
    assert policy_for_loop("dashscope") == {"kind": "none"}
    kling = policy_for_loop("kling")
    assert kling == {"kind": "range", "min": 3, "max": 15, "step": 1}
    assert kling == video_caps(tool="kling_video")["duration_policy"]
    ark = policy_for_loop("ark")
    assert ark["kind"] == "range"
    assert ark["max"] == 30
    assert policy_for_loop("seedance") == ark


def test_validator_grids_follow_converter_table():
    assert JIMENG_GRID == (5, 10)
    assert AGNES_GRID[0] == 4
    assert AGNES_GRID[-1] == 12
    assert 3 not in AGNES_GRID
    assert 18 not in AGNES_GRID


def test_caps_table_matches_loop_helper():
    assert video_caps(tool="jimeng_video")["duration_policy"] == policy_for_loop("jimeng")
    assert video_caps(tool="agnes_video")["duration_policy"] == policy_for_loop("agnes")


def test_snap_none_vs_unspecified():
    assert snap_duration_seconds(7, None) == 10
    assert snap_duration_seconds(3, None) == 5
    assert snap_duration_seconds(7, {"kind": "none"}) == 7
    assert snap_duration_seconds(7, policy_for_loop("jimeng")) == 10
    assert snap_duration_seconds(4, policy_for_loop("agnes")) == 4
    assert snap_duration_seconds(12, policy_for_loop("agnes")) == 12
    assert snap_duration_seconds(4.2, {"kind": "range", "min": 4, "max": 12, "step": 1}) == 5


def test_unspecified_policy_keeps_legacy_five_ten():
    shots = convert_script_to_scene_plan(_script(duration=10))["scene_plan"]["scenes"][0]["shots"]
    assert len(shots) == 2
    assert shots[0]["duration_seconds"] == 5
    assert shots[1]["duration_seconds"] == 5


def test_explicit_none_does_not_snap_inferred():
    text = "字" * 10
    script = _script(narration=text, lines=[{"speaker_id": "a", "text": text}])
    script["sections"][0].pop("duration_seconds", None)
    none_plan = convert_script_to_scene_plan(script, duration_policy={"kind": "none"})
    legacy = convert_script_to_scene_plan(script)
    none_dur = none_plan["scene_plan"]["scenes"][0]["end_seconds"]
    legacy_dur = legacy["scene_plan"]["scenes"][0]["end_seconds"]
    assert none_dur == 2.0
    assert legacy_dur == 5.0


def test_agnes_policy_snaps_inferred_past_ten():
    text = "字" * 60
    script = _script(narration=text, lines=[{"speaker_id": "a", "text": text}])
    script["sections"][0].pop("duration_seconds", None)
    plan = convert_script_to_scene_plan(script, duration_policy=policy_for_loop("agnes"))
    assert plan["scene_plan"]["scenes"][0]["end_seconds"] == 12


def test_over_max_splits_sum_preserved():
    script = _script(duration=20, lines=[{"speaker_id": "a", "text": "走。"}])
    plan = convert_script_to_scene_plan(script, duration_policy=policy_for_loop("agnes"))
    shots = plan["scene_plan"]["scenes"][0]["shots"]
    assert len(shots) >= 2
    assert abs(sum(s["duration_seconds"] for s in shots) - 20) < 0.01


def test_none_loop_still_skips_jimeng_grid():
    script = {"sections": [{"id": "a", "narration": "好", "duration_seconds": 7}]}
    assert not any("网格" in f["message"] for f in check_dialogue_budget(script, video_loop="none"))
    assert any("网格" in f["message"] for f in check_dialogue_budget(script, video_loop="jimeng"))
