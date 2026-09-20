"""P0-identity-memory：角色级身份记忆库（canonical 锚 + 保守更新 + 漂移重拍）。"""

from __future__ import annotations

import json

from montage.engine.identity import (
    canonical_ref_id,
    clear_drift,
    drift_issues,
    empty_memory,
    entry_of,
    evaluate_candidate,
    findings,
    identity_key,
    identity_subject,
    load_memory,
    mark_retaken,
    memory_path,
    parse_identity_key,
    record_drift,
    retake_subjects,
    review_passed,
    save_memory,
)


def _pass(score=0.9):
    return {"ok": True, "score": score, "issues": [], "skipped": False}


def _fail(message="脸型不对"):
    return {
        "ok": False,
        "score": 0.2,
        "issues": [{"severity": "critical", "kind": "人物不一致", "message": message}],
        "skipped": False,
    }


def _skipped():
    return {"ok": False, "score": None, "issues": [], "skipped": True}


def test_identity_key_and_subject_roundtrip():
    assert identity_key("a") == "a"
    assert identity_key("a", "young") == "a:young"
    assert parse_identity_key("a") == ("a", "")
    assert parse_identity_key("a:young") == ("a", "young")
    assert identity_subject("a") == "portrait/a"
    assert identity_subject("a", "young") == "portrait/a:young"


def test_review_passed_requires_score_and_flag():
    assert review_passed(_pass(0.7)) is True
    assert review_passed(_pass(0.5)) is False
    assert review_passed(_pass(None)) is False
    assert review_passed(_fail()) is False
    assert review_passed(_skipped()) is False


def test_first_portrait_passing_review_becomes_canonical():
    mem = empty_memory()
    out = evaluate_candidate(mem, "a", review=_pass(0.85), ref_id="portrait_a", path="/p.png")
    assert out["action"] == "init"
    assert canonical_ref_id(mem, "a") == "portrait_a"
    assert entry_of(mem, "a")["canonical"]["verified"] is True


def test_first_portrait_failing_review_rejects_anchor():
    mem = empty_memory()
    out = evaluate_candidate(mem, "a", review=_fail(), ref_id="portrait_a", path="/p.png")
    assert out["action"] == "reject"
    assert canonical_ref_id(mem, "a") == ""
    assert entry_of(mem, "a")["history"][0]["reason"] == "init_failed_review"


def test_unverified_init_when_vlm_skipped():
    """零密钥环境仍要留下身份锚，但标 verified=False 供人审。"""
    mem = empty_memory()
    out = evaluate_candidate(mem, "a", review=_skipped(), ref_id="portrait_a", path="/p.png")
    assert out["action"] == "init"
    assert entry_of(mem, "a")["canonical"]["verified"] is False


def test_conservative_update_keeps_anchor_when_candidate_worse():
    mem = empty_memory()
    evaluate_candidate(mem, "a", review=_pass(0.95), ref_id="portrait_a", path="/a.png")
    out = evaluate_candidate(mem, "a", review=_pass(0.70), ref_id="portrait_a2", path="/a2.png")
    assert out["action"] == "hold"
    assert canonical_ref_id(mem, "a") == "portrait_a"
    assert entry_of(mem, "a")["history"][-1]["reason"] == "lower_score"


def test_conservative_update_rejects_unverified_candidate():
    mem = empty_memory()
    evaluate_candidate(mem, "a", review=_pass(0.95), ref_id="portrait_a", path="/a.png")
    out = evaluate_candidate(mem, "a", review=_fail(), ref_id="portrait_a2", path="/a2.png")
    assert out["action"] == "hold"
    assert canonical_ref_id(mem, "a") == "portrait_a"
    assert entry_of(mem, "a")["history"][-1]["reason"] == "failed_review"


def test_better_candidate_adopts_and_archives_old():
    mem = empty_memory()
    evaluate_candidate(mem, "a", review=_pass(0.60), ref_id="portrait_a", path="/a.png")
    out = evaluate_candidate(mem, "a", review=_pass(0.98), ref_id="portrait_a2", path="/a2.png")
    assert out["action"] == "adopt"
    assert canonical_ref_id(mem, "a") == "portrait_a2"
    assert entry_of(mem, "a")["history"][-1]["reason"] == "superseded"


def test_drift_threshold_triggers_retake_and_clears_on_pass():
    mem = empty_memory()
    evaluate_candidate(mem, "a", review=_pass(), ref_id="portrait_a", path="/a.png")
    assert record_drift(mem, "a", shot_id="s1", threshold=2)["retake"] is False
    assert retake_subjects(mem) == []
    second = record_drift(mem, "a", shot_id="s2", threshold=2)
    assert second["retake"] is True
    assert retake_subjects(mem) == ["portrait/a"]
    assert findings(mem)[0]["field"] == "identity/portrait/a"

    clear_drift(mem, "a")
    assert entry_of(mem, "a")["drift_count"] == 0


def test_mark_retaken_clears_flag_and_count():
    mem = empty_memory()
    evaluate_candidate(mem, "a", review=_pass(), ref_id="portrait_a", path="/a.png")
    record_drift(mem, "a", threshold=1)
    assert retake_subjects(mem) == ["portrait/a"]
    mark_retaken(mem, "a", ref_id="portrait_a2")
    assert retake_subjects(mem) == []
    assert entry_of(mem, "a")["drift_count"] == 0


def test_drift_issues_only_critical_identity_kind():
    assert drift_issues(_fail()) == _fail()["issues"]
    warning_only = {
        "ok": True,
        "score": 0.8,
        "issues": [{"severity": "warning", "kind": "人物不一致", "message": "轻微"}],
        "skipped": False,
    }
    assert drift_issues(warning_only) == []
    other = {
        "ok": False,
        "score": 0.1,
        "issues": [{"severity": "critical", "kind": "崩坏", "message": "手崩"}],
        "skipped": False,
    }
    assert drift_issues(other) == []
    assert drift_issues(_skipped()) == []


def test_form_scoped_keys_are_independent():
    mem = empty_memory()
    evaluate_candidate(mem, "a", review=_pass(), ref_id="portrait_a", path="/a.png")
    evaluate_candidate(mem, "a:young", review=_pass(), ref_id="portrait_a_young", path="/y.png")
    assert canonical_ref_id(mem, "a") == "portrait_a"
    assert canonical_ref_id(mem, "a:young") == "portrait_a_young"


def test_memory_roundtrip_and_corruption_tolerance(tmp_path):
    mem = empty_memory()
    evaluate_candidate(mem, "a", review=_pass(), ref_id="portrait_a", path="/a.png")
    path = save_memory(tmp_path, mem)
    assert path.endswith("identity_memory.json")
    assert json.loads(memory_path(tmp_path).read_text(encoding="utf-8"))["characters"]["a"]
    assert canonical_ref_id(load_memory(tmp_path), "a") == "portrait_a"

    memory_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert load_memory(tmp_path)["characters"] == {}
    assert canonical_ref_id(load_memory(tmp_path), "a") == ""


def test_load_memory_missing_returns_empty(tmp_path):
    assert load_memory(tmp_path / "nope") == empty_memory()
