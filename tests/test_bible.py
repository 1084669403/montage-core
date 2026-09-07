"""W1 波次 1：series_bible 编译与 purpose=bible 门禁。"""

from __future__ import annotations

from montage.engine.artifacts import ArtifactStore
from montage.engine.bible import compile_bible, write_bible
from montage.engine.project import init_project
from montage.playbooks import get_playbook
from montage.schemas import get_schema
from montage.tools.script_validator import (
    ScriptValidator,
    check_bible,
    check_shot_completeness,
    validate_script,
)


def _ok_chars():
    return [
        {
            "id": "a",
            "name": "阿甲",
            "role": "protagonist",
            "appearance": "黑发齐耳，左眉一道旧疤，铁框眼镜",
            "outfit": "深灰风衣",
            "speech_style": "短句，语速快",
            "contrast_notes": "疤+眼镜",
            "do_not_change": ["左眉疤"],
        },
        {
            "id": "b",
            "name": "阿乙",
            "role": "antagonist",
            "appearance": "银白短发，右耳三枚耳环，锁骨纹身",
            "outfit": "红色皮夹克",
            "speech_style": "拖长尾音，爱冷笑",
            "contrast_notes": "银发耳环",
            "do_not_change": ["耳环"],
        },
    ]


def _ok_shot(verb="抓住衣领推向墙", **extra):
    shot = {
        "shot_id": "sc01_01",
        "blocking": {"x": "左", "z": "近"},
        "subjects": [{
            "id": "a",
            "action": {"verb": verb, "body_part": "右手", "contact": "衣领"},
        }],
        "objects": [{"id": "lighter", "appearance": "铜壳打火机", "position": "右手"}],
        "shot_budget_class": "talk",
    }
    shot.update(extra)
    return shot


def _ok_bible(**overrides):
    bible = {
        "logline": "雨夜巷口两人撕破脸",
        "medium": "film",
        "genres": ["thriller"],
        "playbook": "cyberpunk_neon",
        "characters": _ok_chars(),
        "props": [
            {"id": "lighter", "appearance": "铜壳打火机"},
            {"id": "gun", "appearance": "短管左轮"},
            {"id": "photo", "appearance": "湿透的旧照片"},
        ],
        "scenes": [{
            "id": "sc01",
            "environment": {
                "location": "沿海旧巷",
                "lighting": "霓虹积水",
                "atmosphere": "雨夜",
            },
            "duration_seconds": 10,
            "lines": [
                {"speaker_id": "a", "text": "雨还在下。"},
                {"speaker_id": "b", "text": "你看清楚没有。"},
            ],
            "shots": [_ok_shot()],
        }],
        "library_hit_ids": ["scenes/city-cyberpunk"],
    }
    bible.update(overrides)
    return bible


def test_series_bible_schema_registered():
    assert get_schema("series_bible") is not None
    assert get_schema("episode_plan") is not None
    assert get_schema("episodes") is not None
    char_props = get_schema("script")["properties"]["characters"]["items"]["properties"]
    assert "do_not_change" in char_props
    assert "contrast_notes" in char_props
    assert "age" in char_props
    bible_props = get_schema("series_bible")["properties"]
    for key in (
        "synopsis", "theme", "gold_lines", "music_direction",
        "locations", "extra_notes", "target_duration_seconds",
    ):
        assert key in bible_props, key
    assert "skip_turnaround" in char_props
    kinds = get_schema("asset_manifest")["properties"]["reference_assets"]["items"]["properties"]["kind"]["enum"]
    assert "turnaround" in kinds
    from montage.schemas import VISUAL_DETAILS_SCHEMA

    action = VISUAL_DETAILS_SCHEMA["properties"]["subjects"]["items"]["properties"]["action"]["properties"]
    assert "contact" in action
    assert "body_part" in action


def test_fight_fails_grab_collar_passes():
    bad = _ok_bible()
    bad["scenes"][0]["shots"][0]["subjects"][0]["action"] = {"verb": "两人打架"}
    fail = check_bible(bad)
    assert any("不可拍" in f["message"] for f in fail if f["severity"] == "critical")

    good = check_bible(_ok_bible())
    assert not [f for f in good if f["severity"] == "critical"]


