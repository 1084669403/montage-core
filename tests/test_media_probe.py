"""M2 分析工具测试：抽帧/场景检测/视频分析/音频探测/响度/下载。

- FrameSampler / SceneDetect / AudioEnergy：mock subprocess 输出验证解析。
- VideoAnalyzer / AudioProbe：mock ffprobe 输出验证汇总。
- Downloader：mock urlopen 验证下载与校验分支。
"""

import subprocess
from pathlib import Path

import pytest

from montage.tools import audio_probe, downloader, video_probe
from montage.tools.audio_probe import AudioEnergy, AudioProbe
from montage.tools.downloader import Downloader
from montage.tools.video_probe import FrameSampler, SceneDetect, VideoAnalyzer


def _fake_proc(stderr: str, returncode: int = 0):
    proc = subprocess.CompletedProcess([], returncode)
    proc.stderr = stderr
    proc.stdout = ""
    return proc


# ---------------------------------------------------------------------------
# FrameSampler
# ---------------------------------------------------------------------------


def test_frame_sampler_ok(monkeypatch, tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    out_dir = tmp_path / "frames"
    monkeypatch.setattr(video_probe, "_run_capture", lambda cmd, timeout=600: _fake_proc(""))
    result = FrameSampler().execute({"path": str(src), "output_dir": str(out_dir), "fps": 2})
    assert result.success
    assert result.data["count"] == 0  # 无真实帧文件（mock 不产出）


def test_frame_sampler_missing_input(tmp_path):
    result = FrameSampler().execute({"path": str(tmp_path / "nope.mp4"), "output_dir": str(tmp_path)})
    assert not result.success


# ---------------------------------------------------------------------------
# SceneDetect
# ---------------------------------------------------------------------------


def test_scene_detect_parses_pts(monkeypatch, tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    stderr = (
        "[Parsed_showinfo_1 @ 0x1] n: 75 pts: 3000 pts_time:3.5\n"
        "[Parsed_showinfo_1 @ 0x1] n: 150 pts: 6000 pts_time:7.2\n"
    )
    monkeypatch.setattr(video_probe, "_run_capture", lambda cmd, timeout=900: _fake_proc(stderr))
    result = SceneDetect().execute({"path": str(src), "threshold": 0.3})
    assert result.success
    assert result.data["scene_changes"] == [3.5, 7.2]
    assert result.data["count"] == 2


def test_scene_detect_top_k(monkeypatch, tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    stderr = (
        "pts_time:1.0\npts_time:2.0\npts_time:3.0\n"
    )
    monkeypatch.setattr(video_probe, "_run_capture", lambda cmd, timeout=900: _fake_proc(stderr))
    result = SceneDetect().execute({"path": str(src), "top_k": 2})
    assert result.data["scene_changes"] == [1.0, 2.0]


# ---------------------------------------------------------------------------
# VideoAnalyzer
# ---------------------------------------------------------------------------

_INFO = {
    "format": {"duration": "12.5", "size": "123456"},
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
         "r_frame_rate": "30000/1001"},
        {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
    ],
}


def test_video_analyzer_summary(monkeypatch, tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    monkeypatch.setattr(video_probe, "probe", lambda p: _INFO)
    result = VideoAnalyzer().execute({"path": str(src)})
    assert result.success
    d = result.data
    assert d["duration_seconds"] == 12.5
    assert d["video"]["fps"] == 29.97  # 30000/1001
    assert d["video"]["width"] == 1920
    assert d["has_audio"] is True


def test_video_analyzer_no_audio(monkeypatch, tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    info = {"format": {"duration": "5"}, "streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 360, "r_frame_rate": "30/1"},
    ]}
    monkeypatch.setattr(video_probe, "probe", lambda p: info)
    result = VideoAnalyzer().execute({"path": str(src)})
    assert result.data["has_audio"] is False
    assert result.data["video"]["fps"] == 30.0


def test_video_analyzer_bad_rfr():
    assert video_probe._r_frame_rate("30/1") == 30.0
    assert video_probe._r_frame_rate("30000/1001") == 29.97
    assert video_probe._r_frame_rate("nope") is None
    assert video_probe._r_frame_rate(None) is None


# ---------------------------------------------------------------------------
# AudioProbe
# ---------------------------------------------------------------------------


def test_audio_probe(monkeypatch, tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    info = {"streams": [{"codec_type": "audio", "codec_name": "aac",
                         "sample_rate": "48000", "channels": 2, "duration": "3.0"}]}
    monkeypatch.setattr(audio_probe, "probe", lambda p: info)
    result = AudioProbe().execute({"path": str(src)})
    assert result.success
    assert result.data["has_audio"] is True
    assert result.data["codec"] == "aac"
    assert result.data["sample_rate"] == "48000"


# ---------------------------------------------------------------------------
# AudioEnergy
# ---------------------------------------------------------------------------


def test_audio_energy_normal(monkeypatch, tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    stderr = (
        "[Parsed_volumedetect_0 @ 0x1] mean_volume: -23.5 dB\n"
        "[Parsed_volumedetect_0 @ 0x1] max_volume: -2.0 dB\n"
    )
    monkeypatch.setattr(audio_probe.subprocess, "run", lambda cmd, **kw: _fake_proc(stderr))
    result = AudioEnergy().execute({"path": str(src)})
    assert result.success
    assert result.data["mean_volume_db"] == -23.5
    assert result.data["max_volume_db"] == -2.0
    assert "正常" in result.data["note"]


def test_audio_energy_quiet(monkeypatch, tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    stderr = "mean_volume: -40.0 dB\nmax_volume: -20.0 dB\n"
    monkeypatch.setattr(audio_probe.subprocess, "run", lambda cmd, **kw: _fake_proc(stderr))
    result = AudioEnergy().execute({"path": str(src)})
    assert "偏低" in result.data["note"]


def test_audio_energy_hot(monkeypatch, tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    stderr = "mean_volume: -5.0 dB\nmax_volume: 0.0 dB\n"
    monkeypatch.setattr(audio_probe.subprocess, "run", lambda cmd, **kw: _fake_proc(stderr))
    result = AudioEnergy().execute({"path": str(src)})
    assert "削顶" in result.data["note"]


# ---------------------------------------------------------------------------
# Downloader
# ---------------------------------------------------------------------------


def test_downloader_ok(monkeypatch, tmp_path):
    class FakeResp:
        def read(self):
            return b"media-bytes"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(downloader.urllib.request, "urlopen", lambda req, timeout=120: FakeResp())
    result = Downloader().execute({"url": "http://x/a.mp4", "output_path": str(tmp_path / "a.mp4")})
    assert result.success
    assert result.data["size_bytes"] == len(b"media-bytes")
    assert (tmp_path / "a.mp4").read_bytes() == b"media-bytes"


def test_downloader_verify(monkeypatch, tmp_path):
    class FakeResp:
        def read(self):
            return b"x"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(downloader.urllib.request, "urlopen", lambda req, timeout=120: FakeResp())
    monkeypatch.setattr(downloader, "check_ffprobe", lambda: "ffprobe")
    from montage.compose.ffmpeg_engine import probe as real_probe

    monkeypatch.setattr(downloader, "probe", lambda p: {"format": {"duration": 3.0}})
    result = Downloader().execute({"url": "http://x/a.mp4", "output_path": str(tmp_path / "a.mp4"), "verify": True})
    assert result.success
    assert result.data["verified"] is True
    assert result.data["duration_seconds"] == 3.0


def test_downloader_requires_url(tmp_path):
    result = Downloader().execute({"output_path": str(tmp_path / "a.mp4")})
    assert not result.success


def test_downloader_bad_url(monkeypatch, tmp_path):
    def boom(req, timeout=120):
        raise OSError("connection refused")

    monkeypatch.setattr(downloader.urllib.request, "urlopen", boom)
    result = Downloader().execute({"url": "http://127.0.0.1:1/x.mp4", "output_path": str(tmp_path / "a.mp4")})
    assert not result.success
    assert "下载失败" in result.error


def test_download_rejects_file_url(tmp_path):
    from montage.tools.downloader import download_file

    with pytest.raises(RuntimeError, match="http"):
        download_file("file:///C:/Windows/win.ini", tmp_path / "x.bin")


# ---------------------------------------------------------------------------
# 注册表发现
# ---------------------------------------------------------------------------


def test_m2_tools_discovered():
    from montage.registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    for name in ("frame_sampler", "scene_detect", "video_analyzer", "audio_probe", "audio_energy", "downloader"):
        assert reg.get(name) is not None, name
