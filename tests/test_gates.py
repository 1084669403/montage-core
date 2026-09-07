"""gates 层测试：阶段完成产物门禁 + 工具白名单（不触碰 CheckpointStore 内部）。"""

import pytest

from montage.engine.artifacts import ArtifactStore
from montage.engine.gates import (
    GateError,
    check_tool_allowlist,
    stage_capabilities,
    validate_completion,
)
from montage.engine.project import init_project
from montage.pipelines import CINEMATIC


def _project(tmp_path, pid="demo"):
    return init_project(tmp_path, pid, "演示", "cinematic")


def _write_script(proj):
    ArtifactStore(proj).write("script", {"title": "t", "sections": [{"id": "s", "narration": "n"}]})


# ---------------------------------------------------------------------------
# 产物门禁
# ---------------------------------------------------------------------------


def test_completed_requires_produced_artifacts(tmp_path):
    proj = _project(tmp_path)
    with pytest.raises(GateError, match="缺失产物"):
        validate_completion(proj, "script", CINEMATIC)


def test_gate_ok_when_artifacts_present(tmp_path):
    proj = _project(tmp_path)
    _write_script(proj)
    result = validate_completion(proj, "script", CINEMATIC)
    assert result["ok"] is True
    assert result["missing"] == []


def test_gate_applies_to_all_stages(tmp_path):
    """非门禁阶段（research）同样校验 produces。"""
    proj = _project(tmp_path)
    with pytest.raises(GateError, match="research"):
        validate_completion(proj, "research", CINEMATIC)


def test_gate_relaxed_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MONTAGE_RELAX_GATES", "1")
    proj = _project(tmp_path)
    result = validate_completion(proj, "script", CINEMATIC)
    assert result["ok"] is True  # 降级：不阻塞
    assert result["warnings"] and "MONTAGE_RELAX_GATES" in result["warnings"][0]


def test_gate_pipeline_from_project_json(tmp_path):
    """不传 pipeline 时从 project.json 的 pipeline_type 读取。"""
    proj = _project(tmp_path)
    with pytest.raises(GateError, match="script"):
        validate_completion(proj, "script")


def test_gate_missing_project_json_is_noop(tmp_path):
    # 无 project.json 且无 pipeline → produces 为空 → 不阻塞
    result = validate_completion(tmp_path / "nope", "script", None, strict=True)
    assert result["ok"] is True


def test_gate_does_not_touch_checkpoint_store(tmp_path):
    """产物门禁与 checkpoint 状态机互不干扰（不回溯历史）。"""
    from montage.engine.stages import CheckpointStore, StageStatus

    proj = _project(tmp_path)
    store = CheckpointStore(proj)
    # 无产物也能写 in_progress / awaiting_human（校验只拦 completed）
    store.write("script", StageStatus.IN_PROGRESS.value)
    store.write("script", StageStatus.AWAITING_HUMAN.value, human_approved=True)
    assert store.read("script").status == StageStatus.AWAITING_HUMAN.value


# ---------------------------------------------------------------------------
# 工具白名单（按 capability）
# ---------------------------------------------------------------------------


def test_stage_capabilities_resolved():
    caps = stage_capabilities(CINEMATIC, "script")
    assert "analysis" in caps  # prompt_library_retriever / script_validator
    caps_scene = stage_capabilities(CINEMATIC, "scene_plan")
    assert "prompt_engineering" in caps_scene
    caps_assets = stage_capabilities(CINEMATIC, "assets")
    assert "asset_retrieval" in caps_assets  # asset_retriever 已注册


def test_tool_allowlist_enforced():
    warns = check_tool_allowlist("script", ["analysis", "tts"], CINEMATIC)
    assert any("tts" in w for w in warns)
    # 告警只针对越权能力族（analysis 在 script 白名单内，不应出现违规告警）
    assert not any(w.startswith("阶段 script 工具白名单不允许能力族 analysis") for w in warns)


def test_tool_allowlist_all_allowed():
    assert check_tool_allowlist("script", ["analysis"], CINEMATIC) == []


def test_tool_allowlist_unknown_stage_no_warn():
    assert check_tool_allowlist("nope_stage", ["analysis"], CINEMATIC) == []


def test_tool_allowlist_from_project_dir(tmp_path):
    proj = _project(tmp_path)
    warns = check_tool_allowlist("script", ["tts"], project_dir=proj)
    assert warns  # tts 不在 script 白名单
