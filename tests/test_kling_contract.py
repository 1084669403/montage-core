"""可灵 3.0 官方契约第 0–2 波：装箱 / poll / 视频四分 / Image Omni。零真实 HTTP。"""

from __future__ import annotations

import base64

from montage.providers.kling import (
    KLING_OMNI_CITE_FRAMES,
    KLING_VIDEO_PATHS,
    _omni_task_id,
    _omit_blank_external_id,
    _open_base,
    _open_task_video_url,
    _poll_open_tasks,
    _v1_base,
    kling_attempt_id,
    kling_image_omni_model,
    kling_image_omni_url,
    pack_kling_media,
)

# 1×1 JPEG，体积远小于 10MB。
_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////"
    "////////////////////////////////////////////////////////////wAALCAABAAEBAREA"
    "/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


def test_open_and_v1_base_defaults(monkeypatch):
    monkeypatch.delenv("KLING_OPEN_BASE", raising=False)
    assert _open_base() == "https://api-beijing.klingai.com"
    assert _v1_base() == "https://api-beijing.klingai.com/v1"


def test_open_base_env_override(monkeypatch):
    monkeypatch.setenv("KLING_OPEN_BASE", "https://example.test/")
    assert _open_base() == "https://example.test"
    assert _v1_base() == "https://example.test/v1"


def test_video_paths_are_path_as_model():
    assert KLING_VIDEO_PATHS["kling_omni_30"] == "/omni-video/kling-3.0-omni"
    assert KLING_VIDEO_PATHS["kling_t2v_30"] == "/text-to-video/kling-3.0"
    assert KLING_VIDEO_PATHS["kling_i2v_30"] == "/image-to-video/kling-3.0"
    assert KLING_VIDEO_PATHS["kling_motion_30"] == "/motion-control/kling-3.0"
    for path in KLING_VIDEO_PATHS.values():
        assert not path.startswith("/v1/")
        assert "/videos/" not in path
    assert KLING_OMNI_CITE_FRAMES is False


def test_image_omni_url_default_and_override(monkeypatch):
    monkeypatch.delenv("KLING_OPEN_BASE", raising=False)
    monkeypatch.delenv("KLING_IMAGE_OMNI_PATH", raising=False)
    assert kling_image_omni_url() == "https://api-beijing.klingai.com/v1/images/omni-image"
    monkeypatch.setenv("KLING_IMAGE_OMNI_PATH", "/omni-image/kling-image-3.0-omni")
    assert kling_image_omni_url() == "https://api-beijing.klingai.com/omni-image/kling-image-3.0-omni"


def test_image_omni_model_defaults_to_v3_omni(monkeypatch):
    monkeypatch.delenv("KLING_IMAGE_OMNI_MODEL", raising=False)
    assert kling_image_omni_model() == "kling-v3-omni"
    monkeypatch.setenv("KLING_IMAGE_OMNI_MODEL", "kling-image-o1")
    assert kling_image_omni_model() == "kling-image-o1"
    monkeypatch.setenv("KLING_IMAGE_OMNI_MODEL", "Kling Image 3.0 Omni")
    assert kling_image_omni_model() == "kling-v3-omni"
    monkeypatch.setenv("KLING_IMAGE_OMNI_MODEL", "kling-image-3.0-omni")
    assert kling_image_omni_model() == "kling-v3-omni"
    monkeypatch.setenv("KLING_IMAGE_OMNI_MODEL", "api-key-kling-not-a-model")
    assert kling_image_omni_model() == "kling-v3-omni"


def test_pack_https_passthrough():
    packed = pack_kling_media("https://cdn.example/a.png")
    assert packed["ok"] is True
    assert packed["url"] == "https://cdn.example/a.png"
    assert packed["via"] == "https"


def test_pack_local_jpeg_raw_base64(tmp_path, monkeypatch):
    monkeypatch.delenv("KLING_MEDIA_B64_FORMAT", raising=False)
    path = tmp_path / "tiny.jpg"
    path.write_bytes(_JPEG)
    packed = pack_kling_media(str(path))
    assert packed["ok"] is True
    assert packed["via"] == "base64"
    assert packed["url"] == base64.b64encode(_JPEG).decode("ascii")
    assert not packed["url"].startswith("data:")


def test_pack_data_uri_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("KLING_MEDIA_B64_FORMAT", "data_uri")
    path = tmp_path / "tiny.jpg"
    path.write_bytes(_JPEG)
    packed = pack_kling_media(str(path))
    assert packed["ok"] is True
    assert packed["url"].startswith("data:image/jpeg;base64,")


