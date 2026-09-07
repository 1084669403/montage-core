"""进化方案 P1：方舟 Seedance / 可灵 Omni·2.1 适配器合同。零真实调用。"""

from __future__ import annotations

import json
from io import StringIO

from montage.engine.policy import VIDEO_LOOP_PROVIDERS
from montage.providers.capabilities import (
    apply_kling_omni_refs,
    apply_seedance_content,
    image_caps,
    video_caps,
    video_surface,
)
from montage.providers.jimeng import JimengVideo
from montage.providers.kling import KlingVideo, kling_jwt, kling_authorization
from montage.providers.seedance_ark import SeedanceVideo
from montage.providers.selectors import VideoSelector
from montage.toolbase import ToolStatus


def test_apply_seedance_content_first_last_adaptive():
    payload: dict = {"ratio": "9:16"}
    notes = apply_seedance_content(
        payload,
        text="雨夜街道，{站住}",
        first_url="http://x/f.png",
        last_url="http://x/t.png",
        refs=[{"url": "http://x/ref.png"}],
        caps=video_surface("seedance_25"),
    )
    assert payload["watermark"] is False
    assert payload["ratio"] == "adaptive"
    roles = [item.get("role") for item in payload["content"] if item.get("type") != "text"]
    assert roles == ["first_frame", "last_frame"]
    assert any("互斥" in n for n in notes)
    text_item = next(item for item in payload["content"] if item["type"] == "text")
    assert "雨夜街道" in text_item["text"]


def test_apply_seedance_last_without_first_dropped():
    payload: dict = {}
    notes = apply_seedance_content(payload, text="x", last_url="http://x/t.png")
    assert payload["content"][0]["type"] == "text"
    assert all(item.get("role") != "last_frame" for item in payload["content"])
    assert any("首帧" in n for n in notes)


def test_apply_kling_omni_refs_requires_first_for_tail():
    payload: dict = {}
    notes = apply_kling_omni_refs(payload, last_url="http://x/t.png")
    types = [item["type"] for item in payload.get("contents") or []]
    assert "last_frame" not in types
    assert "image_list" not in payload
    assert any("首帧" in n for n in notes)
    apply_kling_omni_refs(
        payload,
        first_url="http://x/f.png",
        last_url="http://x/t.png",
        refs=[{"url": "http://x/face.png"}],
    )
    types = [item["type"] for item in payload["contents"]]
    assert types == ["first_frame", "last_frame", "refer_image"]
    first = next(c for c in payload["contents"] if c["type"] == "first_frame")
    last = next(c for c in payload["contents"] if c["type"] == "last_frame")
    ref = next(c for c in payload["contents"] if c["type"] == "refer_image")
    assert "id" not in first
    assert "id" not in last
    assert ref["id"] == "image_1"
    assert payload["options"]["watermark_info"]["enabled"] is False


def test_seedance_provider_is_ark_not_volcengine():
    tool = SeedanceVideo()
    assert tool.provider == "ark"
    assert tool.env_keys == ("ARK_API_KEY",)
    assert VIDEO_LOOP_PROVIDERS["volcengine"] == ["volcengine"]
    assert "ark" not in VIDEO_LOOP_PROVIDERS["volcengine"]
    assert video_caps(tool="jimeng_video")["duration_policy"]["values"] == [5, 10]
    assert video_caps(tool="seedance_video")["duration_policy"]["kind"] == "range"
    assert video_caps(provider="ark")["api_id"] == "seedance_25"
    assert video_caps(provider="volcengine")["api_id"] == "jimeng_v30"
    assert video_surface("seedance_25")["wired"] is True
    assert video_surface("seedance_25")["tool"] == "seedance_video"


def test_seedance_cost_not_v30_rate():
    seedance = SeedanceVideo().estimate_cost({"seconds": 5, "api_id": "seedance_25"})
    jimeng = JimengVideo().estimate_cost({"seconds": 5, "prompt": "x"})
    assert seedance > 0
    assert seedance != jimeng


