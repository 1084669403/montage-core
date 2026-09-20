"""P9 生成策略与音频测试：ken_burns / 质量门禁 / ducking·loudnorm / 字幕 / A·B 选优与缓存。

- ken_burns / mix_audio：mock `_run` 验证 ffmpeg 命令构造（沙箱禁止子进程管道）。
- asset_quality_gate：mock 分析输出（stderr 文本）验证检测分支。
- subtitle_builder：纯逻辑（SRT/ASS 生成、断行、时间戳）。
- asset_picker / generation_cache：mock 评分与真实文件缓存逻辑。
"""

import json
from pathlib import Path

import pytest

from montage.compose import ffmpeg_engine as fe
from montage.tools import asset_picker, asset_quality_gate, generation_cache, subtitle_builder


# ---------------------------------------------------------------------------
# C1: ken_burns（zoompan）
# ---------------------------------------------------------------------------


def test_ken_burns_zoom_in_command(monkeypatch, tmp_path):
    img = tmp_path / "img.png"
    img.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    fe.ken_burns(img, tmp_path / "out.mp4", duration=5, zoom="in", pan="right")
    assert len(captured) == 1
    cmd = " ".join(captured[0])
    assert "-loop" in cmd and "zoompan" in cmd
    assert "min(zoom+" in cmd  # zoom in 表达式
    assert "d=150" in cmd  # 5s * 30fps
    assert "fps=30" in cmd
    assert "(on/150)" in cmd  # 向右摇


def test_ken_burns_zoom_out_center(monkeypatch, tmp_path):
    img = tmp_path / "img.png"
    img.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    fe.ken_burns(img, tmp_path / "out.mp4", duration=10, zoom="out", pan="center", fps=24)
    cmd = " ".join(captured[0])
    assert "if(eq(on,0),1.25,max(zoom-" in cmd
    assert "d=240" in cmd  # 10s * 24fps
    assert "iw/2-(iw/zoom/2)" in cmd  # 居中


def test_ken_burns_missing_image_fails(tmp_path):
    from montage.compose.ffmpeg_engine import ComposError

    with pytest.raises(ComposError, match="图片不存在"):
        fe.ken_burns(tmp_path / "nope.png", tmp_path / "o.mp4", 5)


def test_ken_burns_dispatch(monkeypatch, tmp_path):
    img = tmp_path / "img.png"
    img.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 5.0}})
    result = fe.FFmpegCompose().execute({
        "operation": "ken_burns", "image_path": str(img),
        "duration_seconds": 5, "zoom": "in", "pan": "center",
        "output_path": str(tmp_path / "o.mp4"),
    })
    assert result.success
    assert "zoompan" in " ".join(captured[0])


# ---------------------------------------------------------------------------
# C2: asset_quality_gate
# ---------------------------------------------------------------------------

_BLACK_STDERR = "lavfi.signalstats.YAVG=12.5 lavfi.signalstats.YMIN=8.0"
_WHITE_STDERR = "lavfi.signalstats.YAVG=242.0"
_NORMAL_STDERR = "lavfi.signalstats.YAVG=128.0 lavfi.signalstats.YMIN=20.0"


def _fake_probe(duration=5.0):
    def _probe(path):
        return {"format": {"duration": duration}}
    return _probe


def test_gate_missing_file():
    result = asset_quality_gate.check_asset("nope.mp4")
    assert result["ok"] is False
    assert result["issues"][0]["severity"] == "critical"


def test_gate_black_frame(monkeypatch, tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"fake")
    monkeypatch.setattr(asset_quality_gate, "probe", _fake_probe())
    monkeypatch.setattr(asset_quality_gate, "_run_ffmpeg_metadata", lambda p, vf, timeout=60: _BLACK_STDERR)
    monkeypatch.setattr(asset_quality_gate, "_supports_blurdetect", lambda: False)
    result = asset_quality_gate.check_asset(f)
    assert result["ok"] is False
    kinds = {i["kind"] for i in result["issues"]}
    assert "black_frame" in kinds