def test_pack_oversize_uses_jpeg_compress(tmp_path, monkeypatch):
    import montage.providers.kling as kling_mod

    path = tmp_path / "big.png"
    path.write_bytes(b"\x89PNG" + b"x" * 200)
    monkeypatch.setattr(kling_mod, "_jpeg_bytes_under_limit", lambda src, cap: b"tiny-jpeg")
    packed = pack_kling_media(str(path), max_bytes=50)
    assert packed["ok"] is True
    assert packed["url"] == base64.b64encode(b"tiny-jpeg").decode("ascii")
    assert any("jpeg" in n for n in packed["notes"])


def test_pack_local_video_and_audio_rejected(tmp_path):
    mp4 = tmp_path / "clip.mp4"
    mp3 = tmp_path / "voice.mp3"
    mp4.write_bytes(b"fake")
    mp3.write_bytes(b"fake")
    vid = pack_kling_media(str(mp4), kind="video")
    aud = pack_kling_media(str(mp3), kind="audio")
    assert vid["ok"] is False
    assert aud["ok"] is False
    assert any("https" in n for n in vid["notes"])
    https = pack_kling_media("https://cdn.example/v.mp4", kind="video")
    assert https["ok"] is True


def test_omni_task_id_reads_data_id_not_legacy_task_id():
    assert _omni_task_id({"data": {"id": "new-1", "task_id": "old-1"}}) == "new-1"
    assert _omni_task_id({"task_id": "old-1"}) == ""
    assert _omni_task_id({"data": {"task_id": "old-1"}}) == ""
    assert _omni_task_id({"id": "top"}) == ""


def test_open_task_video_url_skips_audio():
    task = {
        "id": "t1",
        "status": "succeeded",
        "outputs": [
            {"type": "audio", "url": "https://x/a.mp3"},
            {"type": "video", "url": "https://x/v.mp4"},
        ],
    }
    assert _open_task_video_url(task) == "https://x/v.mp4"


def test_poll_open_tasks_succeeded(monkeypatch):
    import montage.providers.kling as kling_mod

    monkeypatch.setenv("KLING_API_KEY", "k")
    calls = []

    def fake_get(url, headers=None, timeout=60, params=None):
        calls.append((url, params))
        return {
            "code": 0,
            "data": [
                {
                    "id": "omni-9",
                    "status": "succeeded",
                    "outputs": [
                        {"type": "audio", "url": "https://x/a.mp3"},
                        {"type": "video", "url": "https://x/v.mp4"},
                    ],
                }
            ],
        }

    monkeypatch.setattr(kling_mod, "get_json", fake_get)
    item = _poll_open_tasks("omni-9", timeout=5, interval=0)
    assert item["id"] == "omni-9"
    assert _open_task_video_url(item) == "https://x/v.mp4"
    assert calls[0][0].endswith("/tasks")
    assert calls[0][1] == {"task_ids": "omni-9"}
    assert "/v1/" not in calls[0][0]


def test_poll_open_tasks_failed(monkeypatch):
    import montage.providers.kling as kling_mod
    from montage.providers.http import HttpError

    monkeypatch.setenv("KLING_API_KEY", "k")

    def fake_get(url, headers=None, timeout=60, params=None):
        return {"data": [{"id": "omni-9", "status": "failed", "message": "风控", "outputs": []}]}

    monkeypatch.setattr(kling_mod, "get_json", fake_get)
    try:
        _poll_open_tasks("omni-9", timeout=5, interval=0)
    except HttpError as exc:
        assert "failed" in str(exc)
    else:
        raise AssertionError("failed 应抛 HttpError")


def test_blank_external_task_id_omitted():
    assert "external_task_id" not in _omit_blank_external_id({"external_task_id": ""})
    assert "external_task_id" not in _omit_blank_external_id({"external_task_id": "  "})
    kept = _omit_blank_external_id({"external_task_id": "sh01:2", "watermark_info": {"enabled": False}})
    assert kept["external_task_id"] == "sh01:2"


def test_attempt_id():
    assert kling_attempt_id("sh01", 3) == "sh01:3"
    assert kling_attempt_id("", 1) is None
    assert kling_attempt_id("sh01") == "sh01:1"


