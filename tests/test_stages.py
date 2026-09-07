"""checkpoint 状态机测试：门禁、归档、next_stage。"""

import json

import pytest

from montage.engine.stages import CheckpointStore, StageStatus, STAGE_ORDER


@pytest.fixture
def store(tmp_path):
    return CheckpointStore(tmp_path / "proj")


def test_next_stage_starts_at_research(store):
    assert store.next_stage() == "research"


def test_gate_violation(store):
    with pytest.raises(ValueError, match="GATE VIOLATION"):
        store.write("script", StageStatus.COMPLETED.value, human_approved=False)


def test_complete_with_approval(store):
    store.write("research", StageStatus.COMPLETED.value, human_approved=True)
    assert store.next_stage() == "proposal"


def test_in_progress_no_gate_needed(store):
    cp = store.write("assets", StageStatus.IN_PROGRESS.value)
    assert cp.status == StageStatus.IN_PROGRESS


def test_unknown_stage_rejected(store):
    with pytest.raises(ValueError):
        store.write("nope", StageStatus.IN_PROGRESS.value)


def test_rewrite_archives_history(store):
    store.write("research", StageStatus.AWAITING_HUMAN.value, human_approved=True)
    store.write("research", StageStatus.COMPLETED.value, human_approved=True)
    history = list((store.project_dir / "history").glob("checkpoint_research_*.json"))
    assert len(history) == 1


def test_progress_map(store):
    store.write("research", StageStatus.IN_PROGRESS.value)
    progress = store.progress()
    assert progress["research"] == StageStatus.IN_PROGRESS.value
    assert progress["proposal"] == StageStatus.PENDING.value
    assert len(progress) == len(STAGE_ORDER)


def test_approved_by_produce_completes_gated(store):
    cp = store.write(
        "assets",
        StageStatus.COMPLETED.value,
        human_approved=False,
        approved_by="produce",
    )
    assert cp.status == StageStatus.COMPLETED.value
    assert cp.human_approved is False
    assert cp.approved_by == "produce"


def test_fake_human_still_blocked(store):
    with pytest.raises(ValueError, match="GATE VIOLATION"):
        store.write("script", StageStatus.COMPLETED.value, human_approved=False, approved_by="")
    with pytest.raises(ValueError, match="GATE VIOLATION"):
        store.write("script", StageStatus.COMPLETED.value, human_approved=False, approved_by="robot")


def test_old_human_approved_bool_json(store):
    path = store.project_dir / "checkpoint_script.json"
    path.write_text(
        json.dumps({
            "stage": "script",
            "status": StageStatus.COMPLETED.value,
            "human_approved": True,
            "artifact": None,
            "note": "",
            "updated_at": "2020-01-01T00:00:00+00:00",
        }),
        encoding="utf-8",
    )
    cp = store.read("script")
    assert cp.human_approved is True
    assert cp.approved_by == "human"
    store.write("assets", StageStatus.COMPLETED.value, human_approved=True)
    assert store.read("assets").approved_by == "human"
