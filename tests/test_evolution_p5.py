"""进化方案 P5：返工路由 / Seedance·Omni edit / film_health。零真实调用。"""

from __future__ import annotations

import json
from pathlib import Path

from montage.compose import ffmpeg_engine as fe
from montage.engine.produce import STEP_IDS, run_produce
from montage.engine.rework import clip_http_url, pick_rework_mode, rework_prompt
from montage.pipelines import CINEMATIC
from montage.providers.capabilities import (
    apply_kling_omni_videos,
    apply_seedance_content,
    apply_seedance_rework,
    video_surface,
)
from montage.providers.kling import _video_payload
from montage.providers.seedance_ark import SeedanceVideo
from montage.schemas import get_schema
from montage.toolbase import ToolResult
from montage.tools.film_health import FilmHealth, inspect_film
from montage.tools.shot_runner import ShotRunner, lift_shot_prompts
from test_produce import FakeTool, _bag, _run, _seed_project
from test_shot_runner import _fake_image, _fake_video, _pass_quality


def test_step_ids_unchanged():
    assert STEP_IDS[-3:] == ("finish", "release", "export")
    assert "film_health" not in STEP_IDS


def test_film_health_schema_and_publish_tools():
    assert get_schema("film_health") is not None
    publish = next(s for s in CINEMATIC["stages"] if s["name"] == "publish")
    assert "export_bundle" in publish["tools"]
    assert "film_health" in publish["tools"]
    assert "film_health" not in publish["produces"]
    assert "subtitle_builder" not in publish["tools"]


def test_pick_rework_prefers_edit_with_url():
    shot = {"shot_id": "sh01", "shot_kind": "video", "duration_seconds": 5}
    caps = video_surface("seedance_25")
    existing = {"url": "http://x/v.mp4", "duration_seconds": 5}
    decision = pick_rework_mode(shot, caps=caps, existing_video=existing, retry=True)
    assert decision["mode"] == "edit"
    assert decision["source_url"] == "http://x/v.mp4"
    assert "@视频1" in rework_prompt(shot, "edit")


def test_pick_rework_no_url_regenerates():
    shot = {"shot_id": "sh01", "shot_kind": "video", "rework_mode": "edit"}
    caps = video_surface("seedance_25")
    decision = pick_rework_mode(
        shot, caps=caps, existing_video={"path": "assets/videos/sh01.mp4"}, retry=True,
    )
    assert decision["mode"] == "regenerate"
    assert any("公网" in n for n in decision["notes"])


def test_pick_rework_omni_dialogue_skips_edit():
    shot = {
        "shot_id": "sh01",
        "shot_kind": "video",
        "audio_prompt": {"dialogue": "站住"},
        "api_id": "kling_omni_30",
    }
    caps = video_surface("kling_omni_30")
    existing = {"url": "http://x/v.mp4"}
    decision = pick_rework_mode(shot, caps=caps, existing_video=existing, retry=True)
    assert decision["mode"] == "regenerate"
    assert any("sound=off" in n for n in decision["notes"])


def test_pick_rework_extend_when_longer():
    shot = {"shot_id": "sh01", "shot_kind": "video", "duration_seconds": 12}
    caps = video_surface("seedance_25")
    existing = {"url": "http://x/v.mp4", "duration_seconds": 5}
    decision = pick_rework_mode(shot, caps=caps, existing_video=existing, retry=True)
    assert decision["mode"] == "extend"


def test_pick_rework_unknown_duration_is_edit():
    shot = {"shot_id": "sh01", "shot_kind": "video", "duration_seconds": 5}
    caps = video_surface("seedance_25")
    existing = {"url": "http://x/v.mp4"}
    decision = pick_rework_mode(shot, caps=caps, existing_video=existing, retry=True)
    assert decision["mode"] == "edit"


def test_volcengine_no_edit():
    shot = {"shot_id": "sh01", "shot_kind": "video"}
    caps = video_surface("jimeng_v30")
    existing = {"url": "http://x/v.mp4"}
    decision = pick_rework_mode(shot, caps=caps, existing_video=existing, retry=True)
    assert decision["mode"] == "regenerate"
    assert clip_http_url(existing).startswith("http")


def test_pick_rework_omni_blank_is_regenerate():
    shot = {"shot_id": "sh01", "shot_kind": "video", "duration_seconds": 5}
    caps = video_surface("kling_omni_30")
    existing = {"url": "https://x/v.mp4", "duration_seconds": 5}
    decision = pick_rework_mode(shot, caps=caps, existing_video=existing, retry=True)
    assert decision["mode"] == "regenerate"


