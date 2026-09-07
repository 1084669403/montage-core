"""P0 治理：schema / 管线差异 / run_tool 锁定 / 路径推断。"""

import json

import pytest

from montage.engine.artifacts import ArtifactStore
from montage.engine.gates import GateError, validate_completion
from montage.engine.policy import infer_project_dir, load_loop_policy, load_pipeline_settings
from montage.engine.project import init_project
from montage.engine.runtime import run_tool
from montage.pipelines import CINEMATIC, CLIP_FACTORY, DOCUMENTARY, get_pipeline
from montage.registry import ToolRegistry
from montage.schemas import get_schema
from montage.toolbase import BaseTool, ToolResult, ToolRuntime


def test_required_schemas_exist():
    for name in (
        "research_brief", "proposal_packet", "asset_manifest",
        "publish_log", "clip_plan", "edit_decisions",
    ):
        assert get_schema(name) is not None, name
    assert get_schema("proposal_packet")["required"] == ["concept"]
    rt = get_schema("edit_decisions")["properties"]["render_runtime"]
    assert rt.get("enum") == ["ffmpeg"]


def test_pipelines_differ_and_are_consumed():
    assert CINEMATIC["transition_policy"] == "cinematic_xfade"
    assert CINEMATIC["default_profile"] == "youtube_landscape"
    assert DOCUMENTARY["transition_policy"] == "cut_only"
    assert DOCUMENTARY["default_playbook"] == "documentary_restraint"
    assert DOCUMENTARY["default_profile"] == "douyin_vertical"
    assert DOCUMENTARY["edit_style"] == "documentary"
    assert CLIP_FACTORY["transition_policy"] == "cut_only"
    assert CLIP_FACTORY["default_profile"] == "douyin_vertical"
    script = next(s for s in CLIP_FACTORY["stages"] if s["name"] == "script")
    assert "clip_plan" in script["produces"]
    compose = next(s for s in CINEMATIC["stages"] if s["name"] == "compose")
    assert "edit_advisor" in compose["tools"]
    assets = next(s for s in DOCUMENTARY["stages"] if s["name"] == "assets")
    assert "asset_quality_gate" in assets["tools"]


def test_discover_includes_ffmpeg_compose():
    reg = ToolRegistry()
    assert reg.discover() > 0
    assert reg.get("ffmpeg_compose") is not None
    assert isinstance(reg.import_errors, list)


def test_infer_project_dir_only_known_subdirs(tmp_path):
    proj = tmp_path / "p"
    art = proj / "artifacts"
    art.mkdir(parents=True)
    (art / "script.json").write_text("{}", encoding="utf-8")
    assert infer_project_dir(art / "script.json") == proj
    assert infer_project_dir(tmp_path / "random" / "x.mp4") is None


def test_gate_invalid_includes_field_path(tmp_path):
    proj = init_project(tmp_path, "g", "t", "cinematic")
    ArtifactStore(proj).write("script", {"title": "t"})  # 缺 sections
    with pytest.raises(GateError, match="sections"):
        validate_completion(proj, "script", CINEMATIC)


def test_loop_lock_skips_tts(tmp_path):
    class FakeTts(BaseTool):
        name = "fake_tts"
        capability = "tts"
        provider = "doubao"
        runtime = ToolRuntime.LOCAL

        def execute(self, inputs):
            return ToolResult(success=True, data={"ok": True})

    proj = init_project(tmp_path, "lock", "t", "cinematic")
    ArtifactStore(proj).write("proposal_packet", {
        "concept": "雨夜",
        "allowed_providers": ["volcengine"],
        "video_loop": "volcengine",
    })
    policy = load_loop_policy(proj)
    assert policy["allowed_providers"] == ["volcengine"]
    tts = run_tool(FakeTts(), {"project_dir": str(proj), "text": "你好"})
    assert tts.success
    assert "供应商锁定" not in (tts.error or "")


def test_loop_lock_rejects_foreign_image_provider(tmp_path):
    class FakeAgnes(BaseTool):
        name = "fake_agnes_image"
        capability = "image_generation"
        provider = "agnes"
        runtime = ToolRuntime.LOCAL

        def execute(self, inputs):
            return ToolResult(success=True, data={"ok": True})

    proj = init_project(tmp_path, "lock2", "t", "cinematic")
    ArtifactStore(proj).write("proposal_packet", {
        "concept": "x",
        "allowed_providers": ["volcengine"],
    })
    result = run_tool(FakeAgnes(), {"project_dir": str(proj), "prompt": "x"})
    assert not result.success
    assert "供应商锁定" in result.error


def test_budget_hard_stop(tmp_path):
    class Pricey(BaseTool):
        name = "pricey_image"
        capability = "image_generation"
        provider = "volcengine"

        def estimate_cost(self, inputs):
            return 10.0

        def execute(self, inputs):
            return ToolResult(success=True, data={"ran": True})

    proj = init_project(tmp_path, "bud", "t", "cinematic")
    ArtifactStore(proj).write("proposal_packet", {
        "concept": "x",
        "allowed_providers": ["volcengine"],
        "budget_ceiling_usd": 1.0,
    })
    result = run_tool(Pricey(), {"project_dir": str(proj)})
    assert not result.success
    assert "预算硬停" in result.error


def test_validate_inputs_hooked_in_run_tool():
    class NeedText(BaseTool):
        name = "need_text"
        capability = "analysis"
        provider = "openmontage"
        input_schema = {
            "type": "object",
            "required": ["text"],
            "properties": {"text": {"type": "string"}},
        }

        def execute(self, inputs):
            return ToolResult(success=True, data={"text": inputs["text"]})

    result = run_tool(NeedText(), {})
    assert not result.success
    assert "输入校验失败" in result.error


def test_pipeline_settings_from_project(tmp_path):
    proj = init_project(tmp_path, "doc", "t", "documentary")
    settings = load_pipeline_settings(proj)
    assert settings["transition_policy"] == "cut_only"
    assert settings["default_profile"] == "douyin_vertical"


def test_cli_run_unknown_tool(tmp_path):
    from montage.cli import main

    proj = init_project(tmp_path, "run1", "t", "cinematic")
    payload = tmp_path / "in.json"
    payload.write_text("{}", encoding="utf-8")
    code = main(["run", str(proj), "no_such_tool", "--input", str(payload)])
    assert code == 2


def test_cli_run_json_stdout(tmp_path):
    from contextlib import redirect_stdout
    from io import StringIO

    from montage.cli import main

    proj = init_project(tmp_path, "run2", "t", "cinematic")
    payload = tmp_path / "in.json"
    payload.write_text(json.dumps({"query": "雨夜", "top_k": 1}), encoding="utf-8")
    buf = StringIO()
    with redirect_stdout(buf):
        code = main(["run", str(proj), "prompt_library_retriever", "--input", str(payload)])
    assert code == 0
    data = json.loads(buf.getvalue().strip().splitlines()[-1])
    assert data["success"] is True


def test_cli_caps_strict(tmp_path):
    from montage.cli import main

    proj = init_project(tmp_path, "cap", "t", "cinematic")
    code = main(["check", str(proj), "script", "--caps", "tts", "--caps-strict"])
    assert code == 2


def test_clip_factory_gate_requires_clip_plan(tmp_path):
    proj = init_project(tmp_path, "cf", "t", "clip_factory")
    ArtifactStore(proj).write("script", {"title": "t", "sections": [{"id": "s", "narration": "n"}]})
    with pytest.raises(GateError, match="clip_plan"):
        validate_completion(proj, "script", get_pipeline("clip_factory"))
