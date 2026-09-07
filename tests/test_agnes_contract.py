"""Agnes 官方契约：域名、2.0/2.5 端点、TTS 降级、解析函数。"""

from jsonschema import validate

from montage.providers import agnes
from montage.providers.capabilities import video_caps, video_surface
from montage.schemas import get_schema
from montage.toolbase import ToolStatus
from montage.tools.script_validator import check_agnes_audio_prompts


def test_overseas_host_only_when_base_explicit(monkeypatch):
    monkeypatch.delenv("AGNES_CN_API_KEY", raising=False)
    monkeypatch.delenv("AGNES_API_BASE_URL", raising=False)
    monkeypatch.delenv("AGNES_BASE_URL", raising=False)
    monkeypatch.setenv("AGNES_API_KEY", "k")
    _, base = agnes.agnes_credentials()
    assert base.rstrip("/").endswith("api.agnes-ai.cn/v1")
    monkeypatch.setenv("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1")
    _, overseas = agnes.agnes_credentials()
    assert "apihub.agnes-ai.com" in overseas


def test_cn_host_unchanged(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("AGNES_API_BASE_URL", raising=False)
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    _, base = agnes.agnes_credentials()
    assert base.rstrip("/").endswith("api.agnes-ai.cn/v1")


def test_api_origin_strips_v1():
    assert agnes.api_origin("https://api.agnes-ai.cn/v1") == "https://api.agnes-ai.cn"
    assert agnes.api_origin("https://apihub.agnes-ai.com/v1/") == "https://apihub.agnes-ai.com"


def test_parse_video_result_v20_metadata_url():
    url = agnes.parse_video_result({
        "status": "completed",
        "metadata": {"url": "https://cdn.example/v.mp4"},
    })
    assert url == "https://cdn.example/v.mp4"


def test_parse_video_result_running_has_no_url():
    assert agnes.parse_video_result({"status": "in_progress", "url": None}) is None
    assert agnes.video_error({"status": "failed", "error": {"message": "boom"}}) == "boom"


def test_v20_posts_videos_and_polls_origin_agnesapi(monkeypatch, tmp_path):
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    monkeypatch.delenv("AGNES_API_BASE_URL", raising=False)
    posted: list[tuple[str, dict]] = []
    gotten: list[str] = []

    def fake_post(url, payload, headers=None, timeout=180):
        posted.append((url, payload))
        return {"video_id": "video_1", "status": "queued"}

    def fake_get(url, headers=None, timeout=60, params=None):
        gotten.append(url)
        if params:
            assert params.get("video_id") == "video_1"
        return {
            "status": "completed",
            "metadata": {"url": "https://cdn.example/out.mp4"},
        }

    monkeypatch.setattr(agnes, "post_json", fake_post)
    monkeypatch.setattr(agnes, "get_json", fake_get)
    monkeypatch.setattr(agnes.time, "sleep", lambda _s: None)
    monkeypatch.setattr(agnes, "_download", lambda url, dest: str(dest))

    out = tmp_path / "shot.mp4"
    result = agnes.AgnesVideo().execute({
        "model": "agnes-video-v2.0",
        "prompt": "一只猫在沙滩上走",
        "seconds": 5,
        "image_url": "https://cdn.example/first.png",
        "last_frame_url": "https://cdn.example/next.png",
        "output_path": str(out),
    })
    assert result.success
    assert posted and posted[0][0].endswith("/videos")
    assert "/videos/generations" not in posted[0][0]
    payload = posted[0][1]
    assert payload["num_frames"] == 121
    assert payload["frame_rate"] == 24
    assert payload["negative_prompt"]
    assert "duration" not in payload
    assert "image_list" not in payload
    assert "return_last_frame" not in payload
    assert payload["extra_body"]["mode"] == "keyframes"
    assert payload["extra_body"]["image"] == [
        "https://cdn.example/first.png",
        "https://cdn.example/next.png",
    ]
    assert gotten
    assert gotten[0] == "https://api.agnes-ai.cn/agnesapi"
    assert "/v1/agnesapi" not in gotten[0]
    assert result.data["url"] == "https://cdn.example/out.mp4"
    assert result.data["local_path"] == str(out)


def test_v25_polls_agnesapi_seconds_string(monkeypatch, tmp_path):
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    posted: list[dict] = []
    gotten: list[tuple[str, dict | None]] = []

    def fake_post(url, payload, headers=None, timeout=180):
        posted.append(payload)
        return {"id": "video_xxx", "status": "queued"}

    def fake_get(url, headers=None, timeout=60, params=None):
        gotten.append((url, params))
        return {"status": "completed", "url": "https://cdn.example/v25.mp4"}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    monkeypatch.setattr(agnes, "get_json", fake_get)
    monkeypatch.setattr(agnes.time, "sleep", lambda _s: None)
    monkeypatch.setattr(agnes, "_download", lambda url, dest: str(dest))

    result = agnes.AgnesVideo().execute({
        "model": "agnes-video-2.5",
        "prompt": "雨夜街道",
        "seconds": 5,
        "image_url": "https://cdn.example/first.png",
        "last_frame_url": "https://cdn.example/last.png",
        "seed": 7,
        "output_path": str(tmp_path / "a.mp4"),
    })
    assert result.success
    payload = posted[0]
    assert payload["model"] == "agnes-video-2.5-flash"
    assert payload["mode"] == "keyframe"
    assert payload["first_frame"].endswith("first.png")
    assert payload["last_frame"].endswith("last.png")
    assert payload["seconds"] == "5"
    assert isinstance(payload["seconds"], str)
    assert payload["size"] == "720P"
    assert payload["seed"] == 7
    assert "negative_prompt" not in payload
    assert "num_frames" not in payload
    assert "images" not in payload
    assert "videos" not in payload
    assert gotten[0][0] == "https://api.agnes-ai.cn/agnesapi"
    assert "/videos/video_xxx" not in gotten[0][0]
    assert gotten[0][1]["video_id"] == "video_xxx"
    assert gotten[0][1]["model_name"] == "agnes-video-2.5-flash"


def test_poll_failed_status_does_not_spin(monkeypatch):
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    calls = {"n": 0}

    def fake_post(url, payload, headers=None, timeout=180):
        return {"video_id": "video_bad", "status": "queued"}

    def fake_get(url, headers=None, timeout=60, params=None):
        calls["n"] += 1
        return {"status": "failed", "error": {"message": "bad media"}}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    monkeypatch.setattr(agnes, "get_json", fake_get)
    monkeypatch.setattr(agnes.time, "sleep", lambda _s: None)

    result = agnes.AgnesVideo().execute({"prompt": "x", "seconds": 5})
    assert not result.success
    assert "bad media" in result.error
    assert calls["n"] == 1


def test_agnes_audio_unavailable(monkeypatch):
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    tool = agnes.AgnesAudio()
    assert tool.get_status() == ToolStatus.UNAVAILABLE
    result = tool.execute({"text": "你好"})
    assert not result.success
    assert "无独立 TTS" in result.error


def test_schema_accepts_audio_source():
    schema = get_schema("shot_prompts")
    payload = {
        "version": "1",
        "shots": [{
            "scene_id": "sc01",
            "shot_kind": "video",
            "shot_id": "sh01",
            "audio_source": "agnes_prompt",
        }],
    }
    validate(payload, schema)


def test_agnes_prompt_missing_dialogue_is_warning():
    findings = check_agnes_audio_prompts({
        "shots": [{
            "shot_id": "sh01",
            "audio_source": "agnes_prompt",
            "video_prompt": "【环境】雨夜",
        }],
    })
    assert any("【台词】" in f["message"] for f in findings)
    assert all(f["severity"] == "warning" for f in findings if "【台词】" in f["message"])


def test_video_caps_agnes_continuity():
    caps = video_caps(tool="agnes_video")
    assert caps["api_id"] == "agnes_v25"
    assert caps["last_frame"] is False
    assert caps["continuity_mode"] == "image_ref"
    assert caps["max_ref_images"] == 5
    assert caps["video_ref"] is False
    assert caps["negative_prompt"] is False
    retired = video_surface("agnes_v20")
    assert retired["wired"] is False
    assert retired["passthrough"] is True


def test_v25_reference_adds_picture_placeholder(monkeypatch, tmp_path):
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    posted: list[dict] = []

    def fake_post(url, payload, headers=None, timeout=180):
        posted.append(payload)
        return {"id": "video_ref", "status": "completed", "url": "https://cdn.example/r.mp4"}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    monkeypatch.setattr(agnes, "_download", lambda url, dest: str(dest))

    result = agnes.AgnesVideo().execute({
        "model": "agnes-video-2.5",
        "prompt": "【环境】雨夜街道",
        "seconds": 6,
        "images": ["https://cdn.example/portrait.png"],
        "output_path": str(tmp_path / "r.mp4"),
    })
    assert result.success
    payload = posted[0]
    assert payload["mode"] == "reference"
    assert payload["model"] == "agnes-video-2.5-flash"
    assert payload["images"] == ["https://cdn.example/portrait.png"]
    assert payload["seconds"] == "6"
    assert isinstance(payload["seconds"], str)
    assert "<Picture 1>" in payload["prompt"]
    assert "first_frame" not in payload
    assert "videos" not in payload
    assert "negative_prompt" not in payload


def test_flash_drops_videos_and_stays_text(monkeypatch, tmp_path):
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    posted: list[dict] = []

    def fake_post(url, payload, headers=None, timeout=180):
        posted.append(payload)
        return {"id": "video_v", "status": "completed", "url": "https://cdn.example/v.mp4"}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    monkeypatch.setattr(agnes, "_download", lambda url, dest: str(dest))

    result = agnes.AgnesVideo().execute({
        "model": "agnes-video-2.5",
        "prompt": "补时长",
        "seconds": 8,
        "videos": [{"url": "https://cdn.example/prev.mp4"}],
        "output_path": str(tmp_path / "v.mp4"),
    })
    assert result.success
    payload = posted[0]
    assert payload["model"] == "agnes-video-2.5-flash"
    assert payload["mode"] == "text"
    assert "videos" not in payload
    assert "<Video 1>" not in payload["prompt"]
    assert "first_frame" not in payload


def test_flash_caps_images_at_five(monkeypatch, tmp_path):
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    posted: list[dict] = []

    def fake_post(url, payload, headers=None, timeout=180):
        posted.append(payload)
        return {"id": "video_i", "status": "completed", "url": "https://cdn.example/i.mp4"}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    monkeypatch.setattr(agnes, "_download", lambda url, dest: str(dest))

    urls = [f"https://cdn.example/p{i}.png" for i in range(1, 8)]
    result = agnes.AgnesVideo().execute({
        "prompt": "广场",
        "seconds": 5,
        "images": urls,
        "output_path": str(tmp_path / "i.mp4"),
    })
    assert result.success
    payload = posted[0]
    assert payload["mode"] == "reference"
    assert payload["images"] == urls[:5]
    assert "<Picture 5>" in payload["prompt"]
    assert "<Picture 6>" not in payload["prompt"]


def test_v25_images_win_over_first_frame(monkeypatch, tmp_path):
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    posted: list[dict] = []

    def fake_post(url, payload, headers=None, timeout=180):
        posted.append(payload)
        return {"id": "video_mix", "status": "completed", "url": "https://cdn.example/m.mp4"}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    monkeypatch.setattr(agnes, "_download", lambda url, dest: str(dest))

    result = agnes.AgnesVideo().execute({
        "prompt": "广场",
        "seconds": 8,
        "images": ["https://cdn.example/turnaround.png"],
        "first_frame": "https://cdn.example/frame.png",
        "output_path": str(tmp_path / "m.mp4"),
    })
    assert result.success
    payload = posted[0]
    assert payload["mode"] == "reference"
    assert "first_frame" not in payload
    assert payload["images"] == ["https://cdn.example/turnaround.png"]
    assert payload["seconds"] == "8"


def test_is_v25_recognizes_flash():
    assert agnes._is_v25("agnes-video-2.5-flash")
    assert agnes._is_v25("agnes-video-2.5")
    assert not agnes._is_v25("agnes-video-v2.0")
    assert agnes.resolve_video_model("agnes-video-2.5") == "agnes-video-2.5-flash"
    assert agnes.resolve_video_model("agnes-video-2.5-flash") == "agnes-video-2.5-flash"
    assert agnes.resolve_video_model("agnes-video-v2.0") == "agnes-video-v2.0"


def test_agnes_is_v25_matches_resolve(monkeypatch):
    from montage.tools._shot_route import _agnes_is_v25

    monkeypatch.delenv("AGNES_VIDEO_MODEL", raising=False)
    assert _agnes_is_v25() is True
    monkeypatch.setenv("AGNES_VIDEO_MODEL", "agnes-video-v2.0")
    assert _agnes_is_v25() is False
    monkeypatch.setenv("AGNES_VIDEO_MODEL", "not-a-real-model")
    assert _agnes_is_v25() is True
    assert agnes.resolve_video_model("not-a-real-model") == "agnes-video-2.5-flash"


def test_clip_prompt_does_not_slice_without_fallback():
    long_text = "字" * 3001
    kept, err = agnes._clip_prompt(long_text, fallback=False)
    assert err
    assert kept == long_text
    clipped, err2 = agnes._clip_prompt(long_text, fallback=True)
    assert not err2
    assert len(clipped) <= 3000
