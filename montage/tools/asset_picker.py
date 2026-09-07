"""asset_picker — 素材 A/B 选优（确定性评分，零新依赖）。

生成多张候选后选最优：锐度（blurdetect → 1-blur；版本不支持时用 sobel 边缘
能量兜底）+ 分辨率加权。避免"坏素材进成片"并把选择权交给确定性规则而非碰运气。

评分：score = 0.8*sharpness + 0.2*resolution_norm。
- sharpness ∈ [0,1]：1 = 最清晰（blur=0），0 = 极糊（blur=1）。
- resolution_norm ∈ [0,1]：像素数相对基准 4K 归一。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_engine import check_ffmpeg, check_ffprobe
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_LAVFI_RE = re.compile(r"lavfi\.([a-z_]+)\.([A-Za-z_]+)=([0-9.]+)")
_REF_PIXELS = 3840 * 2160  # 4K 基准


def _probe_size(path: Path) -> tuple[int, int]:
    """ffprobe 返回 (width, height)；失败返回 (0, 0)。"""
    ffprobe = check_ffprobe()
    if not ffprobe:
        return 0, 0
    proc = subprocess.run(  # noqa: S603
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=30,
    )
    if proc.returncode != 0:
        return 0, 0
    parts = proc.stdout.strip().split(",")
    if len(parts) != 2:
        return 0, 0
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0


def _lavfi_values(stderr: str, key: str) -> list[float]:
    return [
        float(v) for fname, k, v in _LAVFI_RE.findall(stderr)
        if k == key and fname in ("blurdetect", "signalstats")
    ]


def sharpness_score(path: Path) -> float | None:
    """锐度评分 ∈ [0,1]（越高越清晰）；分析不可用返回 None。"""
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [ffmpeg, "-hide_banner", "-i", str(path),
             "-vf", "blurdetect,metadata=print:file=-", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    blur_vals = _lavfi_values(proc.stderr, "blur")
    if blur_vals:
        return max(0.0, 1.0 - blur_vals[-1])  # blur 0=清晰 → score 1
    # blurdetect 不可用（旧版 ffmpeg）：sobel 边缘能量兜底
    try:
        proc2 = subprocess.run(  # noqa: S603
            [ffmpeg, "-hide_banner", "-i", str(path),
             "-vf", "sobel,signalstats,metadata=print:file=-", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    yavg_vals = _lavfi_values(proc2.stderr, "YAVG")
    if yavg_vals:
        # 边缘图亮度均值：0-255 → 归一（>60 视为很清晰）
        return min(1.0, yavg_vals[-1] / 60.0)
    return None


def pick_best(
    paths: list[str | Path],
    *,
    resolution_weight: float = 0.2,
) -> dict[str, Any]:
    """从候选中选最优；返回 {best, scores: [{path, sharpness, resolution, score}]}。"""
    scored: list[dict[str, Any]] = []
    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        w, h = _probe_size(path)
        sharp = sharpness_score(path)
        res_norm = min(1.0, (w * h) / _REF_PIXELS) if w and h else 0.0
        score = 0.0
        if sharp is not None:
            score = (1.0 - resolution_weight) * sharp + resolution_weight * res_norm
        scored.append({
            "path": str(path),
            "sharpness": round(sharp, 4) if sharp is not None else None,
            "resolution": f"{w}x{h}" if w else None,
            "score": round(score, 4),
        })
    if not scored:
        return {"best": None, "scores": [], "reasoning": "无有效候选"}
    scored.sort(key=lambda s: s["score"], reverse=True)
    return {
        "best": scored[0]["path"],
        "scores": scored,
        "reasoning": "评分 = 0.8*锐度 + 0.2*分辨率（锐度越高/越清晰越好）",
    }


class AssetPicker(BaseTool):
    """素材 A/B 选优：多候选 → 锐度+分辨率评分 → 最优。"""

    name = "asset_picker"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["paths"],
        "properties": {
            "paths": {"type": "array", "items": {"type": "string"}, "description": "候选素材路径（≥2 张）"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        paths = inputs.get("paths") or []
        if len(paths) < 2:
            return ToolResult(success=False, error="'paths' 至少需要 2 个候选")
        return ToolResult(success=True, data=pick_best(paths))
