"""music_gen — 音乐生成接口（自创实现，供应商未配置时明确提示）。

接口抽象（对齐 bgm 检索后的"无现成曲目"场景）：
- 输入：prompt（情绪/风格/乐器描述）、duration、style；
- 当前无内置音乐生成供应商 → 返回 NEEDS_CONFIG 提示 + 可用替代路径
  （FreePD/incompetech 下载、Suno 等外部服务）。
后续接入具体供应商（Suno / 国内音乐生成）时，把本工具改成按 provider
路由（与 image_selector/video_selector 相同的 selector 模式）。
"""

from __future__ import annotations

from typing import Any

from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus


class MusicGen(BaseTool):
    """音乐生成接口（占位：未配置供应商）。"""

    name = "music_gen"
    version = "0.1.0"
    capability = "music_generation"
    provider = "openmontage"
    runtime = ToolRuntime.API
    env_keys = ()
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "音乐描述（情绪/风格/乐器/节奏）"},
            "duration": {"type": "integer", "default": 30},
            "style": {"type": "string", "description": "风格，如 cinematic / ambient / chinese"},
            "output_path": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        return ToolResult(
            success=False,
            error=(
                "未配置音乐生成供应商。替代路径："
                "① asset_retriever（category=bgm）从本地配方选曲，operation=resolve 下载；"
                "② 接入 Suno/国内音乐生成服务后本工具自动可用（selector 模式）。"
            ),
        )
