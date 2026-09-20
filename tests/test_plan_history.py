"""plan_history / plan_diff 测试（P0-3 时间线：build-vs-buy 版本链）。

关键回归：
- diff 必须**字段级**（只改一个 shot.speed 不能报成「全变」）。
- 幂等：同 overrides 连做两次 replan 不新增版本。
- revert 不删历史且自身记新版本；回滚后必须提示重渲。
- 老项目（有 plan.json 无版本链）零迁移：补 rev=1 再记 rev=2。
- 坏 JSON / 缺文件不抛错、不删快照。
"""

from __future__ import annotations

import json

from montage.tools.auto_edit import (
    DEFAULT_MAX_VERSIONS,
    AutoEdit,
    append_plan_version,
    empty_history,
    history_path,
    load_history,
    plan_diff,
    plan_diff_summary,
    plan_differs,
    plan_duration,
    plan_history_summary,
    read_version,
    revert_plan,
)


def _plan(shots, *, segments=None, params=None, edits=None, updated="2026-01-01T00:00:00+00:00"):
    """最小 plan：shots = {shot_id: (start, end, speed?)}。"""
    seg = {
        "segment_id": "seg_0",
        "source_index": 0,
        "src_start": 0.0,
        "src_end": 20.0,
        "action": "keep",
        "shots": [
            {"shot_id": sid, "start": s, "end": e, "speed": sp}
            for sid, (s, e, sp) in shots.items()
        ],
    }
    return {
        "schema_version": "1.0",
        "session_id": "sess_1",
        "stage": "plan",
        "updated_at": updated,
        "segments": segments or [seg],
        "params": params if params is not None else {"target_duration": None, "bpm": None},
        "edits": edits if edits is not None else [{"shot_id": "a", "transition": "cut"}],
    }


# ---------------------------------------------------------------------------
# plan_diff：字段级
# ---------------------------------------------------------------------------


def test_plan_diff_detects_only_speed_change():
    """验收标准 4：只改速率 ⇒ resped=1、kept=N，不得整片判「全变」。"""
    a = _plan({"a": (0.0, 5.0, 1.0), "b": (5.0, 10.0, 1.0)})
    b = _plan({"a": (0.0, 5.0, 1.1), "b": (5.0, 10.0, 1.0)})
    diff = plan_diff(a, b)
    assert diff["counts"] == {"kept": 2, "added": 0, "removed": 0, "retimed": 0, "resped": 1, "vfxed": 0}
    assert diff["shots_resped"] == ["a"]
    assert diff["shots_kept"] == ["a", "b"]
    assert diff["shots_added"] == [] and diff["shots_removed"] == []
    assert diff["unchanged"] is False


def test_plan_diff_separates_retime_from_respeed():
    a = _plan({"a": (0.0, 5.0, 1.0)})
    b = _plan({"a": (1.0, 5.0, 1.2)})
    diff = plan_diff(a, b)
    assert diff["shots_retimed"] == ["a"]
    assert diff["shots_resped"] == ["a"]
    assert diff["counts"]["retimed"] == 1 and diff["counts"]["resped"] == 1


def test_plan_diff_reports_added_and_removed():
    a = _plan({"a": (0.0, 5.0, 1.0), "b": (5.0, 10.0, 1.0)})
    b = _plan({"a": (0.0, 5.0, 1.0), "c": (5.0, 10.0, 1.0)})
    diff = plan_diff(a, b)
    assert diff["shots_removed"] == ["b"] and diff["shots_added"] == ["c"]
    assert diff["counts"]["kept"] == 1


def test_plan_diff_reports_segment_action_and_params():
    a = _plan({"a": (0.0, 5.0, 1.0)}, params={"min_hold": 1.0, "lut": "x"})
    segs = json.loads(json.dumps(a["segments"]))
    segs[0]["action"] = "drop"
    b = _plan({"a": (0.0, 5.0, 1.0)}, segments=segs, params={"min_hold": 2.0, "lut": "x"})
    diff = plan_diff(a, b)
    assert diff["segments_action_changed"]["seg_0"] == {"before": "keep", "after": "drop"}
    assert list(diff["params_changed"]) == ["min_hold"]
    assert diff["params_changed"]["min_hold"] == {"before": 1.0, "after": 2.0}


