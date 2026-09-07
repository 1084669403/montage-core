"""talking_head — 数字人口播 / 口型同步接口（自创实现，契约待联调）。

能力（对应 OpenMontage avatar/ 系）：
- ``TalkingHead``：形象图 + 台词 → 数字人口播视频。国内候选供应商：可灵（Kling
  Digital Human）、即梦数字人等。契约按"形象图 URL + 台词文本 + 音色"的通用
  形态预留，待具体供应商密钥联调后启用（未配置/待联调时明确报错，不静默失败）。
- ``LipSync``：视频 + 音频 → 口型对齐。国内候选：可灵口型驱动等。同样待联调。

设计约束：与 music_gen/seed_audio 相同——接口骨架 + 清晰报错 + 替代路径，
不假装可用。
"""

from __future__ import annotations

import os
from typing import Any

from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus


class TalkingHead(BaseTool):
    """数字人口播（形象图 + 台词 → 视频）。契约待联调。"""

    name = "talking_head"
    version = "0.1.0"
    capability = "avatar"
    provider = "openmontage"
    runtime = ToolRuntime.API
    env_keys = ()
    input_schema = {
        "type": "object",
        "required": ["image_url", "text"],
        "properties": {
            "image_url": {"type": "string", "description": "人物形象图 URL"},
            "text": {"type": "string", "description": "口播台词"},
            "voice": {"type": "string", "description": "音色（供应商相关）"},
            "output_path": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.1

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        return ToolResult(
            success=False,
            error=(
                "数字人供应商待联调：可接入可灵数字人/即梦数字人后启用"
                "（接口已按形象图+台词+音色的通用形态预留）。替代路径："
                "口播类内容先用 TTS（edge_tts/doubao_tts）+ 静态图 + ken_burns 制作。"
            ),
        )


class LipSync(BaseTool):
    """口型同步（视频 + 音频 → 口型对齐）。契约待联调。"""

    name = "lip_sync"
    version = "0.1.0"
    capability = "avatar"
    provider = "openmontage"
    runtime = ToolRuntime.API
    env_keys = ()
    input_schema = {
        "type": "object",
        "required": ["video_path", "audio_path"],
        "properties": {
            "video_path": {"type": "string"},
            "audio_path": {"type": "string"},
            "output_path": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.1

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        return ToolResult(
            success=False,
            error=(
                "口型同步供应商待联调：可接入可灵口型驱动等后启用。"
                "替代路径：先用 doubao_tts 生成台词音频，再合成时以旁白轨覆盖。"
            ),
        )
