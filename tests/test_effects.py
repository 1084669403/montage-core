"""M1 剪辑特效测试：effects 模块命令构造 + acrossfade 链 + dispatch 分发。

全部 mock `_run`/探测函数验证 ffmpeg 命令构造（沙箱禁止子进程管道）。
"""

import pytest
from pathlib import Path

from montage.compose import effects
from montage.compose import ffmpeg_engine as fe


def _capture(monkeypatch):
    captured: list[list[str]] = []
    monkeypatch.setattr(effects, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    return captured


# ---------------------------------------------------------------------------
# normalize_clip
# ---------------------------------------------------------------------------


def test_normalize_clip_command(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    effects.normalize_clip(tmp_path / "a.mp4", tmp_path / "n.mp4", width=1280, height=720, fps=24)
    cmd = " ".join(captured[0])
    assert "scale=1280:720" in cmd and "pad=1280:720" in cmd
    assert "fps=24" in cmd
    assert "yuv420p" in cmd


# ---------------------------------------------------------------------------
# spatial_compose
# ---------------------------------------------------------------------------


def test_spatial_side_by_side(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    for f in (a, b):
        f.write_bytes(b"x")
    effects.spatial_compose([a, b], tmp_path / "o.mp4", layout="side_by_side")
    cmd = " ".join(captured[0])
    assert "xstack=inputs=2" in cmd
    assert "layout=0_0|960_0" in cmd  # 1920/2


def test_spatial_vertical_stack(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    for f in (a, b):
        f.write_bytes(b"x")
    effects.spatial_compose([a, b], tmp_path / "o.mp4", layout="vertical_stack")
    cmd = " ".join(captured[0])
    assert "xstack=inputs=2" in cmd
    assert "layout=0_0|0_540" in cmd  # 1080/2


def test_spatial_pip(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    for f in (a, b):
        f.write_bytes(b"x")
    effects.spatial_compose([a, b], tmp_path / "o.mp4", layout="picture_in_picture")
    cmd = " ".join(captured[0])
    assert "overlay=" in cmd
    assert "scale=480:270" in cmd  # 1/4 尺寸


def test_spatial_requires_two(tmp_path):
    with pytest.raises(fe.ComposError):
        effects.spatial_compose([tmp_path / "a.mp4"], tmp_path / "o.mp4")


# ---------------------------------------------------------------------------
# blend_layers
# ---------------------------------------------------------------------------


def test_blend_layers_command(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    effects.blend_layers(tmp_path / "b.mp4", tmp_path / "o.mp4", tmp_path / "r.mp4",
                         mode="screen", opacity=0.8)
    cmd = " ".join(captured[0])
    assert "blend=all_mode=screen:all_opacity=0.80" in cmd
    assert "format=rgba" in cmd


def test_blend_layers_bad_mode(tmp_path):
    with pytest.raises(fe.ComposError, match="未知混合模式"):
        effects.blend_layers(tmp_path / "b.mp4", tmp_path / "o.mp4", tmp_path / "r.mp4", mode="nope")


# ---------------------------------------------------------------------------
# change_speed
# ---------------------------------------------------------------------------


def test_speed_normal(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    effects.change_speed(tmp_path / "a.mp4", tmp_path / "s.mp4", factor=1.5)
    cmd = " ".join(captured[0])
    assert "setpts=PTS/1.5000" in cmd
    assert "atempo=1.500" in cmd


def test_speed_high_factor_chains_atempo(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    effects.change_speed(tmp_path / "a.mp4", tmp_path / "s.mp4", factor=4.0)
    cmd = " ".join(captured[0])
    assert "atempo=2.0,atempo=2.0" in cmd  # 4x → 2*2


def test_speed_bad_factor(tmp_path):
    with pytest.raises(fe.ComposError, match="> 0"):
        effects.change_speed(tmp_path / "a.mp4", tmp_path / "s.mp4", factor=0)


# ---------------------------------------------------------------------------
# showcase_card
# ---------------------------------------------------------------------------


def test_showcase_card_with_title(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    effects.showcase_card(tmp_path / "a.mp4", tmp_path / "c.mp4", title="雨夜追凶")
    cmd = " ".join(captured[0])
    assert "drawtext=" in cmd
    assert "雨夜追凶" in cmd
    assert "pad=1080:1920" in cmd


def test_showcase_card_escaping(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    effects.showcase_card(tmp_path / "a.mp4", tmp_path / "c.mp4", title="a:b,c")
    cmd = " ".join(captured[0])
    assert "a\\:b\\,c" in cmd  # 冒号/逗号转义


# ---------------------------------------------------------------------------
# drawtext 字体解析（fontconfig 兜底）
# ---------------------------------------------------------------------------

def test_resolve_font_file_prefers_explicit(tmp_path):
    f = tmp_path / "m y.ttf"
    f.write_bytes(b"x")
    assert fe.resolve_font_file(f) == f


def test_font_param_double_escapes_windows_colon():
    """fontfile 值不加引号、双重转义：带引号+双转义会被解析成字面 \\\\。"""
    found = fe.resolve_font_file()
    if found is None:
        pytest.skip("本机既无资产库字体也无系统字体")
    frag = effects.font_param()
    assert frag.startswith("fontfile=")
    assert frag.endswith(":")
    assert "'" not in frag and '"' not in frag
    if ":" in found.as_posix():
        assert "\\\\:" in frag


def test_font_param_errors_when_no_font(monkeypatch):
    monkeypatch.setattr(fe, "_SYSTEM_FONT_CANDIDATES", ())
    if fe.resolve_font_file() is not None:
        pytest.skip("资产库里有可用字体，无法构造缺字体场景")
    with pytest.raises(fe.ComposError) as exc:
        effects.font_param()
    assert "字体" in str(exc.value)


def _layout_probe(_path):
    return {
        "streams": [
            {"codec_type": "video", "width": 1920, "height": 1080, "r_frame_rate": "24/1"},
            {"codec_type": "audio", "sample_rate": "44100", "channels": 2, "channel_layout": "stereo"},
        ],
        "format": {"duration": "5.0"},
    }


def test_sanitize_drawtext_strips_newlines_and_skips_punct():
    assert effects.sanitize_drawtext("雨夜") == "雨夜"
    assert effects.sanitize_drawtext("a:b,c") == "a\\:b\\,c"
    assert effects.sanitize_drawtext(":\n'") == ""
    assert effects.sanitize_drawtext("  ") == ""


def test_title_card_has_silent_audio(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    monkeypatch.setattr(effects, "probe", _layout_probe)
    src = tmp_path / "final.mp4"
    src.write_bytes(b"x")
    effects.title_card(src, tmp_path / "title.mp4", title="雨夜")
    cmd = " ".join(captured[0])
    assert "anullsrc=r=44100:cl=stereo" in cmd
    assert "color=c=0x101014:s=1920x1080" in cmd
    assert "r=24" in cmd
    assert "-shortest" in cmd
    assert "text='雨夜'" in cmd


def test_title_card_empty_raises():
    with pytest.raises(fe.ComposError):
        effects.title_card("a.mp4", "t.mp4", title=":")


def test_lower_third_enable_after_title(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    monkeypatch.setattr(effects, "probe", _layout_probe)
    src = tmp_path / "final.mp4"
    src.write_bytes(b"x")
    effects.lower_third(src, tmp_path / "l.mp4", text="雨夜 · 林", start_seconds=2.0, duration=4.0)
    cmd = " ".join(captured[0])
    assert "drawtext=" in cmd
    assert "between(t\\,2.000\\,6.000)" in cmd
    assert "雨夜" in cmd


def test_dispatch_title_card(monkeypatch, tmp_path):
    captured: list[list[str]] = []
    monkeypatch.setattr(effects, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(effects, "probe", _layout_probe)
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 2.0}})
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    result = fe.FFmpegCompose().execute({
        "operation": "title_card", "input_path": str(src), "title": "雨夜",
        "output_path": str(tmp_path / "t.mp4"),
    })
    assert result.success
    assert "anullsrc=" in " ".join(captured[0])


# ---------------------------------------------------------------------------
# cut_silence
# ---------------------------------------------------------------------------


def test_detect_silence_parses(monkeypatch, tmp_path):
    stderr = (
        "[silencedetect @ 0x1] silence_start: 1.5\n"
        "[silencedetect @ 0x1] silence_end: 3.2 | silence_duration: 1.7\n"
    )
    monkeypatch.setattr(effects, "_ffmpeg", lambda: "ffmpeg")

    def fake_run(cmd, timeout=600, check=False, error_prefix="FFmpeg 失败"):
        import subprocess
        proc = subprocess.CompletedProcess(cmd, 0)
        proc.stderr = stderr
        return proc

    monkeypatch.setattr(effects, "run_ffmpeg", fake_run)
    ranges = effects.detect_silence(tmp_path / "a.mp4")
    assert ranges == [(1.5, 3.2)]


def test_cut_silence_mark(monkeypatch, tmp_path):
    monkeypatch.setattr(effects, "detect_silence", lambda *a, **kw: [(1.0, 2.0)])
    result = effects.cut_silence(tmp_path / "a.mp4", tmp_path / "o.mp4", action="mark")
    assert result["count"] == 1
    assert result["silence_ranges"] == [(1.0, 2.0)]


def test_cut_silence_remove_command(monkeypatch, tmp_path):
    monkeypatch.setattr(effects, "detect_silence", lambda *a, **kw: [(1.0, 2.0)])
    monkeypatch.setattr(effects, "probe", lambda p: {"format": {"duration": 5.0}})
    captured = _capture(monkeypatch)
    result = effects.cut_silence(tmp_path / "a.mp4", tmp_path / "o.mp4", action="remove")
    assert result["removed_segments"] == 1
    cmd = " ".join(captured[0])
    assert "trim=start=0.000:end=1.000" in cmd
    assert "trim=start=2.000:end=5.000" in cmd
    assert "concat=n=2:v=1:a=1" in cmd


def test_cut_silence_no_silence_copies(monkeypatch, tmp_path):
    monkeypatch.setattr(effects, "detect_silence", lambda *a, **kw: [])
    captured = _capture(monkeypatch)
    effects.cut_silence(tmp_path / "a.mp4", tmp_path / "o.mp4")
    assert "-c" in captured[0] and "copy" in captured[0]


# ---------------------------------------------------------------------------
# auto_reframe
# ---------------------------------------------------------------------------


def test_auto_reframe_center(monkeypatch, tmp_path):
    captured = _capture(monkeypatch)
    result = effects.auto_reframe(tmp_path / "a.mp4", tmp_path / "o.mp4", target="9:16")
    cmd = " ".join(captured[0])
    assert "scale=1080:1920" in cmd and "crop=1080:1920" in cmd
    assert result["mode_used"] == "center"


def test_auto_reframe_face_degrade(monkeypatch, tmp_path):
    """无 OpenCV/未检出人脸 → 降级居中。"""
    monkeypatch.setattr(effects, "_face_center_x_ratio", lambda p: None)
    captured = _capture(monkeypatch)
    result = effects.auto_reframe(tmp_path / "a.mp4", tmp_path / "o.mp4", target="1:1", mode="face")
    cmd = " ".join(captured[0])
    assert "(iw-1080)" not in cmd  # 无 x 偏移
    assert result["mode_used"] == "center"


def test_auto_reframe_face_uses_center(monkeypatch, tmp_path):
    monkeypatch.setattr(effects, "_face_center_x_ratio", lambda p: 0.3)
    captured = _capture(monkeypatch)
    result = effects.auto_reframe(tmp_path / "a.mp4", tmp_path / "o.mp4", target="9:16", mode="face")
    cmd = " ".join(captured[0])
    assert "(iw-1080)*0.300" in cmd
    assert result["mode_used"] == "face"


def test_auto_reframe_bad_target(tmp_path):
    with pytest.raises(fe.ComposError, match="未知比例"):
        effects.auto_reframe(tmp_path / "a.mp4", tmp_path / "o.mp4", target="5:5")


# ---------------------------------------------------------------------------
# stitch_with_transitions 音频 acrossfade
# ---------------------------------------------------------------------------


def test_acrossfade_chain_build():
    clips = [Path("a.mp4"), Path("b.mp4"), Path("c.mp4")]
    transitions = [{"transition": "crossfade", "transition_duration": 0.5},
                   {"transition": "fade_black", "transition_duration": 1.0}]
    fc, out = fe._acrossfade_chain(clips, transitions)
    assert "aformat=sample_rates=48000:channel_layouts=stereo" in fc
    assert "acrossfade=d=0.500" in fc
    assert "acrossfade=d=1.000" in fc
    assert out == "[a2]"
    # aresample 的 cl= 在 ffmpeg 9 报 Option not found，统一走 aformat
    assert "cl=stereo" not in fc


def test_stitch_with_transitions_audio(monkeypatch, tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    for f in (a, b):
        f.write_bytes(b"x")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 5.0}})
    monkeypatch.setattr(fe, "_clip_has_audio", lambda p: True)
    fe.stitch_with_transitions([a, b], [{"transition": "crossfade", "transition_duration": 0.5}],
                               tmp_path / "o.mp4")
    cmd = " ".join(captured[0])
    assert "xfade=transition=fade" in cmd
    assert "acrossfade=d=0.500" in cmd
    assert "-map" in cmd and "[a1]" in cmd


def test_stitch_with_transitions_no_audio(monkeypatch, tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    for f in (a, b):
        f.write_bytes(b"x")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 5.0}})
    monkeypatch.setattr(fe, "_clip_has_audio", lambda p: False)
    fe.stitch_with_transitions(
        [a, b], [{"transition": "crossfade", "transition_duration": 0.5}], tmp_path / "o.mp4"
    )
    cmd = " ".join(captured[0])
    assert "acrossfade" not in cmd
    assert "-an" in cmd


def test_stitch_mixed_audio_pads_silent_track(monkeypatch, tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    for f in (a, b):
        f.write_bytes(b"x")
    has = {str(a): True, str(b): False}

    def _has(path):
        return has.get(str(path), True)

    def ensure(path, work):
        if has.get(str(path)):
            return path
        dest = Path(work) / f"{Path(path).stem}.silent.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"s")
        has[str(dest)] = True
        return dest

    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_clip_has_audio", _has)
    monkeypatch.setattr(fe, "_ensure_audio_track", ensure)
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 5.0}})
    fe.stitch_with_transitions(
        [a, b], [{"transition": "crossfade", "transition_duration": 0.5}], tmp_path / "o.mp4",
    )
    cmd = " ".join(captured[0])
    assert "acrossfade" in cmd
    assert "-an" not in cmd


# ---------------------------------------------------------------------------
# FFmpegCompose dispatch
# ---------------------------------------------------------------------------


def test_dispatch_speed(monkeypatch, tmp_path):
    captured: list[list[str]] = []
    monkeypatch.setattr(effects, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 3.0}})
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    result = fe.FFmpegCompose().execute({
        "operation": "speed", "input_path": str(src), "speed_factor": 2.0,
        "output_path": str(tmp_path / "s.mp4"),
    })
    assert result.success
    assert "atempo=2.000" in " ".join(captured[0])


def test_dispatch_blend_layer_requires_overlay():
    result = fe.FFmpegCompose().execute({
        "operation": "blend_layer", "input_path": "b.mp4",
    })
    assert not result.success
    assert "overlay_path" in result.error


def test_dispatch_auto_reframe(monkeypatch, tmp_path):
    captured: list[list[str]] = []
    monkeypatch.setattr(effects, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 3.0}})
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    result = fe.FFmpegCompose().execute({
        "operation": "auto_reframe", "input_path": str(src),
        "reframe_target": "9:16", "output_path": str(tmp_path / "r.mp4"),
    })
    assert result.success
    assert result.data["mode_used"] == "center"
