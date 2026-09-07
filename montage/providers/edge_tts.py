"""edge_tts — Microsoft Edge TTS（免费在线中文配音）适配器。

依赖可选 pip 包 `edge-tts`（未安装时状态为 UNAVAILABLE）：
  pip install edge-tts

中文音色：zh-CN-XiaoxiaoNeural（女声旁白）/ zh-CN-YunxiNeural（男声）
              / zh-CN-YunyangNeural（播音）。

本文件为全新原创代码。
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"


class EdgeTTS(BaseTool):
    name = "edge_tts"
    version = "0.1.0"
    capability = "tts"
    provider = "edge_tts"
    runtime = ToolRuntime.API
    env_keys = ()  # 无需密钥
    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "voice": {"type": "string", "default": _DEFAULT_VOICE},
            "rate": {"type": "string", "default": "+0%", "description": "语速，如 -10% / +20%"},
            "output_path": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        # 依赖可选包 edge-tts；缺失时标记不可用并提供安装提示
        if self._package_missing():
            return ToolStatus.UNAVAILABLE
        return ToolStatus.AVAILABLE

    @staticmethod
    def _package_missing() -> bool:
        try:
            import edge_tts  # noqa: F401
            return False
        except ImportError:
            return True

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0  # 免费

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        if self._package_missing():
            return ToolResult(
                success=False,
                error="缺少可选依赖 edge-tts：pip install edge-tts",
            )
        import edge_tts  # type: ignore

        text = inputs.get("text")
        if not text:
            return ToolResult(success=False, error="'text' 必填")
        output = Path(inputs.get("output_path", "edge_tts.mp3"))
        output.parent.mkdir(parents=True, exist_ok=True)

        async def _run() -> None:
            communicate = edge_tts.Communicate(
                text,
                voice=inputs.get("voice", _DEFAULT_VOICE),
                rate=inputs.get("rate", "+0%"),
            )
            await communicate.save(str(output))

        try:
            asyncio.run(_run())
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"Edge TTS 失败: {exc}")
        return ToolResult(
            success=True,
            data={"provider": "edge_tts", "voice": inputs.get("voice", _DEFAULT_VOICE), "output": str(output)},
            meta={"provider": "edge_tts"},
            cost_usd=0.0,
        )
