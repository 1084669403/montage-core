"""P0-7a：film_health 长片指标（响度一致性 / 时长口径 / 连续性抽检）。

纯函数部分手造数据，不跑 ffmpeg；VLM 一律注入假 review_fn，不出网。
"""

from __future__ import annotations

import json
from pathlib import Path

from montage.engine.artifacts import ArtifactStore
from montage.engine.project import init_project
from montage.tools.film_health import (
    MAX_CONTINUITY_SAMPLES,
    FilmHealth,
    audio_blocks,
    continuity_anchors,
    continuity_warnings,
    duration_check,
    inspect_film,
    loudness_warnings,
    probe_continuity,
)

# --- 造数据 ------------------------------------------------------------------


def _envelope(means: list[float], *, window: float = 1.0, block_seconds: float = 60.0) -> dict:
    """把「每块均值」摊成 1s 一窗的包络（块内加 ±0.4 抖动，避免伪精确）。"""
    windows = []
    for i, mean in enumerate(means):
        for k in range(int(block_seconds)):
            t = round(i * block_seconds + k * window, 3)
            windows.append({"t": t, "db": round(mean + (0.4 if k % 2 else -0.4), 3)})
    return {"source": "ebur128", "window_seconds": window, "duration": len(windows) * window, "windows": windows}


def _fake_probe(duration: float = 600.0, *, audio: bool = True) -> dict:
    streams = [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "r_frame_rate": "24/1"}]
    if audio:
        streams.append({"codec_type": "audio", "codec_name": "aac"})
    return {"format": {"duration": str(duration), "size": "100"}, "streams": streams}


# --- 响度一致性 ---------------------------------------------------------------


def test_audio_blocks_groups_by_minute_and_reports_spread():
    report = audio_blocks(_envelope([-23.0, -22.0, -14.0]))
    assert report["source"] == "ebur128" and report["unit"] == "LU"
    assert [b["start"] for b in report["blocks"]] == [0.0, 60.0, 120.0]
    assert report["blocks"][0]["mean_db"] == -23.0
    assert report["blocks"][2]["mean_db"] == -14.0
    assert report["median_db"] == -22.0
    assert report["spread"] == 9.0


def test_audio_blocks_marks_silent_block_and_reports_reason():
    envelope = _envelope([-20.0, -19.0])
    envelope["windows"] = [*envelope["windows"], {"t": 120.0, "db": None}]
    report = audio_blocks(envelope)
    assert report["blocks"][-1]["silent"] is True
    assert report["blocks"][-1]["mean_db"] is None

    empty = audio_blocks({"source": "", "windows": [], "reason": "缺少 ffmpeg"})
    assert empty["blocks"] == [] and empty["reason"] == "缺少 ffmpeg"


def test_loudness_warnings_flags_spread_and_outlier():
    warnings = loudness_warnings(audio_blocks(_envelope([-14.0, -15.0, -24.0])))
    fields = [w["field"] for w in warnings]
    assert "audio_loudness_spread" in fields
    assert "audio_loudness_outlier" in fields
    assert all("critical" not in w for w in warnings)


def test_loudness_warnings_quiet_when_consistent():
    assert loudness_warnings(audio_blocks(_envelope([-20.0, -21.0, -19.5]))) == []


def test_loudness_warnings_all_silent_and_unmeasured():
    all_silent = audio_blocks({"source": "ebur128", "windows": [{"t": 0.0, "db": None}]})
    assert "全静音" in loudness_warnings(all_silent)[0]["message"]
    unmeasured = loudness_warnings({"blocks": [], "reason": "缺少 ffmpeg"})
    assert unmeasured[0]["field"] == "audio_consistency"


def test_loudness_outlier_warnings_are_capped():
    means = [-20.0] * 20 + [-40.0, -40.0, -40.0, -40.0, -40.0]
    warnings = loudness_warnings(audio_blocks(_envelope(means)))
    outliers = [w for w in warnings if w["field"] == "audio_loudness_outlier"]
    assert len(outliers) == 4  # 3 条逐段 + 1 条汇总
    assert "未逐条列出" in outliers[-1]["message"]


# --- 时长口径 -----------------------------------------------------------------


def test_duration_check_short_film_keeps_20_percent():
    check = duration_check(100.0, 120.0)
    assert check["delta"] == 0.1667
    assert check["tolerance"] == 0.2 and check["longform"] is False
    assert check["over"] is False


