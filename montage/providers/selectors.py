"""selectors — 能力选型器。

按 capability 从注册表路由到具体供应商工具：
路由优先级：用户显式指定 provider > 图片默认 kling > 首个 AVAILABLE > 报错。

图/视频选型器在 inputs 含 project_dir 时读取 proposal_packet 的闭环锁定
（allowed_providers / video_loop）。TTS 选型器永不套用该锁定。
无锁时图片优先 kling（Kling Image 3.0 Omni）。

选型器本身 capability="selector"（避免被自己路由）。
使用模块级 registry 单例（与 cli/webui 共享一次发现），
并以 (capability, preferred, allowed, lock) 缓存路由结果（仅缓存成功命中）。
"""

from __future__ import annotations

from typing import Any

from montage.engine.policy import (
    LOCK_CAPABILITIES,
    load_loop_policy,
    resolve_allowed_providers,
)
from montage.registry import registry as _module_registry
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus


class _Selector(BaseTool):
    target_capability: str = ""
    runtime = ToolRuntime.LOCAL
    provider = "openmontage"

    def __init__(self) -> None:
        self._registry = _module_registry  # 模块级单例
        self._route_cache: dict[tuple[Any, ...], BaseTool | None] = {}

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        tool = self._pick(inputs)
        return tool.estimate_cost(inputs) if tool else 0.0

    def _policy_for(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if self.target_capability not in LOCK_CAPABILITIES:
            return {}
        proj = inputs.get("project_dir")
        return load_loop_policy(proj) if proj else {}

    def _pick(self, inputs: dict[str, Any]) -> BaseTool | None:
        preferred = inputs.get("provider")
        policy = self._policy_for(inputs)
        allowed = resolve_allowed_providers(
            policy, inputs, capability=self.target_capability,
        )
        lock_pref = bool(
            inputs.get("lock_preferred_provider")
            or policy.get("lock_preferred_provider")
        )
        key = (
            self.target_capability,
            preferred,
            tuple(allowed or ()),
            lock_pref,
        )
        if key in self._route_cache:
            return self._route_cache[key]
        self._registry.discover()
        candidates = list(self._registry.by_capability(self.target_capability))
        if allowed:
            candidates = [t for t in candidates if t.provider in allowed]
        if lock_pref and preferred:
            candidates = [t for t in candidates if t.provider == preferred]
        tool: BaseTool | None = None
        for t in candidates:
            if preferred and t.provider == preferred and t.get_status() == ToolStatus.AVAILABLE:
                tool = t
                break
        if tool is None:
            for name in getattr(self, "preferred_providers", ()) or ():
                for t in candidates:
                    if t.provider == name and t.get_status() == ToolStatus.AVAILABLE:
                        tool = t
                        break
                if tool is not None:
                    break
        if tool is None:
            for t in candidates:
                if t.get_status() == ToolStatus.AVAILABLE:
                    tool = t
                    break
        if tool is not None:
            self._route_cache[key] = tool
        return tool

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        policy = self._policy_for(inputs)
        allowed = resolve_allowed_providers(
            policy, inputs, capability=self.target_capability,
        )
        preferred = inputs.get("provider")
        if allowed and preferred and preferred not in allowed:
            return ToolResult(
                success=False,
                error=f"供应商锁定：指定 provider={preferred} 不在允许列表 {allowed}",
            )
        tool = self._pick(inputs)
        if tool is None:
            extra = f"，允许列表 {allowed}" if allowed else ""
            return ToolResult(
                success=False,
                error=(
                    f"没有可用的 {self.target_capability} 工具"
                    f"（指定 provider={preferred}{extra} 时请检查密钥）"
                ),
            )
        result = tool.execute(inputs)
        result.meta["routed_to"] = f"{tool.name} ({tool.provider})"
        return result


class ImageSelector(_Selector):
    name = "image_selector"
    version = "0.1.0"
    capability = "selector"
    target_capability = "image_generation"
    preferred_providers = ("kling",)
    input_schema = {
        "type": "object",
        "properties": {
            "provider": {"type": "string", "description": "显式指定供应商"},
            "prompt": {"type": "string"},
            "output_path": {"type": "string"},
            "project_dir": {"type": "string"},
            "allowed_providers": {"type": "array", "items": {"type": "string"}},
            "lock_preferred_provider": {"type": "boolean"},
            "video_loop": {"type": "string"},
        },
    }


class VideoSelector(_Selector):
    name = "video_selector"
    version = "0.1.0"
    capability = "selector"
    target_capability = "video_generation"
    input_schema = {
        "type": "object",
        "properties": {
            "provider": {"type": "string"},
            "prompt": {"type": "string"},
            "image_url": {"type": "string", "description": "图生视频参考图"},
            "output_path": {"type": "string"},
            "project_dir": {"type": "string"},
            "allowed_providers": {"type": "array", "items": {"type": "string"}},
            "lock_preferred_provider": {"type": "boolean"},
            "video_loop": {"type": "string"},
        },
    }


class TtsSelector(_Selector):
    name = "tts_selector"
    version = "0.1.0"
    capability = "selector"
    target_capability = "tts"
    input_schema = {
        "type": "object",
        "properties": {
            "provider": {"type": "string"},
            "text": {"type": "string"},
            "voice": {"type": "string"},
            "output_path": {"type": "string"},
            "project_dir": {"type": "string"},
        },
    }
