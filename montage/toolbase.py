"""toolbase — 极简 BaseTool 契约。

所有能力（生成、分析、合成、上传）都通过实现 BaseTool 的子类接入系统。
契约刻意保持小：一个名字、一份输入 schema、一个 execute()、一个成本估算。

确定性说明：
- LOCAL 工具：纯本地、无网络副作用，execute 可重放。
- API 工具：依赖外部供应商，通过 estimate_cost() 参与预算治理。

本文件为全新原创代码，不包含任何第三方许可代码。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class ToolRuntime(str, enum.Enum):
    """工具运行位置。"""

    LOCAL = "local"  # 纯本地计算，无网络
    API = "api"  # 远程供应商 API
    HYBRID = "hybrid"  # 本地 + 远程混合


class ToolStatus(str, enum.Enum):
    """工具可用状态（由 get_status() 计算，不硬编码）。"""

    AVAILABLE = "available"
    NEEDS_CONFIG = "needs_config"  # 缺少环境变量/密钥
    UNAVAILABLE = "unavailable"  # 运行时依赖缺失


@dataclass
class ToolResult:
    """统一执行结果。"""

    success: bool
    data: Any = None
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0


class BaseTool:
    """所有工具必须继承的基类。子类只需覆盖类属性与 execute()。"""

    name: str = "base"
    version: str = "0.0.0"
    capability: str = ""  # 能力族：image_generation / video_generation / tts / analysis / compose ...
    provider: str = ""  # 供应商：agnes / jimeng / dashscope / doubao / edge_tts / ffmpeg / openmontage ...
    runtime: ToolRuntime = ToolRuntime.LOCAL
    input_schema: dict[str, Any] = {}  # JSON Schema（可选）
    env_keys: tuple[str, ...] = ()  # 需要的环境变量名，用于 AVAILABLE 判断与 doctor

    # -- 状态 -------------------------------------------------------------

    def get_status(self) -> ToolStatus:
        """默认：配置了所需 env 即 AVAILABLE；否则 NEEDS_CONFIG。"""
        if self.env_keys and not all(
            _get_env(key) for key in self.env_keys
        ):
            return ToolStatus.NEEDS_CONFIG
        return ToolStatus.AVAILABLE

    def get_info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "capability": self.capability,
            "provider": self.provider,
            "runtime": self.runtime.value,
            "status": self.get_status().value,
            "env_keys": list(self.env_keys),
        }

    # -- 成本 -------------------------------------------------------------

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        """估算一次调用的美元成本；本地工具返回 0。"""
        return 0.0

    # -- 执行 -------------------------------------------------------------

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        raise NotImplementedError(f"{self.name} 未实现 execute()")


def _get_env(key: str) -> str | None:
    import os

    return os.environ.get(key) or None


def validate_inputs(tool: BaseTool, inputs: dict[str, Any]) -> list[str]:
    """按 input_schema 校验输入；返回错误列表，空列表=通过。

    jsonschema 为核心依赖；缺失视为环境损坏。
    """
    if not tool.input_schema:
        return []
    try:
        import jsonschema  # type: ignore
    except ImportError as exc:
        raise RuntimeError("缺少 jsonschema：pip install -e .") from exc
    errors: list[str] = []
    for err in jsonschema.Draft7Validator(tool.input_schema).iter_errors(inputs):
        errors.append(f"{'.'.join(str(p) for p in err.path)}: {err.message}")
    return errors
