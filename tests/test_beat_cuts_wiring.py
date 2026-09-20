"""P0-5 接线：auto_edit 用能量波接管切点 + 量规层 circular 标注。

纯逻辑/接线测试（ffprobe 与场景检测都打桩）；energy_wave 的算法本身在
``tests/test_energy_wave.py``。不调 ffmpeg。
"""

from __future__ import annotations

import json
from pathlib import Path

from montage.engine.edit_metrics import beat_grid, compute_edit_metrics, metric_summary
from montage.tools import auto_edit as ae
from montage.tools.auto_edit import (
    AutoEdit,
    apply_beat_cuts,
    beat_map_contour,
    beat_map_summary,
    load_beat_map,
)

# --- 造数据 ------------------------------------------------------------------


def _fake_beat_map(*, bpm: float = 120.0, cuts: list[float] | None = None) -> dict:
    return {
        "version": "1.0",
        "operation": "beat_map",
        "bpm": bpm,
        "bpm_source": "explicit",
        "beats_per_bar": 4,
        "min_hold": 4.0,
        "max_hold": 12.0,
        "energy_source": "ebur128",
        "duration": 20.0,
        "cuts": [],
        "sources": [
            {
                "path": "/abs/src0.mp4",
                "feasible": True,
                "cuts": [4.0, 8.0, 12.0] if cuts is None else cuts,
                "energy_source": "ebur128",
                "duration": 20.0,
                "warnings": [],
            },
        ],
        "warnings": [],
    }


def _decl(n: int = 1, duration: float = 20.0) -> dict:
    return {
        "kind": "video",
        "has_bgm": True,
        "is_speech": False,
        "language": "zh",
        "files": [
            {"path": f"/abs/src{i}.mp4", "sha256": f"ab{i}", "duration": duration}
            for i in range(n)
        ],
    }


def _stub_plan_deps(monkeypatch, *, changes: list[list[float]], beat_map: dict | None, calls: list):
    monkeypatch.setattr(ae, "probe_sources", lambda paths, **kw: _decl(len(paths)))
    monkeypatch.setattr(
        ae, "analyze_scene_changes",
        lambda path, threshold=0.3: list(changes[int(Path(path).stem[3:])]),
    )

    def _fake(paths, **kwargs):
        calls.append({"paths": [str(p) for p in paths], "kwargs": kwargs})
        return beat_map if beat_map is not None else _fake_beat_map()

    monkeypatch.setattr(ae, "plan_beat_cuts_for_sources", _fake)


# --- apply_beat_cuts ----------------------------------------------------------


def test_apply_beat_cuts_replaces_feasible_source_only():
    beat_map = _fake_beat_map()
    beat_map["sources"].append({
        "path": "/abs/src1.mp4", "feasible": False, "cuts": [],
        "warnings": ["bpm 缺失且估不出拍"], "reason": "",
    })
    merged, note = apply_beat_cuts([[5.0, 9.0], [3.0, 7.0]], beat_map)
    assert merged[0] == [4.0, 8.0, 12.0]
    assert merged[1] == [3.0, 7.0]          # 不可行源保持 scene-change 切点
    assert note["used"] is True
    assert [r["mode"] for r in note["rows"]] == ["replaced", "kept_scene_cuts"]
    assert note["n_cuts"] == 5


def test_apply_beat_cuts_all_infeasible_is_not_used():
    beat_map = _fake_beat_map(cuts=[])
    merged, note = apply_beat_cuts([[5.0], [3.0]], beat_map)
    assert merged == [[5.0], [3.0]]
    assert note["used"] is False
    assert note["reasons"]


# --- 产物读写 -----------------------------------------------------------------