def test_execute_default_still_v1(monkeypatch):
    """默认 api_id 仍 POST /v1/videos/text2video。"""
    from montage.providers.kling import KlingVideo

    monkeypatch.setenv("KLING_API_KEY", "k")
    monkeypatch.delenv("KLING_OPEN_BASE", raising=False)
    calls = []

    def fake_post(url, payload, headers=None, timeout=180):
        calls.append(url)
        return {"task_id": "t1"}

    def fake_get(url, headers=None, timeout=60, params=None):
        return {"task_status": "succeed", "task_result": {"videos": [{"url": "http://x/v.mp4"}]}}

    import montage.providers.kling as kling_mod

    monkeypatch.setattr(kling_mod, "post_json", fake_post)
    monkeypatch.setattr(kling_mod, "get_json", fake_get)
    result = KlingVideo().execute({"prompt": "走", "poll_interval_seconds": 0, "timeout_seconds": 30})
    assert result.success
    assert calls[0] == "https://api-beijing.klingai.com/v1/videos/text2video"
    assert "/omni-video/kling-3.0-omni" not in calls[0]


def _types(payload):
    return [item.get("type") for item in payload.get("contents") or []]


def test_omni_payload_shot_defaults():
    from montage.providers.kling import _video_payload

    path, payload = _video_payload("kling_omni_30", {
        "image_url": "https://x/f.png",
        "last_frame_url": "https://x/t.png",
        "refs": [{"url": "https://x/scene.png"}],
        "elements": [{"element_id": 162}],
        "duration": "5",
        "shot_id": "sh01",
        "attempt": 2,
    }, "雨夜街道")
    assert path == "/omni-video/kling-3.0-omni"
    assert payload["settings"]["multi_shot"] is False
    assert payload["settings"]["duration"] == 5
    assert isinstance(payload["settings"]["duration"], int)
    assert payload["settings"]["resolution"] == "1080p"
    assert payload["settings"]["audio"] == "off"
    assert "aspect_ratio" not in payload["settings"]
    types = _types(payload)
    assert types == ["prompt", "first_frame", "last_frame", "refer_image", "element"]
    first = next(c for c in payload["contents"] if c["type"] == "first_frame")
    last = next(c for c in payload["contents"] if c["type"] == "last_frame")
    ref = next(c for c in payload["contents"] if c["type"] == "refer_image")
    assert "id" not in first
    assert "id" not in last
    assert ref["id"] == "image_1"
    assert payload["options"]["external_task_id"] == "sh01:2"
    assert payload["options"]["watermark_info"]["enabled"] is False


def test_omni_native_only_single_speaker():
    from montage.providers.kling import _video_payload

    _, one = _video_payload("kling_omni_30", {
        "speaker_id": "a", "dialogue": "站住", "duration": 5,
    }, "近景")
    assert one["settings"]["audio"] == "native"
    assert one["settings"]["aspect_ratio"] == "16:9"
    _, many = _video_payload("kling_omni_30", {
        "speaker_ids": ["a", "b"], "dialogue": "站住", "duration": 5,
    }, "近景")
    assert many["settings"]["audio"] == "off"
    _, none = _video_payload("kling_omni_30", {"duration": 5}, "空镜")
    assert none["settings"]["audio"] == "off"


def test_omni_feature_and_base_mutex():
    from montage.providers.kling import _video_payload

    path, feat = _video_payload("kling_omni_30", {
        "video_url": "https://x/v.mp4",
        "refer_type": "feature",
        "duration": 8,
        "image_url": "https://x/f.png",
    }, "改眼镜")
    assert path == "/omni-video/kling-3.0-omni"
    assert feat["settings"]["multi_shot"] is True
    assert feat["settings"]["audio"] == "off"
    assert feat["contents"][0]["text"].startswith("shot 1, 8,")
    assert _types(feat) == ["prompt", "feature_video"]
    _, base = _video_payload("kling_omni_30", {
        "video_url": "https://x/v.mp4",
        "sound": "on",
        "image_url": "https://x/f.png",
    }, "改眼镜")
    assert base["settings"]["multi_shot"] is False
    assert base["settings"]["audio"] == "original"
    assert "duration" not in base["settings"]
    assert _types(base) == ["prompt", "base_video"]


def test_omni_feature_over_10s_rejected():
    from montage.providers.kling import _video_payload

    try:
        _video_payload("kling_omni_30", {
            "video_url": "https://x/v.mp4", "refer_type": "feature", "duration": 12,
        }, "长镜")
    except ValueError as exc:
        assert "feature" in str(exc)
    else:
        raise AssertionError("11–15s feature 应拒绝")