def test_gate_white_frame(monkeypatch, tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"fake")
    monkeypatch.setattr(asset_quality_gate, "probe", _fake_probe())
    monkeypatch.setattr(asset_quality_gate, "_run_ffmpeg_metadata", lambda p, vf, timeout=60: _WHITE_STDERR)
    monkeypatch.setattr(asset_quality_gate, "_supports_blurdetect", lambda: False)
    result = asset_quality_gate.check_asset(f)
    assert any(i["kind"] == "white_frame" for i in result["issues"])


def test_gate_normal_passes(monkeypatch, tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"fake")
    monkeypatch.setattr(asset_quality_gate, "probe", _fake_probe())
    monkeypatch.setattr(asset_quality_gate, "_run_ffmpeg_metadata", lambda p, vf, timeout=60: _NORMAL_STDERR)
    monkeypatch.setattr(asset_quality_gate, "_supports_blurdetect", lambda: False)
    result = asset_quality_gate.check_asset(f, expected_duration=5)
    assert result["ok"] is True


def test_gate_duration_mismatch(monkeypatch, tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"fake")
    monkeypatch.setattr(asset_quality_gate, "probe", _fake_probe(duration=3.0))
    monkeypatch.setattr(asset_quality_gate, "_run_ffmpeg_metadata", lambda p, vf, timeout=60: _NORMAL_STDERR)
    monkeypatch.setattr(asset_quality_gate, "_supports_blurdetect", lambda: False)
    result = asset_quality_gate.check_asset(f, expected_duration=10)
    assert any(i["kind"] == "duration" for i in result["issues"])


def test_gate_blur_detection(monkeypatch, tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"fake")
    monkeypatch.setattr(asset_quality_gate, "probe", _fake_probe())
    blur_stderr = "lavfi.blurdetect.blur=0.75"

    def fake_metadata(path, vf, timeout=60):
        if "signalstats" in vf:
            return _NORMAL_STDERR
        return blur_stderr

    monkeypatch.setattr(asset_quality_gate, "_run_ffmpeg_metadata", fake_metadata)
    monkeypatch.setattr(asset_quality_gate, "_supports_blurdetect", lambda: True)
    result = asset_quality_gate.check_asset(f)
    assert any(i["kind"] == "blur" for i in result["issues"])


def test_gate_corrupt_file(monkeypatch, tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"fake")

    def boom(path):
        raise RuntimeError("ffprobe 失败")

    monkeypatch.setattr(asset_quality_gate, "probe", boom)
    result = asset_quality_gate.check_asset(f)
    assert result["ok"] is False
    assert result["issues"][0]["kind"] == "corrupt"


def test_gate_tool_dispatch(monkeypatch, tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"fake")
    monkeypatch.setattr(asset_quality_gate, "probe", _fake_probe())
    monkeypatch.setattr(asset_quality_gate, "_run_ffmpeg_metadata", lambda p, vf, timeout=60: _NORMAL_STDERR)
    monkeypatch.setattr(asset_quality_gate, "_supports_blurdetect", lambda: False)
    result = asset_quality_gate.AssetQualityGate().execute({"path": str(f)})
    assert result.success
    assert result.data["ok"] is True


# ---------------------------------------------------------------------------
# C3: mix_audio ducking + loudnorm
# ---------------------------------------------------------------------------


def test_mix_audio_default_no_ducking(monkeypatch, tmp_path):
    v, n, m, o = (tmp_path / x for x in ("v.mp4", "n.mp3", "m.mp3", "o.mp4"))
    for f in (v, n, m):
        f.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    fe.mix_audio(v, n, m, o)
    cmd = " ".join(captured[0])
    assert "amix=inputs=2" in cmd
    assert "sidechaincompress" not in cmd
    assert "loudnorm" not in cmd


