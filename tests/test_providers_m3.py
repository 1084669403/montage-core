"""M3 国内供应商测试：状态、输入校验、注册表发现（无网络，不真调 API）。"""

import pytest

from montage.providers import cogvideo, dashscope_image, hunyuan_video, kling, music_gen, piper_tts, seed_audio
from montage.toolbase import ToolStatus


def test_dashscope_image_requires_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    result = dashscope_image.DashscopeImage().execute({"prompt": "测试"})
    assert not result.success
    assert "DASHSCOPE_API_KEY" in result.error


def test_dashscope_image_requires_prompt(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    result = dashscope_image.DashscopeImage().execute({})
    assert not result.success


def test_dashscope_image_status(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    assert dashscope_image.DashscopeImage().get_status() == ToolStatus.AVAILABLE
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    assert dashscope_image.DashscopeImage().get_status() == ToolStatus.NEEDS_CONFIG


def test_wan_video_requires_prompt(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    result = dashscope_image.WanVideo().execute({})
    assert not result.success


def test_wan_video_poll_contract(monkeypatch):
    """万相视频走 DashScope 异步任务模式（与 ASR 同构）。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    calls = []

    def fake_post(url, payload, headers=None, timeout=180):
        calls.append(("post", url))
        return {"output": {"task_id": "t1"}}

    def fake_get(url, headers=None, timeout=60):
        calls.append(("get", url))
        if len([c for c in calls if c[0] == "get"]) == 1:
            return {"output": {"task_status": "RUNNING"}}
        return {"output": {"task_status": "SUCCEEDED", "video_url": "http://x/v.mp4"}}

    monkeypatch.setattr(dashscope_image, "post_json", fake_post)
    monkeypatch.setattr(dashscope_image, "get_json", fake_get)
    result = dashscope_image.WanVideo().execute({
        "prompt": "雨夜城市", "poll_interval_seconds": 0.01, "timeout_seconds": 30,
    })
    assert result.success
    assert result.data["video_url"] == "http://x/v.mp4"
    assert any("video-synthesis" in u for _, u in calls)


def test_cogvideo_requires_key(monkeypatch):
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    result = cogvideo.CogvideoVideo().execute({"prompt": "测试"})
    assert not result.success
    assert "ZHIPU_API_KEY" in result.error


def test_cogvideo_poll_contract(monkeypatch):
    monkeypatch.setenv("ZHIPU_API_KEY", "k")

    def fake_post(url, payload, headers=None, timeout=180):
        return {"id": "gen1", "task_status": "PROCESSING"}

    def fake_get(url, headers=None, timeout=60):
        return {"id": "gen1", "task_status": "SUCCESS",
                "video_result": [{"url": "http://x/v.mp4"}]}

    monkeypatch.setattr(cogvideo, "post_json", fake_post)
    monkeypatch.setattr(cogvideo, "get_json", fake_get)
    result = cogvideo.CogvideoVideo().execute({"prompt": "测试", "poll_interval_seconds": 0.01})
    assert result.success
    assert result.data["video_url"] == "http://x/v.mp4"


def test_kling_requires_key(monkeypatch):
    monkeypatch.delenv("KLING_API_KEY", raising=False)
    result = kling.KlingImage().execute({"prompt": "测试"})
    assert not result.success
    result2 = kling.KlingVideo().execute({"prompt": "测试"})
    assert not result2.success


def test_kling_status(monkeypatch):
    monkeypatch.delenv("KLING_API_KEY", raising=False)
    assert kling.KlingImage().get_status() == ToolStatus.NEEDS_CONFIG


def test_hunyuan_requires_key(monkeypatch):
    monkeypatch.delenv("HUNYUAN_API_KEY", raising=False)
    result = hunyuan_video.HunyuanVideo().execute({"prompt": "测试"})
    assert not result.success
    assert "HUNYUAN_API_KEY" in result.error


def test_piper_status_no_binary(monkeypatch):
    monkeypatch.setattr(piper_tts.PiperTTS, "_binary", staticmethod(lambda: None))
    assert piper_tts.PiperTTS().get_status() == ToolStatus.UNAVAILABLE


def test_piper_requires_text(monkeypatch):
    monkeypatch.setattr(piper_tts.PiperTTS, "_binary", staticmethod(lambda: "piper"))
    result = piper_tts.PiperTTS().execute({})
    assert not result.success


def test_music_gen_needs_config():
    tool = music_gen.MusicGen()
    assert tool.get_status() == ToolStatus.NEEDS_CONFIG
    result = tool.execute({"prompt": "温暖钢琴"})
    assert not result.success
    assert "asset_retriever" in result.error  # 给出替代路径


def test_seed_audio_requires_key(monkeypatch):
    monkeypatch.delenv("DOUBAO_SEED_AUDIO_ACCESSKEY", raising=False)
    monkeypatch.delenv("DOUBAO_SEED_AUDIO_SECRETKEY", raising=False)
    result = seed_audio.SeedAudio().execute({"prompt": "雨声"})
    assert not result.success
    assert "DOUBAO_SEED_AUDIO" in result.error


def test_m3_tools_discovered():
    from montage.registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    for name in ("dashscope_image", "wan_video", "cogvideo_video", "kling_image",
                 "kling_video", "hunyuan_video", "piper_tts", "music_gen", "seed_audio"):
        assert reg.get(name) is not None, name


def test_selector_routes_new_providers(monkeypatch):
    """选型器应能发现新供应商（无密钥时给出明确提示）。"""
    from montage.providers import selectors

    # 无密钥时 video_selector 报"没有可用"而非崩溃
    for k in ("DASHSCOPE_API_KEY", "ZHIPU_API_KEY", "KLING_API_KEY", "HUNYUAN_API_KEY",
              "AGNES_API_KEY", "AGNES_CN_API_KEY", "VOLC_ACCESSKEY", "VOLC_SECRETKEY"):
        monkeypatch.delenv(k, raising=False)
    result = selectors.VideoSelector().execute({"prompt": "x"})
    assert not result.success
