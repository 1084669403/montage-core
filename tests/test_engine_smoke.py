"""project / artifacts / budget / decisions 冒烟测试。"""

import json

from montage.engine.artifacts import ArtifactStore
from montage.engine.budget import BudgetLedger
from montage.engine.decisions import DecisionLog
from montage.engine.project import init_project


def test_init_project_layout(tmp_path):
    proj = init_project(tmp_path, "demo-run", "演示项目", "cinematic")
    assert (proj / "project.json").exists()
    for sub in ("artifacts", "renders", "history"):
        assert (proj / sub).is_dir()
    for sub in ("images", "video", "videos", "audio", "music"):
        assert (proj / "assets" / sub).is_dir()
    assert (proj / "scratch").is_dir()
    assert (proj / "assets" / "placed").is_dir()
    assert (proj / "REVIEW.md").is_file()
    meta = json.loads((proj / "project.json").read_text(encoding="utf-8"))
    assert meta["title"] == "演示项目"


def test_artifact_write_read(tmp_path):
    store = ArtifactStore(tmp_path / "proj")
    store.write("script", {"title": "t", "sections": []})
    assert store.exists("script")
    assert store.read("script")["title"] == "t"
    assert store.list() == ["script"]


def test_budget_estimate_settle(tmp_path):
    ledger = BudgetLedger(tmp_path / "cost.jsonl")
    eid = ledger.estimate("image_generation", "sc01 首帧", "agnes_image", 0.02)
    ledger.settle(eid, 0.018)
    totals = ledger.totals()
    assert totals["entries"] == 2
    assert totals["settled_usd"] == 0.018


def test_budget_ceiling(tmp_path):
    ledger = BudgetLedger(tmp_path / "cost.jsonl", budget_ceiling_usd=0.10)
    assert ledger.over_budget() is False
    ledger.estimate("image_generation", "a", "agnes_image", 0.08)
    assert ledger.over_budget() is False  # 0.08 <= 0.10
    ledger.estimate("image_generation", "b", "agnes_image", 0.05)
    assert ledger.over_budget() is True  # 0.13 > 0.10
    # 无封顶恒为 False
    assert BudgetLedger(tmp_path / "x.jsonl").over_budget() is False


def test_settle_append_only_reconciliation(tmp_path):
    """settle 追加 settlement 行、不反写 estimate 行；对账字段正确。"""
    ledger = BudgetLedger(tmp_path / "cost.jsonl")
    eid = ledger.estimate("tts", "旁白", "doubao_tts", 0.05)
    eid2 = ledger.estimate("image_generation", "首帧", "agnes_image", 0.03)
    ledger.settle(eid, 0.048)

    totals = ledger.totals()
    # 原始行未被改写（estimate 行仍是 estimated，无 actual）
    est_rows = [e for e in ledger.entries() if e.type != "settlement"]
    assert all(e.status == "estimated" for e in est_rows)
    assert all(e.actual_usd is None for e in est_rows)
    # 对账：净未结算 = 0.08 - 0.048
    assert totals["estimated_raw_usd"] == 0.08
    assert totals["settled_usd"] == 0.048
    assert abs(totals["estimated_outstanding_usd"] - 0.032) < 1e-6
    # entries 保持全行（2 estimate + 1 settlement）
    assert totals["entries"] == 3
    assert totals["estimates"] == 2
    assert totals["settlements"] == 1
    # settlement 行关联 estimate_id
    stl = [e for e in ledger.entries() if e.type == "settlement"]
    assert stl[0].estimate_id == eid


def test_totals_by_category(tmp_path):
    ledger = BudgetLedger(tmp_path / "cost.jsonl")
    ledger.estimate("tts", "旁白", "doubao_tts", 0.02)
    ledger.estimate("image_generation", "首帧", "agnes_image", 0.03)
    by_cat = ledger.totals()["by_category"]
    assert by_cat["tts"] == 0.02
    assert by_cat["image_generation"] == 0.03


def test_estimate_checked_flag(tmp_path):
    ledger = BudgetLedger(tmp_path / "cost.jsonl", budget_ceiling_usd=0.10)
    _, ok = ledger.estimate_checked("a", "x", "tool", 0.08)
    assert ok is True
    _, ok2 = ledger.estimate_checked("a", "y", "tool", 0.05)  # 0.13 > 0.10
    assert ok2 is False


def test_decision_log_latest_wins(tmp_path):
    log = DecisionLog(tmp_path / "decisions.jsonl")
    log.log("video_loop", "供应商", "agnes", options_considered=["agnes", "jimeng"])
    log.log("video_loop", "供应商", "jimeng", rejected_because="用户改主意")
    current = log.current()
    assert current[("video_loop", "供应商")].choice == "jimeng"
    assert current[("video_loop", "供应商")].revised is True
    assert len(log.entries()) == 2  # 历史不删除
