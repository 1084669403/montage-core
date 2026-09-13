"""真 ffmpeg 兼容性冒烟：用 lavfi 生成素材，真跑并 ``probe()`` 断言语义。

这些用例把已删除的 ``scratch/probe_*.py`` 排障结论固化成断言，不依赖
``projects/``（probe 脚本已删，回归仍由本文件兜住）：

- ``stitch_with_transitions`` 混合 cut + crossfade（时长 oracle 同 probe_stitch_mixed）
- ``apply_lut`` 全量 / 部分强度（lut3d ``file=`` + Windows 盘符转义）
- ``title_card`` + ``lower_third``（带系统字体的 drawtext）
- ``concat_videos`` 竖向兜底重编码（不再硬拉 1920x1080）
- acrossfade 链有 48kHz 立体声音轨

无 ffmpeg 时整文件 skip（minitest 本机有 ffmpeg 则自动执行）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from montage.compose import effects
from montage.compose import ffmpeg_engine as fe
from montage.compose import ffmpeg_compat

pytestmark = pytest.mark.ffmpeg


def _require_ffmpeg() -> str:
    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.skip("缺少 ffmpeg")
    return ff


def _make_clip(
    ff: str,
    path: Path,
    *,
    color: str = "red",
    seconds: float = 2.0,
    size: str = "320x240",
    fps: int = 30,
    audio: bool = True,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ff, "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s={size}:d={seconds}:r={fps}",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    cmd += ["-t", f"{seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    cmd += [str(path)]
    fe.run_ffmpeg(cmd, timeout=120)
    return path


def _duration(path: Path) -> float:
    return float((fe.probe(path).get("format") or {}).get("duration") or 0)


def _audio_stream(path: Path) -> dict:
    for stream in fe.probe(path).get("streams") or []:
        if stream.get("codec_type") == "audio":
            return stream
    return {}


# ---------------------------------------------------------------------------
# stitch：cut + crossfade 混合，时长 oracle 5.5s（probe_stitch_mixed 结论）
# ---------------------------------------------------------------------------

@pytest.mark.ffmpeg
def test_stitch_mixed_cut_and_crossfade_duration(tmp_path):
    ff = _require_ffmpeg()
    a = _make_clip(ff, tmp_path / "a.mp4", color="red")
    b = _make_clip(ff, tmp_path / "b.mp4", color="green")
    c = _make_clip(ff, tmp_path / "c.mp4", color="blue")
    out = tmp_path / "mixed.mp4"
    # junction0 = cut（默认），junction1 = crossfade 0.5 → 2+2+2-0.5 = 5.5
    fe.stitch_with_transitions(
        [a, b, c],
        [{}, {"transition": "crossfade", "transition_duration": 0.5}],
        out,
    )
    assert out.is_file() and out.stat().st_size > 0
    assert abs(_duration(out) - 5.5) < 0.25
    audio = _audio_stream(out)
    assert audio, "混合转场输出应带音轨"
    assert int(audio.get("sample_rate") or 0) == 48000
    assert int(audio.get("channels") or 0) == 2


@pytest.mark.ffmpeg
def test_stitch_all_cut_is_hard_cut(tmp_path):
    ff = _require_ffmpeg()
    clips = [
        _make_clip(ff, tmp_path / f"c{i}.mp4", color=color)
        for i, color in enumerate(("red", "green", "blue"))
    ]
    out = tmp_path / "cut.mp4"
    fe.stitch_with_transitions(clips, [{}, {}], out)
    # 全硬切无重叠：2+2+2 = 6.0
    assert abs(_duration(out) - 6.0) < 0.25


# ---------------------------------------------------------------------------
# apply_lut：lut3d file= + filter_path 盘符转义（全量 / 部分强度）
# ---------------------------------------------------------------------------

def _repo_lut() -> Path:
    p = Path(__file__).resolve().parents[1] / "assets" / "luts" / "teal-orange.cube"
    if not p.is_file():
        pytest.skip(f"仓库 LUT 缺失: {p}")
    return p


@pytest.mark.ffmpeg
def test_apply_lut_full_and_partial_strength(tmp_path):
    ff = _require_ffmpeg()
    src = _make_clip(ff, tmp_path / "src.mp4", seconds=1.0)
    lut = _repo_lut()

    full = fe.apply_lut(src, lut, tmp_path / "full.mp4", strength=1.0)
    assert full.is_file() and fe.probe(full).get("streams")
    assert abs(_duration(full) - 1.0) < 0.25

    partial = fe.apply_lut(src, lut, tmp_path / "partial.mp4", strength=0.6)
    assert partial.is_file() and fe.probe(partial).get("streams")


def test_lut3d_filter_uses_detected_option(tmp_path):
    lut = tmp_path / "l.cube"
    lut.write_text("LUT_3D_SIZE 2\n0 0 0\n1 1 1\n", encoding="utf-8")
    filt = fe.lut3d_filter(lut)
    if ffmpeg_compat.ffmpeg_binary():
        # 本机有 ffmpeg：按真实探测结果选 file= filename=
        assert filt.startswith("lut3d=file=" if ffmpeg_compat.supports("lut3d_file") else "lut3d=filename=")
    assert ":interp=tetrahedral" in filt


# ---------------------------------------------------------------------------
# drawtext：title_card + lower_third 需要系统字体兜底
# ---------------------------------------------------------------------------

@pytest.mark.ffmpeg
def test_title_card_and_lower_third_with_system_font(tmp_path):
    ff = _require_ffmpeg()
    font = fe.resolve_font_file()
    if font is None:
        pytest.skip("本机无可用系统字体")
    base = _make_clip(ff, tmp_path / "base.mp4", seconds=2.0, size="320x240")

    card = effects.title_card(base, tmp_path / "card.mp4", title="画皮", duration=1.0, fontfile=str(font))
    assert card.is_file()
    assert abs(_duration(card) - 1.0) < 0.25

    l3 = effects.lower_third(base, tmp_path / "l3.mp4", text="王生", start_seconds=0.2, duration=1.0, fontfile=str(font))
    assert l3.is_file()
    assert abs(_duration(l3) - 2.0) < 0.25


# ---------------------------------------------------------------------------
# concat 竖向兜底：探测失败不再回落 1920x1080
# ---------------------------------------------------------------------------

@pytest.mark.ffmpeg
def test_concat_vertical_reencode_keeps_orientation(tmp_path, monkeypatch):
    ff = _require_ffmpeg()
    a = _make_clip(ff, tmp_path / "a.mp4", color="red", size="240x426")
    b = _make_clip(ff, tmp_path / "b.mp4", color="blue", size="240x426")
    out = tmp_path / "v.mp4"

    original = fe._run
    calls = {"n": 0}

    def flaky(cmd, timeout=fe.FFMPEG_TIMEOUT):
        calls["n"] += 1
        if calls["n"] == 1:  # 强制流拷贝失败，走重编码兜底
            raise fe.ComposError("copy failed")
        return original(cmd, timeout=timeout)

    monkeypatch.setattr(fe, "_run", flaky)
    fe.concat_videos([a, b], out)
    assert calls["n"] >= 2
    info = fe.probe(out)
    video = next(s for s in info.get("streams") or [] if s.get("codec_type") == "video")
    assert (int(video["width"]), int(video["height"])) == (240, 426)


def test_first_video_size_returns_none_when_unprobeable(tmp_path):
    missing = tmp_path / "nope.mp4"
    assert fe._first_video_size([missing]) is None


# ---------------------------------------------------------------------------
# 能力探测自身
# ---------------------------------------------------------------------------

def test_supports_unknown_capability_raises():
    with pytest.raises(KeyError):
        ffmpeg_compat.supports("definitely_not_a_cap")


@pytest.mark.ffmpeg
def test_supports_lut3d_file_true_on_modern_ffmpeg():
    _require_ffmpeg()
    assert ffmpeg_compat.supports("lut3d_file") is True