def test_missing_appearance_critical():
    bible = _ok_bible()
    bible["characters"][0]["appearance"] = ""
    findings = check_bible(bible)
    assert any(f["severity"] == "critical" and "appearance" in f["field"] for f in findings)


def test_homogeneity_and_same_speech_style():
    bible = _ok_bible()
    bible["characters"][1]["appearance"] = bible["characters"][0]["appearance"]
    bible["characters"][1]["speech_style"] = bible["characters"][0]["speech_style"]
    findings = check_bible(bible)
    texts = " ".join(f["message"] for f in findings if f["severity"] == "critical")
    assert "重叠" in texts or "speech_style" in texts


def test_default_face_critical():
    pb = get_playbook("chinese_elegance")
    default = pb["asset_generation"]["character_appearance_default"]
    bible = _ok_bible(playbook="chinese_elegance")
    bible["characters"][0]["appearance"] = default
    findings = check_bible(bible, playbook=pb)
    assert any("默认脸" in f["message"] for f in findings)


def test_spoken_skips_homogeneity():
    bible = {
        "logline": "讲量子计算",
        "medium": "spoken",
        "playbook": "spoken_explain",
        "characters": [],
        "scenes": [{
            "id": "sc01",
            "environment": "干净白墙教室",
            "duration_seconds": 8,
            "narration": "今天讲叠加态。",
            "lines": [{"speaker_id": "narrator", "text": "今天讲叠加态。"}],
        }],
    }
    findings = check_bible(bible)
    assert not [f for f in findings if f["severity"] == "critical"]


def test_same_empty_environment_critical():
    bible = _ok_bible()
    bible["scenes"] = [
        {
            "id": "sc01",
            "environment": "一个房间",
            "duration_seconds": 5,
            "lines": [{"speaker_id": "a", "text": "走。"}],
            "shots": [_ok_shot()],
        },
        {
            "id": "sc02",
            "environment": "一个房间",
            "duration_seconds": 5,
            "lines": [{"speaker_id": "b", "text": "停。"}],
            "shots": [_ok_shot(shot_id="sc02_01")],
        },
    ]
    findings = check_bible(bible)
    assert any("空话" in f["message"] for f in findings)


def test_purpose_all_does_not_run_bible_gates():
    script = {
        "title": "t",
        "sections": [{"id": "sc01", "narration": "雨。", "duration_seconds": 5}],
        "characters": [
            {"id": "a", "appearance": "东亚青年男性"},
            {"id": "b", "appearance": "东亚青年男性", "speech_style": ""},
        ],
    }
    result = validate_script(script, purpose="all")
    assert result["pass"] is True


def test_compile_overlays_action_and_caps_props():
    bible = _ok_bible()
    bible["props"] = bible["props"] + [
        {"id": "cup", "appearance": "纸杯"},
        {"id": "bag", "appearance": "帆布袋"},
    ]
    out = compile_bible(bible)
    assert any("截断" in f["message"] for f in out["findings"])
    assert len(out["script"]["props"]) == 3
    errors = ArtifactStore.validate(out["script"], get_schema("script"))
    assert errors == []
    errors = ArtifactStore.validate(out["scene_plan"], get_schema("scene_plan"))
    assert errors == []
    shot = out["scene_plan"]["scenes"][0]["shots"][0]
    action = shot["visual_details"]["subjects"][0]["action"]
    assert "衣领" in str(action.get("verb") or "") or action.get("contact") == "衣领"
    assert action.get("verb") != "说话"
    assert shot["visual_details"]["subjects"][0]["position"] == "left/near"
    assert shot.get("shot_budget_class") == "talk"
    assert shot.get("blocking") == {"x": "left", "z": "near"}


