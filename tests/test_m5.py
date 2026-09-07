"""M5 数字人/录屏测试：ScreenRecorder 命令构造（mock）、接口骨架状态。"""

from pathlib import Path

import pytest

from montage.providers.talking_head import LipSync, TalkingHead
from montage.tools import screen_recorder
from montage.tools.screen_recorder import ScreenRecorder


def test_record_screen_fullscreen_command(monkeypatch, tmp_path):
    captured: list[list[str]] = []

    def fake_run(cmd, capture_output=True, text=True, timeout=600):
        import subprocess

        proc = subprocess.CompletedProcess(cmd, 0)
        proc.stderr = ""
        captured.append(cmd)
        return proc

    monkeypatch.setattr(screen_recorder.platform, "system", lambda: "Windows")
    monkeypatch.setattr(screen_recorder.subprocess, "run", fake_run)
    out = tmp_path / "rec.mp4"
    screen_recorder.record_screen(out, duration=10, fps=24)
    assert captured
    cmd = " ".join(captured[0])
    assert "gdigrab" in cmd
    assert "-t 10.00" in cmd or "-t" in cmd
    assert "libx264" in cmd


def test_record_screen_region(monkeypatch, tmp_path):
    captured: list[list[str]] = []

    def fake_run(cmd, capture_output=True, text=True, timeout=600):
        import subprocess

        proc = subprocess.CompletedProcess(cmd, 0)
        proc.stderr = ""
        captured.append(cmd)
        return proc

    monkeypatch.setattr(screen_recorder.subprocess, "run", fake_run)
    screen_recorder.record_screen(tmp_path / "r.mp4", region="1280x720+100+50")
    cmd = " ".join(captured[0])
    assert "-video_size 1280x720" in cmd
    assert "-offset_x 100" in cmd and "-offset_y 50" in cmd


def test_record_screen_window(monkeypatch, tmp_path):
    captured: list[list[str]] = []

    def fake_run(cmd, capture_output=True, text=True, timeout=600):
        import subprocess

        proc = subprocess.CompletedProcess(cmd, 0)
        proc.stderr = ""
        captured.append(cmd)
        return proc

    monkeypatch.setattr(screen_recorder.subprocess, "run", fake_run)
    screen_recorder.record_screen(tmp_path / "w.mp4", window_title="记事本")
    cmd = " ".join(captured[0])
    assert 'title=记事本' in cmd


def test_screen_recorder_requires_output():
    result = ScreenRecorder().execute({})
    assert not result.success


def test_screen_recorder_dispatch(monkeypatch, tmp_path):
    captured: list[list[str]] = []

    def fake_run(cmd, capture_output=True, text=True, timeout=600):
        import subprocess

        proc = subprocess.CompletedProcess(cmd, 0)
        proc.stderr = ""
        captured.append(cmd)
        return proc

    monkeypatch.setattr(screen_recorder.platform, "system", lambda: "Windows")
    monkeypatch.setattr(screen_recorder.subprocess, "run", fake_run)
    result = ScreenRecorder().execute({
        "output_path": str(tmp_path / "rec.mp4"), "mode": "screen", "duration": 5,
    })
    assert result.success
    assert "gdigrab" in " ".join(captured[0])


def test_talking_head_pending():
    result = TalkingHead().execute({"image_url": "http://x/i.png", "text": "你好"})
    assert not result.success
    assert "待联调" in result.error
    assert TalkingHead().get_status().value == "needs_config"


def test_lip_sync_pending():
    result = LipSync().execute({"video_path": "v.mp4", "audio_path": "a.mp3"})
    assert not result.success
    assert "待联调" in result.error


def test_m5_tools_discovered():
    from montage.registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    for name in ("screen_recorder", "talking_head", "lip_sync"):
        assert reg.get(name) is not None, name