def test_duration_check_longform_tightens_to_10_percent():
    check = duration_check(320.0, 360.0)
    assert check["longform"] is True and check["tolerance"] == 0.1
    assert check["delta"] == 0.1111 and check["over"] is True


def test_duration_check_explicit_tolerance_and_disabled():
    forced = duration_check(320.0, 360.0, tolerance=0.2)
    assert forced["over"] is False
    off = duration_check(10.0, 360.0, tolerance=0)
    assert off["tolerance"] == 0.0 and off["over"] is False and off["delta"] is not None


def test_duration_check_without_target_reports_nothing():
    check = duration_check(100.0, None)
    assert check["delta"] is None and check["over"] is False


def test_inspect_film_warns_with_actual_delta(tmp_path):
    clip = tmp_path / "final.mp4"
    clip.write_bytes(b"vid")
    report = inspect_film(clip, expected_duration=360.0, probe_fn=lambda _p: _fake_probe(320.0))
    assert report["duration_check"]["delta"] == 0.1111
    message = next(w["message"] for w in report["warnings"] if w["field"] == "duration")
    assert "11.1%" in message and "10%" in message and "长片口径" in message


def test_inspect_film_does_not_touch_pass_on_duration(tmp_path):
    clip = tmp_path / "final.mp4"
    clip.write_bytes(b"vid")
    report = inspect_film(clip, expected_duration=100.0, probe_fn=lambda _p: _fake_probe(1000.0))
    assert report["pass"] is True and report["critical"] == []


# --- 连续性抽检 ---------------------------------------------------------------


def test_continuity_anchors_prefers_scene_index_and_spreads():
    scene_index = {"units": [{"unit_id": f"u{i}", "start_seconds": float(i * 10)} for i in range(40)]}
    anchors = continuity_anchors(duration=400.0, scene_index=scene_index, samples=4)
    assert [a["unit_id"] for a in anchors] == ["u0", "u13", "u26", "u39"]


def test_continuity_anchors_falls_back_to_even_sampling():
    anchors = continuity_anchors(duration=400.0, scene_index=None, samples=2)
    assert [a["t"] for a in anchors] == [100.0, 300.0]
    assert continuity_anchors(duration=400.0, scene_index=None, samples=0) == []


def test_continuity_anchors_caps_samples():
    scene_index = {"units": [{"unit_id": f"u{i}", "start_seconds": float(i * 30)} for i in range(60)]}
    anchors = continuity_anchors(duration=1800.0, scene_index=scene_index, samples=99)
    assert len(anchors) == MAX_CONTINUITY_SAMPLES


def _review_factory(calls: list[list[float]], *, drift_at: set[float] | None = None, skipped: bool = False):
    def _review(*, media_path, expected, mode, timestamps):
        calls.append(list(timestamps))
        if skipped:
            return {"ok": False, "skipped": True, "issues": [], "sampled": {}}
        drift = timestamps[0] in (drift_at or set())
        issues = (
            [{"severity": "critical", "kind": "人物不一致", "message": "发色变了"}]
            if drift else []
        )
        return {
            "ok": not drift,
            "skipped": False,
            "score": 0.5 if drift else 0.9,
            "issues": issues,
            "sampled": {"mode": "timestamps", "frames": 1, "sampled": list(timestamps), "missed": []},
        }

    return _review


def test_probe_continuity_counts_drift(tmp_path):
    film = tmp_path / "final.mp4"
    film.write_bytes(b"vid")
    calls: list[list[float]] = []
    report = probe_continuity(
        film,
        anchors=[{"t": 10.0, "unit_id": "u0"}, {"t": 20.0, "unit_id": "u1"}],
        expected={"appearance": "黑发"},
        review_fn=_review_factory(calls, drift_at={20.0}),
    )
    assert report["requested"] == 2 and report["drift"] == 1 and report["critical"] == 1
    assert report["skipped"] is False
    assert [s["unit_id"] for s in report["samples"]] == ["u0", "u1"]
    assert calls == [[10.0], [20.0]]