def test_plan_diff_counts_edit_changes():
    a = _plan({"a": (0.0, 5.0, 1.0)}, edits=[{"shot_id": "a", "transition": "cut"}])
    b = _plan({"a": (0.0, 5.0, 1.0)}, edits=[{"shot_id": "a", "transition": "xfade"}])
    diff = plan_diff(a, b)
    assert diff["edits_changed"] == 1 and diff["unchanged"] is False


def test_plan_diff_unchanged_is_true_for_same_content():
    a = _plan({"a": (0.0, 5.0, 1.0)})
    b = _plan({"a": (0.0, 5.0, 1.0)}, updated="2026-09-09T00:00:00+00:00")
    assert plan_diff(a, b)["unchanged"] is True
    assert plan_differs(a, b) is False


def test_plan_diff_duration_delta():
    a = _plan({"a": (0.0, 6.0, 1.0)})
    b = _plan({"a": (0.0, 4.0, 1.0)})
    diff = plan_diff(a, b)
    assert diff["duration_before"] == 6.0 and diff["duration_after"] == 4.0
    assert diff["duration_delta"] == -2.0


def test_plan_duration_respects_drop_and_speed():
    seg_keep = {"segment_id": "s1", "action": "keep", "shots": [{"shot_id": "a", "start": 0.0, "end": 4.0, "speed": 2.0}]}
    seg_drop = {"segment_id": "s2", "action": "drop", "shots": [{"shot_id": "b", "start": 0.0, "end": 9.0, "speed": 1.0}]}
    plan = _plan({}, segments=[seg_keep, seg_drop])
    assert plan_duration(plan) == 2.0


def test_plan_diff_summary_is_readable():
    a = _plan({"a": (0.0, 5.0, 1.0), "b": (5.0, 10.0, 1.0)})
    b = _plan({"a": (0.0, 5.0, 1.0)})
    text = plan_diff_summary(plan_diff(a, b))
    assert "删1镜" in text and "时长" in text
    assert plan_diff_summary({"unchanged": True}) == "无变化"


# ---------------------------------------------------------------------------
# 版本链
# ---------------------------------------------------------------------------


def test_append_creates_rev1_then_increments(tmp_path):
    a = _plan({"a": (0.0, 5.0, 1.0)})
    r1 = append_plan_version(tmp_path, a, reason="初始草稿")
    assert r1["changed"] is True and r1["rev"] == 1
    assert r1["index"]["current"] == 1
    index = load_history(tmp_path)
    assert len(index["entries"]) == 1
    assert index["entries"][0]["diff_from_prev"] is None  # 首版无 prev

    b = _plan({"a": (0.0, 5.0, 1.2)})
    r2 = append_plan_version(tmp_path, b, reason="加速")
    assert r2["rev"] == 2 and len(load_history(tmp_path)["entries"]) == 2
    assert r2["diff"]["shots_resped"] == ["a"]


def test_append_is_idempotent_for_same_content(tmp_path):
    """验收标准 2：同一内容连记两次 ⇒ 第二次不新增。"""
    a = _plan({"a": (0.0, 5.0, 1.0)})
    append_plan_version(tmp_path, a)
    again = append_plan_version(tmp_path, json.loads(json.dumps(a)))
    assert again["changed"] is False
    assert len(load_history(tmp_path)["entries"]) == 1


def test_append_seeds_legacy_project_with_previous(tmp_path):
    """验收标准 5（零迁移）：老项目有 plan.json 无版本链 ⇒ 补 rev=1 再记 rev=2。"""
    legacy = _plan({"a": (0.0, 5.0, 1.0)})
    new = _plan({"a": (0.0, 5.0, 1.0), "b": (5.0, 9.0, 1.0)})
    recorded = append_plan_version(tmp_path, new, previous=legacy, reason="补录")
    index = load_history(tmp_path)
    assert recorded["rev"] == 2
    assert [e["rev"] for e in index["entries"]] == [1, 2]
    assert index["entries"][0]["reason"] == "历史起点（补录）"
    assert read_version(tmp_path, 1)["segments"] == legacy["segments"]
    assert recorded["diff"]["shots_added"] == ["b"]


def test_append_without_previous_starts_at_rev1(tmp_path):
    recorded = append_plan_version(tmp_path, _plan({"a": (0.0, 1.0, 1.0)}))
    assert recorded["rev"] == 1
    assert len(load_history(tmp_path)["entries"]) == 1


