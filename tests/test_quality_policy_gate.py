from __future__ import annotations

from pathlib import Path

from montage.engine import produce


class _FakeStore:
    def __init__(self, values: dict[str, object | None]):
        self.values = values

    def read(self, name: str):
        return self.values.get(name)


def _progress():
    return {"version": "1", "status": "running", "steps": {}}


def _patch_gate(monkeypatch, *, mode: str, quality: dict, saves: list):
    monkeypatch.setattr(
        produce,
        "ArtifactStore",
        lambda _root: _FakeStore({"edit_metrics": {}, "film_health": {}}),
    )
    monkeypatch.setattr(produce, "load_loop_policy", lambda _root: {"quality_mode": mode})
    monkeypatch.setattr(
        "montage.engine.delivery_report.build_quality_gate",
        lambda **_kwargs: quality,
    )
    monkeypatch.setattr(
        produce,
        "save_progress",
        lambda _root, progress: saves.append(progress),
    )


def test_quality_policy_gate_blocks_full_without_vlm_verification(monkeypatch):
    saves: list = []
    _patch_gate(monkeypatch, mode="full", quality={
        "mode": "full",
        "blocked": True,
        "blocked_reasons": ["full policy requires VLM verification"],
        "vlm": {
            "enabled": False, "skipped": True, "checked": 0,
            "reason": "DASHSCOPE_API_KEY missing", "verified": False,
            "critical_count": 0,
        },
    }, saves=saves)
    progress = _progress()
    result = produce._quality_policy_gate(
        Path("fake-project"), progress, phase="pre_assemble",
    )
    assert result is not None and result["success"] is False
    assert result["code"] == 2
    assert progress["status"] == "fail"
    row = progress["steps"]["quality_policy"]
    assert row["status"] == "fail"
    assert row["phase"] == "pre_assemble"
    assert row["quality_mode"] == "full"
    assert row["vlm_skipped"] is True
    assert len(saves) == 1


def test_quality_policy_gate_allows_degraded_policy(monkeypatch):
    saves: list = []
    _patch_gate(monkeypatch, mode="degraded", quality={
        "mode": "degraded",
        "blocked": False,
        "blocked_reasons": [],
        "vlm": {
            "enabled": False, "skipped": True, "checked": 0,
            "reason": "DASHSCOPE_API_KEY missing", "verified": False,
            "critical_count": 0,
        },
    }, saves=saves)
    progress = _progress()
    result = produce._quality_policy_gate(
        Path("fake-project"), progress, phase="pre_export",
    )
    assert result is None
    assert progress["status"] == "running"
    row = progress["steps"]["quality_policy"]
    assert row["status"] == "ok"
    assert row["phase"] == "pre_export"
    assert row["blocked"] is False
    assert saves == []


def test_quality_policy_gate_records_explicit_degraded_vlm_acceptance(monkeypatch):
    saves: list = []
    _patch_gate(monkeypatch, mode="degraded", quality={
        "mode": "degraded",
        "blocked": False,
        "blocked_reasons": [],
        "vlm": {
            "enabled": False, "skipped": True, "checked": 0,
            "reason": "DASHSCOPE_API_KEY missing", "verified": False,
            "critical_count": 0,
        },
    }, saves=saves)
    progress = _progress()
    result = produce._quality_policy_gate(
        Path("fake-project"), progress, phase="pre_assemble",
        accept_degraded_vlm=True,
    )
    assert result is None
    review = progress["human_review"]
    assert review["decision"] == "accepted_with_degraded_vlm"
    assert review["mode"] == "degraded"
    assert review["reason"] == "DASHSCOPE_API_KEY missing"
    assert review["accepted_at"]
    assert progress["steps"]["quality_policy"]["human_review_decision"] == (
        "accepted_with_degraded_vlm"
    )


def test_quality_policy_gate_keeps_degraded_vlm_pending_without_flag(monkeypatch):
    saves: list = []
    _patch_gate(monkeypatch, mode="degraded", quality={
        "mode": "degraded",
        "blocked": False,
        "blocked_reasons": [],
        "vlm": {
            "enabled": False, "skipped": True, "checked": 0,
            "reason": "DASHSCOPE_API_KEY missing", "verified": False,
            "critical_count": 0,
        },
    }, saves=saves)
    progress = _progress()
    result = produce._quality_policy_gate(
        Path("fake-project"), progress, phase="pre_assemble",
    )
    assert result is None
    assert progress["human_review"]["decision"] == "pending"
    assert progress["human_review"]["accepted_at"] == ""
    assert progress["steps"]["quality_policy"]["human_review_decision"] == "pending"


def test_quality_policy_gate_does_not_accept_degraded_under_full(monkeypatch):
    saves: list = []
    _patch_gate(monkeypatch, mode="full", quality={
        "mode": "full",
        "blocked": True,
        "blocked_reasons": ["full policy requires VLM verification"],
        "vlm": {
            "enabled": False, "skipped": True, "checked": 0,
            "reason": "DASHSCOPE_API_KEY missing", "verified": False,
            "critical_count": 0,
        },
    }, saves=saves)
    progress = _progress()
    result = produce._quality_policy_gate(
        Path("fake-project"), progress, phase="pre_assemble",
        accept_degraded_vlm=True,
    )
    assert result is not None and result["success"] is False
    assert "human_review" not in progress
    assert progress["steps"]["quality_policy"]["human_review_decision"] == "pending"


def test_quality_policy_gate_fails_invalid_mode(monkeypatch):
    saves: list = []
    monkeypatch.setattr(
        produce,
        "ArtifactStore",
        lambda _root: _FakeStore({"edit_metrics": {}, "film_health": {}}),
    )
    monkeypatch.setattr(produce, "load_loop_policy", lambda _root: {"quality_mode": "bad"})
    monkeypatch.setattr(
        "montage.engine.delivery_report.build_quality_gate",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("invalid quality_mode")),
    )
    monkeypatch.setattr(
        produce,
        "save_progress",
        lambda _root, progress: saves.append(progress),
    )
    progress = _progress()
    result = produce._quality_policy_gate(
        Path("fake-project"), progress, phase="pre_assemble",
    )
    assert result is not None and result["success"] is False
    assert "质量策略校验失败" in result["error"]
    assert progress["steps"]["quality_policy"]["status"] == "fail"
    assert len(saves) == 1
