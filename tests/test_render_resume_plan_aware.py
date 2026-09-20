"""render_plan 的 resume 缓存必须认 plan 版本（P0-3 关键回归）。

背景：单镜中间产物原来按**序号**命名（`shot_0000.mp4`），`stitch/grade/profile`
的断点也只看「文件在不在 + sha256 对不对」。结果人工精修 / replan / revert 改完 plan
再渲，会静默复用上一版的裁剪结果——**改完 plan 重渲出旧片**。

这组测试把「plan 变了必须重跑」钉死，同时保证「plan 没变仍然复用」（断点不能退化成全量重跑）。
"""

from __future__ import annotations

import json

import pytest

from montage.tools import auto_edit as ae
from montage.tools.auto_edit import (
    build_plan,
    clip_cache_name,
    plan_fingerprint,
    render_plan,
)


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """替换 ffmpeg 副作用，并把每次 trim / stitch / profile 调用记下来。"""
    from montage.compose import effects
    from montage.compose import ffmpeg_engine as fe
    from montage.tools import system_probe as sp

    calls: list[tuple[str, ...]] = []

    def fake_trim(src_path, dst, start, duration):
        calls.append(("trim", round(float(start), 3), round(float(duration), 3)))
        dst.write_bytes(f"clip@{start}".encode())
        return dst

    def fake_change_speed(src_path, dst, *, factor):
        calls.append(("speed", round(float(factor), 3)))
        dst.write_bytes(b"sped")
        return dst

    def fake_stitch(clips, transitions, output, **kwargs):
        calls.append(("stitch", len(clips)))
        output.write_bytes(b"stitched")
        return output

    def fake_profile(src_path, name, output):
        calls.append(("profile", name))
        output.write_bytes(b"profiled")
        return output

    monkeypatch.setattr(effects, "change_speed", fake_change_speed)
    monkeypatch.setattr(effects, "cut_silence", lambda src, dst, **kw: (dst.write_bytes(b"sil"), dst)[1])
    monkeypatch.setattr(fe, "trim_clip", fake_trim)
    monkeypatch.setattr(fe, "stitch_with_transitions", fake_stitch)
    monkeypatch.setattr(fe, "concat_videos", lambda clips, output, **kw: (output.write_bytes(b"c"), output)[1])
    monkeypatch.setattr(fe, "apply_profile", fake_profile)
    monkeypatch.setattr(fe, "apply_lut", lambda *a, **k: a[2])
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": "6.0"}})
    monkeypatch.setattr(ae, "ffprobe", lambda p: {"format": {"duration": "6.0"}})
    monkeypatch.setattr(ae, "resolve_lut_path", lambda lut_id: None)
    monkeypatch.setattr(sp, "build_hardware_profile", lambda: {"encode": {"crf": 18, "preset": "medium"}})
    return calls


def _plan_for(tmp_path, *, changes=(4.0,)):
    from montage.tools.auto_edit import hash_file

    src = tmp_path / "src.mp4"
    src.write_bytes(b"source")
    return build_plan(
        source_decl={
            "kind": "video", "has_bgm": False, "is_speech": False, "language": "zh",
            "files": [{"path": str(src), "sha256": hash_file(src), "duration": 8.0}],
        },
        style_pack_id="beat",
        scene_changes_by_source=[list(changes)],
        session_id="m1",
        pack_overrides={"lut": ""},
    )


def _trims(calls):
    return [c for c in calls if c[0] == "trim"]


def test_changed_shot_is_retrimmed_after_plan_change(tmp_path, fake_ffmpeg):
    """核心回归：改了镜的 in/out 再渲 ⇒ 必须重跑那一镜（原来 0 镜重跑）。"""
    plan = _plan_for(tmp_path)
    proj = tmp_path / "proj"
    render_plan(proj, plan, preview=False, resume=True)
    first = _trims(fake_ffmpeg)
    assert len(first) == 4

    fake_ffmpeg.clear()
    changed = json.loads(json.dumps(plan))
    changed["segments"][0]["shots"][0]["start"] = 1.5
    changed["segments"][0]["shots"][0]["end"] = 4.0
    render_plan(proj, changed, preview=False, resume=True)
    retrimmed = _trims(fake_ffmpeg)
    assert retrimmed, "改过的镜必须重 trim，不能复用上一版的裁剪结果"
    assert (1.5, 2.5) in [(s, d) for _op, s, d in retrimmed]