def test_t2v_i2v_motion_payloads(monkeypatch):
    from montage.providers.kling import _video_payload

    t_path, t2v = _video_payload("kling_t2v_30", {"duration": 4}, "无图兜底")
    assert t_path == "/text-to-video/kling-3.0"
    assert "contents" not in t2v
    assert t2v["prompt"] == "无图兜底"
    assert t2v["settings"]["multi_shot"] is False
    assert t2v["settings"]["duration"] == 4
    i_path, i2v = _video_payload("kling_i2v_30", {
        "image_url": "https://x/f.png",
        "last_frame_url": "https://x/t.png",
        "elements": [{"element_id": "9"}],
        "duration": 6,
    }, "真插值")
    assert i_path == "/image-to-video/kling-3.0"
    assert _types(i2v) == ["prompt", "first_frame", "last_frame", "element"]
    assert "aspect_ratio" not in i2v["settings"]
    assert "id" not in next(c for c in i2v["contents"] if c["type"] == "first_frame")
    monkeypatch.setenv("KLING_VIDEO_RESOLUTION", "4k")
    m_path, motion = _video_payload("kling_motion_30", {
        "image_url": "https://x/char.png",
        "motion_video_url": "https://x/act.mp4",
    }, "跟着跳")
    assert m_path == "/motion-control/kling-3.0"
    assert motion["settings"]["resolution"] == "1080p"
    assert motion["settings"]["character_orientation"] == "image"
    assert "duration" not in motion["settings"]
    assert _types(motion) == ["prompt", "image", "video"]


def test_omni_execute_posts_open_path(monkeypatch):
    from montage.providers.kling import KlingVideo

    monkeypatch.setenv("KLING_API_KEY", "k")
    monkeypatch.delenv("KLING_OMNI_MODEL", raising=False)
    captured = {}

    def fake_post(url, payload, headers=None, timeout=180):
        captured["url"] = url
        captured["payload"] = payload
        return {"data": {"id": "omni-new"}}

    def fake_get(url, headers=None, timeout=60, params=None):
        captured["poll"] = (url, params)
        return {
            "data": [{
                "id": "omni-new",
                "status": "succeeded",
                "outputs": [
                    {"type": "audio", "url": "https://x/a.mp3"},
                    {"type": "video", "url": "https://x/v.mp4"},
                ],
            }],
        }

    import montage.providers.kling as kling_mod

    monkeypatch.setattr(kling_mod, "post_json", fake_post)
    monkeypatch.setattr(kling_mod, "get_json", fake_get)
    result = KlingVideo().execute({
        "prompt": "挥手",
        "api_id": "kling_omni_30",
        "image_url": "https://x/f.png",
        "poll_interval_seconds": 0,
        "timeout_seconds": 30,
    })
    assert result.success, result.error
    assert captured["url"] == "https://api-beijing.klingai.com/omni-video/kling-3.0-omni"
    assert "/v1/videos/" not in captured["url"]
    assert captured["payload"]["settings"]["multi_shot"] is False
    assert result.data["video_url"] == "https://x/v.mp4"
    assert captured["poll"][1] == {"task_ids": "omni-new"}


def test_estimate_cost_credits_not_zero(monkeypatch):
    from montage.providers.kling import KlingVideo

    monkeypatch.delenv("KLING_USD_PER_CREDIT", raising=False)
    monkeypatch.delenv("KLING_VIDEO_RESOLUTION", raising=False)
    tool = KlingVideo()
    native = tool.estimate_cost({
        "api_id": "kling_omni_30", "duration": 5, "speaker_id": "a", "dialogue": "站住",
    })
    off = tool.estimate_cost({"api_id": "kling_omni_30", "duration": 5})
    assert native > 0
    assert off > 0
    assert native == round(5 * 12 * 0.02, 4)
    assert off == round(5 * 8 * 0.02, 4)
    monkeypatch.setenv("KLING_USD_PER_CREDIT", "0")
    assert tool.estimate_cost({"api_id": "kling_omni_30", "duration": 5}) > 0


def test_image_omni_payload_default_2k_single(monkeypatch):
    from montage.providers.kling import _image_omni_payload

    monkeypatch.setenv("KLING_IMAGE_OMNI_MODEL", "kling-v3-omni")
    monkeypatch.delenv("KLING_IMAGE_RESOLUTION", raising=False)
    payload = _image_omni_payload({
        "image_url": "https://x/sheet.png",
        "refs": [{"url": "https://x/prop.png"}],
        "elements": [{"element_id": 7}],
    }, "白底拼板")
    assert payload["model_name"] == "kling-v3-omni"
    assert payload["resolution"] == "2k"
    assert payload["result_type"] == "single"
    assert payload["aspect_ratio"] == "16:9"
    assert "n" not in payload
    assert payload["image_list"] == [
        {"image": "https://x/sheet.png"},
        {"image": "https://x/prop.png"},
    ]
    assert payload["element_list"] == [{"element_id": "7"}]
    assert payload["watermark_info"]["enabled"] is False
    assert "image" not in payload