def test_history_index_passes_own_schema(tmp_path):
    from montage.engine.artifacts import ArtifactStore
    from montage.schemas import get_schema

    a = _plan({"a": (0.0, 5.0, 1.0)})
    append_plan_version(tmp_path, a)
    append_plan_version(tmp_path, _plan({"a": (0.0, 5.0, 1.1)}), reason="加速")
    index = json.loads(history_path(tmp_path).read_text(encoding="utf-8"))
    assert ArtifactStore.validate(index, get_schema("plan_history")) == []


def test_append_reports_schema_errors(tmp_path):
    assert append_plan_version(tmp_path, _plan({"a": (0.0, 1.0, 1.0)}))["schema_errors"] == []


def test_prune_keeps_recent_and_counts_pruned(tmp_path):
    for i in range(5):
        append_plan_version(tmp_path, _plan({"a": (0.0, float(5 + i), 1.0)}), max_versions=3)
    index = load_history(tmp_path)
    assert len(index["entries"]) == 3
    assert index["pruned"] == 2
    assert [e["rev"] for e in index["entries"]] == [3, 4, 5]
    assert read_version(tmp_path, 1) is None      # 已裁掉
    assert read_version(tmp_path, 5) is not None


def test_prune_zero_means_unlimited(tmp_path):
    for i in range(4):
        append_plan_version(tmp_path, _plan({"a": (0.0, float(5 + i), 1.0)}), max_versions=0)
    assert len(load_history(tmp_path)["entries"]) == 4
    assert load_history(tmp_path)["pruned"] == 0


def test_load_history_tolerates_garbage(tmp_path):
    """验收标准 5：坏 JSON ⇒ 空索引，不抛错。"""
    assert load_history(tmp_path) == empty_history()
    path = history_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 不是 json", encoding="utf-8")
    assert load_history(tmp_path) == empty_history()
    path.write_text('["不是对象"]', encoding="utf-8")
    assert load_history(tmp_path) == empty_history()


def test_read_version_missing_or_corrupt_snapshot(tmp_path):
    append_plan_version(tmp_path, _plan({"a": (0.0, 5.0, 1.0)}))
    assert read_version(tmp_path, 99) is None
    snapshot = tmp_path / "tmp_autoedit" / "plan_history" / "v0001.json"
    snapshot.write_text("坏了", encoding="utf-8")
    assert read_version(tmp_path, 1) is None


def test_history_summary_shape(tmp_path):
    append_plan_version(tmp_path, _plan({"a": (0.0, 5.0, 1.0)}))
    append_plan_version(tmp_path, _plan({"a": (0.0, 5.0, 1.0), "b": (5.0, 8.0, 1.0)}), reason="加一镜")
    summary = plan_history_summary(tmp_path)
    assert summary["current"] == 2 and summary["count"] == 2
    assert summary["entries"][0]["summary"] == ""       # 首版无 diff
    assert "增1镜" in summary["entries"][1]["summary"]
    assert summary["entries"][1]["reason"] == "加一镜"


# ---------------------------------------------------------------------------
# revert
# ---------------------------------------------------------------------------


def test_revert_restores_plan_and_keeps_history(tmp_path):
    """验收标准 3：回滚后 plan.json 等价 v1，且新增一条 revert 版本。"""
    auto = tmp_path / "auto_edit"
    auto.mkdir(parents=True)
    a = _plan({"a": (0.0, 5.0, 1.0)})
    (auto / "plan.json").write_text(json.dumps(a), encoding="utf-8")
    append_plan_version(tmp_path, a)

    b = _plan({"a": (0.0, 2.5, 2.0)})
    (auto / "plan.json").write_text(json.dumps(b), encoding="utf-8")
    append_plan_version(tmp_path, b, reason="加速")

    out = revert_plan(tmp_path, 1)
    assert out["ok"] is True and out["reverted_to"] == 1
    restored = json.loads((auto / "plan.json").read_text(encoding="utf-8"))
    assert plan_differs(restored, a) is False          # 内容等价（updated_at 不算）
    assert restored["updated_at"] != a["updated_at"]   # 时间戳刷新，便于追溯
    index = load_history(tmp_path)
    assert [e["rev"] for e in index["entries"]] == [1, 2, 3]
    assert "revert to v1" in index["entries"][-1]["reason"]
    assert "render" in out["note"]


def test_revert_rejects_unknown_rev(tmp_path):
    out = revert_plan(tmp_path, 7)
    assert out["ok"] is False and "v7" in out["error"]


