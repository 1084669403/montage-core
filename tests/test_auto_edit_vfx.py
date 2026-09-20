"""P0-8 第 2.5 刀 + auto_edit vfx 后续小刀。

覆盖：
- onset 缺省吸附 beat_map 能量峰（镜内相对秒）
- 显式 onset 优先、无 beat_map 回落 0
- replan 写/清 vfx → plan_fingerprint 变
- 改 vfx 不重 trim，但 stitch 因 plan_sha 失效重跑
- CLI --vfx 解析
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from montage.tools import auto_edit as ae
from montage.tools.auto_edit import (
    build_plan,
    plan_diff,
    plan_fingerprint,
    render_plan,
    replan,
    resolve_shot_vfx,
    sanitize_shot_vfx,
)


def test_sanitize_drops_bad_and_caps() -> None:
    rows = sanitize_shot_vfx([
        {"layer": "post", "kind": "impact_flash", "onset": 0.5},
        {"layer": "bogus", "kind": "x"},
        "not-a-dict",
        {"layer": "prompt", "kind": "青色剑气"},
        {"layer": "post", "kind": "zoom_punch"},
        {"layer": "post", "kind": "camera_shake"},
        {"layer": "post", "kind": "extra"},  # 第 5 条被上限裁掉
    ])
    assert len(rows) == 4
    assert rows[0]["kind"] == "impact_flash"
    assert rows[1]["layer"] == "prompt"


def test_resolve_snaps_to_peak_energy_bar_relative() -> None:
    """onset 缺省 → 吸附镜窗内能量最高 bar，返回镜内相对秒。"""
    shot = {"start": 2.0, "end": 6.0, "vfx": [{"layer": "post", "kind": "impact_flash"}]}
    beat_map = {
        "bars": [
            {"start_seconds": 0.0, "end_seconds": 2.0, "energy": 0.9},  # 与镜无重叠（边界）
            {"start_seconds": 2.0, "end_seconds": 4.0, "energy": 0.3},
            {"start_seconds": 4.0, "end_seconds": 6.0, "energy": 0.8},  # 最高且重叠
        ],
    }
    out = resolve_shot_vfx(shot, beat_map)
    assert out[0]["onset"] == pytest.approx(2.0)  # 4.0 - 2.0


def test_resolve_explicit_onset_wins() -> None:
    shot = {
        "start": 0.0, "end": 4.0,
        "vfx": [{"layer": "post", "kind": "impact_flash", "onset": 0.12}],
    }
    beat_map = {"bars": [{"start_seconds": 2.0, "end_seconds": 4.0, "energy": 1.0}]}
    out = resolve_shot_vfx(shot, beat_map)
    assert out[0]["onset"] == pytest.approx(0.12)


def test_resolve_no_beat_map_falls_back_to_zero() -> None:
    shot = {"start": 1.0, "end": 3.0, "vfx": [{"layer": "post", "kind": "zoom_punch"}]}
    out = resolve_shot_vfx(shot, None)
    assert out[0]["onset"] == 0.0


def test_resolve_prompt_layer_untouched() -> None:
    shot = {
        "start": 0.0, "end": 4.0,
        "vfx": [
            {"layer": "prompt", "kind": "青色剑气"},
            {"layer": "post", "kind": "impact_flash"},
        ],
    }
    out = resolve_shot_vfx(shot, {"bars": [{"start_seconds": 1.0, "end_seconds": 2.0, "energy": 1.0}]})
    assert "onset" not in out[0]  # prompt 不吸附
    assert out[1]["onset"] == pytest.approx(1.0)


def test_replan_writes_and_clears_vfx() -> None:
    src = Path("dummy")  # build_plan 只记路径字符串
    plan = build_plan(
        source_decl={
            "kind": "video", "has_bgm": False, "is_speech": False, "language": "zh",
            "files": [{"path": str(src), "sha256": "x", "duration": 8.0}],
        },
        style_pack_id="beat",
        scene_changes_by_source=[[4.0]],
        pack_overrides={"lut": ""},
    )
    sid = plan["segments"][0]["shots"][0]["shot_id"]
    before = plan_fingerprint(plan)
    nxt = replan(plan, {sid: {"vfx": [{"layer": "post", "kind": "impact_flash", "onset": 0.2}]}})
    assert nxt["segments"][0]["shots"][0]["vfx"][0]["kind"] == "impact_flash"
    assert plan_fingerprint(nxt) != before
    cleared = replan(nxt, {sid: {"vfx": []}})
    assert "vfx" not in cleared["segments"][0]["shots"][0]
    assert plan_fingerprint(cleared) == before


def test_plan_diff_reports_vfx_change() -> None:
    a = {
        "segments": [{"segment_id": "s", "action": "keep",
                      "shots": [{"shot_id": "a", "start": 0, "end": 2, "speed": 1.0}]}],
        "params": {}, "edits": [],
    }
    b = json.loads(json.dumps(a))
    b["segments"][0]["shots"][0]["vfx"] = [{"layer": "post", "kind": "impact_flash", "onset": 0.1}]
    diff = plan_diff(a, b)
    assert diff["shots_vfxed"] == ["a"]
    assert diff["counts"]["vfxed"] == 1
    assert diff["unchanged"] is False


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    from montage.compose import effects
    from montage.compose import ffmpeg_engine as fe
    from montage.tools import system_probe as sp

    calls: list[tuple] = []

    def fake_trim(src_path, dst, start, duration):
        calls.append(("trim", round(float(start), 3), round(float(duration), 3)))
        Path(dst).write_bytes(f"clip@{start}".encode())
        return dst

    def fake_vfx(src, dst, vfx_list, *, work_dir=None):
        calls.append(("vfx", Path(dst).name, len(vfx_list or [])))
        Path(dst).write_bytes(b"vfxed")
        return Path(dst)

    monkeypatch.setattr(effects, "change_speed", lambda src, dst, *, factor: (Path(dst).write_bytes(b"sped"), dst)[1])
    monkeypatch.setattr(effects, "cut_silence", lambda src, dst, **kw: (Path(dst).write_bytes(b"sil"), dst)[1])
    monkeypatch.setattr(effects, "apply_post_vfx", fake_vfx)
    monkeypatch.setattr(fe, "trim_clip", fake_trim)
    monkeypatch.setattr(fe, "stitch_with_transitions", lambda clips, tr, out, **kw: (Path(out).write_bytes(b"s"), out)[1])
    monkeypatch.setattr(fe, "concat_videos", lambda clips, out, **kw: (Path(out).write_bytes(b"c"), out)[1])
    monkeypatch.setattr(fe, "apply_profile", lambda src, name, out: (Path(out).write_bytes(b"p"), out)[1])
    monkeypatch.setattr(fe, "apply_lut", lambda *a, **k: a[2])
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": "6.0"}})
    monkeypatch.setattr(ae, "ffprobe", lambda p: {"format": {"duration": "6.0"}})
    monkeypatch.setattr(ae, "resolve_lut_path", lambda lut_id: None)
    monkeypatch.setattr(sp, "build_hardware_profile", lambda: {"encode": {"crf": 18, "preset": "medium"}})
    return calls


def _plan_for(tmp_path, *, changes=(4.0,)):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"source")
    return build_plan(
        source_decl={
            "kind": "video", "has_bgm": False, "is_speech": False, "language": "zh",
            "files": [{"path": str(src), "sha256": ae.hash_file(src), "duration": 8.0}],
        },
        style_pack_id="beat",
        scene_changes_by_source=[list(changes)],
        session_id="vfx1",
        pack_overrides={"lut": ""},
    )


def test_vfx_change_reuses_trim_but_reruns_stitch(tmp_path, fake_ffmpeg) -> None:
    """改 vfx：裁剪缓存命中（不重 trim），plan_sha 变 → stitch 重跑 + 应用特效。"""
    plan = _plan_for(tmp_path)
    proj = tmp_path / "proj"
    render_plan(proj, plan, preview=False, resume=True)
    fake_ffmpeg.clear()

    sid = plan["segments"][0]["shots"][0]["shot_id"]
    changed = replan(plan, {sid: {"vfx": [{"layer": "post", "kind": "impact_flash", "onset": 0.2}]}})
    report = render_plan(proj, changed, preview=False, resume=True)

    trims = [c for c in fake_ffmpeg if c[0] == "trim"]
    assert trims == [], "改 vfx 不得重 trim"
    vfx_calls = [c for c in fake_ffmpeg if c[0] == "vfx"]
    assert len(vfx_calls) == 1
    statuses = {row["step"]: row["status"] for row in report["render_log"]}
    assert statuses["stitch"] == "done"
    assert report["vfx_clips"] == 1
    assert report["plan_sha"] == plan_fingerprint(changed)


def test_cli_load_vfx_arg(tmp_path) -> None:
    from montage.cli import _load_vfx_arg

    inline = _load_vfx_arg('{"seg_0_shot_0":[{"layer":"post","kind":"impact_flash"}]}')
    assert inline["seg_0_shot_0"][0]["kind"] == "impact_flash"

    path = tmp_path / "vfx.json"
    path.write_text(json.dumps({"a": {"layer": "post", "kind": "zoom_punch"}}), encoding="utf-8")
    from_file = _load_vfx_arg(str(path))
    assert isinstance(from_file["a"], list)
    assert from_file["a"][0]["kind"] == "zoom_punch"