def test_pick_rework_omni_feature_ok():
    shot = {
        "shot_id": "sh01",
        "shot_kind": "video",
        "duration_seconds": 5,
        "rework_mode": "feature",
        "revision_note": "眼镜改圆框",
    }
    caps = video_surface("kling_omni_30")
    existing = {"url": "https://x/v.mp4", "duration_seconds": 5}
    decision = pick_rework_mode(shot, caps=caps, existing_video=existing, retry=True)
    assert decision["mode"] == "feature"
    assert decision["source_url"] == "https://x/v.mp4"
    assert rework_prompt(shot, "feature") == "眼镜改圆框"


def test_pick_rework_omni_feature_over_10s_falls_back():
    shot = {
        "shot_id": "sh01",
        "shot_kind": "video",
        "duration_seconds": 12,
        "rework_mode": "feature",
    }
    caps = video_surface("kling_omni_30")
    existing = {"url": "https://x/v.mp4", "duration_seconds": 12}
    decision = pick_rework_mode(shot, caps=caps, existing_video=existing, retry=True)
    assert decision["mode"] == "regenerate"
    assert any("10" in n for n in decision["notes"])


def test_pick_rework_omni_feature_no_url_falls_back():
    shot = {
        "shot_id": "sh01",
        "shot_kind": "video",
        "duration_seconds": 5,
        "rework_mode": "feature",
    }
    caps = video_surface("kling_omni_30")
    decision = pick_rework_mode(
        shot, caps=caps, existing_video={"path": "assets/videos/sh01.mp4"}, retry=True,
    )
    assert decision["mode"] == "regenerate"
    assert any("公网" in n for n in decision["notes"])


def test_pick_rework_feature_non_omni_falls_back():
    shot = {
        "shot_id": "sh01",
        "shot_kind": "video",
        "duration_seconds": 5,
        "rework_mode": "feature",
    }
    caps = video_surface("seedance_25")
    existing = {"url": "http://x/v.mp4", "duration_seconds": 5}
    decision = pick_rework_mode(shot, caps=caps, existing_video=existing, retry=True)
    assert decision["mode"] == "regenerate"
    assert any("Omni" in n for n in decision["notes"])


def test_apply_seedance_rework_no_frames():
    payload: dict = {"ratio": "9:16", "first_frame_url": "http://x/f.png"}
    notes = apply_seedance_rework(
        payload,
        text="修改@视频1。眼镜改圆框。其余保持不变",
        video_url="http://x/v.mp4",
        mode="edit",
        caps=video_surface("seedance_25"),
    )
    assert notes == []
    assert payload["duration"] == -1
    assert payload["ratio"] == "adaptive"
    assert payload["watermark"] is False
    roles = [item.get("role") for item in payload["content"] if item.get("type") != "text"]
    assert roles == ["reference_video"]
    assert "first_frame_url" not in payload


def test_generate_path_still_drops_video_when_frames_locked():
    payload: dict = {"ratio": "9:16"}
    notes = apply_seedance_content(
        payload,
        text="雨夜",
        first_url="http://x/f.png",
        last_url="http://x/t.png",
        video_urls=["http://x/v.mp4"],
        caps=video_surface("seedance_25"),
    )
    assert any("互斥" in n for n in notes)
    roles = [item.get("role") for item in payload["content"] if item.get("type") != "text"]
    assert "reference_video" not in roles
    assert roles == ["first_frame", "last_frame"]


def test_apply_kling_omni_videos_forces_sound_off():
    payload: dict = {"sound": "on"}
    notes = apply_kling_omni_videos(payload, video_url="http://x/v.mp4", refer_type="base")
    assert notes == []
    assert "sound" not in payload
    assert "video_list" not in payload
    assert payload["settings"]["audio"] == "original"
    assert payload["settings"]["multi_shot"] is False
    types = [item["type"] for item in payload["contents"]]
    assert types == ["base_video"]