def test_revert_of_legacy_project_seeds_history(tmp_path):
    """老项目没版本链就回滚：先补历史起点，不丢当前状态。"""
    auto = tmp_path / "auto_edit"
    auto.mkdir(parents=True)
    current = _plan({"a": (0.0, 9.0, 1.0)})
    (auto / "plan.json").write_text(json.dumps(current), encoding="utf-8")
    assert revert_plan(tmp_path, 1)["ok"] is False      # 无历史可回
    append_plan_version(tmp_path, current, reason="起点")
    assert read_version(tmp_path, 1) is not None


# ---------------------------------------------------------------------------
# 工具层：history / revert 操作 + plan/replan 自动记版本
# ---------------------------------------------------------------------------


def _stub_probe(monkeypatch, media, duration=20.0):
    monkeypatch.setattr(
        "montage.tools.auto_edit.probe_sources",
        lambda paths, **kw: {"kind": kw.get("kind"), "is_speech": False,
                             "files": [{"path": str(media), "duration": duration}]},
    )
    monkeypatch.setattr("montage.tools.auto_edit.analyze_scene_changes", lambda path, threshold=0.3: [])


def test_plan_records_rev1_and_reason(tmp_path, monkeypatch):
    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    _stub_probe(monkeypatch, media)
    result = AutoEdit().execute({
        "operation": "plan", "project_dir": str(tmp_path / "proj"),
        "video": str(media), "style": "fresh", "reason": "第一版草稿",
    })
    assert result.success, result.error
    assert result.data["rev"] == 1
    assert result.data["history"]["count"] == 1
    assert result.data["history"]["entries"][0]["reason"] == "第一版草稿"


def test_plan_twice_keeps_single_version_when_unchanged(tmp_path, monkeypatch):
    """同参数再 plan 一次：plan_core（segments/edits/params）等价 ⇒ 不新增版本（幂等）。"""
    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    _stub_probe(monkeypatch, media)
    payload = {"operation": "plan", "project_dir": str(tmp_path / "proj"),
               "video": str(media), "style": "fresh"}
    first = AutoEdit().execute(dict(payload))
    assert first.success, first.error
    assert first.data["rev"] == 1
    # 逼出「session_id/updated_at 变了但内容没变」的场景：正是 plan_core 要挡的
    monkeypatch.setattr("montage.tools.auto_edit.new_session_id", lambda: "sess_other")
    second = AutoEdit().execute(dict(payload))
    assert second.success, second.error
    assert second.data["plan"]["session_id"] == "sess_other"
    assert second.data["rev"] == 1                      # 未新增
    assert second.data["history"]["count"] == 1


def test_replan_records_diff_and_reason(tmp_path, monkeypatch):
    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    _stub_probe(monkeypatch, media)
    project = str(tmp_path / "proj")
    assert AutoEdit().execute({"operation": "plan", "project_dir": project,
                               "video": str(media), "style": "fresh"}).success
    out = AutoEdit().execute({
        "operation": "replan", "project_dir": project,
        "overrides": {"seg_0": {"action": "drop"}}, "reason": "整段不要",
    })
    assert out.success, out.error
    assert out.data["rev"] == 2 and out.data["recorded"] is True
    assert out.data["diff"]["segments_action_changed"]["seg_0"] == {"before": "keep", "after": "drop"}
    assert "keep→drop" in out.data["diff_summary"]
    assert out.data["history"]["entries"][-1]["reason"] == "整段不要"


def test_replan_idempotent_does_not_add_version(tmp_path, monkeypatch):
    """验收标准 2：同 overrides 连做两次 ⇒ 第二次 changed=False 且不新增。"""
    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    _stub_probe(monkeypatch, media)
    project = str(tmp_path / "proj")
    assert AutoEdit().execute({"operation": "plan", "project_dir": project,
                               "video": str(media), "style": "fresh"}).success
    payload = {"operation": "replan", "project_dir": project,
               "overrides": {"seg_0": {"action": "drop"}}, "reason": "整段不要"}
    first = AutoEdit().execute(dict(payload))
    assert first.data["rev"] == 2
    second = AutoEdit().execute(dict(payload))
    assert second.success, second.error
    assert second.data["changed"] is False
    assert second.data["recorded"] is False
    assert second.data["rev"] == 2
    assert second.data["history"]["count"] == 2