def test_mix_audio_trims_and_fades_when_duration_known(monkeypatch, tmp_path):
    v, n, m, o = (tmp_path / x for x in ("v.mp4", "n.mp3", "m.mp3", "o.mp4"))
    for f in (v, n, m):
        f.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": "12.0" if p == v else "90.0"}})
    fe.mix_audio(v, n, m, o)
    cmd = " ".join(captured[0])
    assert "atrim=0:12.000" in cmd
    assert "afade=t=in" in cmd
    assert "afade=t=out" in cmd


def test_mix_audio_ducking(monkeypatch, tmp_path):
    v, n, m, o = (tmp_path / x for x in ("v.mp4", "n.mp3", "m.mp3", "o.mp4"))
    for f in (v, n, m):
        f.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    fe.mix_audio(v, n, m, o, ducking=True)
    cmd = " ".join(captured[0])
    assert "sidechaincompress" in cmd
    assert "threshold=0.02:ratio=8:attack=20:release=400" in cmd
    assert "[n][duck]amix" in cmd


def test_mix_audio_loudnorm(monkeypatch, tmp_path):
    v, n, o = (tmp_path / x for x in ("v.mp4", "n.mp3", "o.mp4"))
    for f in (v, n):
        f.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    fe.mix_audio(v, n, None, o, loudnorm=True)
    cmd = " ".join(captured[0])
    assert "loudnorm=I=-14.0:TP=-1.5:LRA=11" in cmd


def test_mix_audio_loudnorm_without_narration(monkeypatch, tmp_path):
    """无旁白的 AI 对白片也必须整体响度归一，避免段落间电平脱节。"""
    v, m, o = (tmp_path / x for x in ("v.mp4", "m.mp3", "o.mp4"))
    for f in (v, m):
        f.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    fe.mix_audio(v, None, m, o, loudnorm=True)
    cmd = " ".join(captured[0])
    assert "loudnorm=I=-14.0:TP=-1.5:LRA=11" in cmd


def test_mix_audio_loudnorm_source_audio_only(monkeypatch, tmp_path):
    """保留原生音轨且无 BGM 时，也必须对源音轨做整体响度归一。"""
    v, o = (tmp_path / x for x in ("v.mp4", "o.mp4"))
    v.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "_has_audio_stream", lambda p: True)
    fe.mix_audio(v, None, None, o, loudnorm=True)
    cmd = " ".join(captured[0])
    assert "-map" in cmd and "[a]" in cmd
    assert "dynaudnorm=p=0.80:m=6.0:r=0.30" in cmd
    assert "loudnorm=I=-14.0:TP=-1.5:LRA=11" in cmd


def test_mix_audio_dispatch_ducking(monkeypatch, tmp_path):
    v, n, m, o = (tmp_path / x for x in ("v.mp4", "n.mp3", "m.mp3", "o.mp4"))
    for f in (v, n, m):
        f.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 5.0}})
    result = fe.FFmpegCompose().execute({
        "operation": "mix_audio", "input_path": str(v),
        "narration_path": str(n), "music_path": str(m),
        "output_path": str(o), "ducking": True, "loudnorm": True,
    })
    assert result.success
    cmd = " ".join(captured[0])
    assert "sidechaincompress" in cmd and "loudnorm" in cmd


# ---------------------------------------------------------------------------
# C4: subtitle_builder
# ---------------------------------------------------------------------------

_SENTS = [
    {"text": "雨夜，霓虹下的城市街道。", "start_seconds": 0.0, "end_seconds": 2.5},
    {"text": "他转身，消失在巷口。", "start_seconds": 2.5, "end_seconds": 5.0},
]


def test_srt_generation():
    srt = subtitle_builder.timestamps_to_srt(_SENTS)
    assert "00:00:00,000 --> 00:00:02,500" in srt
    assert "00:00:02,500 --> 00:00:05,000" in srt
    assert "雨夜，霓虹下的城市街道。" in srt
    assert srt.count("\n\n") == 1  # 两段


