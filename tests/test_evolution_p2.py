"""进化方案 P2：_route_shot / 按 API 面提示词 / 片内音。零真实调用。"""

from __future__ import annotations

import json
from pathlib import Path

from lib.shot_prompt_builder import build_shot_prompt_pair
from montage.engine.policy import (
    VIDEO_LOOP_PROVIDERS,
    keep_embedded_audio,
    resolve_allowed_providers,
    shot_keeps_embedded_audio,
)
from montage.providers.capabilities import policy_for_loop, video_caps, video_surface
from montage.providers.prompt_adapter import adapt_visual_prompt
from montage.providers.selectors import VideoSelector
from montage.schemas import get_schema
from montage.tools.shot_runner import _prompt_inputs, _route_shot, loop_family

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "agnes_v20_prompt_golden.json"


def _shot(**extra):
    row = {
        "shot_id": "sh01",
        "scene_id": "sc01",
        "shot_kind": "video",
        "duration_seconds": 8,
        "audio_prompt": {"dialogue": "站住"},
        "reference_asset_ids": ["portrait_a"],
    }
    row.update(extra)
    return row


def test_volcengine_not_upgraded_to_seedance(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("SEEDANCE_MODEL", "ep-x")
    route = _route_shot(
        _shot(api_id="seedance_25"),
        video_loop="volcengine",
        vid_prov="volcengine",
        vid_name="jimeng_video",
        has_first_frame=True,
    )
    assert route["api_id"] == "jimeng_v30"
    assert route["family"] == "volcengine"
    assert any("忽略" in n for n in route["notes"])
    inputs = _prompt_inputs(
        _shot(), None, "", agnes_loop=False, vid_prov="volcengine", api_id="jimeng_v30",
    )
    assert inputs["jimeng_prompt"] is True
    assert inputs["provider_max_chars"] == 800


def test_selector_volcengine_still_jimeng_with_ark_key(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("VOLC_ACCESSKEY", "ak")
    monkeypatch.setenv("VOLC_SECRETKEY", "sk")
    sel = VideoSelector()
    sel._route_cache.clear()
    picked = sel._pick({"allowed_providers": ["volcengine"], "prompt": "x"})
    assert picked is not None
    assert picked.name == "jimeng_video"
    assert "ark" not in VIDEO_LOOP_PROVIDERS["volcengine"]


def test_ark_loop_routes_seedance_dialect():
    route = _route_shot(
        _shot(duration_seconds=20),
        video_loop="ark",
        vid_prov="ark",
        vid_name="seedance_video",
        has_first_frame=True,
    )
    assert route["api_id"] == "seedance_25"
    assert route["audio_source"] == "jimeng_prompt"
    assert route["generate_audio"] is True
    inputs = _prompt_inputs(
        _shot(), None, "", agnes_loop=False, vid_prov="ark", api_id="seedance_25",
    )
    assert inputs["jimeng_prompt"] is False
    assert inputs["provider_max_chars"] == 4000
    builder = {
        "first_frame_prompt": "黑发青年站在雨夜街道",
        "video_prompt": "青年向前跑，霓虹倒映积水",
        "negative_prompt": "low quality",
    }
    adapted = adapt_visual_prompt(
        "seedance_25",
        builder,
        refs=[{"role": "角色外貌"}],
        dialogue="站住",
        duration_seconds=20,
    )
    assert "[0s-20s]" in adapted["video_prompt"]
    assert "@图片1用于角色外貌，不采用背景" in adapted["video_prompt"]
    assert "{站住}" in adapted["video_prompt"]
    assert "【字幕】" not in adapted["video_prompt"]


def test_ark_hero_short_picks_20_pro():
    route = _route_shot(
        _shot(duration_seconds=10, shot_budget_class="hero"),
        video_loop="seedance",
        vid_prov="ark",
        vid_name="seedance_video",
    )
    assert route["api_id"] == "seedance_20_pro"
    assert loop_family("seedance", "") == "ark"


def test_kling_omni_dialect_when_model_set(monkeypatch):
    monkeypatch.setenv("KLING_OMNI_MODEL", "kling-v3-omni")
    route = _route_shot(
        _shot(),
        video_loop="kling",
        vid_prov="kling",
        vid_name="kling_video",
        has_first_frame=True,
    )
    assert route["api_id"] == "kling_omni_30"
    assert route["sound"] == "on"
    assert route["audio_source"] == "kling_prompt"
    adapted = adapt_visual_prompt(
        "kling_omni_30",
        {"video_prompt": "青年向前跑", "first_frame_prompt": "站在街上"},
        refs=[{"role": "角色外貌"}],
        dialogue="站住",
    )
    assert "@image_1" in adapted["video_prompt"]
    assert "<<<image_" not in adapted["video_prompt"]
    assert "@图片" not in adapted["video_prompt"]


def test_kling_without_omni_model_stays_omni(monkeypatch):
    monkeypatch.delenv("KLING_OMNI_MODEL", raising=False)
    monkeypatch.delenv("KLING_I2V_MODEL", raising=False)
    monkeypatch.delenv("KLING_FORCE_V1", raising=False)
    route = _route_shot(
        _shot(),
        video_loop="kling",
        vid_prov="kling",
        vid_name="kling_video",
        has_first_frame=True,
    )
    assert route["api_id"] == "kling_omni_30"
    assert route["audio_source"] == "kling_prompt"


def test_kling_21_pro_requires_first_frame(monkeypatch):
    monkeypatch.setenv("KLING_I2V_MODEL", "kling-v2-1")
    monkeypatch.delenv("KLING_OMNI_MODEL", raising=False)
    monkeypatch.delenv("KLING_FORCE_V1", raising=False)
    route = _route_shot(
        _shot(api_id="kling_i2v_21_pro", shot_budget_class="hero"),
        video_loop="kling",
        vid_prov="kling",
        vid_name="kling_video",
        has_first_frame=False,
    )
    assert route["api_id"] == "kling_omni_30"
    assert route["degraded"] is True


def test_agnes_prompt_inputs_and_adapter_match_golden():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    inputs = _prompt_inputs(
        golden["shot"], None, "", agnes_loop=True, vid_prov="agnes", api_id="agnes_v20",
    )
    assert inputs["agnes_audio"] is True
    assert inputs["jimeng_prompt"] is False
    assert inputs["provider_max_chars"] == 3000
    pair = build_shot_prompt_pair(golden["shot"], **golden["builder_kwargs"])
    adapted = adapt_visual_prompt("agnes_v20", pair)
    assert adapted["passthrough"] is True
    assert adapted["video_prompt"] == golden["video_prompt"]
    assert adapted["first_frame_prompt"] == golden["first_frame_prompt"]
    assert adapted["negative_prompt"] == golden["negative_prompt"]
    route = _route_shot(
        golden["shot"],
        video_loop="agnes",
        vid_prov="agnes",
        vid_name="agnes_video",
        video_surface_id="agnes_v25",
    )
    assert route["api_id"] == "agnes_v25"
    assert route["audio_source"] == "agnes_prompt"
    v20 = _route_shot(
        {**golden["shot"], "api_id": "agnes_v20"},
        video_loop="agnes",
        vid_prov="agnes",
        vid_name="agnes_video",
    )
    assert v20["api_id"] == "agnes_v20"
    default_inputs = _prompt_inputs(
        golden["shot"], None, "", agnes_loop=True, vid_prov="agnes",
    )
    assert default_inputs["agnes_audio"] is False


def test_keep_embedded_audio_native_sources():
    assert keep_embedded_audio({"shots": [{"audio_source": "jimeng_prompt"}]}) is True
    assert keep_embedded_audio({"shots": [{"audio_source": "kling_prompt"}]}) is False
    assert shot_keeps_embedded_audio({"dialogue_audio_mode": "native"}) is True
    assert keep_embedded_audio({"shots": [{"audio_source": "none"}]}) is False
    assert keep_embedded_audio({"shots": []}) is False


def test_kling_force_v1(monkeypatch):
    monkeypatch.setenv("KLING_FORCE_V1", "1")
    monkeypatch.delenv("KLING_OMNI_MODEL", raising=False)
    route = _route_shot(
        _shot(),
        video_loop="kling",
        vid_prov="kling",
        vid_name="kling_video",
        has_first_frame=True,
    )
    assert route["api_id"] == "kling_v1"


def test_kling_multi_speaker_audio_off(monkeypatch):
    monkeypatch.delenv("KLING_FORCE_V1", raising=False)
    route = _route_shot(
        _shot(lines=[
            {"speaker_id": "a", "text": "站住"},
            {"speaker_id": "b", "text": "别跑"},
        ]),
        video_loop="kling",
        vid_prov="kling",
        vid_name="kling_video",
        has_first_frame=True,
    )
    assert route["api_id"] == "kling_omni_30"
    assert route["sound"] == "off"
    assert route["audio_source"] == ""


def test_keep_embedded_audio_ark_loop(tmp_path):
    art = tmp_path / "artifacts"
    art.mkdir()
    (art / "proposal_packet.json").write_text(
        json.dumps({"concept": "雨", "video_loop": "ark"}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert keep_embedded_audio(None, tmp_path) is True
    (art / "proposal_packet.json").write_text(
        json.dumps({"concept": "雨", "video_loop": "volcengine"}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert keep_embedded_audio(None, tmp_path) is False


def test_ark_image_lock_routes_to_seedream():
    policy = {"video_loop": "ark", "allowed_providers": ["ark"]}
    assert resolve_allowed_providers(policy, {}, capability="video_generation") == ["ark"]
    # P6 起 ark 环生图走 seedream_image（方舟 Seedream），不再重定向即梦；
    # 旧即梦行为可用 image_providers=["volcengine"] 显式找回
    assert resolve_allowed_providers(policy, {}, capability="image_generation") == ["ark"]
    assert resolve_allowed_providers(
        {"image_providers": ["volcengine"]}, {}, capability="image_generation",
    ) == ["volcengine"]


def test_policy_for_loop_ark_range_volcengine_unchanged():
    assert policy_for_loop("volcengine") == {"kind": "enum", "values": [5, 10]}
    assert policy_for_loop("jimeng") == policy_for_loop("volcengine")
    ark = policy_for_loop("ark")
    assert ark["kind"] == "range"
    assert ark["min"] == 4
    assert ark["max"] == 30
    assert policy_for_loop("seedance") == ark
    assert policy_for_loop("agnes")["kind"] == "range"
    assert policy_for_loop("agnes")["max"] == 12
    assert video_caps(tool="jimeng_video")["duration_policy"]["values"] == [5, 10]


def test_proposal_schema_accepts_ark():
    from jsonschema import validate

    validate({"concept": "雨", "video_loop": "ark", "video_surface": "seedance_25"}, get_schema("proposal_packet"))
    validate(
        {"concept": "雨", "video_loop": "seedance"},
        get_schema("proposal_packet"),
    )


def test_proposal_schema_frames_mode_enum():
    from jsonschema import ValidationError, validate

    validate(
        {"concept": "雨", "frames_mode": "keyframe"},
        get_schema("proposal_packet"),
    )
    try:
        validate({"concept": "雨", "frames_mode": "nope"}, get_schema("proposal_packet"))
    except ValidationError:
        pass
    else:  # pragma: no cover - 失败路径
        raise AssertionError("未知 frames_mode 应被 schema 拒绝")


def test_frames_mode_defaults_to_preview(tmp_path):
    from montage.engine.policy import load_loop_policy, normalize_frames_mode

    assert normalize_frames_mode(None) == "preview"
    assert normalize_frames_mode("") == "preview"
    assert normalize_frames_mode("bogus") == "preview"
    assert normalize_frames_mode("REFERENCE_FIRST") == "reference_first"
    art = tmp_path / "artifacts"
    art.mkdir()
    (art / "proposal_packet.json").write_text(
        json.dumps({"concept": "雨", "frames_mode": "keyframe"}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert load_loop_policy(tmp_path)["frames_mode"] == "keyframe"
    # 缺字段回落 preview
    (art / "proposal_packet.json").write_text(
        json.dumps({"concept": "雨"}, ensure_ascii=False), encoding="utf-8",
    )
    assert load_loop_policy(tmp_path)["frames_mode"] == "preview"


def test_wired_surfaces_reachable():
    assert video_surface("seedance_25")["wired"] is True
    assert video_surface("kling_omni_30")["wired"] is True
    assert video_surface("kling_i2v_21_pro")["wired"] is True
    assert video_surface("agnes_v25")["wired"] is True
    assert video_surface("agnes_v20")["wired"] is False


def test_multi_shot_stays_shot_by_shot_http():
    route = _route_shot(
        _shot(gen_strategy="single_call_multi_shot", duration_seconds=20),
        video_loop="ark",
        vid_prov="ark",
        vid_name="seedance_video",
    )
    assert any("逐镜" in n for n in route["notes"])
    assert route["gen_strategy"] == "single_call_multi_shot"
