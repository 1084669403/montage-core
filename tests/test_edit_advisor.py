"""edit_advisor 确定性转场顾问测试。"""

from montage.tools.edit_advisor import EditAdvisor, suggest_transitions

SCENES = [
    {"id": "sc01", "narrative_role": "establish_context", "shot_language": {"camera_movement": "static"}},
    {"id": "sc02", "narrative_role": "build_tension", "shot_language": {"camera_movement": "handheld"}},
    {"id": "sc03", "narrative_role": "deliver_payload", "hero_moment": True, "shot_language": {"camera_movement": "dolly_in"}},
    {"id": "sc04", "narrative_role": "resolution", "type": "transition"},
]


def test_documentary_never_uses_flashy():
    suggestions = suggest_transitions(SCENES, style="documentary")
    forbidden = {"wipe", "push", "zoom", "zoom_punch", "blur", "glitch"}
    for s in suggestions:
        assert s["suggested_transition"] not in forbidden


def test_cinematic_hero_gets_negative_gap():
    suggestions = suggest_transitions(SCENES, style="cinematic")
    hero_junction = [s for s in suggestions if s["to_scene"] == "sc03"]
    assert hero_junction
    assert hero_junction[0].get("negative_gap_seconds") is not None


def test_tool_execute():
    tool = EditAdvisor()
    result = tool.execute({"scenes": SCENES, "style": "cinematic"})
    assert result.success
    assert result.data["count"] == len(SCENES) - 1


def test_tool_requires_two_scenes():
    tool = EditAdvisor()
    result = tool.execute({"scenes": [SCENES[0]]})
    assert not result.success