def test_unchanged_plan_still_reuses_clips(tmp_path, fake_ffmpeg):
    """断点不能退化：plan 没变时单镜产物仍复用（否则每次全量重跑）。"""
    plan = _plan_for(tmp_path)
    proj = tmp_path / "proj"
    render_plan(proj, plan, preview=False, resume=True)
    fake_ffmpeg.clear()
    render_plan(proj, plan, preview=False, resume=True)
    assert _trims(fake_ffmpeg) == []
    assert ("stitch", 4) not in fake_ffmpeg
    assert ("profile", "youtube_landscape") not in fake_ffmpeg


def test_speed_change_invalidates_that_clip_only(tmp_path, fake_ffmpeg):
    plan = _plan_for(tmp_path)
    proj = tmp_path / "proj"
    render_plan(proj, plan, preview=False, resume=True)
    fake_ffmpeg.clear()
    changed = json.loads(json.dumps(plan))
    changed["segments"][0]["shots"][1]["speed"] = 1.2
    render_plan(proj, changed, preview=False, resume=True)
    retrimmed = _trims(fake_ffmpeg)
    assert len(retrimmed) == 1, "只有改速率的那一镜要重做"
    assert retrimmed[0][1:] == (2.0, 2.0)   # 第二镜的位置
    assert ("speed", 1.2) in fake_ffmpeg


def test_step_complete_rejects_other_plan_version(tmp_path):
    out = tmp_path / "tmp_autoedit" / "x.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"payload")
    ae.mark_step(tmp_path, "stitch", out, plan_sha="aaaa")
    assert ae.step_complete(tmp_path, "stitch", plan_sha="aaaa") == out
    assert ae.step_complete(tmp_path, "stitch", plan_sha="bbbb") is None   # 别的版本不复用
    assert ae.step_complete(tmp_path, "stitch") == out                     # 不传=旧行为


def test_render_log_records_rerun_after_plan_change(tmp_path, fake_ffmpeg):
    plan = _plan_for(tmp_path)
    proj = tmp_path / "proj"
    render_plan(proj, plan, preview=False, resume=True)
    fake_ffmpeg.clear()
    changed = json.loads(json.dumps(plan))
    changed["segments"][0]["shots"][0]["end"] = 3.5
    report = render_plan(proj, changed, preview=False, resume=True)
    statuses = {row["step"]: row["status"] for row in report["render_log"]}
    assert statuses["stitch"] == "done", "plan 变了 stitch 必须重算"
    assert statuses["profile"] == "done"
    assert report["plan_sha"] == plan_fingerprint(changed)


def test_report_carries_plan_sha(tmp_path, fake_ffmpeg):
    plan = _plan_for(tmp_path)
    report = render_plan(tmp_path / "proj", plan, preview=False, resume=True)
    assert report["plan_sha"] == plan_fingerprint(plan)
    on_disk = json.loads((tmp_path / "proj" / "auto_edit" / "report.json").read_text(encoding="utf-8"))
    assert on_disk["plan_sha"] == plan_fingerprint(plan)


def test_clip_cache_name_is_content_addressed():
    shot = {"shot_id": "s0", "start": 0.0, "end": 2.0, "speed": 1.0}
    a = clip_cache_name(0, shot, "src.mp4")
    assert a == clip_cache_name(0, dict(shot), "src.mp4")          # 稳定
    assert a != clip_cache_name(0, {**shot, "end": 2.5}, "src.mp4")  # 内容变则名字变
    assert a != clip_cache_name(0, shot, "other.mp4")
    assert a != clip_cache_name(0, shot, "src.mp4", is_speech=True)
    assert a.startswith("shot_0000_") and a.endswith(".mp4")


def test_index_shift_does_not_collide(tmp_path, fake_ffmpeg):
    """删掉前面的镜导致序号平移：新占位的镜不能捡到旧文件。"""
    plan = _plan_for(tmp_path)
    proj = tmp_path / "proj"
    render_plan(proj, plan, preview=False, resume=True)
    fake_ffmpeg.clear()
    shifted = json.loads(json.dumps(plan))
    shifted["segments"][0]["shots"] = shifted["segments"][0]["shots"][2:]   # 只剩后两镜
    render_plan(proj, shifted, preview=False, resume=True)
    assert len(_trims(fake_ffmpeg)) == 2
