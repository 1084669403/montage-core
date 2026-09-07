"""合成引擎测试：状态、输入校验、时间轴规划（不依赖 ffmpeg 的逻辑部分）。

真实 ffmpeg 冒烟测试可用 MONTAGE_REAL_FFMPEG=1 环境变量开启
（沙箱环境禁止子进程管道捕获，用户本机无此限制）。
"""

import os

import pytest

from montage.compose import ffmpeg_engine as fe
from montage.compose import narration
from montage.toolbase import ToolStatus


def test_status_reflects_ffmpeg():
    status = fe.FFmpegCompose().get_status()
    assert status in (ToolStatus.AVAILABLE, ToolStatus.UNAVAILABLE)


def test_requires_operation():
    result = fe.FFmpegCompose().execute({})
    assert not result.success
    # 无 ffmpeg 时给出安装提示
    if fe.check_ffmpeg() is None:
        assert "ffmpeg" in result.error


def test_concat_missing_clips_fails():
    result = fe.FFmpegCompose().execute({"operation": "concat", "clips": ["nope1.mp4", "nope2.mp4"]})
    assert not result.success
    assert "不存在" in result.error or "ffmpeg" in result.error


def test_concat_file_line_escapes_quote(tmp_path):
    from pathlib import Path

    from montage.compose.ffmpeg_engine import concat_file_line

    target = tmp_path / "it' s.mp4"
    line = concat_file_line(target)
    assert line.startswith("file '")
    assert r"'\''" in line


def test_assemble_empty_cuts_fails():
    result = fe.FFmpegCompose().execute({"operation": "assemble", "edit_decisions": {"cuts": []}})
    assert not result.success


def test_assemble_rejects_non_ffmpeg_runtime(monkeypatch):
    monkeypatch.setattr(fe, "check_ffmpeg", lambda: "ffmpeg")
    result = fe.FFmpegCompose().execute({
        "operation": "assemble",
        "edit_decisions": {
            "cuts": [{"clip_path": "a.mp4"}],
            "render_runtime": "remotion",
        },
    })
    assert not result.success
    assert "ffmpeg" in (result.error or "").lower()


def test_trim_missing_input_fails():
    result = fe.FFmpegCompose().execute({"operation": "trim", "input_path": "nope.mp4"})
    assert not result.success


def test_plan_narration_timeline(monkeypatch):
    # 纯逻辑：桩掉 _audio_duration，验证起止时间与静音间隔计算
    monkeypatch.setattr(narration, "_audio_duration", lambda p: 2.0)
    timeline = narration.plan_narration(
        [
            {"id": "sc01", "narration_audio": "a.wav"},
            {"id": "sc02", "narration_audio": "b.wav"},
            {"id": "sc03", "narration_audio": "c.wav"},
        ],
        gap_seconds=0.5,
    )
    assert len(timeline) == 3
    assert timeline[0]["start_seconds"] == 0.0
    assert timeline[0]["end_seconds"] == 2.0
    assert timeline[1]["start_seconds"] == 2.5
    assert timeline[2]["start_seconds"] == 5.0


def test_plan_narration_keeps_speaker_and_voice(monkeypatch):
    monkeypatch.setattr(narration, "_audio_duration", lambda p: 1.0)
    timeline = narration.plan_narration([
        {"id": "a", "narration_audio": "a.wav", "speaker_id": "li_ming", "voice": "male_low"},
        {"id": "b", "narration_audio": "b.wav", "speaker_id": "narrator", "voice": "female_soft"},
    ])
    assert timeline[0]["speaker_id"] == "li_ming"
    assert timeline[1]["voice"] == "female_soft"


def test_plan_narration_skips_empty_audio():
    timeline = narration.plan_narration(
        [
            {"id": "a", "narration_audio": ""},
            {"id": "b"},  # 无该字段
        ]
    )
    assert timeline == []


def test_plan_narration_raises_on_missing_file():
    with pytest.raises(fe.ComposError):
        narration.plan_narration([{"id": "a", "narration_audio": "missing.wav"}])


def test_real_ffmpeg_smoke(tmp_path):
    """真实 ffmpeg 冒烟（需 MONTAGE_REAL_FFMPEG=1；沙箱环境自动跳过）。"""
    if os.environ.get("MONTAGE_REAL_FFMPEG") != "1":
        pytest.skip("需设置 MONTAGE_REAL_FFMPEG=1 才运行真实 ffmpeg 冒烟")
    if fe.check_ffmpeg() is None:
        pytest.skip("本机无 ffmpeg")
    fe._run([
        fe.check_ffmpeg(), "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:a", "pcm_s16le", str(tmp_path / "a.wav"),
    ])
    fe._run([
        fe.check_ffmpeg(), "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=1",
        "-c:a", "pcm_s16le", str(tmp_path / "b.wav"),
    ])
    timeline = narration.plan_narration(
        [
            {"id": "sc01", "narration_audio": str(tmp_path / "a.wav")},
            {"id": "sc02", "narration_audio": str(tmp_path / "b.wav")},
        ],
        gap_seconds=0.5,
    )
    assert len(timeline) == 2
    assert abs(timeline[0]["duration_seconds"] - 2.0) < 0.1
    assert abs(timeline[1]["start_seconds"] - 2.5) < 0.1