def test_probe_continuity_stops_when_vlm_not_configured(tmp_path):
    film = tmp_path / "final.mp4"
    film.write_bytes(b"vid")
    calls: list[list[float]] = []
    report = probe_continuity(
        film,
        anchors=[{"t": 10.0}, {"t": 20.0}, {"t": 30.0}],
        review_fn=_review_factory(calls, skipped=True),
    )
    assert report["skipped"] is True
    assert len(calls) == 1
    assert "DASHSCOPE_API_KEY" in report["reason"]


def test_probe_continuity_counts_failed_frame_extraction(tmp_path):
    film = tmp_path / "final.mp4"
    film.write_bytes(b"vid")

    def _review(*, media_path, expected, mode, timestamps):
        return {"ok": True, "skipped": False, "issues": [], "sampled": {"sampled": [], "missed": [timestamps[0]]}}

    report = probe_continuity(film, anchors=[{"t": 10.0}], review_fn=_review)
    assert report["failed"] == 1 and report["sampled"] == 0


def test_continuity_warnings_are_advisory_only():
    assert continuity_warnings({"requested": 0}) == []
    drift = continuity_warnings({"requested": 4, "drift": 2, "failed": 0, "samples": []})
    assert drift[0]["field"] == "continuity_drift" and "不挡 export" in drift[0]["message"]
    skipped = continuity_warnings({"requested": 3, "skipped": True, "reason": "缺少 DASHSCOPE_API_KEY（VLM 未配置）"})
    assert "未执行" in skipped[0]["message"]


# --- 工具层接线 ---------------------------------------------------------------


def _project(tmp_path: Path, *, chapters: bool = False) -> Path:
    proj = init_project(tmp_path, "p", "测试片", "cinematic")
    store = ArtifactStore(proj)
    bible: dict = {"title": "测试片", "target_duration_seconds": 360.0}
    if chapters:
        bible["chapters"] = [{"id": "ch01", "title": "第一段"}]
    bible["characters"] = [{"id": "c1", "name": "阿岚", "appearance": "黑长直", "outfit": "深蓝风衣"}]
    store.write("series_bible", bible, schema=None)
    store.write("scene_index", {
        "version": "v1",
        "shots": [{"shot_id": "s0"}],
        "units": [{"unit_id": "u0", "start_seconds": 5.0}],
    }, schema=None)
    return proj


def test_tool_falls_back_to_auto_edit_final(tmp_path):
    proj = _project(tmp_path)
    (proj / "auto_edit").mkdir()
    (proj / "auto_edit" / "final.mp4").write_bytes(b"vid")
    result = FilmHealth().execute({"project_dir": str(proj)})
    assert result.success
    assert result.data["path_source"] == "auto_edit"
    assert result.data["path"].endswith("auto_edit\\final.mp4") or result.data["path"].endswith("auto_edit/final.mp4")


def test_tool_prefers_renders_over_auto_edit(tmp_path):
    proj = _project(tmp_path)
    (proj / "renders").mkdir(exist_ok=True)
    (proj / "renders" / "final.mp4").write_bytes(b"vid")
    (proj / "auto_edit").mkdir()
    (proj / "auto_edit" / "final.mp4").write_bytes(b"vid")
    result = FilmHealth().execute({"project_dir": str(proj)})
    assert result.data["path_source"] == "renders"


def test_tool_uses_chapters_for_longform_tolerance(tmp_path, monkeypatch):
    proj = _project(tmp_path, chapters=True)
    (proj / "renders").mkdir(exist_ok=True)
    (proj / "renders" / "final.mp4").write_bytes(b"vid")
    monkeypatch.setattr(
        "montage.tools.film_health.probe",
        lambda _p: _fake_probe(300.0),
    )
    monkeypatch.setattr("montage.tools.film_health.check_ffprobe", lambda: "ffprobe")
    result = FilmHealth().execute({"project_dir": str(proj)})
    check = result.data["duration_check"]
    assert check["longform"] is True and check["tolerance"] == 0.1 and check["over"] is True


