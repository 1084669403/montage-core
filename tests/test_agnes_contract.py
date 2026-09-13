"""Agnes 官方契约：域名、2.0/2.5 端点、TTS 降级、解析函数。"""

import base64

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


def test_v25_keyframe_uses_first_frame_url_and_omits_ratio(monkeypatch, tmp_path):
    """keyframe 真 I2V：认 first_frame_url，且不叠可能冲突的 aspect_ratio。"""
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    posted: list[dict] = []

    def fake_post(url, payload, headers=None, timeout=180):
        posted.append(payload)
        return {"id": "video_k", "status": "completed", "url": "https://cdn.example/k.mp4"}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    monkeypatch.setattr(agnes, "_download", lambda url, dest: str(dest))

    result = agnes.AgnesVideo().execute({
        "prompt": "首帧",
        "seconds": 6,
        "first_frame_url": "https://cdn.example/f.png",
        "aspect_ratio": "16:9",
        "output_path": str(tmp_path / "k.mp4"),
    })
    assert result.success
    payload = posted[0]
    assert payload["mode"] == "keyframe"
    assert payload["first_frame"] == "https://cdn.example/f.png"
    assert "aspect_ratio" not in payload


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


# ---- Agnes Image 2.5 Flash 契约 ----

def test_image_t2i_payload_contract(monkeypatch):
    """文生图：单模型 2.5、档位+ratio、response_format 进 extra_body 禁顶层。"""
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    posted: list[tuple[str, dict]] = []

    def fake_post(url, payload, headers=None, timeout=300):
        posted.append((url, payload))
        return {"created": 1, "data": [{"url": "https://cdn.example/out.png", "b64_json": None, "revised_prompt": None}]}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    result = agnes.AgnesImage().execute({"prompt": "浮空城市", "size": "2K", "ratio": "16:9"})
    assert result.success
    url, payload = posted[0]
    assert url.endswith("/images/generations")
    assert payload["model"] == "agnes-image-2.5-flash"
    assert payload["prompt"] == "浮空城市"
    assert payload["size"] == "2K"
    assert payload["ratio"] == "16:9"
    assert payload["extra_body"] == {"response_format": "url"}
    assert "response_format" not in payload
    assert "tags" not in payload
    assert "resolution" not in payload
    assert "return_base64" not in payload
    assert result.data["url"] == "https://cdn.example/out.png"
    assert result.data["revised_prompt"] is None


def test_image_edit_multi_image_composition_contract(monkeypatch):
    """编辑/多图合成：extra_body.image 保序多张、同模型 2.5、档位+ratio 统一。"""
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    posted: list[tuple[str, dict]] = []

    def fake_post(url, payload, headers=None, timeout=300):
        posted.append((url, payload))
        return {"created": 1, "data": [{"url": "https://cdn.example/comp.png", "b64_json": None, "revised_prompt": None}]}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    refs = ["https://cdn.example/char-1.png", "https://cdn.example/char-2.png"]
    result = agnes.AgnesImage().execute({
        "prompt": "合成战斗场景",
        "operation": "image_edit",
        "reference_urls": refs,
        "size": "2K",
        "ratio": "3:2",
    })
    assert result.success
    payload = posted[0][1]
    assert payload["model"] == "agnes-image-2.5-flash"
    assert payload["size"] == "2K"
    assert payload["ratio"] == "3:2"
    assert payload["extra_body"]["image"] == refs
    assert payload["extra_body"]["response_format"] == "url"
    assert "response_format" not in payload
    assert "tags" not in payload


def test_image_t2i_return_base64(monkeypatch, tmp_path):
    """文生图 Base64：顶层 return_base64=true → data[0].b64_json 落盘。"""
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    posted: list[tuple[str, dict]] = []
    b64 = base64.b64encode(b"png-bytes").decode("ascii")

    def fake_post(url, payload, headers=None, timeout=300):
        posted.append((url, payload))
        return {"created": 1, "data": [{"url": None, "b64_json": b64, "revised_prompt": None}]}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    out = tmp_path / "b64.png"
    result = agnes.AgnesImage().execute({"prompt": "玻璃方块", "return_base64": True, "output_path": str(out)})
    assert result.success
    assert posted[0][1]["return_base64"] is True
    assert "extra_body" not in posted[0][1]
    assert result.data["local_path"] == str(out)
    assert out.read_bytes() == b"png-bytes"