def test_image_omni_missing_model_uses_v3_omni(monkeypatch):
    from montage.providers.kling import _image_omni_payload

    monkeypatch.delenv("KLING_IMAGE_OMNI_MODEL", raising=False)
    payload = _image_omni_payload({}, "白底")
    assert payload["model_name"] == "kling-v3-omni"


def test_image_omni_rejects_v1(monkeypatch):
    from montage.providers.kling import KlingImage, _image_omni_payload

    monkeypatch.setenv("KLING_API_KEY", "k")
    monkeypatch.setenv("KLING_IMAGE_OMNI_MODEL", "kling-v1")
    try:
        _image_omni_payload({}, "x")
    except ValueError as exc:
        assert "kling-v1" in str(exc)
    else:
        raise AssertionError("kling-v1 应报错")
    result = KlingImage().execute({"prompt": "白底", "poll_interval_seconds": 0})
    assert not result.success
    assert "kling-v1" in (result.error or "")


def test_image_omni_execute_v1_poll(monkeypatch):
    from montage.providers.kling import KlingImage

    monkeypatch.setenv("KLING_API_KEY", "k")
    monkeypatch.setenv("KLING_IMAGE_OMNI_MODEL", "kling-v3-omni")
    monkeypatch.delenv("KLING_IMAGE_OMNI_POLL", raising=False)
    monkeypatch.delenv("KLING_IMAGE_OMNI_PATH", raising=False)
    captured = {}

    def fake_post(url, payload, headers=None, timeout=180):
        captured["url"] = url
        captured["payload"] = payload
        return {"task_id": "img-1"}

    def fake_get(url, headers=None, timeout=60, params=None):
        captured["poll"] = url
        return {
            "task_status": "succeed",
            "task_result": {"images": [{"url": "https://x/look.png"}]},
        }

    import montage.providers.kling as kling_mod

    monkeypatch.setattr(kling_mod, "post_json", fake_post)
    monkeypatch.setattr(kling_mod, "get_json", fake_get)
    result = KlingImage().execute({
        "prompt": "白底定妆",
        "poll_interval_seconds": 0,
        "timeout_seconds": 30,
    })
    assert result.success, result.error
    assert captured["url"] == "https://api-beijing.klingai.com/v1/images/omni-image"
    assert "/text2image" not in captured["url"]
    assert captured["payload"]["resolution"] == "2k"
    assert captured["payload"]["result_type"] == "single"
    assert captured["poll"].endswith("/omni-image/img-1")
    assert result.data["urls"] == ["https://x/look.png"]


def test_image_omni_execute_tasks_poll(monkeypatch):
    from montage.providers.kling import KlingImage

    monkeypatch.setenv("KLING_API_KEY", "k")
    monkeypatch.setenv("KLING_IMAGE_OMNI_MODEL", "kling-v3-omni")
    monkeypatch.setenv("KLING_IMAGE_OMNI_POLL", "tasks")
    captured = {}

    def fake_post(url, payload, headers=None, timeout=180):
        captured["url"] = url
        return {"data": {"id": "img-open"}}

    def fake_get(url, headers=None, timeout=60, params=None):
        captured["poll"] = (url, params)
        return {
            "data": [{
                "id": "img-open",
                "status": "succeeded",
                "outputs": [
                    {"type": "video", "url": "https://x/no.mp4"},
                    {"type": "image", "url": "https://x/still.png"},
                ],
            }],
        }

    import montage.providers.kling as kling_mod

    monkeypatch.setattr(kling_mod, "post_json", fake_post)
    monkeypatch.setattr(kling_mod, "get_json", fake_get)
    result = KlingImage().execute({
        "prompt": "空镜",
        "poll_interval_seconds": 0,
        "timeout_seconds": 30,
    })
    assert result.success, result.error
    assert result.data["urls"] == ["https://x/still.png"]
    assert captured["poll"][0].endswith("/tasks")
    assert captured["poll"][1] == {"task_ids": "img-open"}


def test_image_cost_2k_not_zero(monkeypatch):
    from montage.providers.kling import KlingImage

    monkeypatch.delenv("KLING_USD_PER_CREDIT", raising=False)
    monkeypatch.delenv("KLING_IMAGE_RESOLUTION", raising=False)
    tool = KlingImage()
    single = tool.estimate_cost({"prompt": "a"})
    series = tool.estimate_cost({"prompt": "a", "result_type": "series", "series_amount": 4})
    assert single > 0
    assert series > single
    assert single == round(4 * 0.02, 4)
    monkeypatch.setenv("KLING_USD_PER_CREDIT", "0")
    assert tool.estimate_cost({"prompt": "a"}) > 0


