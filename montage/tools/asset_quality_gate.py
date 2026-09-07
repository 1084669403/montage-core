"""asset_quality_gate — 素材质量门禁（确定性规则，零新依赖）。

对生成/下载的图片或视频做快速质量检查，坏素材（黑帧/白帧/严重模糊/时长不符）
不进成片：assets 阶段生成后跑一次，critical 问题返回导演改 seed 重生成。

检测项（全部基于 ffmpeg/ffprobe 输出，确定性规则）：
- 黑帧/白帧：`signalstats` 的 YAVG（亮度均值）过低/过高。
- 模糊：`blurdetect` 滤镜（ffmpeg ≥ 5.1）；版本不支持时跳过并提示。
- 时长：ffprobe 实际时长 vs 期望时长（误差 > 1.5s 视为剪辑错位风险）。
- 缺失/损坏：ffprobe 失败即 critical。

沙箱注意：分析需运行 ffmpeg 子进程；测试用 mock 注入 stderr 文本。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_engine import check_ffmpeg, check_ffprobe, probe
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

# 亮度阈值（0-255，YUV 范围 16-235 为有效视频区间）
_BLACK_YAVG = 18.0
_WHITE_YAVG = 238.0
# blurdetect 输出：0=清晰，≈1=极糊；> 0.5 视为严重模糊
_BLUR_THRESHOLD = 0.5
# 时长允许误差（秒）
_DURATION_TOLERANCE = 1.5

_LAVFI_RE = re.compile(r"lavfi\.([a-z_]+)\.([A-Za-z_]+)=([0-9.]+)")


def _run_ffmpeg_metadata(path: Path, vf: str, timeout: int = 60) -> str:
    """运行 ffmpeg 分析滤镜并返回 stderr（含 lavfi.metadata 输出）。"""
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("缺少 ffmpeg")
    proc = subprocess.run(  # noqa: S603
        [ffmpeg, "-hide_banner", "-i", str(path), "-vf", vf, "-f", "null", "-"],
        capture_output=True, text=True, timeout=timeout,
    )
    return proc.stderr


def _parse_lavfi(stderr: str) -> dict[str, float]:
    """从 stderr 提取 lavfi.<filter>.<key>=<value> 键值（取最后一次出现）。"""
    out: dict[str, float] = {}
    for match in _LAVFI_RE.finditer(stderr):
        fname, key, val = match.group(1), match.group(2), match.group(3)
        out[f"{fname}.{key}"] = float(val)
    return out


def _sharpness(stderr: str) -> float | None:
    """blurdetect 输出；无 blur 键返回 None（滤镜不可用/未输出）。"""
    vals = _parse_lavfi(stderr)
    if "blurdetect.blur" in vals:
        return vals["blurdetect.blur"]
    return None


def _supports_blurdetect() -> bool:
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        return False
    proc = subprocess.run(  # noqa: S603
        [ffmpeg, "-hide_banner", "-filters"],
        capture_output=True, text=True, timeout=30,
    )
    return " blurdetect " in proc.stdout or "blurdetect" in proc.stdout


def check_asset(
    path: str | Path,
    *,
    expected_duration: float | None = None,
    check_blur: bool = True,
) -> dict[str, Any]:
    """检查单个素材；返回 {ok, issues: [{severity, kind, message, proposed_fix}]}。

    severity：critical（黑帧/白帧/损坏，必须重生成）/ warning（模糊/时长，建议处理）。
    """
    path = Path(path)
    issues: list[dict[str, str]] = []
    if not path.exists():
        return {
            "ok": False,
            "issues": [{
                "severity": "critical", "kind": "missing",
                "message": f"素材不存在: {path}",
                "proposed_fix": "重新生成该素材",
            }],
        }

    # 时长 + 可读性（ffprobe 失败 = 损坏）
    try:
        info = probe(path)
        duration = float((info.get("format") or {}).get("duration", 0))
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "issues": [{
                "severity": "critical", "kind": "corrupt",
                "message": f"素材无法解析（可能损坏）: {exc}",
                "proposed_fix": "重新生成或重新导出该素材",
            }],
        }

    if expected_duration is not None and abs(duration - expected_duration) > _DURATION_TOLERANCE:
        issues.append({
            "severity": "warning", "kind": "duration",
            "message": f"时长 {duration:.1f}s 与期望 {expected_duration:.1f}s 偏差过大",
            "proposed_fix": "检查生成参数（seconds/frames）或重新生成",
        })

    # 亮度（黑帧/白帧）
    try:
        stderr = _run_ffmpeg_metadata(
            path, "signalstats,metadata=print:file=-"
        )
    except (RuntimeError, subprocess.TimeoutExpired):
        issues.append({
            "severity": "warning", "kind": "analysis_unavailable",
            "message": "亮度分析不可用（缺少 ffmpeg）",
            "proposed_fix": "安装 ffmpeg 后重新检查",
        })
        return {"ok": not any(i["severity"] == "critical" for i in issues), "issues": issues}

    stats = _parse_lavfi(stderr)
    yavg = stats.get("signalstats.YAVG")
    if yavg is not None:
        if yavg < _BLACK_YAVG:
            issues.append({
                "severity": "critical", "kind": "black_frame",
                "message": f"画面近乎全黑（YAVG={yavg:.1f}）",
                "proposed_fix": "检查光照/曝光提示词，换 seed 重新生成",
            })
        elif yavg > _WHITE_YAVG:
            issues.append({
                "severity": "critical", "kind": "white_frame",
                "message": f"画面近乎全白（YAVG={yavg:.1f}）",
                "proposed_fix": "检查过曝提示词，换 seed 重新生成",
            })

    # 模糊
    if check_blur:
        if _supports_blurdetect():
            try:
                blur_stderr = _run_ffmpeg_metadata(path, "blurdetect,metadata=print:file=-")
                blur = _sharpness(blur_stderr)
                if blur is not None and blur > _BLUR_THRESHOLD:
                    issues.append({
                        "severity": "warning", "kind": "blur",
                        "message": f"画面模糊（blur={blur:.2f} > {_BLUR_THRESHOLD}）",
                        "proposed_fix": "降低运动幅度/检查对焦描述，换 seed 重新生成",
                    })
            except (RuntimeError, subprocess.TimeoutExpired):
                pass  # 模糊检测失败不阻塞

    ok = not any(i["severity"] == "critical" for i in issues)
    return {"ok": ok, "issues": issues, "duration_seconds": duration}


class AssetQualityGate(BaseTool):
    """素材质量门禁：黑帧/白帧/模糊/时长检查。"""

    name = "asset_quality_gate"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "description": "素材文件路径（图片/视频）"},
            "expected_duration": {"type": "number", "description": "期望时长（秒），视频素材建议提供"},
            "check_blur": {"type": "boolean", "default": True},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        result = check_asset(
            inputs.get("path", ""),
            expected_duration=inputs.get("expected_duration"),
            check_blur=bool(inputs.get("check_blur", True)),
        )
        return ToolResult(success=True, data=result)