def test_image_t2i_return_base64_warns_no_public_url(monkeypatch, tmp_path):
    """return_base64 输出无公网 URL，meta.warnings 提醒不能直接喂视频。"""
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    b64 = base64.b64encode(b"png-bytes").decode("ascii")

    def fake_post(url, payload, headers=None, timeout=300):
        return {"created": 1, "data": [{"url": None, "b64_json": b64}]}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    out = tmp_path / "b64w.png"
    result = agnes.AgnesImage().execute({
        "prompt": "玻璃方块", "return_base64": True, "output_path": str(out),
    })
    assert result.success
    warnings = (result.meta or {}).get("warnings") or []
    assert any("无公网 URL" in w for w in warnings)


def test_v25_drops_local_refs_and_overflow_with_warnings():
    urls = [f"https://cdn.example/p{i}.png" for i in range(1, 7)]
    payload, warnings = agnes._payload_v25(
        {"images": ["/local/a.png", "data:image/png;base64,xx", *urls], "seconds": 6},
        "m",
        "p",
    )
    assert payload["mode"] == "reference"
    assert payload["images"] == urls[:5]
    assert any("非公网" in w for w in warnings)
    assert any("超过上限" in w for w in warnings)


def test_v25_ignores_local_first_frame_with_warning():
    payload, warnings = agnes._payload_v25(
        {"first_frame": "/local/f.png", "seconds": 6}, "m", "p",
    )
    assert payload["mode"] == "text"
    assert any("首帧非公网" in w for w in warnings)


def test_image_edit_b64_json_response(monkeypatch, tmp_path):
    """编辑 b64_json：extra_body.response_format=b64_json → 落盘。"""
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    b64 = base64.b64encode(b"edit-bytes").decode("ascii")
    posted: list[tuple[str, dict]] = []

    def fake_post(url, payload, headers=None, timeout=300):
        posted.append((url, payload))
        return {"created": 1, "data": [{"url": None, "b64_json": b64, "revised_prompt": None}]}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    out = tmp_path / "edit.png"
    result = agnes.AgnesImage().execute({
        "prompt": "改橙色",
        "operation": "image_edit",
        "reference_urls": ["https://cdn.example/in.png"],
        "response_format": "b64_json",
        "output_path": str(out),
    })
    assert result.success
    payload = posted[0][1]
    assert payload["extra_body"]["response_format"] == "b64_json"
    assert "return_base64" not in payload
    assert out.read_bytes() == b"edit-bytes"


def test_image_edit_packs_local_paths_as_data_uri(monkeypatch, tmp_path):
    """本地路径装箱：https 直传 + 本地 png 转 data:image/png;base64；缺文件硬失败。"""
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    posted: list[tuple[str, dict]] = []
    local_png = tmp_path / "portrait.png"
    local_png.write_bytes(b"local-img")

    def fake_post(url, payload, headers=None, timeout=300):
        posted.append((url, payload))
        return {"created": 1, "data": [{"url": "https://cdn.example/o.png", "b64_json": None, "revised_prompt": None}]}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    result = agnes.AgnesImage().execute({
        "prompt": "定妆参考",
        "operation": "image_reference",
        "reference_urls": ["https://cdn.example/in.png", str(local_png)],
    })
    assert result.success
    image = posted[0][1]["extra_body"]["image"]
    assert image[0] == "https://cdn.example/in.png"
    assert image[1].startswith("data:image/png;base64,")
    assert base64.b64decode(image[1].split(",", 1)[1]) == b"local-img"

    missing = agnes.AgnesImage().execute({
        "prompt": "x",
        "operation": "image_edit",
        "reference_urls": [str(tmp_path / "nope.png")],
    })
    assert not missing.success
    assert "不存在" in missing.error


def test_image_t2i_ignores_response_format_and_edit_ignores_return_base64(monkeypatch):
    """防非法组合：文生图忽略 response_format 输入；编辑忽略 return_base64 输入。"""
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    seen: list[dict] = []

    def fake_post(url, payload, headers=None, timeout=300):
        seen.append(payload)
        return {"created": 1, "data": [{"url": "https://cdn.example/o.png", "b64_json": None, "revised_prompt": None}]}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    r1 = agnes.AgnesImage().execute({"prompt": "x", "response_format": "b64_json"})
    assert r1.success
    assert seen[0]["extra_body"] == {"response_format": "url"}
    assert "return_base64" not in seen[0]

    r2 = agnes.AgnesImage().execute({
        "prompt": "x",
        "operation": "image_edit",
        "reference_urls": ["https://cdn.example/in.png"],
        "return_base64": True,
    })
    assert r2.success
    assert "return_base64" not in seen[1]
    assert seen[1]["extra_body"]["response_format"] == "url"


