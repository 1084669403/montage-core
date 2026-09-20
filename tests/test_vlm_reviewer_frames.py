"""P0-7b：vlm_reviewer 分段抽帧。

纯逻辑（采样点/降级阶梯/mock post）不碰 ffmpeg；真抽帧那条缺 ffmpeg 直接 skip。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from montage.tools import vlm_reviewer as vr

# --- 采样点（纯函数） ---------------------------------------------------------


def test_sample_timestamps_takes_midpoints():
    assert vr.sample_timestamps(600.0, 3) == [100.0, 300.0, 500.0]


def test_sample_timestamps_single_and_degenerate():
    assert vr.sample_timestamps(600.0, 1) == [300.0]
    assert vr.sample_timestamps(0.0, 3) == []
    assert vr.sample_timestamps(600.0, 0) == []


def test_default_timestamps_skips_images(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    assert vr.default_timestamps(img, 3) == []


def test_extract_frames_missing_media_counts_all_as_missed(tmp_path):
    out = vr.extract_frames(tmp_path / "nope.mp4", [1.0, 2.0])
    assert out["frames"] == []
    assert out["missed"] == [1.0, 2.0]


def test_extract_frames_passes_image_through(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    out = vr.extract_frames(img, [1.0])
    assert out["frames"] == [str(img)]


# --- review_media 接线（mock post，不出网） -----------------------------------


def _fake_post(record: list[dict]):
    def _post(url, payload, **kwargs):
        record.append(payload)
        return {"choices": [{"message": {"content": '{"ok":true,"score":0.9,"issues":[]}'}}]}

    return _post


def test_review_media_without_key_is_skipped(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    report = vr.review_media(media_path=str(tmp_path / "a.png"))
    assert report["skipped"] is True and report["ok"] is False


def test_review_media_explicit_timestamps_sends_multiple_frames(monkeypatch, tmp_path, real_ffmpeg):
    video = _make_video(tmp_path / "clip.mp4", seconds=6)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake")
    sent: list[dict] = []
    report = vr.review_media(
        media_path=str(video),
        mode="first_frame",
        timestamps=[1.0, 3.0, 5.0],
        post_fn=_fake_post(sent),
    )
    images = [c for c in sent[0]["messages"][0]["content"] if c["type"] == "image_url"]
    assert len(images) == 3
    assert report["sampled"] == {
        "mode": "timestamps", "frames": 3, "sampled": [1.0, 3.0, 5.0], "missed": [],
    }


def test_review_media_video_clip_mode_samples_evenly(monkeypatch, tmp_path, real_ffmpeg):
    video = _make_video(tmp_path / "clip.mp4", seconds=6)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake")
    sent: list[dict] = []
    report = vr.review_media(
        media_path=str(video), mode="video_clip", post_fn=_fake_post(sent),
    )
    images = [c for c in sent[0]["messages"][0]["content"] if c["type"] == "image_url"]
    assert len(images) == vr.MAX_FRAMES
    assert report["sampled"]["mode"] == "video_clip"
    assert report["sampled"]["frames"] == vr.MAX_FRAMES
    # 时长为 6s 的视频，三点应落在三段中点（±0.6s 容忍封装时长误差）
    assert all(0.4 <= t <= 5.6 for t in report["sampled"]["sampled"])


def test_review_media_default_mode_keeps_single_frame(monkeypatch, tmp_path, real_ffmpeg):
    """老行为回归：不传 timestamps、mode=first_frame → 仍只发一帧。"""
    video = _make_video(tmp_path / "clip.mp4", seconds=4)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake")
    sent: list[dict] = []
    report = vr.review_media(media_path=str(video), post_fn=_fake_post(sent))
    images = [c for c in sent[0]["messages"][0]["content"] if c["type"] == "image_url"]
    assert len(images) == 1
    assert report["sampled"]["mode"] == "single"


def test_review_media_max_frames_truncates(monkeypatch, tmp_path, real_ffmpeg):
    video = _make_video(tmp_path / "clip.mp4", seconds=6)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake")
    sent: list[dict] = []
    report = vr.review_media(
        media_path=str(video),
        timestamps=[0.5, 1.5, 2.5, 3.5],
        max_frames=2,
        post_fn=_fake_post(sent),
    )
    images = [c for c in sent[0]["messages"][0]["content"] if c["type"] == "image_url"]
    assert len(images) == 2
    assert report["sampled"]["sampled"] == [0.5, 1.5]


def test_review_media_unreachable_timestamps_falls_back_to_warning(monkeypatch, tmp_path, real_ffmpeg):
    """点全抽不出来 → 只给 warning，不抛、不误判成 critical。"""
    video = _make_video(tmp_path / "clip.mp4", seconds=4)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake")
    report = vr.review_media(media_path=str(video), timestamps=[999.0], post_fn=_fake_post([]))
    assert report["ok"] is True
    assert report["sampled"]["frames"] == 0
    assert report["sampled"]["missed"] == [999.0]
    assert [i["kind"] for i in report["issues"]] == ["崩坏"]


def test_frames_prompt_mentions_order_when_multi(monkeypatch, tmp_path, real_ffmpeg):
    video = _make_video(tmp_path / "clip.mp4", seconds=6)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake")
    sent: list[dict] = []
    vr.review_media(media_path=str(video), timestamps=[1.0, 2.0], post_fn=_fake_post(sent))
    text = next(c["text"] for c in sent[0]["messages"][0]["content"] if c["type"] == "text")
    assert "共 2 帧" in text
    # 老纪律：仍然不许输出时间码
    assert "不要输出时间码" in text


# --- helpers ------------------------------------------------------------------


def _make_video(path: Path, *, seconds: float) -> Path:
    import subprocess

    from montage.compose.ffmpeg_engine import check_ffmpeg

    ffmpeg = check_ffmpeg()
    assert ffmpeg is not None
    subprocess.run(
        [
            ffmpeg, "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={seconds}",
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def real_ffmpeg():
    from montage.compose.ffmpeg_engine import check_ffmpeg

    if check_ffmpeg() is None:
        pytest.skip("缺少 ffmpeg")
    return check_ffmpeg()
