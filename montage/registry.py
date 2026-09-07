"""registry — 工具注册表与选型路由。

- discover()：扫描 montage.tools / montage.providers / montage.compose，
  自动收集 BaseTool 子类；模块导入失败记入 import_errors，不改返回值。
- 选型器（selector）模式：按 capability 查询全部可用工具，由调用方/LLM 决定路由。
- provider_menu_summary()：给人类看的"能力菜单"（x/y 已配置 + 缺失项一键修复提示）。

本文件为全新原创代码。
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Iterable

from montage.toolbase import BaseTool, ToolStatus


class ToolRegistry:
    """进程内工具注册表。按 name 注册，按 capability/provider 查询。"""

    def __init__(self) -> None:
        self._tools: dict[str, type[BaseTool]] = {}
        self._discovered = False
        self.import_errors: list[str] = []

    # -- 注册与发现 ---------------------------------------------------------

    def register(self, cls: type[BaseTool]) -> type[BaseTool]:
        if not (isinstance(cls, type) and issubclass(cls, BaseTool)):
            raise TypeError(f"{cls} 不是 BaseTool 子类")
        if cls.name == "base" or not cls.name:
            raise ValueError(f"{cls} 未设置 name")
        if cls.name in self._tools:
            raise ValueError(f"工具名重复: {cls.name}")
        self._tools[cls.name] = cls
        return cls

    def discover(
        self,
        packages: Iterable[str] = (
            "montage.tools",
            "montage.providers",
            "montage.compose",
        ),
    ) -> int:
        """扫描指定包下的所有模块，收集 BaseTool 子类。幂等。"""
        if self._discovered:
            return len(self._tools)
        self.import_errors = []
        for pkg_name in packages:
            try:
                pkg = importlib.import_module(pkg_name)
            except ImportError as exc:
                self.import_errors.append(f"{pkg_name}: {exc}")
                continue
            if not hasattr(pkg, "__path__"):
                continue
            for info in pkgutil.iter_modules(pkg.__path__):
                if info.name.startswith("_"):
                    continue
                mod_name = f"{pkg_name}.{info.name}"
                try:
                    module = importlib.import_module(mod_name)
                except Exception as exc:  # noqa: BLE001
                    self.import_errors.append(f"{mod_name}: {exc}")
                    continue
                for _, obj in vars(module).items():
                    if (
                        isinstance(obj, type)
                        and issubclass(obj, BaseTool)
                        and obj is not BaseTool
                    ):
                        try:
                            self.register(obj)
                        except ValueError:
                            pass
        self._discovered = True
        return len(self._tools)

    # -- 查询 ---------------------------------------------------------------

    def get(self, name: str) -> type[BaseTool] | None:
        return self._tools.get(name)

    def instances(self) -> list[BaseTool]:
        return [cls() for cls in self._tools.values()]

    def by_capability(self, capability: str) -> list[BaseTool]:
        return [
            t
            for t in self.instances()
            if t.capability == capability
        ]

    def by_provider(self, provider: str) -> list[BaseTool]:
        return [t for t in self.instances() if t.provider == provider]

    def capability_catalog(self) -> dict[str, list[str]]:
        """能力族 -> 工具名列表（含 provider 标注）。"""
        catalog: dict[str, list[str]] = {}
        for tool in self.instances():
            catalog.setdefault(tool.capability, []).append(
                f"{tool.name} ({tool.provider})"
            )
        return dict(sorted(catalog.items()))

    # -- 人类可读菜单 -------------------------------------------------------

    def provider_menu_summary(self) -> dict[str, Any]:
        """给人类看的能力菜单汇总：每能力族 已配置/总数 + 缺失项修复提示。"""
        by_cap: dict[str, list[BaseTool]] = {}
        for tool in self.instances():
            by_cap.setdefault(tool.capability, []).append(tool)

        capabilities: list[dict[str, Any]] = []
        setup_offers: list[dict[str, Any]] = []
        for cap in sorted(by_cap):
            tools = by_cap[cap]
            configured = [t for t in tools if t.get_status() == ToolStatus.AVAILABLE]
            capabilities.append(
                {
                    "capability": cap,
                    "configured": len(configured),
                    "total": len(tools),
                    "tools": [
                        {
                            "name": t.name,
                            "provider": t.provider,
                            "status": t.get_status().value,
                            "env_keys": list(t.env_keys),
                        }
                        for t in tools
                    ],
                }
            )
            for tool in tools:
                if tool.get_status() == ToolStatus.NEEDS_CONFIG:
                    setup_offers.append(
                        {
                            "tool": tool.name,
                            "provider": tool.provider,
                            "capability": cap,
                            "env_keys": list(tool.env_keys),
                            "hint": f"设置 {' 和 '.join(tool.env_keys)} 后即可使用",
                        }
                    )
        return {
            "capabilities": capabilities,
            "setup_offers": setup_offers,
            "runtime_warnings": list(self.import_errors),
            "import_errors": list(self.import_errors),
        }


# 进程级默认注册表（简单起见，多例场景自行 new）
registry = ToolRegistry()
