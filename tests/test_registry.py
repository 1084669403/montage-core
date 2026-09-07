"""registry 测试：注册、去重、能力菜单。"""

import pytest

from montage.registry import ToolRegistry
from montage.toolbase import BaseTool, ToolRuntime


class ToolA(BaseTool):
    name = "test_a"
    capability = "image_generation"
    provider = "demo"


class ToolB(BaseTool):
    name = "test_b"
    capability = "tts"
    provider = "demo"
    env_keys = ("MISSING_KEY_XYZ",)


def test_register_and_get():
    reg = ToolRegistry()
    reg.register(ToolA)
    assert reg.get("test_a") is ToolA
    assert reg.get("nope") is None


def test_duplicate_name_rejected():
    reg = ToolRegistry()

    class Dupe(BaseTool):
        name = "test_a"
        capability = "x"

    reg.register(ToolA)
    with pytest.raises(ValueError):
        reg.register(Dupe)


def test_by_capability_and_provider():
    reg = ToolRegistry()
    reg.register(ToolA)
    reg.register(ToolB)
    assert [t.name for t in reg.by_capability("tts")] == ["test_b"]
    assert len(reg.by_provider("demo")) == 2


def test_menu_summary_counts_configured():
    reg = ToolRegistry()
    reg.register(ToolA)
    reg.register(ToolB)
    summary = reg.provider_menu_summary()
    caps = {c["capability"]: c for c in summary["capabilities"]}
    assert caps["image_generation"]["configured"] == 1
    assert caps["tts"]["configured"] == 0  # 缺 env
    assert len(summary["setup_offers"]) == 1


def test_discover_missing_package_is_safe():
    reg = ToolRegistry()
    assert reg.discover(("no.such.package",)) == 0


def test_selector_uses_singleton_registry():
    """选型器使用模块级 registry 单例（与 cli/webui 共享一次发现）。"""
    from montage.providers import selectors

    sel = selectors.ImageSelector()
    assert sel._registry is selectors._module_registry
