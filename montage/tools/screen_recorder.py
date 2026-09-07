"""screen_recorder — 屏幕录制（100% 自创实现，ffmpeg 桌面采集）。

- ``screen``：全屏录制（Windows gdigrab / Linux x11grab / macOS avfoundation）。
- ``region``：指定区域录制（offset + size）。
- ``window``：按窗口标题录制。
- 可指定时长（不指定则手动 Ctrl+C 停止）、帧率、输出编码。

用于 screen-demo / 教程录制类管线（OpenMontage capture 系能力）。
"""

from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_engine import ComposError, check_ffmpeg
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus


def _desktop_input() -> list[str]:
    """按平台返回桌面采集输入参数。"""
    system = platform.system()
    if system == "Windows":
        return ["-f", "gdigrab", "-i", "desktop"]
    if system == "Linux":
        return ["-f", "x11grab", "-i", os.environ.get("DISPLAY", ":0")]
    if system == "Darwin":
        return ["-f", "avfoundation", "-i", "1:none"]
    raise ComposError(f"不支持的录制平台: {system}")


def record_screen(
    output: str | Path,
    *,
    duration: float = 0.0,
    fps: int = 30,
    region: str = "",
    window_title: str = "",
    timeout: int = 600,
) -> Path:
    """屏幕录制；region 形如 1280x720+100+50；window_title 按窗口录制。"""
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [ffmpeg, "-y"]
    if window_title:
        cmd += ["-f", "gdigrab", "-i", f"title={window_title}"]
    elif region:
        # 区域：WxH+X+Y
        size, _, offset = region.partition("+")
        cmd += ["-f", "gdigrab", "-offset_x", offset.split("+")[0],
                "-offset_y", offset.split("+")[1], "-video_size", size, "-i", "desktop"]
    else:
        cmd += _desktop_input()
    cmd += ["-framerate", str(fps)]
    if duration > 0:
        cmd += ["-t", f"{duration:.2f}"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-pix_fmt", "yuv420p", str(output)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
    except subprocess.TimeoutExpired:
        # 手动停止录制：超时不视为失败（用户 Ctrl+C 结束）
        return output
    if proc.returncode != 0:
        raise ComposError(f"录制失败(exit {proc.returncode}): {proc.stderr[-300:]}")
    return output


class ScreenRecorder(BaseTool):
    """屏幕录制工具（全屏/区域/窗口）。"""

    name = "screen_recorder"
    version = "0.1.0"
    capability = "capture"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["output_path"],
        "properties": {
            "output_path": {"type": "string"},
            "mode": {"type": "string", "enum": ["screen", "region", "window"], "default": "screen"},
            "duration": {"type": "number", "default": 0, "description": "时长秒（0=手动停止）"},
            "fps": {"type": "integer", "default": 30},
            "region": {"type": "string", "description": "区域（mode=region）：WxH+X+Y，如 1280x720+100+50"},
            "window_title": {"type": "string", "description": "窗口标题（mode=window）"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if check_ffmpeg() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        mode = inputs.get("mode", "screen")
        out = Path(inputs.get("output_path") or "")
        if not out.name:
            return ToolResult(success=False, error="'output_path' 必填")
        try:
            path = record_screen(
                out,
                duration=float(inputs.get("duration", 0)),
                fps=int(inputs.get("fps", 30)),
                region=inputs.get("region", "") if mode == "region" else "",
                window_title=inputs.get("window_title", "") if mode == "window" else "",
            )
        except ComposError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(
            success=True,
            data={"output": str(path), "mode": mode,
                  "note": "录制完成（手动停止时返回已写文件）"},
        )