def test_seedance_needs_ark_key_and_model(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    monkeypatch.delenv("SEEDANCE_MODEL", raising=False)
    monkeypatch.delenv("ARK_VIDEO_MODEL", raising=False)
    tool = SeedanceVideo()
    assert tool.get_status() == ToolStatus.NEEDS_CONFIG
    assert not tool.execute({"prompt": "雨"}).success
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    assert tool.get_status() == ToolStatus.AVAILABLE
    result = tool.execute({"prompt": "雨"})
    assert not result.success
    assert "SEEDANCE_MODEL" in (result.error or "")


def test_seedance_http_contract(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("SEEDANCE_MODEL", "ep-test")
    calls = []

    def fake_post(url, payload, headers=None, timeout=180):
        calls.append(("post", url, payload, headers))
        return {"id": "cgt-1"}

    def fake_get(url, headers=None, timeout=60, params=None):
        calls.append(("get", url))
        return {
            "id": "cgt-1",
            "status": "succeeded",
            "content": {"video_url": "http://x/v.mp4", "last_frame_url": "http://x/last.png"},
        }

    import montage.providers.seedance_ark as ark

    monkeypatch.setattr(ark, "post_json", fake_post)
    monkeypatch.setattr(ark, "get_json", fake_get)
    result = SeedanceVideo().execute({
        "prompt": "雨夜",
        "seconds": 5,
        "image_url": "http://x/f.png",
        "last_frame_url": "http://x/t.png",
        "poll_interval_seconds": 0,
        "timeout_seconds": 30,
    })
    assert result.success
    post = next(c for c in calls if c[0] == "post")
    assert post[3]["Authorization"] == "Bearer ark-key"
    assert "/api/v3/contents/generations/tasks" in post[1]
    body = post[2]
    assert body["watermark"] is False
    assert body["return_last_frame"] is True
    assert body["generate_audio"] is True
    assert body["ratio"] == "adaptive"
    assert body["model"] == "ep-test"
    assert result.data["video_url"] == "http://x/v.mp4"
    assert result.data["last_frame_url"] == "http://x/last.png"


def test_selector_volcengine_still_jimeng(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("VOLC_ACCESSKEY", "ak")
    monkeypatch.setenv("VOLC_SECRETKEY", "sk")
    sel = VideoSelector()
    sel._route_cache.clear()
    picked = sel._pick({"allowed_providers": ["volcengine"], "prompt": "x"})
    assert picked is not None
    assert picked.name == "jimeng_video"
    sel._route_cache.clear()
    ark = sel._pick({"allowed_providers": ["ark"], "prompt": "x"})
    assert ark is not None
    assert ark.name == "seedance_video"


def test_kling_jwt_and_bearer(monkeypatch):
    monkeypatch.setenv("KLING_API_KEY", "ak")
    monkeypatch.delenv("KLING_API_SECRET", raising=False)
    assert kling_authorization() == "Bearer ak"
    token = kling_jwt("ak", "sk", now=1_700_000_000)
    header, payload, _sig = token.split(".")
    import base64

    def dec(part: str) -> dict:
        pad = "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part + pad))

    assert dec(header)["alg"] == "HS256"
    assert dec(payload)["iss"] == "ak"
    monkeypatch.setenv("KLING_API_SECRET", "sk")
    assert kling_authorization(now=1_700_000_000).startswith("Bearer eyJ")


def test_kling_v1_default_endpoint(monkeypatch):
    monkeypatch.setenv("KLING_API_KEY", "k")
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
    assert "text2video" in calls[0]
    assert "omni-video" not in calls[0]


def test_kling_omni_and_21_contract(monkeypatch):
    monkeypatch.setenv("KLING_API_KEY", "k")
    monkeypatch.setenv("KLING_OMNI_MODEL", "kling-v3-omni")
    monkeypatch.setenv("KLING_I2V_MODEL", "kling-v2-1")
    import montage.providers.kling as kling_mod

    no_first = KlingVideo().execute({"prompt": "走", "api_id": "kling_i2v_21_pro"})
    assert not no_first.success
    assert "无首帧" in (no_first.error or "")

    captured = {}

    def fake_post(url, payload, headers=None, timeout=180):
        captured["url"] = url
        captured["payload"] = payload
        if "/omni-video/" in url:
            return {"data": {"id": "omni1"}}
        return {"data": {"task_id": "t21"}}

    def fake_get(url, headers=None, timeout=60, params=None):
        if "tasks" in url:
            return {
                "data": [{
                    "id": "omni1",
                    "status": "succeeded",
                    "outputs": [{"type": "video", "url": "http://x/omni.mp4"}],
                }],
            }
        return {
            "data": {
                "task_status": "succeed",
                "task_result": {"videos": [{"url": "http://x/omni.mp4"}]},
            }
        }

    monkeypatch.setattr(kling_mod, "post_json", fake_post)
    monkeypatch.setattr(kling_mod, "get_json", fake_get)
    result = KlingVideo().execute({
        "prompt": "<<<image_1>>> 挥手",
        "api_id": "kling_omni_30",
        "image_url": "http://x/f.png",
        "last_frame_url": "http://x/t.png",
        "sound": "on",
        "poll_interval_seconds": 0,
        "timeout_seconds": 30,
    })
    assert result.success
    assert captured["url"].endswith("/omni-video/kling-3.0-omni")
    assert "/v1/videos/" not in captured["url"]
    assert captured["payload"]["options"]["watermark_info"]["enabled"] is False
    assert captured["payload"]["settings"]["audio"] == "native"
    assert captured["payload"]["settings"]["multi_shot"] is False
    types = [item["type"] for item in captured["payload"]["contents"]]
    assert types[:3] == ["prompt", "first_frame", "last_frame"]
    assert "id" not in captured["payload"]["contents"][1]

    result21 = KlingVideo().execute({
        "prompt": "走",
        "api_id": "kling_i2v_21_pro",
        "image_url": "http://x/f.png",
        "poll_interval_seconds": 0,
        "timeout_seconds": 30,
    })
    assert result21.success
    assert "image2video" in captured["url"]
    assert captured["payload"]["mode"] == "pro"
    assert captured["payload"]["image"] == "http://x/f.png"


def test_kling_image_reference_caps():
    img = image_caps(tool="kling_image")
    assert img["image_reference"] is True
    assert "image_list" in img["ref_url_fields"]


def test_doctor_lists_ark_key_separately():
    from contextlib import redirect_stdout
    from montage.cli import main

    buf = StringIO()
    with redirect_stdout(buf):
        code = main(["doctor", "--json"])
    assert code == 0
    data = json.loads(buf.getvalue())
    video = next(c for c in data["capabilities"] if c["capability"] == "video_generation")
    names = {t["name"]: t for t in video["tools"]}
    assert "seedance_video" in names
    assert names["seedance_video"]["provider"] == "ark"
    assert names["seedance_video"]["env_keys"] == ["ARK_API_KEY"]
    assert "VOLC_ACCESSKEY" in names["jimeng_video"]["env_keys"]
    surfaces = {row["api_id"]: row for row in data["video_surfaces"]}
    assert surfaces["seedance_25"]["tool"] == "seedance_video"
    assert surfaces["seedance_25"]["wired"] is True
