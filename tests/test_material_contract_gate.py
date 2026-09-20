"""Tests for the read-only material contract gate around produce assemble."""

from __future__ import annotations

from pathlib import Path

from montage.engine import produce


class _FakeStore:
    def __init__(self, values: dict[str, object | None]):
        self.values = values

    def read(self, _name: str):
        return self.values.get(_name)


def _progress():
    return {"version": "1", "status": "running", "steps": {}}


def _patch_store(monkeypatch, values: dict[str, object | None], saves: list):
    monkeypatch.setattr(produce, "ArtifactStore", lambda _root: _FakeStore(values))
    monkeypatch.setattr(
        produce,
        "save_progress",
        lambda _root, progress: saves.append(progress),
    )


def test_material_contract_gate_blocks_critical_without_saving_project(monkeypatch):
    saves: list = []
    _patch_store(
        monkeypatch,
        {"scene_plan": {}, "asset_manifest": {}, "compose_plan": {}},
        saves,
    )
    contract = {
        "pass": False,
        "summary": {"required_shots": 1, "actual_ai_video_count": 0},
        "findings": [
            {"severity": "critical", "field": "sh01", "message": "expected video, got image"},
            {"severity": "warning", "field": "path", "message": "absolute path"},
        ],
    }
    monkeypatch.setattr(produce, "build_material_contract", lambda **_kwargs: contract)
    progress = _progress()
    result = produce._material_contract_gate(
        Path("fake-project"), progress, phase="pre_assemble", all_ai_video=True,
    )
    assert result is not None and result["success"] is False
    assert result["code"] == 2
    assert progress["status"] == "fail"
    row = progress["steps"]["material_contract"]
    assert row["status"] == "fail"
    assert row["phase"] == "pre_assemble"
    assert row["critical_count"] == 1
    assert row["blocked"] is True
    assert len(saves) == 1


def test_material_contract_gate_allows_warning_only_contract(monkeypatch):
    saves: list = []
    _patch_store(
        monkeypatch,
        {"scene_plan": {}, "asset_manifest": {}, "compose_plan": {}},
        saves,
    )
    contract = {
        "pass": True,
        "summary": {"required_shots": 1, "actual_ai_video_count": 1},
        "findings": [
            {"severity": "warning", "field": "path", "message": "absolute path"},
        ],
    }
    monkeypatch.setattr(produce, "build_material_contract", lambda **_kwargs: contract)
    progress = _progress()
    result = produce._material_contract_gate(
        Path("fake-project"), progress, phase="post_assemble", all_ai_video=True,
    )
    assert result is None
    assert progress["status"] == "running"
    row = progress["steps"]["material_contract"]
    assert row["status"] == "ok"
    assert row["phase"] == "post_assemble"
    assert row["blocked"] is False
    assert saves == []


def test_material_contract_gate_requires_core_artifacts(monkeypatch):
    saves: list = []
    _patch_store(monkeypatch, {"scene_plan": {}, "asset_manifest": None}, saves)
    progress = _progress()
    result = produce._material_contract_gate(
        Path("fake-project"), progress, phase="pre_assemble", all_ai_video=True,
    )
    assert result is not None and result["success"] is False
    assert progress["steps"]["material_contract"]["status"] == "fail"
    assert len(saves) == 1
