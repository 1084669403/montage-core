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


def test_cinematic_hero_after_calm_camera_gets_negative_gap():
    """hero 切点且前镜非高能运镜 → punch-in + 负空隙（制造张力）。"""
    scenes = [
        {"id": "sc01", "narrative_role": "establish_context", "shot_language": {"camera_movement": "static"}},
        {
            "id": "sc02",
            "narrative_role": "build_tension",
            "hero_moment": True,
            "shot_language": {"camera_movement": "static"},
        },
        {"id": "sc03", "narrative_role": "deliver_payload", "hero_moment": True, "shot_language": {"camera_movement": "dolly_in"}},
    ]
    junction = [s for s in suggest_transitions(scenes, style="cinematic") if s["to_scene"] == "sc03"][0]
    assert junction["suggested_transition"] == "zoom_punch"
    assert junction["negative_gap_seconds"] == 0.4


def test_cinematic_hero_after_high_energy_camera_is_hard_cut():
    """前镜已是高能运镜 → 更利落的硬切，且 cut 不得挂负空隙。

    cut 与负空隙（转场重叠）互斥：挂上 0.4s 会让装配端误判"需要转场"，
    进而走 xfade 链并发出 ffmpeg 不存在的 ``transition=cut`` 直接渲染失败。
    """
    junction = [s for s in suggest_transitions(SCENES, style="cinematic") if s["to_scene"] == "sc03"][0]
    assert junction["suggested_transition"] == "cut"
    assert junction.get("negative_gap_seconds") is None
    assert "硬切" in junction.get("note", "")


def test_tool_execute():
    tool = EditAdvisor()
    result = tool.execute({"scenes": SCENES, "style": "cinematic"})
    assert result.success
    assert result.data["count"] == len(SCENES) - 1


def test_tool_requires_two_scenes():
    tool = EditAdvisor()
    result = tool.execute({"scenes": [SCENES[0]]})
    assert not result.success
