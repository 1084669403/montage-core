"""enhance — 画质增强工具（100% 自创实现，可选依赖 + 降级模式）。

- ``Upscaler``：超分。优先 realesrgan-ncnn-vulkan（本地可执行，若在 PATH）；
  缺失时降级 ffmpeg lanczos 放大 + 锐化（基础放大，注明质量差异）。
- ``BgRemover``：去背。优先 rembg（pip install rembg，U2Net）；
  缺失且提供绿幕 key_color 时降级 ffmpeg chromakey；否则明确报错。
- ``FaceRestorer``：人脸修复。依赖 gfpgan（pip install gfpgan + torch）；
  缺失时返回 UNAVAILABLE 提示（无内置降级路径，避免假修复）。

可选依赖模式与 edge_tts/piper 一致：核心包零硬依赖，安装后自动可用。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_engine import ComposError, check_ffmpeg
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus


def _run(cmd: list[str], timeout: int = 1800) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
    if proc.returncode != 0:
        tail = proc.stderr[-400:] if proc.stderr else ""
        raise ComposError(f"命令失败(exit {proc.returncode}): {tail}")


# ---------------------------------------------------------------------------
# 可选依赖检测（模块级函数：避免 staticmethod 描述符在测试/替换时被破坏）
# ---------------------------------------------------------------------------


def _realesrgan_binary() -> str | None:
    return shutil.which("realesrgan-ncnn-vulkan")


def _rembg_available() -> bool:
    try:
        import rembg  # noqa: F401
        return True
    except ImportError:
        return False


def _gfpgan_available() -> bool:
    try:
        import gfpgan  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# 超分
# ---------------------------------------------------------------------------


class Upscaler(BaseTool):
    """超分：realesrgan-ncnn-vulkan 优先，ffmpeg lanczos 降级。"""

    name = "upscaler"
    version = "0.1.0"
    capability = "enhancement"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["path", "output_path"],
        "properties": {
            "path": {"type": "string"},
            "output_path": {"type": "string"},
            "scale": {"type": "integer", "default": 2, "enum": [2, 4], "description": "放大倍数"},
            "model": {"type": "string", "default": "realesr-animevideov3", "description": "realesrgan 模型名（降级路径忽略）"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        src = Path(inputs.get("path") or "")
        out = Path(inputs.get("output_path") or "")
        if not src.exists():
            return ToolResult(success=False, error=f"输入不存在: {src}")
        out.parent.mkdir(parents=True, exist_ok=True)
        scale = int(inputs.get("scale", 2))

        binary = _realesrgan_binary()
        if binary:
            try:
                _run([
                    binary, "-i", str(src), "-o", str(out),
                    "-s", str(scale),
                    "-n", inputs.get("model", "realesr-animevideov3"),
                ], timeout=1800)
            except ComposError as exc:
                return ToolResult(success=False, error=str(exc))
            return ToolResult(
                success=True,
                data={"output": str(out), "scale": scale, "engine": "realesrgan-ncnn-vulkan"},
            )

        # 降级：ffmpeg lanczos 放大 + 锐化
        if check_ffmpeg() is None:
            return ToolResult(
                success=False,
                error="缺少 realesrgan-ncnn-vulkan 且无 ffmpeg：安装 realesrgan-ncnn-vulkan 或 ffmpeg",
            )
        vf = f"scale=iw*{scale}:ih*{scale}:flags=lanczos,unsharp=5:5:0.8:5:5:0.0"
        try:
            _run([
                check_ffmpeg(), "-y", "-i", str(src),
                "-vf", vf, "-q:v", "2", str(out),
            ], timeout=600)
        except ComposError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(
            success=True,
            data={
                "output": str(out), "scale": scale, "engine": "ffmpeg-lanczos",
                "note": "基础放大（非 AI 超分）：安装 realesrgan-ncnn-vulkan 可获得更好细节",
            },
        )


# ---------------------------------------------------------------------------
# 去背
# ---------------------------------------------------------------------------


class BgRemover(BaseTool):
    """去背：rembg 优先；绿幕（chromakey）降级；否则明确报错。"""

    name = "bg_remover"
    version = "0.1.0"
    capability = "enhancement"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["path", "output_path"],
        "properties": {
            "path": {"type": "string", "description": "输入图片/视频"},
            "output_path": {"type": "string"},
            "method": {"type": "string", "enum": ["auto", "chromakey"], "default": "auto"},
            "key_color": {"type": "string", "default": "0x00FF00", "description": "chromakey 抠像色（如绿幕 0x00FF00）"},
            "similarity": {"type": "number", "default": 0.12},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        src = Path(inputs.get("path") or "")
        out = Path(inputs.get("output_path") or "")
        if not src.exists():
            return ToolResult(success=False, error=f"输入不存在: {src}")
        out.parent.mkdir(parents=True, exist_ok=True)
        method = inputs.get("method", "auto")

        if method == "auto" and _rembg_available():
            try:
                from rembg import remove  # type: ignore

                data = src.read_bytes()
                result = remove(data)
                out.write_bytes(result)
            except Exception as exc:  # noqa: BLE001
                return ToolResult(success=False, error=f"rembg 去背失败: {exc}")
            return ToolResult(
                success=True,
                data={"output": str(out), "engine": "rembg", "transparent": True},
            )

        if check_ffmpeg() is not None:
            # chromakey 降级：把指定颜色替换为透明（输出 PNG 序列）或纯色背景
            key = str(inputs.get("key_color", "0x00FF00"))
            sim = float(inputs.get("similarity", 0.12))
            vf = f"chromakey={key}:{sim}:0.2"
            try:
                _run([
                    check_ffmpeg(), "-y", "-i", str(src),
                    "-vf", vf, "-c:v", "libvpx-vp9", str(out),
                ], timeout=600)
            except ComposError as exc:
                return ToolResult(success=False, error=str(exc))
            return ToolResult(
                success=True,
                data={
                    "output": str(out), "engine": "chromakey",
                    "note": "绿幕抠像：仅当素材背景为该 key_color 时有效；"
                            "AI 去背请 pip install rembg",
                },
            )

        return ToolResult(
            success=False,
            error="缺少 rembg（pip install rembg）与 ffmpeg：AI 去背需 rembg，绿幕抠像需 ffmpeg",
        )


# ---------------------------------------------------------------------------
# 人脸修复
# ---------------------------------------------------------------------------


class FaceRestorer(BaseTool):
    """人脸修复/增强：依赖 gfpgan（可选）。无内置降级（避免假修复）。"""

    name = "face_restorer"
    version = "0.1.0"
    capability = "enhancement"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["path", "output_path"],
        "properties": {
            "path": {"type": "string", "description": "含人脸的图片"},
            "output_path": {"type": "string"},
            "upscale": {"type": "integer", "default": 1, "description": "修复同时放大的倍数"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if _gfpgan_available() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        src = Path(inputs.get("path") or "")
        out = Path(inputs.get("output_path") or "")
        if not src.exists():
            return ToolResult(success=False, error=f"输入不存在: {src}")
        if not _gfpgan_available():
            return ToolResult(
                success=False,
                error=(
                    "缺少 gfpgan（pip install gfpgan，需 torch）：人脸修复是重依赖功能，"
                    "未安装时不做假修复。安装后本工具自动可用。"
                ),
            )
        try:
            import torch  # noqa: F401
            from gfpgan import GFPGANer  # type: ignore

            restorer = GFPGANer(
                model_path=None, upscale=int(inputs.get("upscale", 1)),
                arch="clean", channel_multiplier=2,
            )
            _, _, output = restorer.enhance(
                str(src), has_aligned=False, only_center_face=False, paste_back=True,
            )
            import cv2  # type: ignore

            cv2.imwrite(str(out), output)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"人脸修复失败: {exc}")
        return ToolResult(
            success=True,
            data={"output": str(out), "engine": "gfpgan", "upscale": int(inputs.get("upscale", 1))},
        )