def test_seedance_edit_http_duration_minus_one(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("SEEDANCE_MODEL", "ep-test")
    import montage.providers.seedance_ark as ark

    captured = {}

    def fake_post(url, payload, headers=None, timeout=180):
        captured["payload"] = payload
        return {"id": "cgt-e"}

    def fake_get(url, headers=None, timeout=60, params=None):
        return {"id": "cgt-e", "status": "succeeded", "content": {"video_url": "http://x/e.mp4"}}

    monkeypatch.setattr(ark, "post_json", fake_post)
    monkeypatch.setattr(ark, "get_json", fake_get)
    result = SeedanceVideo().execute({
        "prompt": "修改@视频1。其余保持不变",
        "rework_mode": "edit",
        "video_urls": ["http://x/v.mp4"],
        "first_frame_url": "http://x/f.png",
        "last_frame_url": "http://x/t.png",
        "poll_interval_seconds": 0,
        "timeout_seconds": 30,
    })
    assert result.success, result.error
    body = captured["payload"]
    assert body["duration"] == -1
    roles = [item.get("role") for item in body["content"] if item.get("type") != "text"]
    assert roles == ["reference_video"]
    assert all(item.get("role") != "first_frame" for item in body["content"])


def test_kling_omni_edit_payload():
    path, payload = _video_payload(
        "kling_omni_30",
        {
            "model_name": "kling-v3-omni",
            "video_url": "http://x/v.mp4",
            "sound": "on",
            "image_url": "http://x/f.png",
        },
        "改眼镜",
    )
    assert path == "/omni-video/kling-3.0-omni"
    assert payload["settings"]["audio"] == "original"
    assert payload["settings"]["multi_shot"] is False
    types = [item["type"] for item in payload["contents"]]
    assert types == ["prompt", "base_video"]
    assert all(item.get("type") != "first_frame" for item in payload["contents"])


def test_inspect_film_missing_is_critical(tmp_path):
    report = inspect_film(tmp_path / "nope.mp4")
    assert report["pass"] is False
    assert report["critical"]


def test_inspect_film_probe_ok(tmp_path):
    clip = tmp_path / "final.mp4"
    clip.write_bytes(b"vid")

    def fake_probe(_path):
        return {
            "format": {"duration": "8.0", "size": "100"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720, "r_frame_rate": "24/1"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }

    report = inspect_film(clip, expected_duration=8, probe_fn=fake_probe)
    assert report["pass"] is True
    assert report["probe"]["has_audio"] is True
    assert report["critical"] == []


def test_film_health_tool_writes_artifact(tmp_path):
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    (proj / "renders").mkdir()
    result = FilmHealth().execute({"project_dir": str(proj)})
    assert result.success
    assert result.data["pass"] is False
    assert (proj / "artifacts" / "film_health.json").is_file()


def test_film_health_blocks_export(tmp_path):
    proj = _seed_project(tmp_path)
    tools, _order, _bgm = _bag(proj)

    def health(_inputs):
        return ToolResult(success=True, data={"pass": False, "critical": [{"message": "成片不存在"}]})

    tools["film_health"] = FakeTool("film_health", health)
    result = _run(proj, tools)
    assert not result["success"]
    assert "成片不存在" in (result["error"] or "")
    assert tools["export_bundle"].calls == []


def test_skip_export_ignores_health_critical(tmp_path):
    proj = _seed_project(tmp_path)
    tools, _order, _bgm = _bag(proj)

    def health(_inputs):
        return ToolResult(success=True, data={"pass": False, "critical": [{"message": "成片不存在"}]})

    tools["film_health"] = FakeTool("film_health", health)
    result = run_produce(
        proj,
        tools=tools,
        run_tool_fn=lambda tool, inputs: tool.execute(inputs),
        skip_export=True,
        sample_hero=False,
    )
    assert result["success"]
    assert result["progress"]["status"] == "ok"


def test_shot_runner_retry_edit_skips_frames(tmp_path, monkeypatch):
    monkeypatch.setenv("SEEDANCE_MODEL", "ep-x")
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"video_loop": "ark"}),
        encoding="utf-8",
    )
    calls: list[dict] = []

    def track_video(inputs):
        calls.append(dict(inputs))
        return _fake_video(inputs)

    plan = {
        "scenes": [{
            "id": "sc01",
            "shots": [{
                "shot_id": "sh01",
                "scene_id": "sc01",
                "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {"environment": "雨夜巷口"},
                "revision_note": "眼镜改成圆框",
            }],
        }],
    }
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=track_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    first = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert first.success, first.error
    assert calls and not calls[0].get("rework_mode")
    n_first = len(calls)
    again = tool.execute({
        "scene_plan": plan,
        "project_dir": str(proj),
        "dry_run": False,
        "retry_ids": ["sh01"],
    })
    assert again.success, again.error
    assert len(calls) > n_first
    edit = calls[-1]
    assert edit.get("rework_mode") == "edit"
    assert edit.get("video_url") == "https://example.test/vid.mp4"
    assert not edit.get("first_frame_url")
    assert not edit.get("image_url")
    assert "@视频1" in edit.get("prompt", "")
    lifted = lift_shot_prompts(plan["scenes"][0]["shots"])
    assert lifted["shots"][0].get("revision_note") == "眼镜改成圆框"


