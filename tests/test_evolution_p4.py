"""进化方案 P4：节拍→运镜 / 构图审计 / 四拍覆盖。零真实调用。"""

from __future__ import annotations

from lib.shot_prompt_builder import _MOVEMENT_PHRASES, build_shot_prompt_pair
from montage.engine.bible import compile_bible
from montage.engine.shot_language import (
    BEAT_TO_CAMERA,
    LEGAL_MOVEMENT,
    camera_for_beat,
    fill_shot_language,
)
from montage.playbooks import get_playbook
from montage.tools.script_to_scene_plan import convert_script_to_scene_plan
from montage.tools.script_validator import (
    check_beat_coverage,
    check_composition,
    validate_script,
)


def test_beat_table_uses_builder_enums():
    assert LEGAL_MOVEMENT <= set(_MOVEMENT_PHRASES)
    assert set(BEAT_TO_CAMERA.values()) <= LEGAL_MOVEMENT
    assert "push_in" not in LEGAL_MOVEMENT
    assert "pull_out" not in LEGAL_MOVEMENT


def test_aliases_map_to_canonical_moves():
    assert camera_for_beat("hook") == "dolly_in"
    assert camera_for_beat("establish_context") == "dolly_in"
    assert camera_for_beat("build_tension") == "handheld"
    assert camera_for_beat("deliver_payload") == "zoom_in"
    assert camera_for_beat("resolution") == "dolly_out"
    assert camera_for_beat("unknown_role") == "static"


def test_spoken_playbook_overrides_static():
    pb = get_playbook("spoken_explain")
    assert camera_for_beat("hook", pb) == "static"
    assert camera_for_beat("reveal", pb) == "static"


def test_convert_hook_scene_gets_dolly_in():
    script = {
        "title": "t",
        "environment": "雨夜巷口",
        "sections": [{
            "id": "sc01",
            "duration_seconds": 5,
            "narration": "他站住。",
        }],
    }
    plan = convert_script_to_scene_plan(script)["scene_plan"]
    scene = plan["scenes"][0]
    assert scene["narrative_role"] == "hook"
    assert scene["shots"][0]["shot_language"]["camera_movement"] == "dolly_in"
    assert scene["shots"][0]["shot_language"]["shot_size"] == "medium"


def test_spoken_convert_stays_static():
    script = {
        "title": "t",
        "environment": "室内",
        "sections": [{
            "id": "sc01",
            "duration_seconds": 5,
            "narration": "今天讲三点。",
        }],
    }
    plan = convert_script_to_scene_plan(script, get_playbook("spoken_explain"))["scene_plan"]
    assert plan["scenes"][0]["shots"][0]["shot_language"]["camera_movement"] == "static"


def test_fill_does_not_overwrite_explicit_static():
    plan = {
        "scenes": [{
            "id": "sc01",
            "narrative_role": "hook",
            "shots": [{
                "shot_id": "sh01",
                "shot_language": {"camera_movement": "static", "shot_size": "wide"},
            }],
        }],
    }
    fill_shot_language(plan)
    assert plan["scenes"][0]["shots"][0]["shot_language"]["camera_movement"] == "static"


def test_fill_empty_from_role():
    plan = {
        "scenes": [{
            "id": "sc01",
            "narrative_role": "reveal",
            "shots": [{"shot_id": "sh01", "shot_language": {}}],
        }],
    }
    fill_shot_language(plan)
    sl = plan["scenes"][0]["shots"][0]["shot_language"]
    assert sl["camera_movement"] == "zoom_in"
    assert sl["shot_size"] == "medium"


def test_compile_copies_structure_and_fills_language():
    bible = {
        "title": "雨",
        "playbook": "cyberpunk_neon",
        "structure": {"hook": "雨夜站住", "landing": "离开"},
        "scenes": [{
            "id": "sc01",
            "narration": "他站住。",
            "duration_seconds": 5,
        }],
    }
    out = compile_bible(bible, duration_policy={"kind": "none"})
    assert out["script"]["structure"]["hook"] == "雨夜站住"
    shot = out["scene_plan"]["scenes"][0]["shots"][0]
    assert shot["shot_language"]["camera_movement"] == "dolly_in"


def test_compile_overlay_role_wins_camera():
    bible = {
        "title": "雨",
        "playbook": "cyberpunk_neon",
        "scenes": [{
            "id": "sc01",
            "narration": "他离开。",
            "duration_seconds": 5,
            "narrative_role": "landing",
        }],
    }
    out = compile_bible(bible, duration_policy={"kind": "none"})
    assert out["scene_plan"]["scenes"][0]["narrative_role"] == "landing"
    assert out["scene_plan"]["scenes"][0]["shots"][0]["shot_language"]["camera_movement"] == "dolly_out"


