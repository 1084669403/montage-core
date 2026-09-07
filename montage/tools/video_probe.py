"""video_probe — 视频分析工具（抽帧 / 场景检测 / 综合探测，100% 自创实现）。

基于 ffmpeg 标准滤镜与 ffprobe，不参照任何第三方源码：
- ``FrameSampler``：按 fps 抽帧到目录（素材审阅/预览用）。
- ``SceneDetect``：scene 滤镜检测镜头切换时间点（剪辑切点参考）。
- ``VideoAnalyzer``：ffprobe 综合信息（时长/分辨率/fps/编码/码率/音轨）。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_engine import check_ffmpeg, check_ffprobe, probe
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_PTS_RE = re.compile(r"pts_time:([0-9.]+)")


def _run_capture(cmd: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603


class FrameSampler(BaseTool):
    """抽帧：按 fps 从视频抽取帧到输出目录。"""

    name = "frame_sampler"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["path", "output_dir"],
        "properties": {
            "path": {"type": "string"},
            "output_dir": {"type": "string"},
            "fps": {"type": "number", "default": 1.0, "description": "每秒抽帧数"},
            "format": {"type": "string", "enum": ["png", "jpg"], "default": "png"},
            "max_frames": {"type": "integer", "default": 0, "description": "帧数上限（0=不限）"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if check_ffmpeg() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        path = Path(inputs.get("path") or "")
        out_dir = Path(inputs.get("output_dir") or "")
        if not path.exists():
            return ToolResult(success=False, error=f"输入不存在: {path}")
        if check_ffmpeg() is None:
            return ToolResult(success=False, error="缺少 ffmpeg")
        out_dir.mkdir(parents=True, exist_ok=True)
        fps = float(inputs.get("fps", 1.0))
        fmt = inputs.get("format", "png")
        ext = "png" if fmt == "png" else "jpg"
        pattern = out_dir / f"frame_%04d.{ext}"
        vf = f"fps={fps}"
        if inputs.get("max_frames"):
            vf += f",select='lt(n,{int(inputs['max_frames'])})'"
        cmd = [
            check_ffmpeg(), "-y", "-i", str(path),
            "-vf", vf, "-q:v", "2", "-fps_mode", "vfr", str(pattern),
        ]
        try:
            proc = _run_capture(cmd)
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error="抽帧超时")
        if proc.returncode != 0:
            return ToolResult(success=False, error=f"抽帧失败: {proc.stderr[-300:]}")
        frames = sorted(out_dir.glob(f"frame_*.{ext}"))
        return ToolResult(success=True, data={"count": len(frames), "dir": str(out_dir), "frames": [str(f) for f in frames[:20]]})


class SceneDetect(BaseTool):
    """场景检测：scene 滤镜找镜头切换时间点（剪辑切点参考）。"""

    name = "scene_detect"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string"},
            "threshold": {"type": "number", "default": 0.3, "description": "变化敏感度 0-1（越小越敏感）"},
            "top_k": {"type": "integer", "default": 0, "description": "返回前 N 个切点（0=全部）"},
        },
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
        thr = float(inputs.get("threshold", 0.3))
        cmd = [
            check_ffmpeg(), "-hide_banner", "-i", str(path),
            "-vf", f"select='gt(scene,{thr})',showinfo",
            "-f", "null", "-",
        ]
        try:
            proc = _run_capture(cmd, timeout=900)
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, error="场景检测超时")
        if proc.returncode != 0 and "select" in proc.stderr[-200:]:
            return ToolResult(success=False, error=f"场景检测失败: {proc.stderr[-300:]}")
        times = [float(p) for p in _PTS_RE.findall(proc.stderr)]
        top_k = int(inputs.get("top_k", 0) or 0)
        if top_k > 0:
            times = times[:top_k]
        return ToolResult(
            success=True,
            data={
                "threshold": thr,
                "scene_changes": times,
                "count": len(times),
                "usage": "切点可作为 edit_decisions 的候选 cut 位置（与 edit_advisor 建议结合）",
            },
        )


class VideoAnalyzer(BaseTool):
    """视频综合分析：ffprobe 汇总时长/分辨率/fps/编码/码率/音轨。"""

    name = "video_analyzer"
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

        streams = info.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
        fmt = info.get("format") or {}
        fps = _r_frame_rate(video.get("r_frame_rate"))

        return ToolResult(
            success=True,
            data={
                "duration_seconds": float(fmt.get("duration", 0) or 0),
                "size_bytes": int(fmt.get("size", 0) or 0),
                "video": {
                    "codec": video.get("codec_name"),
                    "width": video.get("width"),
                    "height": video.get("height"),
                    "fps": fps,
                },
                "audio": {
                    "codec": audio.get("codec_name"),
                    "sample_rate": audio.get("sample_rate"),
                    "channels": audio.get("channels"),
                },
                "has_audio": bool(audio),
            },
        )


def _r_frame_rate(value: Any) -> float | None:
    """'30000/1001' → 29.97。"""
    if not value:
        return None
    try:
        num, _, den = str(value).partition("/")
        if den:
            return round(int(num) / int(den), 3)
        return float(num)
    except (ValueError, ZeroDivisionError):
        return None
