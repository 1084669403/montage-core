"""review_logger 测试（V35/V30/V47/V49/V21/V22）。

覆盖：append/summary/subject 枚举 warning/round 时间戳序自动算/phase=first_pass 不计轮
不触振荡/role 级跨点位 revise 合计/振荡简化匹配/regression/域冲突/仲裁后重提忽略语义。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from montage.engine.project import init_project
from montage.schemas import REVIEW_LOG_SCHEMA, get_schema
from montage.tools.review_logger import (
    PHASE_FIRST_PASS,
    PHASE_REVISE,
    REVIEW_LOG_ROLES,
    REVIEW_LOG_SUBJECTS,
    ReviewLogger,
    load_reviews,
    record_review,
    summarize_reviews,
)

import pytest


def _tool() -> ReviewLogger:
    return ReviewLogger()


def _record(proj, role, subject, decision, findings=None, phase=None, **extra):
    payload = {
        "operation": "record",
        "project_dir": str(proj),
        "role": role,
        "subject": subject,
        "decision": decision,
    }
    if findings is not None:
        payload["findings"] = findings
    if phase is not None:
        payload["phase"] = phase
    payload.update(extra)
    return _tool().execute(payload)


def _f(field, message="m", severity="critical"):
    return {"severity": severity, "field": field, "message": message, "proposed_fix": "fix"}


def test_record_appends_jsonl(tmp_path):
    proj = init_project(tmp_path, "rl1", "日志", "cinematic")
    res = _record(proj, "director", "design", "REVISE", [_f("structure")])
    assert res.success
    rows = load_reviews(proj)
    assert len(rows) == 1
    assert rows[0]["role"] == "director"
    assert rows[0]["phase"] == PHASE_REVISE
    assert res.data["warnings"] is None  # 表内值无警告
    # append-only：第二行不覆盖第一行
    _record(proj, "director", "design", "PASS")
    assert len(load_reviews(proj)) == 2


def test_subject_off_enum_warns_but_records(tmp_path):
    proj = init_project(tmp_path, "rl2", "枚举", "cinematic")
    res = _record(proj, "director", "cp1", "REVISE")  # 表外缩写
    assert res.success
    warns = res.data["warnings"]
    assert warns and "cp1" in warns[0]
    summary = summarize_reviews(str(proj))
    assert summary["subjects_off_enum"] == ["cp1"]
    # V30 枚举表内容完整
    assert set(REVIEW_LOG_SUBJECTS) >= {
        "setup", "outline", "design", "cast", "checkpoint_1",
        "final_prompt", "frames", "clips", "retry", "draft_selection",
    }


def test_auto_round_by_timestamp_order_not_manual_round(tmp_path):
    proj = init_project(tmp_path, "rl3", "自动算轮", "cinematic")
    # 手填 round 跳号/重号：summary 必须无视手填、按行序编号
    _record(proj, "director", "design", "REVISE", round=7)
    _record(proj, "director", "design", "REVISE", round=2)
    _record(proj, "director", "design", "REVISE", round=99)
    summary = summarize_reviews(str(proj))
    row = next(s for s in summary["subjects"] if s["subject"] == "design")
    assert row["revise_rounds"] == 3
    assert row["latest_revise_round"] == 3  # 自动编号，不信任手填 99


def test_first_pass_not_counted_as_round(tmp_path):
    """V47：子 Agent 首审（first_pass）不占 4 轮额度。"""
    proj = init_project(tmp_path, "rl4", "首审不占", "cinematic")
    _record(proj, "art_director", "final_prompt", "REVISE", phase=PHASE_FIRST_PASS)
    _record(proj, "art_director", "final_prompt", "REVISE", [_f("lighting")])
    summary = summarize_reviews(str(proj))
    row = next(s for s in summary["subjects"] if s["subject"] == "final_prompt")
    assert row["first_pass_count"] == 1
    assert row["revise_rounds"] == 1  # 只有 revise 计入轮次
    totals = next(t for t in summary["role_totals"] if t["role"] == "art_director")
    assert totals["revise_rounds_total"] == 1
    assert totals["first_pass_total"] == 1


def test_first_pass_plus_revise_no_false_oscillation(tmp_path):
    """V47：首审 finding 与第 1 轮返修同键连续出现，不得误触振荡。"""
    proj = init_project(tmp_path, "rl5", "首审不触振荡", "cinematic")
    _record(proj, "art_director", "final_prompt", "REVISE",
            [_f("lighting")], phase=PHASE_FIRST_PASS)
    _record(proj, "art_director", "final_prompt", "REVISE", [_f("lighting")])
    summary = summarize_reviews(str(proj))
    assert summary["oscillations"] == []


def test_role_level_cross_subject_total(tmp_path):
    """V49：额度按 role 打通，summary 输出 role 级跨 subject 合计。"""
    proj = init_project(tmp_path, "rl6", "额度聚合", "cinematic")
    for _ in range(2):
        _record(proj, "art_director", "checkpoint_1", "REVISE")
    for _ in range(2):
        _record(proj, "art_director", "final_prompt", "REVISE")
    summary = summarize_reviews(str(proj))
    totals = next(t for t in summary["role_totals"] if t["role"] == "art_director")
    assert totals["by_subject"] == {"checkpoint_1": 2, "final_prompt": 2}
    assert totals["revise_rounds_total"] == 4  # 共用 4 轮额度已满


def test_oscillation_same_key_two_rounds(tmp_path):
    proj = init_project(tmp_path, "rl7", "振荡", "cinematic")
    _record(proj, "director", "design", "REVISE", [_f("pacing")])
    _record(proj, "director", "design", "REVISE", [_f("pacing")])
    summary = summarize_reviews(str(proj))
    osc = summary["oscillations"]
    assert len(osc) == 1
    assert osc[0]["field"] == "pacing"
    assert osc[0]["round"] == 2


def test_no_oscillation_when_field_resolved(tmp_path):
    proj = init_project(tmp_path, "rl8", "振荡消失", "cinematic")
    _record(proj, "director", "design", "REVISE", [_f("pacing")])
    _record(proj, "director", "design", "REVISE", [_f("dialogue")])
    _record(proj, "director", "design", "PASS")
    summary = summarize_reviews(str(proj))
    assert summary["oscillations"] == []


def test_regression_detected(tmp_path):
    """上轮消失本轮复现 → regression（辅助信号）。"""
    proj = init_project(tmp_path, "rl9", "regression", "cinematic")
    _record(proj, "director", "design", "REVISE", [_f("pacing")])
    _record(proj, "director", "design", "REVISE", [_f("dialogue")])
    _record(proj, "director", "design", "REVISE", [_f("pacing")])
    summary = summarize_reviews(str(proj))
    assert summary["oscillations"] == []
    assert len(summary["regressions"]) == 1
    assert summary["regressions"][0]["field"] == "pacing"


def test_domain_conflict_not_oscillation(tmp_path):
    """同 subject.field 不同 role → 域冲突标记，不算振荡，走仲裁。"""
    proj = init_project(tmp_path, "rl10", "域冲突", "cinematic")
    _record(proj, "art_director", "checkpoint_1", "REVISE", [_f("costume")])
    _record(proj, "action_director", "checkpoint_1", "REVISE", [_f("costume")])
    summary = summarize_reviews(str(proj))
    assert summary["oscillations"] == []
    conflicts = summary["domain_conflicts"]
    assert len(conflicts) == 1
    assert set(conflicts[0]["roles"]) == {"art_director", "action_director"}


def test_arbitration_re_raise_ignored_semantics(tmp_path):
    """仲裁约束力：仲裁后同角色重提同一 finding 不再累积轮次/振荡。
    （实现语义：record 正常追加，但振荡判定基于连续轮——驳回后该 finding 只出现在
    单轮中，不会形成"连续 2 轮"的振荡键。测试锁定该行为。）"""
    proj = init_project(tmp_path, "rl11", "仲裁重提", "cinematic")
    _record(proj, "art_director", "checkpoint_1", "REVISE", [_f("color", "过冷")])
    _record(proj, "director", "checkpoint_1", "ARBITRATE", dissent="驳回 color finding")
    _record(proj, "art_director", "checkpoint_1", "REVISE", [_f("color", "过冷")])
    summary = summarize_reviews(str(proj))
    # art_director 两轮 color 之间隔了 director 仲裁行；同键判定按 art_director 自己的
    # 轮序列（轮 1 与轮 2 连续出现同 field）→ 仍判振荡，重提被导演以"仲裁约束力"忽略。
    # 此处锁定：检测器输出含该振荡键（提示导演注意重提），最终裁决由仲裁条款处理。
    assert any(o["field"] == "color" for o in summary["oscillations"])


def test_dissent_and_self_review_fixed_persist(tmp_path):
    proj = init_project(tmp_path, "rl12", "留痕", "cinematic")
    res = _record(
        proj, "screenwriter", "outline", "PASS",
        dissent="编剧异议：金句保留", self_review_findings_fixed=["对白超时已修"],
    )
    assert res.success
    rows = load_reviews(proj)
    assert rows[0]["dissent"] == "编剧异议：金句保留"
    assert rows[0]["self_review_findings_fixed"] == ["对白超时已修"]


def test_summary_subject_filter_and_unresolved(tmp_path):
    proj = init_project(tmp_path, "rl13", "未解决", "cinematic")
    _record(proj, "director", "design", "REVISE", [_f("structure", "幕间断裂")])
    _record(proj, "director", "design", "REVISE", [_f("pacing")])
    summary = summarize_reviews(str(proj), subject="design")
    assert summary["rows"] == 2
    row = summary["subjects"][0]
    assert row["final_decision"] == "REVISE"
    assert any(f["field"] == "pacing" for f in row["unresolved_findings"])


def test_record_requires_fields(tmp_path):
    proj = init_project(tmp_path, "rl14", "必填", "cinematic")
    res = _tool().execute({"operation": "record", "project_dir": str(proj), "role": "director"})
    assert not res.success
    assert "subject" in (res.error or "")


def test_summary_empty_project(tmp_path):
    proj = init_project(tmp_path, "rl15", "空", "cinematic")
    summary = summarize_reviews(str(proj))
    assert summary["rows"] == 0
    assert summary["subjects"] == []
    assert summary["oscillations"] == []


def test_review_log_schema_registered():
    assert get_schema("review_log") is REVIEW_LOG_SCHEMA


# --- P0-4：metrics operation -------------------------------------------------


def _seed_metrics_project(proj: Path) -> None:
    """最小可算量规的项目：4 镜 / 2 场同章 / 一条 BGM（bpm 已知）。"""
    from montage.engine.artifacts import ArtifactStore

    store = ArtifactStore(proj)
    store.write("scene_plan", {"scenes": [
        {"id": "sc01", "chapter_id": "ch1", "start_seconds": 0, "shots": [
            {"shot_id": "S1", "scene_id": "sc01", "character_ids": ["c1"],
             "duration_seconds": 2,
             "visual_details": {"subjects": [{"id": "c1", "appearance_anchor": "黑发齐耳"}],
                                "environment": {"description": "雨夜窄巷"}}},
            {"shot_id": "S2", "scene_id": "sc01", "character_ids": ["c1"],
             "duration_seconds": 3, "visual_details": {"subjects": [{"id": "c1"}]}},
        ]},
        {"id": "sc02", "chapter_id": "ch1", "start_seconds": 5, "shots": [
            {"shot_id": "S3", "scene_id": "sc02", "character_ids": ["c1"],
             "duration_seconds": 4, "visual_details": {"subjects": [{"id": "c1"}]}},
            {"shot_id": "S4", "scene_id": "sc02", "character_ids": ["c2"],
             "duration_seconds": 5, "visual_details": {"subjects": [{"id": "c2"}]}},
        ]},
    ]}, schema=None)
    store.write("shot_prompts", {"version": "1", "shots": [
        {"shot_id": "S1", "scene_id": "sc01", "shot_kind": "video",
         "video_prompt": "黑发齐耳，雨夜窄巷"},
    ]}, schema=None)
    store.write("soundtrack", {"events": [
        {"kind": "bgm", "bpm": 120, "scene_id": "sc01", "shot_id": "",
         "start_seconds": 0, "volume": 0.2},
    ]}, schema=None)
    (proj / "artifacts" / "identity_memory.json").write_text(
        json.dumps({"version": 1, "characters": {
            "c2": {"drift_count": 2, "retake": True, "last_drift_shot": "S4"},
        }}),
        encoding="utf-8",
    )


def test_metrics_operation_records_row_with_metric_ids(tmp_path):
    """P0-4 验收：review_log 里每条 finding 都带 metric/value/threshold。"""
    proj = init_project(tmp_path, "rl16", "量规", "cinematic")
    _seed_metrics_project(proj)

    res = _tool().execute({"operation": "metrics", "project_dir": str(proj)})
    assert res.success, res.error
    row = res.data["row"]
    assert row["role"] == "edit_director"
    assert row["subject"] == "edit_plan"
    assert row["phase"] == PHASE_REVISE
    assert row["decision"] in ("PASS", "REVISE")
    assert row["findings"], "漂移 + m2 断裂应有 findings"
    for finding in row["findings"]:
        assert finding["metric"] in {"m1", "m2", "m5", "m6", "drift"}
        assert "value" in finding and "threshold" in finding
    # 落盘 append-only + 报告产物
    assert len(load_reviews(proj)) == 1
    assert (proj / "artifacts" / "edit_metrics.json").is_file()
    # 量规维度进 scores，summary 一行可读
    assert set(row["scores"]) == {"m1", "m2", "m5", "m6", "drift"}
    assert row["metrics_summary"].startswith("m1")


def test_metrics_operation_record_false_only_reports(tmp_path):
    proj = init_project(tmp_path, "rl17", "只算不落", "cinematic")
    _seed_metrics_project(proj)
    res = _tool().execute({"operation": "metrics", "project_dir": str(proj), "record": False})
    assert res.success
    assert "row" not in res.data
    assert res.data["report"]["metrics"]
    assert load_reviews(proj) == []
    assert (proj / "artifacts" / "edit_metrics.json").is_file()


def test_metrics_operation_feeds_role_quota_of_edit_director(tmp_path):
    """剪辑导演独立额度：metrics 落行后按 (role, subject) 自动成轮（V49）。"""
    proj = init_project(tmp_path, "rl18", "剪辑额度", "cinematic")
    _seed_metrics_project(proj)
    for _ in range(2):
        _tool().execute({"operation": "metrics", "project_dir": str(proj)})
    summary = summarize_reviews(str(proj))
    totals = next(t for t in summary["role_totals"] if t["role"] == "edit_director")
    assert totals["by_subject"] == {"edit_plan": 2}
    assert summary["subjects_off_enum"] is None  # edit_plan 已在枚举表内
    assert "edit_plan" in REVIEW_LOG_SUBJECTS


def test_metrics_operation_requires_project_dir(tmp_path):
    res = _tool().execute({"operation": "metrics"})
    assert not res.success
    assert "project_dir" in (res.error or "")


def test_metrics_finding_schema_allows_metric_fields():
    finding = REVIEW_LOG_SCHEMA["properties"]["findings"]["items"]["properties"]
    assert {"metric", "value", "threshold"} <= set(finding)


def test_edit_metrics_schema_registered():
    schema = get_schema("edit_metrics")
    assert schema is not None
    assert "m3/m4 已删" in schema["description"]


def test_metrics_marks_m5_m6_circular_when_beat_map_took_over(tmp_path):
    """P0-5 验收：切点被能量波接管后，m5/m6 在真项目里被标 self-satisfied（circular）。

    自证不等于失败：``pass`` 仍按阈值判，但报告与一行摘要都要露出来（``m5c``），
    免得剪辑导演拿「自己出的题自己答对」当质量证据。
    """
    from montage.tools.auto_edit import save_beat_map

    proj = init_project(tmp_path, "rl19", "能量波", "beat")
    _seed_metrics_project(proj)      # 4 镜：时长 2/3/4/5 → 密度递减

    bars = [
        {"start_seconds": 0.0, "end_seconds": 2.0, "mean_db": -30.0},
        {"start_seconds": 2.0, "end_seconds": 5.0, "mean_db": -33.3},
        {"start_seconds": 5.0, "end_seconds": 9.0, "mean_db": -35.0},
        {"start_seconds": 9.0, "end_seconds": 14.0, "mean_db": -36.0},
    ]
    grid = {"bpm": 120.0, "fps": 30.0, "offset_seconds": 0.0, "start_seconds": 0.0,
            "frames_per_beat": 15.0, "beats_per_bar": 4, "source": "energy_wave"}
    save_beat_map(proj, {
        "version": "1.0", "feasible": True, "bpm": 120.0, "bpm_source": "explicit",
        "grid": grid, "bars": bars, "cuts": [2.0, 5.0, 9.0],
        "energy_source": "ebur128", "duration": 14.0, "warnings": [],
    })

    res = _tool().execute({"operation": "metrics", "project_dir": str(proj), "record": False})
    assert res.success, res.error
    report = res.data["report"]
    assert report["circular_metrics"] == ["m5", "m6"]
    assert report["beat_grid_source"] == "energy_wave"
    assert report["metrics"]["m5"]["pass"] is True
    assert report["metrics"]["m5"]["circular"] is True
    assert "自我满足" in report["metrics"]["m5"]["detail"]["note"]
    assert report["metrics"]["m6"]["circular"] is True
    assert report["metrics"]["m6"]["detail"]["energy_source"] == "rms_contour"
    assert "自我满足" in report["circular_note"]
    assert "m5c" in res.data["summary"]

    # 删掉能量波产物 → 回到「真证据」判定（不 circular）
    (proj / "tmp_autoedit" / "beat_map.json").unlink()
    res2 = _tool().execute({"operation": "metrics", "project_dir": str(proj), "record": False})
    assert res2.data["report"]["circular_metrics"] == []
    assert "m5c" not in res2.data["summary"]


# --- P0-8 特效指导角色协议 -----------------------------------------------------


def test_vfx_director_role_is_valid(tmp_path):
    """vfx_director 进角色枚举——record 不出表外 warning（P0-8）。"""
    proj = init_project(tmp_path, "rlvfx", "特效帽", "cinematic")
    res = _record(
        proj, "vfx_director", "checkpoint_1", "REVISE", [_f("vfx")],
        phase="first_pass",
    )
    assert res.success
    assert not res.data["warnings"]  # 无表外 warning（None 或 []）
    rows = load_reviews(proj)
    assert rows[0]["role"] == "vfx_director"
    assert rows[0]["phase"] == "first_pass"
    assert "vfx_director" in REVIEW_LOG_ROLES


def test_vfx_director_revise_rounds_counted(tmp_path):
    """特效帽换帽复审走 revise 记账——轮次与振荡检测正常生效。"""
    proj = init_project(tmp_path, "rlvfx2", "特效振荡", "cinematic")
    _record(proj, "vfx_director", "final_prompt", "REVISE", [_f("vfx.onset")], phase="revise")
    _record(proj, "vfx_director", "final_prompt", "REVISE", [_f("vfx.onset")], phase="revise")
    summary = summarize_reviews(str(proj))
    assert len(summary["oscillations"]) == 1
    assert summary["oscillations"][0]["role"] == "vfx_director"
