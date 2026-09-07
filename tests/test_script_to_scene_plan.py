"""script_to_scene_plan：1 section = 1 scene + N shots；纯函数；不覆盖精修。"""

import json
from pathlib import Path

from montage.engine.artifacts import ArtifactStore
from montage.playbooks import get_playbook
from montage.registry import ToolRegistry
from montage.schemas import get_schema
from montage.tools.script_to_scene_plan import (
    ScriptToScenePlan,
    convert_script_to_scene_plan,
)


def _fixture():
    path = Path(__file__).parent / "fixtures" / "script_complete.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_tool_discovered():
    reg = ToolRegistry()
    reg.discover()
    assert reg.get("script_to_scene_plan") is not None


def test_fixture_converts_and_schema_valid():
    script = _fixture()
    out = convert_script_to_scene_plan(script, get_playbook("cyberpunk_neon"))
    plan = out["scene_plan"]
    errors = ArtifactStore.validate(plan, get_schema("scene_plan"))
    assert errors == []
    assert len(plan["scenes"]) == 1
    scene = plan["scenes"][0]
    assert scene["id"] == "sc01"
    shots = scene["shots"]
    assert shots
    assert abs(sum(s["duration_seconds"] for s in shots) - (scene["end_seconds"] - scene["start_seconds"])) < 0.01
    assert scene["end_seconds"] - scene["start_seconds"] == 10
    assert plan["character_registry"][0]["appearance"] == "黑发青年，左眉一道旧疤"
    assert plan["character_registry"][0]["outfit_anchor"] == "深灰风衣"
    env = shots[0]["visual_details"]["environment"]
    assert isinstance(env, str) and "沿海" in env
    assert shots[0]["visual_details"]["subjects"][0]["action"]["verb"]
    assert shots[0]["shot_kind"] == "video"
    assert shots[0]["cut"] == "bridge"


def test_two_lines_split_into_two_five_second_shots():
    script = {
        "title": "t",
        "environment": "雨夜巷口",
        "characters": [{"id": "a", "appearance": "黑发", "outfit": "风衣"}],
        "sections": [{
            "id": "sc01",
            "duration_seconds": 10,
            "narration": "雨还在下。你看清楚没有。",
            "lines": [
                {"speaker_id": "a", "text": "雨还在下。"},
                {"speaker_id": "a", "text": "你看清楚没有。"},
            ],
        }],
    }
    plan = convert_script_to_scene_plan(script)["scene_plan"]
    shots = plan["scenes"][0]["shots"]
    assert len(shots) == 2
    assert shots[0]["duration_seconds"] == 5
    assert shots[1]["duration_seconds"] == 5
    assert shots[0]["audio_prompt"]["dialogue"][0]["dialogue_text"] == "雨还在下。"


def test_degraded_split_without_lines():
    script = {
        "title": "t",
        "environment": "室内",
        "sections": [{
            "id": "sc01",
            "duration_seconds": 10,
            "narration": "第一句。第二句。",
        }],
    }
    out = convert_script_to_scene_plan(script)
    assert any("降级切分" in f["message"] for f in out["findings"])
    assert len(out["scene_plan"]["scenes"][0]["shots"]) == 2


def test_refuse_overwrite_without_flag():
    script = _fixture()
    existing = convert_script_to_scene_plan(script)["scene_plan"]
    existing["scenes"][0]["description"] = "人工精修过的描述"
    tool = ScriptToScenePlan()
    result = tool.execute({"script": script, "scene_plan": existing})
    assert not result.success
    assert result.data["refused"] is True
    redone = tool.execute({"script": script, "scene_plan": existing, "overwrite": True})
    assert redone.success


def test_cinematic_stage_lists_converter():
    from montage.pipelines import CINEMATIC, CLIP_FACTORY, DOCUMENTARY

    cine = next(s["tools"] for s in CINEMATIC["stages"] if s["name"] == "scene_plan")
    doc = next(s["tools"] for s in DOCUMENTARY["stages"] if s["name"] == "scene_plan")
    clip = next(s["tools"] for s in CLIP_FACTORY["stages"] if s["name"] == "scene_plan")
    assert "script_to_scene_plan" in cine
    assert "script_to_scene_plan" in doc
    assert "script_to_scene_plan" not in clip


def test_long_section_caps_shots():
    lines = [{"speaker_id": "a", "text": f"这是第{i}句对白。"} for i in range(8)]
    script = {
        "title": "t",
        "characters": [{"id": "a", "appearance": "x", "outfit": "y"}],
        "sections": [{"id": "sc01", "duration_seconds": 10, "narration": "x", "lines": lines}],
    }
    shots = convert_script_to_scene_plan(script)["scene_plan"]["scenes"][0]["shots"]
    assert len(shots) == 2  # 10s 网格最多 2 镜
    assert abs(sum(s["duration_seconds"] for s in shots) - 10) < 0.01