def test_element_crud_payload_drops_face(monkeypatch):
    from montage.providers.kling import element_slots_from_crops, kling_create_element

    slots = element_slots_from_crops({
        "portrait": "https://x/p.png",
        "face": "https://x/face.png",
        "side": "https://x/s.png",
        "back": "https://x/b.png",
        "tq": "https://x/t.png",
    })
    assert slots["frontal"] == "https://x/p.png"
    assert slots["refer"] == ["https://x/s.png", "https://x/b.png", "https://x/t.png"]
    assert "face" in slots["discarded"]

    monkeypatch.setenv("KLING_API_KEY", "k")
    captured = {}

    def fake_post(url, payload, headers=None, timeout=180):
        captured["url"] = url
        captured["payload"] = payload
        return {"task_id": "el-1"}

    def fake_get(url, headers=None, timeout=60, params=None):
        return {"task_status": "succeed", "data": {"element_id": 162}}

    import montage.providers.kling as kling_mod

    monkeypatch.setattr(kling_mod, "post_json", fake_post)
    monkeypatch.setattr(kling_mod, "get_json", fake_get)
    result = kling_create_element(
        element_name="阿宁",
        frontal_image=slots["frontal"],
        refer_images=slots["refer"],
        poll_interval_seconds=0,
        timeout_seconds=30,
    )
    assert result["ok"] is True
    assert result["element_id"] == "162"
    assert captured["url"].endswith("/general/advanced-custom-elements")
    body = captured["payload"]
    assert body["reference_type"] == "image_refer"
    assert body["element_image_list"]["frontal_image"] == "https://x/p.png"
    refs = body["element_image_list"]["refer_images"]
    assert [r["image_url"] for r in refs] == ["https://x/s.png", "https://x/b.png", "https://x/t.png"]
    assert all("face" not in str(r) for r in refs)

    captured.clear()
    long = kling_create_element(
        element_name="甲" * 25,
        frontal_image=slots["frontal"],
        refer_images=slots["refer"],
        element_description="乙" * 150,
        poll_interval_seconds=0,
        timeout_seconds=30,
    )
    assert long["ok"] is True
    assert len(captured["payload"]["element_name"]) == 20
    assert len(captured["payload"]["element_description"]) == 100
    assert "element_voice_id" not in captured["payload"]


def test_voice_skips_local_and_clones_https(tmp_path, monkeypatch):
    from montage.providers.kling import kling_create_voice

    local = tmp_path / "line.wav"
    local.write_bytes(b"fake")
    skipped = kling_create_voice(voice_name="阿宁", voice_url=str(local))
    assert skipped["skipped"] is True
    assert skipped["ok"] is False
    assert any("https" in n for n in skipped["notes"])

    monkeypatch.setenv("KLING_API_KEY", "k")
    captured = {}

    def fake_post(url, payload, headers=None, timeout=180):
        captured["url"] = url
        captured["payload"] = payload
        return {"task_id": "vo-1"}

    def fake_get(url, headers=None, timeout=60, params=None):
        return {"task_status": "succeed", "data": {"voice_id": "v-9"}}

    import montage.providers.kling as kling_mod

    monkeypatch.setattr(kling_mod, "post_json", fake_post)
    monkeypatch.setattr(kling_mod, "get_json", fake_get)
    cloned = kling_create_voice(
        voice_name="阿宁",
        voice_url="https://cdn.example/v.wav",
        poll_interval_seconds=0,
        timeout_seconds=30,
    )
    assert cloned["ok"] is True
    assert cloned["voice_id"] == "v-9"
    assert captured["url"].endswith("/general/custom-voices")
    assert captured["payload"]["voice_url"] == "https://cdn.example/v.wav"


def test_look_sheet_cell_fractions():
    from montage.providers.kling import LOOK_SHEET_CELLS, element_slots_from_crops

    assert LOOK_SHEET_CELLS["portrait"] == {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0}
    assert LOOK_SHEET_CELLS["face"]["x"] == 0.5
    assert LOOK_SHEET_CELLS["side"]["x"] == 0.75
    assert LOOK_SHEET_CELLS["back"]["y"] == 0.5
    assert LOOK_SHEET_CELLS["tq"] == {"x": 0.75, "y": 0.5, "w": 0.25, "h": 0.5}
    slots = element_slots_from_crops({k: k for k in LOOK_SHEET_CELLS})
    assert "face" not in slots["refer"]
    assert slots["frontal"] == "portrait"


