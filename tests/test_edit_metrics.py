"""P0-4 edit_metrics：DIRECT m1/m2/m5/m6 + 身份漂移客观量规（零 LLM 纯函数）。

验收锚点（计划 P0-4）：
- 每条 finding 带 metric / value / threshold；
- m3（光流运动连续）/m4（显著性构图一致）**不实现**，且不引入光流/显著性依赖。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from montage.engine import edit_metrics as em


def _shot(sid: str, scene: str, start: float, dur: float, **extra) -> dict:
    return {
        "shot_id": sid,
        "scene_id": scene,
        "start_seconds": start,
        "end_seconds": start + dur,
        "duration_seconds": dur,
        **extra,
    }


# --- 登记表 ------------------------------------------------------------------


def test_metric_ids_are_direct_subset_plus_drift():
    assert em.metric_ids() == ("m1", "m2", "m5", "m6", "drift")


def test_m3_m4_are_explicitly_deleted_not_implemented():
    assert em.DELETED_METRICS == ("m3", "m4")
    assert "m3" not in em.metric_ids() and "m4" not in em.metric_ids()
    assert not (set(em.DELETED_METRICS) & set(em.metric_ids()))


def test_no_optical_flow_or_saliency_dependency_in_module():
    """m3/m4 的算力（光流/显著性）不得被引入——源码级锁死。"""
    source = Path(em.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    banned = ("cv2", "opencv", "numpy", "torch", "scipy", "librosa", "skimage")
    for name in imported:
        assert not name.startswith(banned), f"量规模块不得引入 {name}（m3/m4 已删）"


def test_specs_have_threshold_and_direction():
    for metric, spec in em.metric_specs().items():
        assert "threshold" in spec and "direction" in spec, metric
        assert spec["direction"] in ("higher", "lower")


# --- m1 ---------------------------------------------------------------------


def test_anchor_tokens_splits_chinese_punctuation():
    assert em.anchor_tokens("黑发齐耳，左眉一道旧疤") == ["黑发齐耳", "左眉一道旧疤"]
    assert em.anchor_tokens("") == []
    assert em.anchor_tokens("甲") == []  # 过短碎片丢弃


def test_m1_requires_every_anchor_group_in_prompt():
    shots = [
        _shot("S1", "sc01", 0, 2, visual_details={
            "subjects": [{"id": "c1", "appearance_anchor": "黑发齐耳"}],
            "environment": {"description": "雨夜窄巷"},
        }),
        _shot("S2", "sc01", 2, 3, visual_details={"subjects": [{"id": "c1"}]}),
    ]
    result = em.m1_prompt_relevance(
        shots,
        # S1 三组锚（appearance/outfit/场景）全进提示词；S2 两组锚一个都没进
        prompts={"S1": "黑发齐耳，灰风衣，雨夜窄巷", "S2": "他抬头"},
        registry={"c1": {"appearance": "黑发齐耳", "outfit_anchor": "灰风衣"}},
    )
    assert result["value"] == 0.6
    assert [f["field"] for f in result["findings"]] == ["m1/S2"]
    assert all(f["metric"] == "m1" and f["threshold"] == 1.0 for f in result["findings"])


def test_m1_fails_when_vlm_marks_shot_not_ok():
    shots = [_shot("S1", "sc01", 0, 2, visual_details={"subjects": [{"id": "c1"}]})]
    result = em.m1_prompt_relevance(
        shots,
        prompts={"S1": "黑发齐耳"},
        registry={"c1": {"appearance": "黑发齐耳"}},
        vlm={"shots": [{"shot_id": "S1", "ok": False, "issues": [{"kind": "崩坏"}]}]},
    )
    assert result["value"] == 0.0
    assert result["detail"]["vlm_failed"] == ["S1"]


def test_m1_treats_skipped_vlm_as_not_checked():
    shots = [_shot("S1", "sc01", 0, 2, visual_details={"subjects": [{"id": "c1"}]})]
    result = em.m1_prompt_relevance(
        shots,
        prompts={"S1": "黑发齐耳"},
        registry={"c1": {"appearance": "黑发齐耳"}},
        vlm={"skipped": True, "shots": [{"shot_id": "S1", "skipped": True}]},
    )
    assert result["value"] == 1.0
    assert result["detail"]["vlm_checked"] == 0
    assert result["detail"]["vlm_skipped"] is True


# --- m2 ---------------------------------------------------------------------


def test_m2_accepts_same_scene_and_shared_character():
    plan = {"scenes": [{"id": "sc01", "chapter_id": "ch1"}, {"id": "sc02", "chapter_id": "ch1"}]}
    shots = [
        _shot("S1", "sc01", 0, 2, visual_details={"subjects": [{"id": "c1"}]}),
        _shot("S2", "sc01", 2, 2, visual_details={"subjects": [{"id": "c2"}]}),
        _shot("S3", "sc02", 4, 2, visual_details={"subjects": [{"id": "c1"}]}),
    ]
    result = em.m2_semantic_coherence(shots, scene_plan=plan)
    assert result["value"] == 1.0
    assert result["findings"] == []


def test_m2_flags_orphan_cut_across_scene_and_chapter():
    plan = {"scenes": [{"id": "sc01", "chapter_id": "ch1"}, {"id": "sc09", "chapter_id": "ch3"}]}
    shots = [
        _shot("S1", "sc01", 0, 2, visual_details={"subjects": [{"id": "c1"}]}),
        _shot("S2", "sc09", 2, 2, visual_details={"subjects": [{"id": "c2"}]}),
    ]
    result = em.m2_semantic_coherence(shots, scene_plan=plan)
    assert result["value"] == 0.0
    finding = result["findings"][0]
    assert finding["metric"] == "m2" and finding["value"] == 0.0 and finding["threshold"] == 1.0
    assert "语义断裂" in finding["message"]


def test_m2_shared_prop_is_enough_across_chapters():
    plan = {"scenes": [{"id": "sc01", "chapter_id": "ch1"}, {"id": "sc09", "chapter_id": "ch3"}]}
    shots = [
        _shot("S1", "sc01", 0, 2, visual_details={"objects": [{"id": "sword"}]}),
        _shot("S2", "sc09", 2, 2, visual_details={"objects": [{"id": "sword"}]}),
    ]
    assert em.m2_semantic_coherence(shots, scene_plan=plan)["value"] == 1.0


# --- m5 ---------------------------------------------------------------------


def test_beat_grid_reads_bpm_from_bgm_event():
    grid = em.beat_grid({"events": [{"kind": "bgm", "bpm": 120, "start_seconds": 4}]}, fps=30)
    assert grid is not None
    assert grid["bpm"] == 120
    assert grid["frames_per_beat"] == pytest.approx(15.0)


def test_beat_grid_none_without_bpm():
    assert em.beat_grid({"events": [{"kind": "bgm", "bpm": 0}]}) is None
    assert em.beat_grid({}) is None


def test_m5_skips_with_reason_when_no_bpm():
    shots = [_shot("S1", "sc01", 0, 2), _shot("S2", "sc01", 2, 2)]
    result = em.beat_alignment(shots, em.beat_grid(None))
    assert result["skipped"] is True
    assert result["value"] == 0.0
    assert "bpm" in result["reason"]
    assert result["findings"] == []


def test_m5_flags_cut_off_the_beat():
    grid = em.beat_grid({"events": [{"kind": "bgm", "bpm": 120, "start_seconds": 0}]}, fps=30)
    # 15 帧/拍 = 0.5s；2.2s = 66 帧 → 距最近拍（60/75 帧）6 帧 → 超出容差 2
    shots = [_shot("S1", "sc01", 0, 2.2), _shot("S2", "sc01", 2.2, 2.0)]
    result = em.beat_alignment(shots, grid)
    assert result["value"] == 0.0
    finding = result["findings"][0]
    assert finding["metric"] == "m5"
    assert finding["value"] == pytest.approx(6.0)
    assert finding["threshold"] == float(em.DEFAULT_BEAT_TOLERANCE_FRAMES)


def test_m5_passes_on_beat_cuts():
    grid = em.beat_grid({"events": [{"kind": "bgm", "bpm": 120, "start_seconds": 0}]}, fps=30)
    shots = [_shot("S1", "sc01", 0, 2), _shot("S2", "sc01", 2.0, 2.0)]
    result = em.beat_alignment(shots, grid)
    assert result["value"] == 1.0
    assert result["findings"] == []


# --- m6 ---------------------------------------------------------------------


def test_m6_skips_below_min_shots():
    shots = [_shot("S1", "sc01", 0, 2), _shot("S2", "sc01", 2, 2)]
    result = em.m6_energy_visual(shots, soundtrack={})
    assert result["skipped"] is True
    assert result["value"] == 0.0
    assert "不足" in result["reason"]


def test_m6_positive_when_loud_shot_is_cut_faster():
    shots = [
        _shot("S1", "sc01", 0, 6, audio_prompt={"sfx": []}),
        _shot("S2", "sc01", 6, 6, audio_prompt={"sfx": []}),
        _shot("S3", "sc01", 12, 2, audio_prompt={"sfx": ["轰"]}),
        _shot("S4", "sc01", 14, 2, audio_prompt={"sfx": ["轰"]}),
    ]
    soundtrack = {"events": [
        {"kind": "bgm", "bpm": 120, "scene_id": "sc01", "start_seconds": 0, "volume": 0.05},
    ]}
    result = em.m6_energy_visual(shots, soundtrack=soundtrack)
    assert result["skipped"] is False
    assert result["value"] > 0.9
    assert result["detail"]["energy_source"] == "audio_plan"


def test_m6_uses_rms_contour_when_supplied():
    shots = [
        _shot("S1", "sc01", 0, 6, audio_prompt={"sfx": []}),
        _shot("S2", "sc01", 6, 6, audio_prompt={"sfx": []}),
        _shot("S3", "sc01", 12, 2, audio_prompt={"sfx": []}),
        _shot("S4", "sc01", 14, 2, audio_prompt={"sfx": []}),
    ]
    contour = [
        {"start_seconds": 0, "end_seconds": 6, "rms_db": -30},
        {"start_seconds": 6, "end_seconds": 12, "rms_db": -20},
        {"start_seconds": 12, "end_seconds": 14, "rms_db": -5},
        {"start_seconds": 14, "end_seconds": 16, "rms_db": -4},
    ]
    result = em.m6_energy_visual(shots, soundtrack={}, contour=contour)
    assert result["skipped"] is False
    assert result["value"] > 0.9  # 响处快切
    assert result["detail"]["energy_source"] == "rms_contour"


def test_m6_skips_when_durations_identical_and_no_energy_variance():
    shots = [
        _shot(f"S{i}", "sc01", i * 2, 2, audio_prompt={"sfx": []}) for i in range(4)
    ]
    result = em.m6_energy_visual(shots, soundtrack={})
    assert result["skipped"] is True
    assert "方差" in result["reason"]


# --- drift ------------------------------------------------------------------


def test_drift_metric_counts_characters_and_thresholds_zero():
    memory = {"characters": {
        "c1": {"drift_count": 2, "retake": True, "last_drift_shot": "S9"},
        "c2": {"drift_count": 1, "retake": False},
        "c3": {"drift_count": 0, "retake": False},
    }}
    result = em.drift_metric(memory)
    assert result["value"] == 2.0
    assert len(result["findings"]) == 2
    for finding in result["findings"]:
        assert finding["metric"] == "drift"
        assert finding["threshold"] == 0.0
    assert result["findings"][0]["severity"] == "warning"  # retake 的才是 warning


def test_drift_metric_clean_when_no_drift():
    result = em.drift_metric({"characters": {"c1": {"drift_count": 0, "retake": False}}})
    assert result["value"] == 0.0
    assert result["findings"] == []


# --- 汇总 -------------------------------------------------------------------


def _report(**kwargs):
    plan = {"scenes": [
        {"id": "sc01", "chapter_id": "ch1"},
        {"id": "sc02", "chapter_id": "ch1"},
        {"id": "sc03", "chapter_id": "ch1"},
        {"id": "sc04", "chapter_id": "ch1"},
    ]}
    shots = [
        _shot("S1", "sc01", 0, 2, visual_details={"subjects": [{"id": "c1"}]}),
        _shot("S2", "sc01", 2, 2, visual_details={"subjects": [{"id": "c1"}]}),
        _shot("S3", "sc02", 4, 2, visual_details={"subjects": [{"id": "c1"}]}),
        _shot("S4", "sc03", 6, 2, visual_details={"subjects": [{"id": "c1"}]}),
    ]
    base = dict(
        shots=shots,
        scene_plan=plan,
        registry={"c1": {"appearance": "黑发齐耳"}},
        prompts={s["shot_id"]: "黑发齐耳" for s in shots},
        soundtrack={"events": [{"kind": "bgm", "bpm": 120, "scene_id": "sc01", "volume": 0.1}]},
    )
    base.update(kwargs)
    return em.compute_edit_metrics(**base)


def test_compute_edit_metrics_every_finding_has_metric_value_threshold():
    report = _report(
        identity_memory={"characters": {"c1": {"drift_count": 2, "retake": True}}},
    )
    assert set(report["metrics"]) == {"m1", "m2", "m5", "m6", "drift"}
    assert report["deleted_metrics"] == ["m3", "m4"]
    assert report["pass"] is False
    assert report["findings"]
    for finding in em.metric_findings(report):
        assert finding["metric"] in report["metrics"]
        assert isinstance(finding["value"], float)
        assert isinstance(finding["threshold"], float)


def test_compute_edit_metrics_passes_on_clean_report():
    report = _report()
    assert report["pass"] is True
    assert em.overall_decision(report) == "PASS"
    assert em.metric_findings(report) == []
    # m6 在能量全同场景下跳过，但跳过不算不达标
    assert report["metrics"]["m6"]["skipped"] is True
    assert report["metrics"]["m6"]["pass"] is True


def test_metric_summary_is_ascii_marked():
    report = _report(identity_memory={"characters": {"c1": {"drift_count": 1}}})
    summary = em.metric_summary(report)
    assert summary.startswith("m1")
    assert "!" in summary  # drift 未过
    summary.encode("gbk")  # Windows 控制台不得炸


def test_compute_edit_metrics_handles_empty_project():
    report = em.compute_edit_metrics(shots=[])
    assert report["pass"] is True
    assert report["metrics"]["m1"]["skipped"] is False
    assert report["metrics"]["m2"]["skipped"] is True
    assert report["metrics"]["m5"]["skipped"] is True
    assert report["metrics"]["m6"]["skipped"] is True


# --- 停点卡接线 ---------------------------------------------------------------


def test_metrics_summary_line_hints_when_artifact_missing(tmp_path):
    from montage.engine.director import _metrics_summary_line

    line = _metrics_summary_line(tmp_path)
    assert line["label"] == "客观量规"
    assert "未生成" in line["value"]


def test_metrics_summary_line_reads_artifact(tmp_path):
    from montage.engine.director import _metrics_summary_line

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "edit_metrics.json").write_text(
        json.dumps(_report()), encoding="utf-8"
    )
    assert "m1" in _metrics_summary_line(tmp_path)["value"]


def test_metrics_summary_line_flags_corrupt_artifact(tmp_path):
    from montage.engine.director import _metrics_summary_line

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "edit_metrics.json").write_text("{not json", encoding="utf-8")
    assert "损坏" in _metrics_summary_line(tmp_path)["value"]
    assert _metrics_summary_line(None) is None


def test_metrics_summary_line_spells_out_circular_metrics(tmp_path):
    """P0-5：停点卡里 m5/m6 若自我满足，必须写明「不作放行证据」，别只给个 c 标记。"""
    from montage.engine.director import _metrics_summary_line

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    report = _report()
    report["circular_metrics"] = ["m5", "m6"]
    report["metrics"]["m5"]["circular"] = True
    (artifacts / "edit_metrics.json").write_text(json.dumps(report), encoding="utf-8")

    line = _metrics_summary_line(tmp_path)
    assert "m5c" in line["value"]
    assert "自我满足" in line["value"] and "不作放行证据" in line["value"]
    assert line["circular_metrics"] == ["m5", "m6"]

    report["circular_metrics"] = []
    (artifacts / "edit_metrics.json").write_text(json.dumps(report), encoding="utf-8")
    assert "自我满足" not in _metrics_summary_line(tmp_path)["value"]


def test_await_clips_card_shows_objective_metrics(tmp_path):
    """assemble 前人审：await_clips 停点卡带客观量规摘要（P0-4）。"""
    from montage.engine.director import build_review_card

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "edit_metrics.json").write_text(json.dumps(_report()), encoding="utf-8")
    card = build_review_card(
        "await_clips",
        bible={"title": "样片"},
        scene_plan={"scenes": []},
        manifest={"items": []},
        project_dir=str(tmp_path),
    )
    labels = [row["label"] for row in card["summary"]]
    assert "客观量规" in labels


def test_edit_metrics_is_forbidden_card_path():
    """量规报告是机器重写产物，停点卡不得手改（_FORBIDDEN_PATH）。"""
    from montage.engine.director import _FORBIDDEN_PATH

    assert "edit_metrics" in _FORBIDDEN_PATH
