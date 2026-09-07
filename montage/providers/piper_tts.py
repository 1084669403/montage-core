"""piper_tts — Piper 离线本地 TTS（自创实现，可选依赖）。

依赖可选 pip 包 piper-tts（未安装或模型缺失时状态为 UNAVAILABLE）：
  pip install piper-tts
  下载中文模型：zh_CN-haru-medium（见 https://github.com/rhasspy/piper）

调用方式：命令行 ``piper --model <model> --output_file out.wav``（stdin 文本）。
完全离线、免费、可商用（MIT 模型），适合无网络/隐私场景的配音兜底。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_DEFAULT_MODEL = "zh_CN-haru-medium"


class PiperTTS(BaseTool):
    """Piper 离线中文 TTS（本地命令行）。"""

    name = "piper_tts"
    version = "0.1.0"
    capability = "tts"
    provider = "piper"
    runtime = ToolRuntime.LOCAL
    env_keys = ()
    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "model": {"type": "string", "default": _DEFAULT_MODEL},
            "output_path": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self._binary() else ToolStatus.UNAVAILABLE

    @staticmethod
    def _binary() -> str | None:
        return shutil.which("piper")

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        binary = self._binary()
        if not binary:
            return ToolResult(
                success=False,
                error="缺少 piper 可执行文件：pip install piper-tts 并下载中文模型"
                      "（zh_CN-haru-medium），或改用 edge_tts/doubao_tts",
            )
        text = inputs.get("text")
        if not text:
            return ToolResult(success=False, error="'text' 必填")
        output = Path(inputs.get("output_path", "renders/piper.wav"))
        output.parent.mkdir(parents=True, exist_ok=True)
        model = inputs.get("model", _DEFAULT_MODEL)
        try:
            proc = subprocess.run(  # noqa: S603
                [binary, "--model", model, "--output_file", str(output)],
                input=text.encode("utf-8"),
                capture_output=True, timeout=180,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error="Piper TTS 超时")
        if proc.returncode != 0:
            return ToolResult(
                success=False,
                error=f"Piper 失败: {proc.stderr[-300:]}（模型不存在时先下载 zh_CN-haru-medium）",
            )
        return ToolResult(
            success=True,
            data={"provider": "piper", "model": model, "output": str(output), "format": "wav"},
            meta={"provider": "piper", "model": model},
            cost_usd=0.0,
        )