# ---------------------------------------------------------------------------
# assemble 数据流（A3）：edit_decisions 可从产物文件读取
# ---------------------------------------------------------------------------

def test_assemble_reads_edit_decisions_artifact(monkeypatch, tmp_path):
    """无入参 edit_decisions，仅传 edit_decisions_path 也可装配。"""
    import json as _json

    proj = tmp_path / "proj"
    (proj / "artifacts").mkdir(parents=True)
    for c in ("c1.mp4", "c2.mp4"):
        (tmp_path / c).write_bytes(b"fake")
    decisions = {
        "cuts": [
            {"clip_path": str(tmp_path / "c1.mp4")},
            {"clip_path": str(tmp_path / "c2.mp4")},
        ]
    }
    (proj / "artifacts" / "edit_decisions.json").write_text(
        _json.dumps(decisions, ensure_ascii=False), encoding="utf-8"
    )

    calls: list[tuple] = []

    def fake_concat(clips, out, **kw):
        calls.append(("concat", len(clips)))
        return out

    def fake_mix(video, narration, music, output, **kw):
        return video

    monkeypatch.setattr(fe, "concat_videos", fake_concat)
    monkeypatch.setattr(fe, "mix_audio", fake_mix)
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 1.0}})
    result = fe.FFmpegCompose().execute({
        "operation": "assemble",
        "edit_decisions_path": str(proj / "artifacts" / "edit_decisions.json"),
        "output_path": str(tmp_path / "final.mp4"),
    })
    assert result.success
    assert calls and calls[0] == ("concat", 2)


def test_assemble_missing_both_sources_fails():
    result = fe.FFmpegCompose().execute({"operation": "assemble"})
    assert not result.success
    assert "edit_decisions" in result.error


def test_assemble_missing_artifact_path_fails(tmp_path):
    result = fe.FFmpegCompose().execute({
        "operation": "assemble",
        "edit_decisions_path": str(tmp_path / "nope.json"),
    })
    assert not result.success


def test_assemble_uses_transitions(monkeypatch, tmp_path):
    """cuts 带转场定义 → 走 stitch_with_transitions（xfade）而非 concat。"""
    for c in ("c1.mp4", "c2.mp4"):
        (tmp_path / c).write_bytes(b"fake")
    calls: list[str] = []

    def fake_stitch(clips, transitions, out, **kw):
        calls.append("stitch")
        return out

    def fake_concat(clips, out, **kw):
        calls.append("concat")
        return out

    def fake_mix(video, narration, music, output, **kw):
        return video

    monkeypatch.setattr(fe, "stitch_with_transitions", fake_stitch)
    monkeypatch.setattr(fe, "concat_videos", fake_concat)
    monkeypatch.setattr(fe, "mix_audio", fake_mix)
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 1.0}})
    result = fe.FFmpegCompose().execute({
        "operation": "assemble",
        "edit_decisions": {
            "cuts": [
                {"clip_path": str(tmp_path / "c1.mp4")},
                {"clip_path": str(tmp_path / "c2.mp4"), "transition_in": "crossfade", "transition_duration": 0.5},
            ]
        },
        "output_path": str(tmp_path / "final.mp4"),
    })
    assert result.success
    assert calls == ["stitch"]


def test_assemble_defaults_ducking_loudnorm(monkeypatch, tmp_path):
    (tmp_path / "c1.mp4").write_bytes(b"fake")
    captured: dict = {}

    def fake_concat(clips, out, **kw):
        return out

    def fake_mix(video, narration, music, output, **kw):
        captured.update(kw)
        return output

    monkeypatch.setattr(fe, "concat_videos", fake_concat)
    monkeypatch.setattr(fe, "mix_audio", fake_mix)
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 1.0}})
    result = fe.FFmpegCompose().execute({
        "operation": "assemble",
        "edit_decisions": {"cuts": [{"clip_path": str(tmp_path / "c1.mp4")}]},
        "output_path": str(tmp_path / "final.mp4"),
        "narration_path": str(tmp_path / "n.mp3"),
        "music_path": str(tmp_path / "m.mp3"),
    })
    assert result.success
    assert captured.get("ducking") is True
    assert captured.get("loudnorm") is True


def test_assemble_writes_render_report(monkeypatch, tmp_path):
    proj = tmp_path / "proj"
    (proj / "artifacts").mkdir(parents=True)
    (tmp_path / "c1.mp4").write_bytes(b"fake")
    monkeypatch.setattr(fe, "concat_videos", lambda clips, out, **kw: out)
    monkeypatch.setattr(fe, "mix_audio", lambda *a, **k: tmp_path / "final.mp4")
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": "3.5"}})
    (tmp_path / "final.mp4").write_bytes(b"out")
    result = fe.FFmpegCompose().execute({
        "operation": "assemble",
        "project_dir": str(proj),
        "edit_decisions": {"cuts": [{"clip_path": str(tmp_path / "c1.mp4")}]},
        "output_path": str(tmp_path / "final.mp4"),
    })
    assert result.success
    report = (proj / "artifacts" / "render_report.json").read_text(encoding="utf-8")
    assert "final.mp4" in report
    assert result.data["render_report"]["output_path"]