def test_tool_skips_loudness_without_audio_stream(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    (proj / "renders").mkdir(exist_ok=True)
    (proj / "renders" / "final.mp4").write_bytes(b"vid")
    monkeypatch.setattr("montage.tools.film_health.probe", lambda _p: _fake_probe(360.0, audio=False))
    monkeypatch.setattr("montage.tools.film_health.check_ffprobe", lambda: "ffprobe")
    called: list[str] = []

    def _loudness(*_a, **_k):
        called.append("loudness")
        return {}

    monkeypatch.setattr("montage.tools.film_health.probe_loudness", _loudness)
    calls: list[list[float]] = []
    monkeypatch.setattr("montage.tools.vlm_reviewer.review_media", _review_factory(calls))
    result = FilmHealth().execute({"project_dir": str(proj), "continuity_samples": 4})
    assert "audio_consistency" not in result.data
    assert called == []            # 无音轨 → 不跑响度
    assert "continuity" in result.data  # 抽检是画面的活，与音轨无关
    assert len(calls) == 1


def test_tool_disabled_by_default_never_calls_vlm(tmp_path, monkeypatch):
    """默认路径（produce 走的就是它）零 API 增量：不抽检。"""
    proj = _project(tmp_path)
    (proj / "renders").mkdir(exist_ok=True)
    (proj / "renders" / "final.mp4").write_bytes(b"vid")
    monkeypatch.setattr("montage.tools.film_health.probe", lambda _p: _fake_probe(360.0))
    monkeypatch.setattr("montage.tools.film_health.check_ffprobe", lambda: "ffprobe")
    monkeypatch.setattr("montage.tools.film_health.probe_loudness", lambda *a, **k: audio_blocks(_envelope([-20.0])))
    reached: list[float] = []

    def _boom(*, media_path, expected, mode, timestamps):
        reached.append(timestamps[0])
        raise AssertionError("默认不该动 VLM")

    monkeypatch.setattr("montage.tools.vlm_reviewer.review_media", _boom)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake")
    result = FilmHealth().execute({"project_dir": str(proj)})
    assert reached == []
    assert "continuity" not in result.data
    assert result.data["audio_consistency"]["blocks"]
    assert FilmHealth().estimate_cost({"continuity_samples": 0}) == 0.0


def test_expected_reference_merges_bible_registry_and_continuity(tmp_path):
    from montage.tools.film_health import _expected_reference

    proj = _project(tmp_path)
    store = ArtifactStore(proj)
    store.write("scene_plan", {"character_registry": [
        {"id": "c1", "name": "阿岚", "appearance": "黑长直", "outfit_anchor": "深蓝风衣"},
    ]}, schema=None)
    store.write("continuity", {"characters": [{"id": "c1", "outfit": "深蓝风衣"}]}, schema=None)
    ref = _expected_reference(proj)
    assert ref["appearance"].count("黑长直") == 1        # bible 与 registry 逐字相同 → 去重
    assert "阿岚" in ref["appearance"] and "深蓝风衣" in ref["outfit"]
    assert ref["source"] == "series_bible+scene_plan+continuity"


def test_tool_writes_continuity_and_caps_samples(tmp_path, monkeypatch):
    proj = _project(tmp_path)
    (proj / "renders").mkdir(exist_ok=True)
    (proj / "renders" / "final.mp4").write_bytes(b"vid")
    monkeypatch.setattr("montage.tools.film_health.probe", lambda _p: _fake_probe(360.0))
    monkeypatch.setattr("montage.tools.film_health.check_ffprobe", lambda: "ffprobe")
    monkeypatch.setattr("montage.tools.film_health.probe_loudness", lambda *a, **k: audio_blocks(_envelope([-20.0])))
    calls: list[list[float]] = []
    monkeypatch.setattr(
        "montage.tools.vlm_reviewer.review_media",
        _review_factory(calls, drift_at={5.0}),
    )
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake")
    result = FilmHealth().execute({"project_dir": str(proj), "continuity_samples": 99})
    assert len(calls) <= MAX_CONTINUITY_SAMPLES
    continuous = result.data["continuity"]
    assert continuous["drift"] == 1
    # 默认参考文本取自 bible 定妆（阿岚/黑长直/深蓝风衣）
    assert result.data["continuity"]["expected_source"] == "series_bible"
    assert any(w["field"] == "continuity_drift" for w in result.data["warnings"])
    assert any("截断" in w["message"] for w in result.data["warnings"] if w["field"] == "continuity_sample")
    # 抽检结论绝不进 critical
    assert result.data["critical"] == []
    saved = json.loads((proj / "artifacts" / "film_health.json").read_text(encoding="utf-8"))
    assert saved["continuity"]["drift"] == 1
    assert FilmHealth().estimate_cost({"continuity_samples": 4}) > 0
