"""剧本质量层测试：schema 扩展向后兼容 / script_validator / playbooks / profiles / apply_profile。

- schema 扩展（characters[]/structure/character_registry）必须向后兼容旧产物。
- script_validator：对白预算、即梦时长网格、可拍性心理词、人物引用一致性。
- playbooks：4 个原创 playbook 自动发现，键对齐 shot_prompt_builder 消费点。
- profiles：平台档案参数、scale_filter 表达式。
- apply_profile：mock `_run` 验证 ffmpeg 命令构造（真实 ffmpeg 冒烟同
  test_compose.py 约定，用 MONTAGE_REAL_FFMPEG=1 门控）。
"""

import os

from lib.asset_catalog import search  # noqa: F401  (确保资产层导入正常)

from montage.compose import ffmpeg_engine as fe
from montage.compose import profiles as pf
from montage.playbooks import get_playbook, list_playbooks
from montage.engine.artifacts import ArtifactStore
from montage.schemas import get_schema
from montage.script_fields import flatten_environment, section_spoken_text
from montage.tools.script_validator import (
    ScriptValidator,
    check_character_refs,
    check_completeness,
    check_dialogue_budget,
    check_filmability,
    check_shot_completeness,
    validate_script,
)


# ---------------------------------------------------------------------------
# schema 扩展向后兼容
# ---------------------------------------------------------------------------


def _valid_sections():
    return [{"id": "sc01", "narration": "雨夜，霓虹下的城市街道。", "duration_seconds": 5}]


def test_script_schema_backward_compatible():
    """旧脚本（仅 title+sections）仍通过扩展后的 schema。"""
    schema = get_schema("script")
    old = {"title": "雨夜追凶", "sections": _valid_sections()}
    # 松校验：直接校验 required 字段存在即可
    assert schema["required"] == ["title", "sections"]
    assert old["title"] and old["sections"]


def test_script_schema_accepts_characters_and_structure():
    new = {
        "title": "雨夜追凶",
        "sections": _valid_sections(),
        "characters": [
            {"id": "li_ming", "name": "黎明", "role": "protagonist",
             "appearance": "黑发青年，左眉一道旧疤", "outfit": "深灰风衣",
             "speech_style": "短句，语速快", "arc": "从逃避到承担"},
        ],
        "structure": {"hook": "雨夜命案", "escalation": "追凶受阻", "reveal": "真凶是旧识", "landing": "选择正义"},
    }
    schema = get_schema("script")
    assert "characters" in schema["properties"]
    assert "structure" in schema["properties"]


def test_scene_plan_schema_has_character_registry():
    schema = get_schema("scene_plan")
    assert "character_registry" in schema["properties"]
    assert "character_ids" in schema["properties"]["scenes"]["items"]["properties"]


def test_script_schema_accepts_environment_and_lines():
    schema = get_schema("script")
    props = schema["properties"]
    assert "environment" in props
    assert "props" in props
    assert "tone" in props
    assert "lines" in props["sections"]["items"]["properties"]
    assert "voice_id" in props["characters"]["items"]["properties"]


def test_old_script_still_schema_valid():
    schema = get_schema("script")
    old = {"title": "雨夜追凶", "sections": _valid_sections()}
    assert ArtifactStore.validate(old, schema) == []


def test_complete_fixture_schema_valid():
    import json
    from pathlib import Path

    path = Path(__file__).parent / "fixtures" / "script_complete.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    errors = ArtifactStore.validate(data, get_schema("script"))
    assert errors == []


def test_scene_plan_nested_shots_schema():
    schema = get_schema("scene_plan")
    plan = {
        "scenes": [{
            "id": "sc01",
            "description": "他后退两步",
            "emotion": "压抑",
            "shots": [{
                "shot_id": "sc01_a",
                "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {"environment": "雨夜窄巷", "subjects": [{"id": "li_ming", "action": {"verb": "后退"}}]},
            }],
        }],
    }
    assert ArtifactStore.validate(plan, schema) == []


def test_flatten_environment_object_and_string():
    assert flatten_environment("雨夜巷口") == "雨夜巷口"
    assert "沿海" in flatten_environment({
        "location": "沿海城市旧巷",
        "lighting": "霓虹冷光",
        "atmosphere": "雨夜",
    })
    assert flatten_environment(None) == ""


def test_section_spoken_text_prefers_lines():
    sec = {
        "narration": "旁白全文很长很长",
        "lines": [{"speaker_id": "a", "text": "短句。"}],
    }
    assert section_spoken_text(sec) == "短句。"
    assert section_spoken_text({"narration": "只有旁白"}) == "只有旁白"