def test_assemble_can_disable_ducking(monkeypatch, tmp_path):
    (tmp_path / "c1.mp4").write_bytes(b"fake")
    captured: dict = {}

    def fake_concat(clips, out, **kw):
        return out

    def fake_mix(video, narration, music, output, **kw):
        captured.update(kw)
        return output

    monkeypatch.setattr(fe, "concat_videos", fake_concat)
    monkeypatch.setattr(fe, "mix_audio", fake_mix)
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 1.0}})
    result = fe.FFmpegCompose().execute({
        "operation": "assemble",
        "edit_decisions": {"cuts": [{"clip_path": str(tmp_path / "c1.mp4")}]},
        "output_path": str(tmp_path / "final.mp4"),
        "ducking": False,
        "loudnorm": False,
    })
    assert result.success
    assert captured.get("ducking") is False
    assert captured.get("loudnorm") is False


# ---------------------------------------------------------------------------
# 转场拼接（B1）：xfade 链命令构造
# ---------------------------------------------------------------------------

def test_stitch_transitions_command(monkeypatch, tmp_path):
    a, b, c = tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "c.mp4"
    for f in (a, b, c):
        f.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 5.0}})
    fe.stitch_with_transitions(
        [a, b, c],
        [
            {"transition": "crossfade", "transition_duration": 0.5},
            {"transition": "fade_black", "transition_duration": 1.0},
        ],
        tmp_path / "out.mp4",
    )
    assert len(captured) == 1
    cmd = " ".join(captured[0])
    # 负空隙重叠：offset = 累计时长 - 转场时长
    assert "xfade=transition=fade:duration=0.500:offset=4.500" in cmd
    assert "xfade=transition=fadeblack:duration=1.000:offset=9.000" in cmd
    assert "-an" in cmd


def test_stitch_cut_no_overlap(monkeypatch, tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    for f in (a, b):
        f.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 5.0}})
    fe.stitch_with_transitions([a, b], [{"transition": "cut"}], tmp_path / "out.mp4")
    cmd = " ".join(captured[0])
    assert "transition=cut" in cmd
    assert "offset=5.000" in cmd  # 硬切无重叠


def test_stitch_negative_gap_alias(monkeypatch, tmp_path):
    """negative_gap_seconds 等价于转场时长（edit_advisor 建议的负空隙）。"""
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    for f in (a, b):
        f.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 5.0}})
    fe.stitch_with_transitions([a, b], [{"transition": "crossfade", "negative_gap_seconds": 0.4}], tmp_path / "o.mp4")
    cmd = " ".join(captured[0])
    assert "offset=4.600" in cmd


def test_stitch_requires_two_clips(tmp_path):
    from montage.compose.ffmpeg_engine import ComposError

    try:
        fe.stitch_with_transitions([tmp_path / "a.mp4"], [], tmp_path / "o.mp4")
        assert False, "应抛出 ComposError"
    except ComposError as exc:
        assert "至少 2 个片段" in str(exc)


# ---------------------------------------------------------------------------
# 配音装配工具化（A5）：plan_narration / assemble_narration
# ---------------------------------------------------------------------------

def test_narration_operations_require_sections():
    result = fe.FFmpegCompose().execute({"operation": "plan_narration"})
    assert not result.success
    assert "sections" in result.error


def test_plan_narration_timeline_via_tool(monkeypatch):
    from montage.compose import narration

    monkeypatch.setattr(narration, "_audio_duration", lambda p: 2.0)
    result = fe.FFmpegCompose().execute({
        "operation": "plan_narration",
        "sections": [
            {"id": "a", "narration_audio": "a.wav"},
            {"id": "b", "narration_audio": "b.wav"},
        ],
        "gap_seconds": 0.5,
    })
    assert result.success
    tl = result.data["timeline"]
    assert tl[1]["start_seconds"] == 2.5


def test_assemble_narration_dispatch(monkeypatch, tmp_path):
    """assemble_narration 工具正确调用 narration.assemble_narration 并透传参数。"""
    from montage.compose import narration

    calls: list[tuple] = []

    def fake_assemble(sections, output, gap_seconds=0.4):
        calls.append((sections, output, gap_seconds))
        return {"output": str(output), "timeline": [], "total_seconds": 2.0}

    monkeypatch.setattr(narration, "assemble_narration", fake_assemble)
    result = fe.FFmpegCompose().execute({
        "operation": "assemble_narration",
        "sections": [{"id": "a", "narration_audio": "a.wav"}],
        "gap_seconds": 0.3,
        "output_path": str(tmp_path / "n.mp3"),
    })
    assert result.success
    assert calls and calls[0][2] == 0.3