def test_shot_runner_kling_retry_defaults_regenerate(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"video_loop": "kling"}),
        encoding="utf-8",
    )
    calls: list[dict] = []

    def track_video(inputs):
        calls.append(dict(inputs))
        return _fake_video(inputs)

    plan = {
        "scenes": [{
            "id": "sc01",
            "shots": [{
                "shot_id": "sh01",
                "scene_id": "sc01",
                "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {"environment": "雨夜巷口"},
                "api_id": "kling_omni_30",
            }],
        }],
    }
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=track_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    first = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert first.success, first.error
    n_first = len(calls)
    again = tool.execute({
        "scene_plan": plan,
        "project_dir": str(proj),
        "dry_run": False,
        "retry_ids": ["sh01"],
    })
    assert again.success, again.error
    assert len(calls) > n_first
    retry = calls[-1]
    assert retry.get("rework_mode") not in ("edit", "extend", "feature")
    assert not retry.get("refer_type")


def test_shot_runner_kling_feature_payload(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"video_loop": "kling"}),
        encoding="utf-8",
    )
    calls: list[dict] = []

    def track_video(inputs):
        calls.append(dict(inputs))
        return _fake_video(inputs)

    plan = {
        "scenes": [{
            "id": "sc01",
            "shots": [{
                "shot_id": "sh01",
                "scene_id": "sc01",
                "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {"environment": "雨夜巷口"},
                "revision_note": "眼镜改成圆框",
                "api_id": "kling_omni_30",
            }],
        }],
    }
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=track_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    first = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert first.success, first.error
    n_first = len(calls)
    plan["scenes"][0]["shots"][0]["rework_mode"] = "feature"
    again = tool.execute({
        "scene_plan": plan,
        "project_dir": str(proj),
        "dry_run": False,
        "retry_ids": ["sh01"],
    })
    assert again.success, again.error
    assert len(calls) > n_first
    feat = calls[-1]
    assert feat.get("rework_mode") == "feature"
    assert feat.get("refer_type") == "feature"
    assert feat.get("video_url") == "https://example.test/vid.mp4"
    assert feat.get("audio") == "off"
    assert feat.get("sound") == "off"
    assert not feat.get("first_frame_url")
    assert not feat.get("image_url")
    assert "眼镜改成圆框" in feat.get("prompt", "")
    assert "@视频1" not in feat.get("prompt", "")


def test_shot_runner_kling_feature_12s_falls_back(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"video_loop": "kling"}),
        encoding="utf-8",
    )
    calls: list[dict] = []

    def track_video(inputs):
        calls.append(dict(inputs))
        return _fake_video(inputs)

    plan = {
        "scenes": [{
            "id": "sc01",
            "shots": [{
                "shot_id": "sh01",
                "scene_id": "sc01",
                "shot_kind": "video",
                "duration_seconds": 12,
                "visual_details": {"environment": "雨夜巷口"},
                "rework_mode": "feature",
                "api_id": "kling_omni_30",
            }],
        }],
    }
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=track_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    first = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert first.success, first.error
    again = tool.execute({
        "scene_plan": plan,
        "project_dir": str(proj),
        "dry_run": False,
        "retry_ids": ["sh01"],
    })
    assert again.success, again.error
    retry = calls[-1]
    assert retry.get("rework_mode") != "feature"
    assert retry.get("refer_type") != "feature"


def test_retake_segment_commands(monkeypatch, tmp_path):
    src = tmp_path / "src.mp4"
    mid = tmp_path / "mid.mp4"
    src.write_bytes(b"src")
    mid.write_bytes(b"mid")
    monkeypatch.setattr(fe, "check_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(fe, "probe", lambda _p: {"format": {"duration": "10"}})
    cmds: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: cmds.append(list(cmd)))
    out = tmp_path / "out.mp4"
    fe.retake_segment(src, mid, out, 2.0, 3.0)
    blob = " ".join(" ".join(c) for c in cmds)
    assert "-ss 0.00" in blob
    assert "-ss 5.00" in blob
    assert "-c copy" in blob