def test_look_sheet_qc_retry_then_series(tmp_path, monkeypatch):
    from montage.providers import _kling_looksheet as sheet
    from montage.providers.kling import crop_look_sheet, look_sheet_next_action, qc_look_sheet_cell

    assert look_sheet_next_action(qc_ok=True, attempt=1) == "ok"
    assert look_sheet_next_action(qc_ok=False, attempt=1) == "retry_sheet"
    assert look_sheet_next_action(qc_ok=False, attempt=2) == "series"

    src = tmp_path / "look_sheet_c1.png"
    src.write_bytes(_JPEG)

    def fake_crop(src_path, dest, cell):
        dest.write_bytes(_JPEG)
        return True

    monkeypatch.setattr(sheet, "_ffmpeg_crop_cell", fake_crop)
    monkeypatch.setattr(sheet, "_analyze_cell_pixels", lambda path: {
        "ok": True, "width": 100, "height": 100,
        "nonwhite_ratio": 0.01, "touches_edge": {},
    })
    first = crop_look_sheet(src, tmp_path / "crops", "c1")
    assert first["ok"] is False
    assert first["discarded_for_element"] == ["face"]
    assert look_sheet_next_action(qc_ok=first["ok"], attempt=1) == "retry_sheet"
    qc = qc_look_sheet_cell(tmp_path / "crops" / "portrait_c1.png")
    assert qc["ok"] is False
    assert any("过空" in n or "边长" in n or "过小" in n for n in qc["notes"])
    assert look_sheet_next_action(qc_ok=False, attempt=2) == "series"


def test_registry_skips_element_voice_tools():
    from montage.registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    assert reg.get("kling_image") is not None
    assert reg.get("kling_video") is not None
    assert reg.get("kling_element") is None
    assert reg.get("kling_voice") is None


def test_image_selector_prefers_kling(monkeypatch):
    from montage.providers.selectors import ImageSelector

    monkeypatch.setenv("KLING_API_KEY", "k")
    monkeypatch.setenv("AGNES_API_KEY", "a")
    monkeypatch.setenv("AGNES_CN_API_KEY", "a")
    sel = ImageSelector()
    sel._route_cache.clear()
    picked = sel._pick({"prompt": "x"})
    assert picked is not None
    assert picked.name == "kling_image"
    sel._route_cache.clear()
    locked = sel._pick({"prompt": "x", "allowed_providers": ["agnes"]})
    assert locked is not None
    assert locked.name == "agnes_image"


def test_adapter_skips_first_last_for_image_n():
    from montage.providers.prompt_adapter import adapt_visual_prompt

    adapted = adapt_visual_prompt(
        "kling_omni_30",
        {"video_prompt": "雨夜街道"},
        refs=[
            {"type": "first_frame", "url": "https://x/f.png"},
            {"type": "refer_image", "url": "https://x/scene.png"},
            {"type": "element", "element_id": 162},
        ],
        dialogue="站住",
    )
    prompt = adapted["video_prompt"]
    assert "@image_1" in prompt
    assert "@element_1" in prompt
    assert "@image_2" not in prompt
    assert "<<<image_" not in prompt
    assert adapted["valid"] is True
    first = adapt_visual_prompt(
        "kling_omni_30",
        {
            "video_prompt": "@element_1 走路",
            "first_frame_prompt": "<<<object_1>>>站定；<<<image_1>>>锁场景结构",
        },
        refs=[{"type": "element", "element_id": 162}],
    )
    assert "<<<object_1>>>" in first["first_frame_prompt"]
    assert "<<<image_1>>>" in first["first_frame_prompt"]
    assert "<<<image_" not in first["video_prompt"]


def test_element_uncited_is_invalid():
    from montage.providers.capabilities import kling_omni_elements_cited
    from montage.providers.prompt_adapter import adapt_visual_prompt

    ok, notes = kling_omni_elements_cited("雨夜街道", 1)
    assert ok is False
    assert any("工牌" in n for n in notes)
    adapted = adapt_visual_prompt(
        "kling_omni_30",
        {"video_prompt": "雨夜街道"},
        refs=[{"element_id": 9, "type": "element"}],
    )
    assert adapted["valid"] is True
    assert "@element_1" in adapted["video_prompt"]


