"""audio_probe — 音频探测与能量分析（100% 自创实现）。

- ``AudioProbe``：ffprobe 音频流信息（编码/采样率/声道/时长）。
- ``AudioEnergy``：volumedetect 分析响度（mean/max dB），供混音电平决策
  （旁白过小要提、过大会削顶，见 audio_mixer 的 ducking/loudnorm 前置检查）。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_engine import check_ffmpeg, check_ffprobe, probe
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_VOLUME_RE = re.compile(r"(mean_volume|max_volume):\s*(-?[0-9.]+) dB")


class AudioProbe(BaseTool):
    """音频流探测：编码/采样率/声道/时长。"""

    name = "audio_probe"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}},
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if check_ffprobe() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        path = Path(inputs.get("path") or "")
        if not path.exists():
            return ToolResult(success=False, error=f"输入不存在: {path}")
        try:
            info = probe(path)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"无法解析: {exc}")
        audio = next(
            (s for s in (info.get("streams") or []) if s.get("codec_type") == "audio"),
            {},
        )
        if not audio:
            return ToolResult(success=True, data={"has_audio": False})
        return ToolResult(
            success=True,
            data={
                "has_audio": True,
                "codec": audio.get("codec_name"),
                "sample_rate": audio.get("sample_rate"),
                "channels": audio.get("channels"),
                "duration_seconds": float(audio.get("duration") or 0),
                "bit_rate": audio.get("bit_rate"),
            },
        )


class AudioEnergy(BaseTool):
    """音频响度分析：volumedetect 的 mean/max dB，供混音电平决策。"""

    name = "audio_energy"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["path"],
        "properties": {"path": {"type": "string"}},
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if check_ffmpeg() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        path = Path(inputs.get("path") or "")
        if not path.exists():
            return ToolResult(success=False, error=f"输入不存在: {path}")
        if check_ffmpeg() is None:
            return ToolResult(success=False, error="缺少 ffmpeg")
        try:
            proc = subprocess.run(  # noqa: S603
                [check_ffmpeg(), "-hide_banner", "-i", str(path),
                 "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True, text=True, timeout=600,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error="响度分析超时")
        if proc.returncode != 0 and "volumedetect" not in proc.stderr:
            return ToolResult(success=False, error=f"响度分析失败: {proc.stderr[-300:]}")
        values = dict(_VOLUME_RE.findall(proc.stderr))
        mean = float(values.get("mean_volume", 0) or 0)
        maxv = float(values.get("max_volume", 0) or 0)
        # 电平建议：旁白 mean 低于 -30dB 偏小、高于 -10dB 接近削顶
        if mean < -30:
            note = "电平偏低，建议提升音量或检查录制"
        elif mean > -10:
            note = "电平偏高，有削顶风险，建议压低"
        else:
            note = "电平正常"
        return ToolResult(
            success=True,
            data={
                "mean_volume_db": round(mean, 2),
                "max_volume_db": round(maxv, 2),
                "note": note,
                "usage": "混音前先跑 audio_energy，再决定 music_volume / ducking 参数",
            },
        )