# ---------------------------------------------------------------------------
# script_validator
# ---------------------------------------------------------------------------

_SCRIPT = {
    "title": "雨夜追凶",
    "sections": [{"id": "sc01", "narration": "雨夜，霓虹下的城市街道。", "duration_seconds": 5}],
    "characters": [
        {"id": "li_ming", "name": "黎明", "appearance": "黑发青年，左眉一道旧疤", "outfit": "深灰风衣"},
    ],
}

_SCENE_PLAN = {
    "scenes": [
        {"id": "sc01", "description": "他感到害怕，内心挣扎", "character_ids": ["li_ming"]},
    ],
    "character_registry": [
        {"id": "li_ming", "appearance": "黑发青年，左眉一道旧疤", "outfit_anchor": "深灰风衣"},
    ],
}


def test_dialogue_budget_ok():
    script = {
        "sections": [{"id": "sc01", "narration": "雨夜，霓虹下的城市街道。", "duration_seconds": 10}],
    }
    findings = check_dialogue_budget(script, video_loop="jimeng")
    assert all(f["severity"] != "critical" for f in findings)


def test_dialogue_budget_overload_critical():
    script = {
        "sections": [{"id": "sc01", "narration": "这是一段非常长的旁白，超过了五秒镜头的口播预算，需要压缩字数。", "duration_seconds": 5}],
    }
    findings = check_dialogue_budget(script)
    assert any(f["severity"] == "critical" for f in findings)
    overload = next(f for f in findings if f["severity"] == "critical")
    assert "proposed_fix" in overload  # critical 必须带修复方案


def test_jimeng_grid_check():
    script = {"sections": [{"id": "a", "narration": "好", "duration_seconds": 7}]}
    findings = check_dialogue_budget(script, video_loop="jimeng")
    assert any("网格" in f["message"] for f in findings)


def test_filmability_flags_psych_words():
    findings = check_filmability(_SCENE_PLAN)
    assert any(f["severity"] == "warning" for f in findings)
    flagged = next(f for f in findings if f["severity"] == "warning")
    assert "proposed_fix" in flagged


def test_character_refs_unknown_id():
    scene_plan = {
        "scenes": [{"id": "s", "description": "x", "character_ids": ["ghost"]}],
        "character_registry": [],
    }
    findings = check_character_refs(_SCRIPT, scene_plan)
    assert any(f["severity"] == "critical" for f in findings)


def test_character_refs_appearance_mismatch():
    scene_plan = {
        "scenes": [{"id": "s", "description": "x", "character_ids": ["li_ming"]}],
        "character_registry": [{"id": "li_ming", "appearance": "完全不同的外貌"}],
    }
    findings = check_character_refs(_SCRIPT, scene_plan)
    assert any(f["severity"] == "warning" and "不一致" in f["message"] for f in findings)


def test_validate_script_pass_flag():
    ok_scene = {
        "scenes": [{"id": "sc01", "description": "他后退两步，手扶墙，指节发白", "character_ids": ["li_ming"]}],
        "character_registry": [{"id": "li_ming", "appearance": "黑发青年，左眉一道旧疤", "outfit_anchor": "深灰风衣"}],
    }
    result = validate_script(_SCRIPT, ok_scene)
    assert result["pass"] is True
    assert result["counts"]["critical"] == 0


def test_script_validator_tool_dispatch():
    result = ScriptValidator().execute({"script": _SCRIPT, "scene_plan": _SCENE_PLAN})
    assert result.success
    assert "findings" in result.data
    assert result.data["counts"]["total"] >= 1


def test_script_validator_registry_discovered():
    from montage.registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    assert reg.get("script_validator") is not None


def test_completeness_warnings_not_critical():
    """旧剧本缺 environment/lines：warning，pass 仍为 True。"""
    result = validate_script(_SCRIPT, purpose="completeness")
    assert result["pass"] is True
    assert result["counts"]["critical"] == 0
    assert any("environment" in f["field"] for f in result["findings"])
    assert any("lines" in f["field"] for f in result["findings"])


def test_completeness_fixture_clean():
    import json
    from pathlib import Path

    data = json.loads((Path(__file__).parent / "fixtures" / "script_complete.json").read_text(encoding="utf-8"))
    findings = check_completeness(data)
    assert findings == []


def test_dialogue_budget_uses_lines_not_long_narration():
    script = {
        "sections": [{
            "id": "sc01",
            "duration_seconds": 5,
            "narration": "这是一段非常长的旁白，超过了五秒镜头的口播预算，需要压缩字数。",
            "lines": [{"speaker_id": "li_ming", "text": "雨停了。"}],
        }],
    }
    findings = check_dialogue_budget(script)
    assert all(f["severity"] != "critical" for f in findings)