def test_apply_kling_omni_image_refs_and_video_mutex():
    from montage.providers.capabilities import (
        apply_kling_omni_image_refs,
        apply_kling_omni_refs,
        apply_kling_omni_videos,
    )

    still: dict = {}
    apply_kling_omni_image_refs(still, [{"url": "https://x/a.png"}, {"url": "https://x/b.png"}])
    assert still["image_list"] == [{"image": "https://x/a.png"}, {"image": "https://x/b.png"}]
    assert still["resolution"] == "2k"
    packed: dict = {}
    apply_kling_omni_image_refs(packed, [
        {"kind": "portrait", "element_id": 7, "url": "https://x/p.png"},
        {"kind": "scene_ref", "url": "https://x/s.png"},
    ])
    assert packed["elements"] == [{"element_id": "7"}]
    assert packed["image_list"] == [{"image": "https://x/s.png"}]
    payload: dict = {}
    apply_kling_omni_refs(
        payload,
        prompt="改眼镜",
        first_url="https://x/f.png",
        last_url="https://x/t.png",
    )
    notes = apply_kling_omni_videos(
        payload, video_url="https://x/v.mp4", refer_type="feature", prompt="改眼镜",
    )
    types = [c["type"] for c in payload["contents"]]
    assert types == ["prompt", "feature_video"]
    assert payload["settings"]["audio"] == "off"
    assert payload["settings"]["multi_shot"] is True
    assert any("互斥" in n for n in notes)


def test_kling_force_and_disable_omni_flags(monkeypatch):
    from montage.tools.shot_runner import _route_shot

    monkeypatch.setenv("KLING_DISABLE_OMNI", "1")
    monkeypatch.delenv("KLING_FORCE_V1", raising=False)
    t2v = _route_shot(
        {"shot_id": "s", "shot_kind": "video", "duration_seconds": 5},
        video_loop="kling", vid_prov="kling", vid_name="kling_video",
        has_first_frame=False,
    )
    assert t2v["api_id"] == "kling_t2v_30"
    i2v = _route_shot(
        {"shot_id": "s", "shot_kind": "video", "duration_seconds": 5},
        video_loop="kling", vid_prov="kling", vid_name="kling_video",
        has_first_frame=True,
    )
    assert i2v["api_id"] == "kling_i2v_30"


def test_cast_needs_url_kling_allows_local():
    from montage.tools.shot_runner import _cast_needs_url, _agnes_cast_needs_url

    assert _cast_needs_url({"video_loop": "kling"}) is False
    assert _agnes_cast_needs_url({"video_loop": "kling"}) is False


def test_omni_video_403_does_not_fallback_v1(monkeypatch):
    from montage.providers.http import HttpError
    from montage.providers.kling import KlingVideo

    monkeypatch.setenv("KLING_API_KEY", "k")
    monkeypatch.delenv("KLING_OPEN_BASE", raising=False)
    calls: list[str] = []

    def fake_post(url, payload, headers=None, timeout=180):
        calls.append(url)
        raise HttpError(403, url, "forbidden")

    import montage.providers.kling as kling_mod

    monkeypatch.setattr(kling_mod, "post_json", fake_post)
    result = KlingVideo().execute({
        "prompt": "走",
        "api_id": "kling_omni_30",
        "poll_interval_seconds": 0,
        "timeout_seconds": 30,
    })
    assert not result.success
    assert "403" in (result.error or "")
    assert calls == ["https://api-beijing.klingai.com/omni-video/kling-3.0-omni"]
    assert all("/v1/videos/" not in url for url in calls)


def test_image_omni_403_does_not_change_path(monkeypatch):
    from montage.providers.http import HttpError
    from montage.providers.kling import KlingImage

    monkeypatch.setenv("KLING_API_KEY", "k")
    monkeypatch.setenv("KLING_IMAGE_OMNI_MODEL", "kling-v3-omni")
    monkeypatch.delenv("KLING_IMAGE_OMNI_PATH", raising=False)
    monkeypatch.delenv("KLING_IMAGE_OMNI_POLL", raising=False)
    calls: list[str] = []

    def fake_post(url, payload, headers=None, timeout=180):
        calls.append(url)
        raise HttpError(403, url, "forbidden")

    import montage.providers.kling as kling_mod

    monkeypatch.setattr(kling_mod, "post_json", fake_post)
    result = KlingImage().execute({
        "prompt": "白底",
        "poll_interval_seconds": 0,
        "timeout_seconds": 30,
    })
    assert not result.success
    assert "403" in (result.error or "")
    assert calls == ["https://api-beijing.klingai.com/v1/images/omni-image"]
    assert all("/omni-image/kling-image" not in url for url in calls)


def test_native_audio_loops_still_exclude_kling():
    from montage.engine.policy import NATIVE_AUDIO_LOOPS

    assert "kling" not in NATIVE_AUDIO_LOOPS
    assert "agnes" in NATIVE_AUDIO_LOOPS