def test_overlay_copies_cut_and_location():
    bible = _ok_bible()
    bible["scenes"][0]["shots"][0]["cut"] = "hard"
    bible["scenes"][0]["shots"][0]["location_id"] = "alley"
    out = compile_bible(bible)
    shot = out["scene_plan"]["scenes"][0]["shots"][0]
    assert shot["cut"] == "hard"
    assert shot["location_id"] == "alley"


def test_compile_aligns_location_id_from_locations():
    bible = _ok_bible()
    bible["locations"] = [{
        "id": "alley",
        "name": "沿海旧巷",
        "sensory": "霓虹积水",
        "appearance": "雨夜旧巷无人",
    }]
    out = compile_bible(bible)
    scene = out["scene_plan"]["scenes"][0]
    shot = scene["shots"][0]
    assert scene.get("location_id") == "alley"
    assert shot.get("location_id") == "alley"
    assert shot.get("location_sensory") == "霓虹积水"
    assert "霓虹积水" in str((shot.get("visual_details") or {}).get("environment") or "")
    assert out["scene_plan"].get("locations")
    errors = ArtifactStore.validate(out["scene_plan"], get_schema("scene_plan"))
    assert errors == []


def test_compile_copies_scene_bgm_id():
    bible = _ok_bible()
    bible["scenes"][0]["bgm_id"] = "bgm/dark-drone"
    out = compile_bible(bible)
    assert out["scene_plan"]["scenes"][0].get("bgm_id") == "bgm/dark-drone"
    errors = ArtifactStore.validate(out["scene_plan"], get_schema("scene_plan"))
    assert errors == []


def test_env_mismatch_without_hard_cut_is_warning():
    bible = _ok_bible()
    bible["scenes"].append({
        "id": "sc02",
        "environment": "天台风很大",
        "duration_seconds": 5,
        "lines": [{"speaker_id": "b", "text": "结束了。"}],
        "shots": [_ok_shot(shot_id="sc02_01")],
    })
    findings = check_bible(bible)
    assert any("仍会桥接" in f["message"] and f["severity"] == "warning" for f in findings)


def test_compile_episode_plan_filters_scenes():
    bible = _ok_bible()
    bible["scenes"].append({
        "id": "sc02",
        "environment": "天台",
        "duration_seconds": 5,
        "lines": [{"speaker_id": "b", "text": "结束了。"}],
        "shots": [_ok_shot(shot_id="sc02_01")],
    })
    out = compile_bible(bible, episode_plan={"scene_ids": ["sc01"], "character_ids": ["a"]})
    assert [s["id"] for s in out["scene_plan"]["scenes"]] == ["sc01"]
    assert [c["id"] for c in out["script"]["characters"]] == ["a"]


def test_write_bible_prev_on_second_write(tmp_path):
    proj = init_project(tmp_path, "w1", "圣经", "cinematic")
    first = {"logline": "v1", "scenes": []}
    write_bible(proj, first)
    assert (proj / "artifacts" / "series_bible.json").is_file()
    assert not (proj / "artifacts" / "series_bible.prev.json").exists()
    write_bible(proj, {"logline": "v2", "scenes": []})
    prev = (proj / "artifacts" / "series_bible.prev.json").read_text(encoding="utf-8")
    assert "v1" in prev


def test_tool_purpose_bible():
    result = ScriptValidator().execute({"purpose": "bible", "bible": _ok_bible()})
    assert result.success
    assert result.data["pass"] is True


def test_none_loop_skips_jimeng_grid():
    plan = {
        "scenes": [{
            "id": "sc01",
            "description": "巷口",
            "shots": [{
                "shot_id": "a",
                "shot_kind": "video",
                "duration_seconds": 7,
                "visual_details": {
                    "environment": "雨夜窄巷",
                    "subjects": [{"id": "x", "action": {"verb": "走"}}],
                },
            }],
        }],
    }
    none_findings = check_shot_completeness(plan, video_loop="none")
    assert not any("网格" in f["message"] for f in none_findings)
    volc = check_shot_completeness(plan, video_loop="volcengine")
    assert any("网格" in f["message"] for f in volc)
