"""进化方案 P0：API 面能力表 / 提示词档案 / adapter / doctor。零真实调用。"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

from jsonschema import validate

from lib.shot_prompt_builder import build_shot_prompt_pair
from montage.engine.policy import VIDEO_LOOP_PROVIDERS
from montage.providers.capabilities import (
    doctor_video_surfaces,
    image_caps,
    list_video_surfaces,
    policy_for_loop,
    video_caps,
    video_surface,
)
from montage.providers.prompt_adapter import adapt_visual_prompt
from montage.providers.video_prompts import prompt_profile
from montage.schemas import NESTED_SHOT_SCHEMA, get_schema

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "agnes_v20_prompt_golden.json"


def test_new_flags_default_false_on_unknown():
    vid = video_caps(tool="nope")
    assert vid["first_frame"] is False
    assert vid["multi_shot"] is False
    assert vid["native_audio"] is False
    assert vid["edit_clip"] is False
    assert vid["citation_syntax"] == ""
    assert vid["passthrough"] is False
    img = image_caps(tool="nope")
    assert img["turnaround"] is False


def test_wired_tools_keep_legacy_duration_and_api_id():
    jimeng = video_caps(tool="jimeng_video")
    assert jimeng["duration_policy"] == {"kind": "enum", "values": [5, 10]}
    assert jimeng["api_id"] == "jimeng_v30"
    assert jimeng["camera_control"] is True
    assert jimeng["native_audio"] is False
    assert jimeng["multi_shot"] is False
    agnes = video_caps(tool="agnes_video")
    assert agnes["duration_policy"] == {"kind": "range", "min": 4, "max": 12, "step": 1}
    assert agnes["api_id"] == "agnes_v25"
    assert agnes["passthrough"] is False
    assert agnes["native_audio"] is True
    kling = video_caps(tool="kling_video")
    assert kling["api_id"] == "kling_omni_30"
    assert kling["last_frame"] is True
    assert kling["native_audio"] is True


def test_policy_for_loop_jimeng_agnes_unchanged():
    assert policy_for_loop("jimeng") == {"kind": "enum", "values": [5, 10]}
    assert policy_for_loop("volcengine") == policy_for_loop("jimeng")
    agnes = policy_for_loop("agnes")
    assert agnes["kind"] == "range"
    assert agnes["min"] == 4
    assert agnes["max"] == 12


def test_surfaces_seedance_omni_agnes():
    s25 = video_surface("seedance_25")
    assert s25["wired"] is True
    assert s25["fallback_api_id"] == "jimeng_v30"
    assert s25["native_audio"] is True
    assert s25["multi_shot"] is True
    assert s25["edit_clip"] is True
    assert s25["extend_clip"] is True
    assert s25["citation_syntax"] == "@图片N"
    assert s25["duration_policy"] == {"kind": "range", "min": 4, "max": 30, "step": 1}
    omni = video_surface("kling_omni_30")
    assert omni["wired"] is True
    assert omni["multi_shot_max"] == 6
    assert omni["citation_syntax"] == "@image_N"
    assert omni["last_frame_requires_first"] is True
    assert omni["negative_prompt"] is False
    pro = video_surface("kling_i2v_21_pro")
    assert pro["requires_first_frame"] is True
    assert pro["native_audio"] is False
    a20 = video_surface("agnes_v20")
    assert a20["wired"] is False
    assert a20["passthrough"] is True
    a25 = video_surface("agnes_v25")
    assert a25["wired"] is True
    assert a25["models"] == ["agnes-video-2.5-flash"]
    assert a25["continuity_mode"] == "image_ref"
    assert a25["max_ref_images"] == 5
    assert a25["video_ref"] is False
    ids = {row["api_id"] for row in list_video_surfaces()}
    assert {"seedance_25", "kling_omni_30", "agnes_v25", "jimeng_v30"} <= ids


def test_kling_loop_lock_uses_omni_grid():
    assert VIDEO_LOOP_PROVIDERS["kling"] == ["kling"]
    assert policy_for_loop("kling") == {"kind": "range", "min": 3, "max": 15, "step": 1}
    assert video_surface("kling_omni_30")["duration_policy"]["kind"] == "range"


def test_prompt_profiles_agnes_passthrough_not_english_six_part():
    agnes = prompt_profile("agnes_v20")
    assert agnes["passthrough"] is True
    assert agnes["max_chars"] == 3000
    assert agnes["style"] == "agnes_sections"
    assert "Subject" not in str(agnes)
    v30 = prompt_profile("jimeng_v30")
    assert v30["max_chars"] == 800
    s25 = prompt_profile("seedance_25")
    assert s25["max_chars"] > 800
    assert "【字幕】" in (s25.get("forbidden") or ())


def test_adapter_same_builder_to_dialects():
    builder = {
        "first_frame_prompt": "黑发青年站在雨夜街道",
        "video_prompt": "青年向前跑，霓虹倒映积水",
        "negative_prompt": "low quality",
    }
    refs = [{"role": "角色外貌"}]
    seedance = adapt_visual_prompt(
        "seedance_25", builder, refs=refs, dialogue="站住"
    )
    assert seedance["passthrough"] is False
    assert "@图片1用于角色外貌，不采用背景" in seedance["video_prompt"]
    assert "{站住}" in seedance["video_prompt"]
    assert "【字幕】" not in seedance["video_prompt"]
    kling = adapt_visual_prompt(
        "kling_omni_30", builder, refs=refs, dialogue="站住"
    )
    assert "@image_1" in kling["video_prompt"]
    assert "<<<image_" not in kling["video_prompt"]
    assert "@图片" not in kling["video_prompt"]
    assert "对白：站住" in kling["video_prompt"]


def test_agnes_v20_adapter_matches_golden_verbatim():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    pair = build_shot_prompt_pair(golden["shot"], **golden["builder_kwargs"])
    assert pair.get("first_frame_prompt") == golden["first_frame_prompt"]
    assert pair.get("video_prompt") == golden["video_prompt"]
    assert pair.get("negative_prompt") == golden["negative_prompt"]
    adapted = adapt_visual_prompt("agnes_v20", pair)
    assert adapted["passthrough"] is True
    assert adapted["valid"] is True
    assert adapted["video_prompt"] == golden["video_prompt"]
    assert adapted["first_frame_prompt"] == golden["first_frame_prompt"]
    assert adapted["negative_prompt"] == golden["negative_prompt"]
    assert adapted["video_prompt"] is pair.get("video_prompt")


def test_schema_optional_p0_fields():
    shot = {
        "shot_id": "sh01",
        "shot_kind": "video",
        "dialogue_audio_mode": "native",
        "gen_strategy": "shot_by_shot",
    }
    validate(shot, NESTED_SHOT_SCHEMA)
    packet = {"concept": "雨", "video_loop": "kling"}
    validate(packet, get_schema("proposal_packet"))


def test_doctor_json_lists_video_surfaces():
    from montage.cli import main

    buf = StringIO()
    from contextlib import redirect_stdout

    with redirect_stdout(buf):
        code = main(["doctor", "--json"])
    assert code == 0
    data = json.loads(buf.getvalue())
    surfaces = {row["api_id"]: row for row in data["video_surfaces"]}
    assert surfaces["seedance_25"]["native_audio"] is True
    assert surfaces["seedance_25"]["wired"] is True
    assert surfaces["kling_omni_30"]["multi_shot"] is True
    assert surfaces["agnes_v20"]["passthrough"] is True
    assert surfaces["jimeng_v30"]["wired"] is True
    compact = {row["api_id"]: row for row in doctor_video_surfaces()}
    assert compact["seedance_25"]["citation_syntax"] == "@图片N"
    assert compact["agnes_v25"]["models"] == ["agnes-video-2.5-flash"]
    assert compact["agnes_v25"]["continuity_mode"] == "image_ref"
    assert compact["agnes_v25"]["max_ref_images"] == 5
    assert compact["agnes_v25"]["video_ref"] is False


def test_video_gen_knowledge_pages_exist():
    folder = ROOT / "prompt_library" / "video_gen"
    index = (folder / "INDEX.md").read_text(encoding="utf-8")
    assert "读 caps" in index
    assert "passthrough" in index
    agnes = (folder / "agnes.md").read_text(encoding="utf-8")
    assert "agnes-video-2.5-flash" in agnes
    assert "agnesapi" in agnes
    assert "官方英文六段式" in agnes
