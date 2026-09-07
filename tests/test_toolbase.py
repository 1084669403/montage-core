"""toolbase 契约测试。"""

import pytest

from montage.toolbase import (
    BaseTool,
    ToolResult,
    ToolRuntime,
    ToolStatus,
    validate_inputs,
)


class SampleLocalTool(BaseTool):
    name = "sample_local"
    version = "1.0.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {"text": {"type": "string"}},
    }

    def execute(self, inputs):
        return ToolResult(success=True, data={"length": len(inputs["text"])})


class SampleApiTool(BaseTool):
    name = "sample_api"
    capability = "tts"
    provider = "demo"
    runtime = ToolRuntime.API
    env_keys = ("DEMO_KEY",)

    def execute(self, inputs):
        return ToolResult(success=False, error="not called in tests")


def test_contract_fields():
    tool = SampleLocalTool()
    assert tool.name == "sample_local"
    assert tool.capability == "analysis"
    assert tool.runtime == ToolRuntime.LOCAL


def test_status_depends_on_env(monkeypatch):
    monkeypatch.delenv("DEMO_KEY", raising=False)
    assert SampleApiTool().get_status() == ToolStatus.NEEDS_CONFIG
    monkeypatch.setenv("DEMO_KEY", "x")
    assert SampleApiTool().get_status() == ToolStatus.AVAILABLE


def test_local_tool_always_available():
    assert SampleLocalTool().get_status() == ToolStatus.AVAILABLE


def test_execute_returns_toolresult():
    result = SampleLocalTool().execute({"text": "你好"})
    assert result.success
    assert result.data["length"] == 2


def test_input_validation():
    assert validate_inputs(SampleLocalTool(), {"text": "ok"}) == []
    assert validate_inputs(SampleLocalTool(), {}) != []


def test_unimplemented_execute_raises():
    class Noop(BaseTool):
        name = "noop"
        capability = "analysis"

    with pytest.raises(NotImplementedError):
        Noop().execute({})
