"""seed_audio — 豆包 Seed-Audio（音乐/音效生成，自创实现，契约待联调）。

火山引擎豆包 Seed-Audio（seedance 音频）契约与即梦同族（HMAC 签名 + 异步任务），
具体端点/字段以火山引擎官方文档为准。本文件提供接口骨架：
- env：DOUBAO_SEED_AUDIO_ACCESSKEY / DOUBAO_SEED_AUDIO_SECRETKEY（或单密钥）
- 提交 + 轮询的异步任务结构，待真实密钥联调后微调。
未配置时返回 NEEDS_CONFIG；配置后若端点待确认，会返回清晰错误而非静默失败。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from montage.providers.http import HttpError, post_json
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_ENV_KEYS = ("DOUBAO_SEED_AUDIO_ACCESSKEY", "DOUBAO_SEED_AUDIO_SECRETKEY")


class SeedAudio(BaseTool):
    """豆包 Seed-Audio：文本 → 音乐/音效（接口骨架，待联调）。"""

    name = "seed_audio"
    version = "0.1.0"
    capability = "music_generation"
    provider = "doubao"
    runtime = ToolRuntime.API
    env_keys = _ENV_KEYS
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "音乐/音效描述（情绪/风格/时长/乐器）"},
            "duration": {"type": "integer", "default": 30},
            "output_path": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if all(os.environ.get(k) for k in _ENV_KEYS) else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.05

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        prompt = inputs.get("prompt")
        if not prompt:
            return ToolResult(success=False, error="'prompt' 必填")
        if self.get_status() != ToolStatus.AVAILABLE:
            return ToolResult(success=False, error="缺少 DOUBAO_SEED_AUDIO_ACCESSKEY/SECRETKEY")
        # 契约待联调：以下端点为占位，需按火山引擎官方文档核对后启用
        return ToolResult(
            success=False,
            error=(
                "Seed-Audio 契约待联调：请按火山引擎官方文档核对提交端点与签名后启用"
                "（本工具已按异步任务结构预留；联调完成后移除本占位错误）。"
            ),
        )