def test_image_and_video_estimate_cost_zero(monkeypatch):
    """免费期：图/视频 estimate_cost 均报 0（预算门禁对 0 安全旁路）。"""
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    assert agnes.AgnesImage().estimate_cost({"size": "4K", "reference_urls": ["u"] * 6}) == 0.0
    assert agnes.AgnesVideo().estimate_cost({"seconds": 12}) == 0.0


def test_pack_agnes_image_rejects_bad_suffix(tmp_path):
    bad = tmp_path / "ref.xyz"
    bad.write_bytes(b"junk")
    packed, errors = agnes.pack_agnes_image([str(bad)])
    assert packed == []
    assert errors and "不受支持" in errors[0]

    packed, errors = agnes.pack_agnes_image(["https://x/a.png", "", "https://x/a.png"])
    assert errors == []
    assert packed == ["https://x/a.png"]


# ---- Video 2.5 Flash：aspect_ratio / audios 上限 ----

def test_v25_aspect_ratio_passthrough_and_garbage_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    posted: list[dict] = []

    def fake_post(url, payload, headers=None, timeout=180):
        posted.append(payload)
        return {"id": "v", "status": "completed", "url": "https://cdn.example/v.mp4"}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    monkeypatch.setattr(agnes, "_download", lambda url, dest: str(dest))

    agnes.AgnesVideo().execute({
        "prompt": "雨夜", "seconds": 5, "aspect_ratio": "9:16",
        "output_path": str(tmp_path / "a.mp4"),
    })
    assert posted[0]["aspect_ratio"] == "9:16"
    assert "2:3" not in agnes.AGNES_VIDEO_RATIOS

    agnes.AgnesVideo().execute({
        "prompt": "雨夜", "seconds": 5, "aspect_ratio": "2:3",
        "output_path": str(tmp_path / "b.mp4"),
    })
    assert posted[1]["aspect_ratio"] == "16:9"  # 非法（仅图片合法）落回默认


def test_v25_caps_audios_at_three_with_warning(monkeypatch, tmp_path):
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    posted: list[dict] = []

    def fake_post(url, payload, headers=None, timeout=180):
        posted.append(payload)
        return {"id": "v", "status": "completed", "url": "https://cdn.example/v.mp4"}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    monkeypatch.setattr(agnes, "_download", lambda url, dest: str(dest))

    audios = [f"https://cdn.example/a{i}.mp3" for i in range(1, 6)]
    result = agnes.AgnesVideo().execute({
        "prompt": "广场", "seconds": 5, "audios": audios,
        "output_path": str(tmp_path / "v.mp4"),
    })
    assert result.success
    payload = posted[0]
    assert payload["mode"] == "reference"
    assert payload["audios"] == audios[:3]
    assert "<Audio 3>" in payload["prompt"]
    assert "<Audio 4>" not in payload["prompt"]
    assert any("audio" in w for w in (result.meta or {}).get("warnings") or [])


def test_v25_reference_keeps_images_at_five(monkeypatch, tmp_path):
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    posted: list[dict] = []

    def fake_post(url, payload, headers=None, timeout=180):
        posted.append(payload)
        return {"id": "v", "status": "completed", "url": "https://cdn.example/v.mp4"}

    monkeypatch.setattr(agnes, "post_json", fake_post)
    monkeypatch.setattr(agnes, "_download", lambda url, dest: str(dest))

    urls = [f"https://cdn.example/p{i}.png" for i in range(1, 8)]
    result = agnes.AgnesVideo().execute({
        "prompt": "广场", "seconds": 5, "images": urls,
        "output_path": str(tmp_path / "v.mp4"),
    })
    assert result.success
    assert posted[0]["images"] == urls[:5]
    assert agnes.AGNES_IMAGE_RATIOS == frozenset(agnes._RATIOS)


def test_http_error_status_propagated_for_429(monkeypatch, tmp_path):
    """429 状态码必须进 meta["http_status"]，供上层退避（不做字符串匹配）。"""
    from montage.providers.http import HttpError

    monkeypatch.setenv("AGNES_CN_API_KEY", "k")

    def boom(*_a, **_k):
        raise HttpError(429, "https://api.example/v1/videos", "rate limited")

    monkeypatch.setattr(agnes, "post_json", boom)
    r_img = agnes.AgnesImage().execute({"prompt": "x"})
    assert not r_img.success
    assert (r_img.meta or {}).get("http_status") == 429
    r_vid = agnes.AgnesVideo().execute({"prompt": "x", "seconds": 5})
    assert not r_vid.success
    assert (r_vid.meta or {}).get("http_status") == 429