def test_history_operation_returns_versions_and_plan(tmp_path, monkeypatch):
    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    _stub_probe(monkeypatch, media)
    project = str(tmp_path / "proj")
    assert AutoEdit().execute({"operation": "plan", "project_dir": project,
                               "video": str(media), "style": "fresh"}).success
    hist = AutoEdit().execute({"operation": "history", "project_dir": project})
    assert hist.success and hist.data["history"]["current"] == 1
    assert "plan" not in hist.data                     # 不指定 rev 不带正文
    one = AutoEdit().execute({"operation": "history", "project_dir": project, "rev": 1})
    assert one.success and one.data["plan"]["segments"]
    missing = AutoEdit().execute({"operation": "history", "project_dir": project, "rev": 9})
    assert missing.success is False


def test_history_operation_on_empty_project(tmp_path):
    """验收标准 5：老项目无版本链 ⇒ 空索引不抛错。"""
    out = AutoEdit().execute({"operation": "history", "project_dir": str(tmp_path / "fresh")})
    assert out.success, out.error
    assert out.data["history"]["count"] == 0 and out.data["history"]["current"] == 0


def test_revert_operation_restores_and_reports(tmp_path, monkeypatch):
    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    _stub_probe(monkeypatch, media)
    project = str(tmp_path / "proj")
    assert AutoEdit().execute({"operation": "plan", "project_dir": project,
                               "video": str(media), "style": "fresh"}).success
    assert AutoEdit().execute({"operation": "replan", "project_dir": project,
                               "overrides": {"seg_0": {"action": "drop"}}}).success
    out = AutoEdit().execute({"operation": "revert", "project_dir": project, "rev": 1})
    assert out.success, out.error
    assert out.data["reverted_to"] == 1 and out.data["rev"] == 3
    assert out.data["history"]["count"] == 3
    current = json.loads((tmp_path / "proj" / "auto_edit" / "plan.json").read_text(encoding="utf-8"))
    assert current["segments"][0]["action"] == "keep"


def test_revert_requires_rev(tmp_path):
    out = AutoEdit().execute({"operation": "revert", "project_dir": str(tmp_path / "proj")})
    assert out.success is False and "rev" in out.error


def test_decisions_jsonl_records_revisions(tmp_path, monkeypatch):
    """快照不进交付包，所以审计必须落在 decisions.jsonl（导出的那份）。"""
    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    _stub_probe(monkeypatch, media)
    project = tmp_path / "proj"
    assert AutoEdit().execute({"operation": "plan", "project_dir": str(project),
                               "video": str(media), "style": "fresh", "reason": "起草"}).success
    assert AutoEdit().execute({"operation": "replan", "project_dir": str(project),
                               "overrides": {"seg_0": {"action": "drop"}},
                               "reason": "删开场"}).success
    lines = [json.loads(l) for l in (project / "decisions.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    cats = [l for l in lines if l["category"] == "auto_edit"]
    assert len(cats) >= 2
    assert "rev 1" in cats[-2]["choice"] or "rev 1" in cats[0]["choice"]
    assert "rev 2" in cats[-1]["choice"] and "删开场" in cats[-1]["choice"]
    assert "keep→drop" in cats[-1]["rejected_because"]   # diff 摘要进审计


def test_max_versions_is_sticky_across_calls(tmp_path, monkeypatch):
    """上限是项目级策略：plan 时设一次，后续 replan 不传也沿用（否则会悄悄回 50）。"""
    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    _stub_probe(monkeypatch, media)
    project = str(tmp_path / "proj")
    assert AutoEdit().execute({"operation": "plan", "project_dir": project,
                               "video": str(media), "style": "fresh",
                               "max_versions": 1}).success
    for i in range(2):
        out = AutoEdit().execute({
            "operation": "replan", "project_dir": project,
            "overrides": {"seg_0": {"action": "drop" if i == 0 else "keep"}},
        })
        assert out.success, out.error
    index = load_history(project)
    assert index["max_versions"] == 1          # 策略被记住
    assert len(index["entries"]) == 1          # 只留最新一版
    assert index["pruned"] == 2


def test_max_versions_can_be_overridden_later(tmp_path):
    append_plan_version(tmp_path, _plan({"a": (0.0, 5.0, 1.0)}), max_versions=1)
    append_plan_version(tmp_path, _plan({"a": (0.0, 6.0, 1.0)}))
    assert load_history(tmp_path)["max_versions"] == 1
    append_plan_version(tmp_path, _plan({"a": (0.0, 7.0, 1.0)}), max_versions=0)
    index = load_history(tmp_path)
    assert index["max_versions"] == 0 and index["pruned"] == 1


def test_default_max_versions_constant():
    assert DEFAULT_MAX_VERSIONS == 50
