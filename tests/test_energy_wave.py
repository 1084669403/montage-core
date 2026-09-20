"""P0-5 energy_wave：能量包络 / tempo 兜底 / 拍网格 / Bar-DP 切点。

纯函数部分不碰 ffmpeg（能量包络用手造 dict 注入）；只有最后一条真跑 ffmpeg，
缺 ffmpeg 直接 skip。
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from montage.engine import energy_wave as ew

# --- 造数据 ------------------------------------------------------------------


def _envelope(
    *,
    duration: float = 16.0,
    window: float = 0.1,
    quiet_db: float = -45.0,
    loud_db: float = -20.0,
    split: float = 8.0,
) -> dict:
    """前一半安静、后一半高潮的包络（0.1s 窗）。"""
    n = int(duration / window)
    windows = []
    for i in range(n):
        t = round(i * window, 3)
        windows.append({"t": t, "db": loud_db if t >= split else quiet_db})
    return {"source": "ebur128", "window_seconds": window, "duration": duration, "windows": windows}


def _periodic_envelope(*, bpm: float, duration: float = 16.0, window: float = 0.1) -> dict:
    """拍点位置给一个尖峰（峰宽 ±半窗），自相关应该估回同一个 bpm。"""
    beat = 60.0 / bpm
    n = int(duration / window)
    windows = []
    for i in range(n):
        t = i * window
        k = round(t / beat)
        db = -18.0 if abs(t - k * beat) <= window / 2 else -50.0
        windows.append({"t": round(t, 3), "db": db})
    return {"source": "ebur128", "window_seconds": window, "duration": duration, "windows": windows}


def _grid(bpm: float = 120.0, *, beats_per_bar: int = 4, fps: float = 30.0) -> dict:
    grid = ew.beat_grid(bpm=bpm, fps=fps, beats_per_bar=beats_per_bar)
    assert grid is not None
    return grid


def _bars(envelope: dict, grid: dict) -> list[dict]:
    rows = ew.bar_energies(envelope, grid)
    assert rows
    return rows


# --- 拍网格 ------------------------------------------------------------------


def test_beat_grid_is_p04_shape_plus_bar_keys():
    grid = _grid(120.0)
    # P0-4 edit_metrics.beat_grid 的键一个不少（供货契约）
    for key in ("bpm", "fps", "offset_seconds", "start_seconds", "frames_per_beat", "source"):
        assert key in grid
    assert grid["beats_per_bar"] == 4
    assert grid["bar_seconds"] == pytest.approx(2.0)


def test_beat_grid_rejects_bad_bpm_or_fps():
    assert ew.beat_grid(bpm=0.0) is None
    assert ew.beat_grid(bpm=-120.0) is None
    assert ew.beat_grid(bpm=120.0, fps=0.0) is None


# --- tempo 兜底 ---------------------------------------------------------------


def test_estimate_tempo_recovers_periodic_bpm():
    est = ew.estimate_tempo(_periodic_envelope(bpm=120.0))
    assert est["source"] == "estimated"
    assert est["bpm"] == pytest.approx(120.0, abs=3.0)
    assert est["confidence"] > 0.3


def test_estimate_tempo_rejects_flat_and_short():
    flat = {"window_seconds": 0.1, "duration": 4.0,
            "windows": [{"t": i * 0.1, "db": -30.0} for i in range(40)]}
    est = ew.estimate_tempo(flat)
    assert est["bpm"] == 0.0
    assert "无起伏" in est["reason"]

    short = {"window_seconds": 0.1, "duration": 0.3,
             "windows": [{"t": 0.0, "db": -30.0}, {"t": 0.1, "db": -20.0}]}
    assert ew.estimate_tempo(short)["bpm"] == 0.0


# --- 能量波（bar 聚合） --------------------------------------------------------


def test_bar_energies_normalizes_quiet_low_loud_high():
    grid = _grid(120.0)
    rows = _bars(_envelope(), grid)
    quiet = [r for r in rows if r["start_seconds"] < 8.0]
    loud = [r for r in rows if r["start_seconds"] >= 8.0]
    assert quiet and loud
    assert max(r["energy"] for r in quiet) <= 0.01
    assert min(r["energy"] for r in loud) >= 0.99
    assert all(0.0 <= r["energy"] <= 1.0 for r in rows)


def test_bar_energies_empty_without_windows_or_grid():
    assert ew.bar_energies({"windows": [], "duration": 8.0}, _grid()) == []
    assert ew.bar_energies(_envelope(), {"bar_seconds": 0.0}) == []


# --- Bar-DP 切点 --------------------------------------------------------------


def _gaps(cuts: list[float], duration: float) -> list[float]:
    seq = [0.0, *cuts, duration]
    return [round(b - a, 4) for a, b in pairwise(seq)]


def test_plan_beat_cuts_density_follows_energy():
    grid = _grid(120.0)
    bars = _bars(_envelope(), grid)
    out = ew.plan_beat_cuts(bars, grid, min_hold=1.0, max_hold=4.0, duration=16.0)
    assert out["feasible"] is True
    quiet = [b for b in out["bars"] if b["start_seconds"] < 8.0]
    loud = [b for b in out["bars"] if b["start_seconds"] >= 8.0]
    assert sum(b["cuts"] for b in loud) > sum(b["cuts"] for b in quiet)
    assert out["cuts"]


def test_plan_beat_cuts_all_on_grid_and_holds_respected():
    grid = _grid(120.0)
    bars = _bars(_envelope(), grid)
    duration = 16.0
    out = ew.plan_beat_cuts(bars, grid, min_hold=1.0, max_hold=4.0, duration=duration)
    beat = 60.0 / grid["bpm"]
    for cut in out["cuts"]:
        k = cut / beat
        assert abs(k - round(k)) < 1e-6, f"切点 {cut} 不在拍位上"
    for gap in _gaps(out["cuts"], duration):
        assert gap >= 1.0 - 1e-6, f"破 min_hold: {gap}"
        assert gap <= 4.0 + 1e-6, f"破 max_hold: {gap}"


def test_plan_beat_cuts_tail_too_short_is_eaten():
    grid = _grid(120.0)
    bars = _bars(_envelope(split=7.5), grid)
    # DP 会把最后一刀落在 15.5s（末镜只剩 0.5s）→ 必须吃掉它，而不是留个碎末镜
    out = ew.plan_beat_cuts(bars, grid, min_hold=1.0, max_hold=4.0, duration=16.0)
    assert out["feasible"] is True
    assert 15.5 not in out["cuts"]
    assert all(gap >= 1.0 - 1e-6 for gap in _gaps(out["cuts"], 16.0))
    assert "末镜过短" in out["reason"]


def test_plan_beat_cuts_tail_within_hold_keeps_last_cut():
    grid = _grid(120.0)
    bars = _bars(_envelope(split=7.5), grid)
    out = ew.plan_beat_cuts(bars, grid, min_hold=1.0, max_hold=4.0, duration=16.5)
    assert 15.5 in out["cuts"]          # 末镜 1.0s，合法 → 不该被吃掉
    assert out["reason"] == ""


def test_plan_beat_cuts_respects_tight_holds():
    grid = _grid(120.0)
    bars = _bars(_envelope(), grid)
    out = ew.plan_beat_cuts(bars, grid, min_hold=2.0, max_hold=2.0, duration=16.0)
    for gap in _gaps(out["cuts"], 16.0):
        assert gap == pytest.approx(2.0, abs=1e-6)


def test_plan_beat_cuts_infeasible_branch_reports_reason(monkeypatch):
    grid = _grid(120.0)
    bars = _bars(_envelope(), grid)
    monkeypatch.setattr(ew, "_bar_cut_options", lambda *a, **k: [])
    out = ew.plan_beat_cuts(bars, grid, min_hold=1.0, max_hold=4.0, duration=16.0)
    assert out["feasible"] is False
    assert out["cuts"] == []
    assert "min_hold" in out["reason"] and "max_hold" in out["reason"]


def test_plan_beat_cuts_without_grid_is_infeasible():
    out = ew.plan_beat_cuts([], {"bpm": 0.0}, min_hold=1.0, max_hold=4.0)
    assert out["feasible"] is False
    assert "无有效拍网格" in out["reason"]


# --- 组装：build_beat_map -----------------------------------------------------


def test_build_beat_map_missing_audio_reports_reason_not_raise(tmp_path):
    out = ew.build_beat_map(tmp_path / "nope.wav", min_hold=1.0, max_hold=4.0)
    assert out["feasible"] is False
    assert out["cuts"] == []
    assert out["grid"] is None
    assert out["warnings"]


def test_build_beat_map_uses_explicit_bpm_without_estimate():
    env = _envelope()
    out = ew.build_beat_map("dummy.mp3", bpm=100.0, min_hold=1.0, max_hold=4.0, envelope=env)
    assert out["feasible"] is True
    assert out["bpm"] == 100.0
    assert out["bpm_source"] == "explicit"
    assert out["grid"]["source"] == "energy_wave"
    assert all("自相关" not in w for w in out["warnings"])


def test_build_beat_map_estimated_bpm_always_warns():
    env = _periodic_envelope(bpm=120.0)
    out = ew.build_beat_map("dummy.mp3", bpm=0.0, min_hold=1.0, max_hold=4.0, envelope=env)
    assert out["bpm_source"] == "estimated"
    assert any("非曲库数据" in w for w in out["warnings"])


def test_build_beat_map_flags_density_clamped_by_min_hold():
    env = _envelope()
    out = ew.build_beat_map("dummy.mp3", bpm=120.0, min_hold=2.0, max_hold=4.0, envelope=env)
    assert out["density_clamped"] is True
    assert any("夹紧" in w for w in out["warnings"])


def test_measure_energy_envelope_missing_file_is_empty(tmp_path):
    env = ew.measure_energy_envelope(tmp_path / "nope.wav")
    assert env["source"] == ""
    assert env["windows"] == []
    assert env["reason"]


# --- 真跑 ffmpeg（缺 ffmpeg 就 skip） ------------------------------------------


def test_real_audio_click_track_prefers_ebur128_and_splits_density(tmp_path):
    from montage.compose.ffmpeg_engine import check_ffmpeg

    if check_ffmpeg() is None:
        pytest.skip("无 ffmpeg")

    import subprocess

    path = tmp_path / "clicks.wav"
    # 120bpm 点击轨、前 8s 轻后 8s 响：既有拍又有能量对比
    subprocess.run(
        [
            check_ffmpeg(), "-y", "-v", "error",
            "-f", "lavfi", "-i",
            (
                "aevalsrc="
                "'0.06*sin(2*PI*1000*t)*exp(-40*mod(t,0.5))*(lt(t,8))+"
                "0.6*sin(2*PI*1000*t)*exp(-40*mod(t,0.5))*(gte(t,8))':d=16"
            ),
            str(path),
        ],
        check=True,
    )
    env = ew.measure_energy_envelope(path)
    assert env["source"] in {"ebur128", "pcm_rms"}
    assert len(env["windows"]) >= 100

    out = ew.build_beat_map(path, bpm=0.0, min_hold=1.0, max_hold=4.0)
    assert out["feasible"] is True
    beat = 60.0 / out["bpm"]
    for cut in out["cuts"]:
        k = cut / beat
        assert abs(k - round(k)) < 1e-6
    for gap in _gaps(out["cuts"], out["duration"]):
        assert 1.0 - 0.05 <= gap <= 4.0 + 0.05