def test_shot_completeness_flags_missing_action():
    plan = {
        "scenes": [{
            "id": "sc01",
            "description": "巷口",
            "shots": [{
                "shot_id": "a",
                "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {"environment": "雨夜窄巷", "subjects": [{"id": "x"}]},
            }],
        }],
    }
    findings = check_shot_completeness(plan)
    assert any("可拍动作" in f["message"] for f in findings)
    assert all(f["severity"] != "critical" for f in findings)


# ---------------------------------------------------------------------------
# playbooks
# ---------------------------------------------------------------------------


def test_playbooks_discovered():
    pbs = list_playbooks()
    ids = {p["id"] for p in pbs}
    assert {"chinese_elegance", "cyberpunk_neon", "healing_japanese", "documentary_restraint",
            "anime_shonen", "manga_panel", "spoken_explain"} <= ids


def test_playbook_structure_aligned_with_builder():
    pb = get_playbook("chinese_elegance")
    assert pb is not None
    assert pb["identity"]["mood"]
    assert pb["visual_language"]["aesthetic"]
    gen = pb["asset_generation"]
    assert gen["character_appearance_default"]
    assert gen["image_negative_prompt"]
    assert gen["consistency_anchors"]
    assert pb["quality_rules"]
    assert pb["script_style"]["template_id"]


def test_playbook_unknown_returns_none():
    assert get_playbook("nope") is None


def test_playbook_as_style_context():
    """playbook dict 可直接作为 style_context 传给镜头提示词构建器。"""
    from lib.shot_prompt_builder import build_shot_prompt_pair

    pb = get_playbook("cyberpunk_neon")
    shot = {
        "shot_kind": "video",
        "visual_details": {
            "environment": "雨夜赛博都市",
            "lighting": "霓虹冷光",
            "cinematography": {"angle": "low", "frame_composition": "主体居中"},
            "subjects": [{"id": "runner", "action": {"verb": "奔跑"}}],
        },
    }
    pair = build_shot_prompt_pair(shot, style_context=pb, english_visual=False)
    assert pair["first_frame_prompt"]
    assert "赛博朋克" in pair["first_frame_prompt"] or "霓虹" in pair["first_frame_prompt"]


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------


def test_profiles_list():
    names = [p["name"] for p in pf.list_profiles()]
    assert "douyin_vertical" in names and "youtube_landscape" in names


def test_profile_parameters():
    p = pf.get_profile("douyin_vertical")
    assert p is not None
    assert (p.width, p.height) == (1080, 1920)
    assert p.fps == 30
    assert "scale=1080:1920" in p.scale_filter()
    assert "pad=1080:1920" in p.scale_filter()


def test_profile_unknown():
    assert pf.get_profile("nope") is None


def test_apply_profile_command(monkeypatch, tmp_path):
    src = tmp_path / "src.mp4"
    out = tmp_path / "out.mp4"
    src.write_bytes(b"fake")
    captured: list[list[str]] = []

    def fake_run(cmd, timeout=1800):
        captured.append(cmd)

    monkeypatch.setattr(fe, "_run", fake_run)
    fe.apply_profile(src, "youtube_landscape", out)
    assert len(captured) == 1
    cmd = " ".join(captured[0])
    assert "scale=1920:1080" in cmd
    assert "pad=1920:1080" in cmd
    assert "fps=30" in cmd
    assert "-crf" in cmd


def test_apply_profile_unknown_fails(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake")
    try:
        fe.apply_profile(src, "nope", tmp_path / "out.mp4")
        assert False, "应抛出 ComposError"
    except fe.ComposError as exc:
        assert "未知渲染档案" in str(exc)


def test_real_ffmpeg_apply_profile_smoke(tmp_path):
    """真实 ffmpeg 冒烟（需 MONTAGE_REAL_FFMPEG=1；沙箱环境自动跳过）。"""
    if os.environ.get("MONTAGE_REAL_FFMPEG") != "1":
        import pytest

        pytest.skip("需设置 MONTAGE_REAL_FFMPEG=1 才运行真实 ffmpeg 冒烟")
    if fe.check_ffmpeg() is None:
        import pytest

        pytest.skip("本机无 ffmpeg")
    src = tmp_path / "src.mp4"
    fe._run([
        fe.check_ffmpeg(), "-y", "-f", "lavfi",
        "-i", "testsrc=size=640x360:duration=1",
        "-c:v", "libx264", str(src),
    ])
    out = tmp_path / "profiled.mp4"
    fe.apply_profile(src, "douyin_vertical", out)
    info = fe.probe(out)
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    assert (video.get("width"), video.get("height")) == (1080, 1920)