def test_ass_generation():
    ass = subtitle_builder.timestamps_to_ass(_SENTS, font="Source Han Sans SC", font_size=64)
    assert "[Script Info]" in ass and "[V4+ Styles]" in ass
    assert "Source Han Sans SC,64" in ass
    assert "0:00:00.00,0:00:02.50" in ass
    assert "Dialogue: 0," in ass


def test_ass_escaping():
    ass = subtitle_builder.timestamps_to_ass([{"text": "说{a}话", "start_seconds": 0, "end_seconds": 1}])
    assert "说（a）话" in ass  # {} 转义为中文括号


def test_wrap_text_break():
    lines = subtitle_builder.wrap_text("这是一段很长的台词需要断行处理成多行字幕显示", max_chars=10)
    assert len(lines) >= 2
    assert all(len(l) <= 10 for l in lines)


def test_wrap_text_keeps_punct():
    lines = subtitle_builder.wrap_text("你好，世界。你好，世界。", max_chars=6)
    # 标点不落行首
    for l in lines:
        assert l and l[0] not in subtitle_builder._NO_BREAK_AFTER


def test_format_timestamp():
    assert subtitle_builder.format_timestamp(0) == "00:00:00,000"
    assert subtitle_builder.format_timestamp(3661.5) == "01:01:01,500"
    assert subtitle_builder.format_timestamp(2.5, ass=True) == "0:00:02.50"


def test_subtitle_builder_tool_dispatch(tmp_path):
    out = tmp_path / "subs.ass"
    result = subtitle_builder.SubtitleBuilder().execute({
        "sentences": _SENTS, "format": "ass", "output_path": str(out),
    })
    assert result.success
    assert result.data["format"] == "ass"
    assert out.exists()
    assert "[Events]" in out.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# C5: asset_picker + generation_cache
# ---------------------------------------------------------------------------


def test_pick_best_uses_sharpness(monkeypatch, tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(b"1")
    b.write_bytes(b"2")
    monkeypatch.setattr(asset_picker, "_probe_size", lambda p: (1920, 1080))
    monkeypatch.setattr(asset_picker, "sharpness_score", lambda p: 0.9 if p.name == "b.png" else 0.5)
    result = asset_picker.pick_best([str(a), str(b)])
    assert result["best"] == str(b)
    assert result["scores"][0]["score"] > result["scores"][1]["score"]


def test_pick_best_empty():
    result = asset_picker.pick_best([])
    assert result["best"] is None


def test_pick_best_requires_two():
    result = asset_picker.AssetPicker().execute({"paths": ["a.png"]})
    assert not result.success


def test_cache_key_stable_and_order_free():
    k1 = generation_cache.cache_key(prompt="x", seed=1, provider="jimeng")
    k2 = generation_cache.cache_key(provider="jimeng", seed=1, prompt="x")
    assert k1 == k2
    assert len(k1) == 16
    assert k1 != generation_cache.cache_key(prompt="x", seed=2, provider="jimeng")


def test_cache_put_get(tmp_path):
    cache = generation_cache.GenerationCache(tmp_path / ".cache")
    src = tmp_path / "gen.mp4"
    src.write_bytes(b"media")
    key = generation_cache.cache_key(prompt="p", seed=7)
    put = cache.execute({"operation": "put", "key": key, "path": str(src)})
    assert put.success
    got = cache.execute({"operation": "get", "key": key})
    assert got.success and got.data["hit"] is True
    assert Path(got.data["path"]).exists()
    # 未命中
    miss = cache.execute({"operation": "get", "params": {"prompt": "other"}})
    assert miss.success and miss.data["hit"] is False


def test_cache_stats(tmp_path):
    cache = generation_cache.GenerationCache(tmp_path / ".cache")
    src = tmp_path / "g.mp4"
    src.write_bytes(b"m")
    cache.execute({"operation": "put", "params": {"prompt": "p"}, "path": str(src)})
    stats = cache.execute({"operation": "stats"})
    assert stats.data["entries"] == 1