def test_compile_respects_fill_off():
    bible = {
        "title": "雨",
        "fill_shot_language": False,
        "scenes": [{
            "id": "sc01",
            "narration": "他站住。",
            "duration_seconds": 5,
        }],
    }
    compiled = compile_bible(bible, duration_policy={"kind": "none"})
    assert compiled["scene_plan"]["scenes"][0]["shots"][0]["shot_language"]["camera_movement"] == "static"

    plan = {
        "scenes": [{
            "id": "sc01",
            "narrative_role": "hook",
            "shots": [{"shot_id": "sh01", "shot_language": {}}],
        }],
    }
    fill_shot_language(plan, enabled=False)
    assert not plan["scenes"][0]["shots"][0]["shot_language"].get("camera_movement")


def test_compile_overlay_explicit_camera():
    bible = {
        "title": "雨",
        "playbook": "cyberpunk_neon",
        "scenes": [{
            "id": "sc01",
            "narration": "他站住。",
            "duration_seconds": 5,
            "narrative_role": "hook",
            "shots": [{
                "shot_id": "sc01_01",
                "shot_language": {"camera_movement": "static"},
                "subjects": [{"id": "a", "action": {"verb": "站住", "contact": "地面"}}],
                "blocking": {"x": "center", "z": "mid"},
            }],
        }],
    }
    out = compile_bible(bible, duration_policy={"kind": "none"})
    assert out["scene_plan"]["scenes"][0]["shots"][0]["shot_language"]["camera_movement"] == "static"


def test_composition_close_far_warns():
    plan = {
        "scenes": [{
            "id": "sc01",
            "shots": [{
                "shot_id": "sh01",
                "shot_language": {"shot_size": "close_up"},
                "blocking": {"x": "center", "z": "far"},
            }],
        }],
    }
    findings = check_composition(plan)
    assert any("出画" in f["message"] for f in findings)
    assert all(f["severity"] != "critical" for f in findings)


def test_composition_stamped_positions_not_occlusion():
    plan = {
        "scenes": [{
            "id": "sc01",
            "shots": [{
                "shot_id": "sh01",
                "shot_language": {"shot_size": "medium"},
                "blocking": {"x": "left", "z": "mid"},
                "visual_details": {
                    "subjects": [
                        {"id": "a", "position": "left/mid"},
                        {"id": "b", "position": "left/mid"},
                    ],
                },
            }],
        }],
    }
    findings = check_composition(plan)
    assert not any("遮挡" in f["message"] for f in findings)


def test_composition_explicit_subject_overlap():
    plan = {
        "scenes": [{
            "id": "sc01",
            "shots": [{
                "shot_id": "sh01",
                "shot_language": {"shot_size": "medium"},
                "blocking": {"x": "center", "z": "mid"},
                "visual_details": {
                    "subjects": [
                        {"id": "a", "blocking": {"x": "left", "z": "near"}},
                        {"id": "b", "blocking": {"x": "left", "z": "near"}},
                    ],
                },
            }],
        }],
    }
    findings = check_composition(plan)
    assert any("遮挡" in f["message"] for f in findings)


def test_beat_coverage_single_scene_suggestion():
    script = {
        "structure": {
            "hook": "开场",
            "escalation": "升级",
            "reveal": "揭晓",
            "landing": "收束",
        },
    }
    plan = {"scenes": [{"id": "sc01", "narrative_role": "hook"}]}
    result = validate_script(script, plan, purpose="beat_coverage")
    assert result["pass"] is True
    assert any(f["severity"] == "suggestion" for f in result["findings"])


def test_beat_coverage_multi_scene_warning():
    script = {
        "structure": {
            "hook": "开场",
            "escalation": "升级",
            "reveal": "揭晓",
            "landing": "收束",
        },
    }
    plan = {
        "scenes": [
            {"id": "sc01", "narrative_role": "hook"},
            {"id": "sc02", "narrative_role": "escalation"},
            {"id": "sc03", "narrative_role": "reveal"},
            {"id": "sc04", "narrative_role": "establish_context"},
        ],
    }
    findings = check_beat_coverage(script, plan)
    assert any(f["severity"] == "warning" and "landing" in f["message"] for f in findings)


def test_beat_coverage_empty_structure_skips():
    assert check_beat_coverage({"title": "t"}, {"scenes": [{"id": "sc01"}]}) == []


def test_agnes_builder_untouched_with_filled_language():
    shot = {
        "shot_kind": "video",
        "shot_language": {"shot_size": "medium", "camera_movement": "dolly_in"},
        "visual_details": {
            "environment": "雨夜街道",
            "subjects": [{"id": "a", "appearance_anchor": "黑发青年", "action": {"verb": "走"}}],
        },
        "audio_prompt": {"dialogue": [{"text": "站住"}]},
    }
    pair = build_shot_prompt_pair(shot, agnes_audio=True, provider_max_chars=3000)
    assert "【台词】" in (pair["video_prompt"] or "")
    assert "【声音】" in (pair["video_prompt"] or "") or "【镜头】" in (pair["video_prompt"] or "")