def test_load_beat_map_tolerates_garbage(tmp_path):
    assert load_beat_map(tmp_path) is None
    path = ae.beat_map_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{坏 json", encoding="utf-8")
    assert load_beat_map(tmp_path) is None


def test_beat_map_contour_maps_bars_to_rms_db():
    beat_map = _fake_beat_map()
    beat_map["bars"] = [
        {"start_seconds": 0.0, "end_seconds": 2.0, "mean_db": -30.0},
        {"start_seconds": 2.0, "end_seconds": 4.0, "mean_db": -18.5},
        {"start_seconds": 4.0, "end_seconds": 6.0},
    ]
    rows = beat_map_contour(beat_map)
    assert rows == [
        {"start_seconds": 0.0, "end_seconds": 2.0, "rms_db": -30.0},
        {"start_seconds": 2.0, "end_seconds": 4.0, "rms_db": -18.5},
    ]
    assert beat_map_contour(None) == []


def test_beat_map_summary_is_compact():
    beat_map = _fake_beat_map()
    beat_map["cuts_by_source"] = {
        "used": True,
        "rows": [{"index": 0, "path": "/abs/src0.mp4", "mode": "replaced", "before": 2, "after": 3}],
    }
    summary = beat_map_summary(beat_map)
    assert summary["used"] is True
    assert summary["bpm"] == 120.0
    assert summary["per_source"] == [{
        "path": "/abs/src0.mp4", "feasible": True, "cuts": 3,
        "mode": "replaced", "scene_cuts_before": 2, "replaced": 3,
    }]
    assert "sources" not in summary          # 大数组不外传
    assert beat_map_summary(None)["used"] is False


def test_plan_beat_cuts_for_sources_marks_feasible_and_keeps_source_order(monkeypatch):
    """顶层 feasible 是量规层判定 circular 的依据；缺了就等于白跑（真跑时踩过）。"""
    from montage.engine import energy_wave as ew

    def _fake(path, **kwargs):
        ok = Path(path).name == "good.mp4"
        return {
            "path": str(path), "bpm": 120.0 if ok else 0.0,
            "bpm_source": "explicit" if ok else "",
            "grid": {"bpm": 120.0} if ok else None,
            "cuts": [4.0, 8.0] if ok else [],
            "bars": [], "energy_source": "ebur128" if ok else "",
            "duration": 16.0, "feasible": ok,
            "warnings": [] if ok else ["测不出"],
        }

    monkeypatch.setattr(ew, "build_beat_map", _fake)
    out = ae.plan_beat_cuts_for_sources(
        [Path("/abs/bad.mp4"), Path("/abs/good.mp4")], bpm=120.0, min_hold=1.0, max_hold=4.0,
    )
    assert out["feasible"] is True
    assert [r["path"].endswith(("bad.mp4", "good.mp4")) for r in out["sources"]] == [True, True]
    assert out["sources"][0]["feasible"] is False      # 单源失败不牵连另一源
    assert out["warnings"]
    # 顶层必须自带 grid/bars/cuts：量规层只读顶层，漏了 m5/m6 会静默退回 soundtrack
    assert out["grid"]["bpm"] == 120.0
    assert out["cuts"] == [4.0, 8.0]
    assert isinstance(out["bars"], list)

    monkeypatch.setattr(ew, "build_beat_map", lambda path, **kw: _fake("/abs/bad.mp4", **kw))
    failed = ae.plan_beat_cuts_for_sources([Path("/abs/bad.mp4")])
    assert failed["feasible"] is False
    assert failed["grid"] is None
    assert failed["cuts"] == []


# --- _plan 接线 ---------------------------------------------------------------


def test_plan_uses_beat_cuts_and_persists_beat_map(tmp_path, monkeypatch):
    calls: list = []
    _stub_plan_deps(monkeypatch, changes=[[5.0, 9.0, 15.0]], beat_map=_fake_beat_map(), calls=calls)

    result = AutoEdit().execute({
        "operation": "plan", "project_dir": str(tmp_path),
        "video": str(tmp_path / "src0.mp4"), "style": "documentary", "has_bgm": True,
    })
    assert result.success, result.error
    assert calls, "应调用能量波分析"
    assert calls[0]["kwargs"]["min_hold"] == 4.0     # 风格包硬约束直接传下去
    assert calls[0]["kwargs"]["max_hold"] == 12.0
    assert result.data["beat_cuts"]["used"] is True

    plan = json.loads((tmp_path / "auto_edit" / "plan.json").read_text(encoding="utf-8"))
    assert plan["params"]["bpm"] == 120.0
    starts = [s["start"] for s in plan["segments"][0]["shots"]]
    assert starts == [0.0, 4.0, 8.0, 12.0]          # 切点被能量波替换（原 5/9/15 已让位）

    saved = load_beat_map(tmp_path)
    assert saved and saved["cuts_by_source"]["used"] is True

    decisions = (tmp_path / "decisions.jsonl").read_text(encoding="utf-8")
    assert "P0-5 能量波切点接管" in decisions


def test_plan_keeps_scene_cuts_for_speech(tmp_path, monkeypatch):
    calls: list = []
    _stub_plan_deps(monkeypatch, changes=[[5.0, 9.0]], beat_map=_fake_beat_map(), calls=calls)
    monkeypatch.setattr(ae, "probe_sources", lambda paths, **kw: {**_decl(len(paths)), "is_speech": True})

    result = AutoEdit().execute({
        "operation": "plan", "project_dir": str(tmp_path),
        "video": str(tmp_path / "src0.mp4"), "has_bgm": True, "is_speech": True,
    })
    assert result.success, result.error
    assert not calls, "对白片不该跑能量波接管"
    assert result.data["beat_cuts"]["used"] is False
    assert "切断句子" in result.data["beat_cuts"]["reason"]
    starts = [s["start"] for s in result.data["plan"]["segments"][0]["shots"]]
    assert 5.0 in starts


def test_plan_keeps_scene_cuts_without_bgm_or_when_disabled(tmp_path, monkeypatch):
    calls: list = []
    _stub_plan_deps(monkeypatch, changes=[[5.0, 9.0]], beat_map=_fake_beat_map(), calls=calls)
    result = AutoEdit().execute({
        "operation": "plan", "project_dir": str(tmp_path),
        "video": str(tmp_path / "src0.mp4"),
    })
    assert result.success, result.error
    assert not calls
    assert "has_bgm=false" in result.data["beat_cuts"]["reason"]

    # 先跑一次带 BGM 的 → 产物在盘上
    ok = AutoEdit().execute({
        "operation": "plan", "project_dir": str(tmp_path),
        "video": str(tmp_path / "src0.mp4"), "has_bgm": True,
    })
    assert ok.success and ae.beat_map_path(tmp_path).is_file()

    # 再显式关掉 → 旧产物必须清掉（否则量规层拿它把 m5/m6 误标 circular）
    result = AutoEdit().execute({
        "operation": "plan", "project_dir": str(tmp_path),
        "video": str(tmp_path / "src0.mp4"), "has_bgm": True, "beat_cuts": False,
    })
    assert result.success, result.error
    assert "beat_cuts=false" in result.data["beat_cuts"]["reason"]
    assert not ae.beat_map_path(tmp_path).exists()


def test_clear_beat_map_is_idempotent(tmp_path):
    assert ae.clear_beat_map(tmp_path) is False        # 本来就没有
    ae.save_beat_map(tmp_path, _fake_beat_map())
    assert ae.beat_map_path(tmp_path).is_file()
    assert ae.clear_beat_map(tmp_path) is True
    assert ae.clear_beat_map(tmp_path) is False


def test_plan_warns_when_target_duration_fights_beat_grid(tmp_path, monkeypatch):
    calls: list = []
    _stub_plan_deps(monkeypatch, changes=[[5.0]], beat_map=_fake_beat_map(), calls=calls)
    result = AutoEdit().execute({
        "operation": "plan", "project_dir": str(tmp_path),
        "video": str(tmp_path / "src0.mp4"), "has_bgm": True, "target_duration": 10.0,
    })
    assert result.success, result.error
    assert any("target_duration" in w for w in result.data["beat_map"]["warnings"])


def test_pack_holds_follow_pacing_overrides():
    assert AutoEdit._pack_holds("documentary", None) == (4.0, 12.0)
    assert AutoEdit._pack_holds("documentary", {"pacing": {"min_hold": 0.5}}) == (0.5, 12.0)


# --- 量规层 circular -----------------------------------------------------------


def _shots() -> list[dict]:
    """镜长有差异（1s/3s 交替）→ 密度有方差，m6 才可算。"""
    rows = []
    t = 0.0
    for i in range(6):
        dur = 1.0 if i % 2 == 0 else 3.0
        rows.append({"shot_id": f"s{i}", "scene_id": f"sc{i}", "start_seconds": t,
                     "end_seconds": t + dur, "duration_seconds": dur})
        t += dur
    return rows


def _bars() -> list[dict]:
    """bar 形状（= build_beat_map 的 bars）：能量随密度同向（响处短镜）。"""
    return [
        {"start_seconds": 0.0, "end_seconds": 2.0, "mean_db": -12.0},
        {"start_seconds": 2.0, "end_seconds": 6.0, "mean_db": -30.0},
        {"start_seconds": 6.0, "end_seconds": 8.0, "mean_db": -12.0},
        {"start_seconds": 8.0, "end_seconds": 12.0, "mean_db": -30.0},
        {"start_seconds": 12.0, "end_seconds": 14.0, "mean_db": -12.0},
        {"start_seconds": 14.0, "end_seconds": 18.0, "mean_db": -30.0},
    ]


def _contour() -> list[dict]:
    return beat_map_contour({"bars": _bars()})


def test_beat_map_grid_is_preferred_and_marked_circular():
    beat_map = _fake_beat_map()
    beat_map["feasible"] = True
    beat_map["grid"] = {
        "bpm": 120.0, "fps": 30.0, "offset_seconds": 0.0, "start_seconds": 0.0,
        "frames_per_beat": 15.0, "beats_per_bar": 4, "source": "energy_wave",
    }
    grid = beat_grid(None, beat_map=beat_map)
    assert grid["bpm"] == 120.0
    assert grid["source"] == "energy_wave"
    assert grid["circular"] is True
    assert beat_grid(None, beat_map={"feasible": False, "grid": beat_map["grid"]}) is None


def test_compute_edit_metrics_marks_m5_m6_circular():
    beat_map = _fake_beat_map()
    beat_map["feasible"] = True
    beat_map["grid"] = {
        "bpm": 120.0, "fps": 30.0, "offset_seconds": 0.0, "start_seconds": 0.0,
        "frames_per_beat": 15.0, "source": "energy_wave",
    }
    report = compute_edit_metrics(shots=_shots(), energy_contour=_contour(), beat_map=beat_map)
    assert report["circular_metrics"] == ["m5", "m6"]
    assert report["metrics"]["m5"]["circular"] is True
    assert report["metrics"]["m5"]["detail"]["circular"] is True
    assert "自我满足" in report["metrics"]["m5"]["detail"]["note"]
    assert report["metrics"]["m1"]["circular"] is False
    summary = metric_summary(report)
    assert "m5c" in summary and "m6c" in summary


def test_compute_edit_metrics_without_beat_map_is_not_circular():
    report = compute_edit_metrics(shots=_shots())
    assert report["circular_metrics"] == []
    assert report["circular_note"] == ""
    assert report["metrics"]["m5"]["skipped"] is True
    assert "m5~" in metric_summary(report)


def test_skipped_metric_is_not_marked_circular():
    """跳过 = 没证据，谈不上自证：bar 太小/源缺失时不该出现 circular_metrics。"""
    beat_map = _fake_beat_map()
    beat_map["feasible"] = True
    beat_map["grid"] = {
        "bpm": 120.0, "fps": 30.0, "offset_seconds": 0.0, "start_seconds": 0.0,
        "frames_per_beat": 15.0, "source": "energy_wave",
    }
    report = compute_edit_metrics(shots=_shots()[:1], beat_map=beat_map)
    assert report["metrics"]["m5"]["skipped"] is True
    assert report["metrics"]["m5"]["circular"] is False
    assert report["circular_metrics"] == []


def test_review_logger_reads_beat_map_from_project(tmp_path, monkeypatch):
    from montage.tools import review_logger as rl

    monkeypatch.setattr(rl, "_timeline_shots", lambda store: _shots())

    beat_map = _fake_beat_map()
    beat_map["feasible"] = True
    beat_map["grid"] = {
        "bpm": 120.0, "fps": 30.0, "offset_seconds": 0.0, "start_seconds": 0.0,
        "frames_per_beat": 15.0, "source": "energy_wave",
    }
    beat_map["bars"] = _bars()
    ae.save_beat_map(tmp_path, beat_map)

    report = rl.compute_project_metrics(tmp_path)
    assert report["circular_metrics"] == ["m5", "m6"]
    assert report["beat_grid_source"] == "energy_wave"
    assert report["metrics"]["m6"]["detail"]["energy_source"] == "rms_contour"

    # DP 不可行 → 切点没被能量波接管，指标回到真证据
    beat_map["feasible"] = False
    ae.save_beat_map(tmp_path, beat_map)
    assert rl.compute_project_metrics(tmp_path)["circular_metrics"] == []
