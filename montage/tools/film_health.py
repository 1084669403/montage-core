"""film_health — 成片发布前整片探测（P5）。

只看 renders/final.mp4 的确定性硬伤（存在/可读/视频流/时长）。
不要对成片再跑 asset_quality_gate 的 blurdetect。缺 ffmpeg 只 warning，不挡导出。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from montage.compose.ffmpeg_engine import check_ffprobe, probe
from montage.engine.artifacts import ArtifactStore
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

ProbeFn = Callable[[Path], dict[str, Any]]


def _r_frame_rate(value: Any) -> float | None:
    if not value:
        return None
    text = str(value)
    if "/" in text:
        num, den = text.split("/", 1)
        try:
            d = float(den)
            return float(num) / d if d else None
        except (TypeError, ValueError):
            return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def inspect_film(
    path: str | Path,
    *,
    expected_duration: float | None = None,
    probe_fn: ProbeFn | None = None,
) -> dict[str, Any]:
    """返回 {pass, critical, warnings, probe, path}。缺文件即 critical。"""
    target = Path(path)
    critical: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    report: dict[str, Any] = {"path": str(target), "probe": {}}
    if not target.is_file():
        critical.append({"field": "path", "message": f"成片不存在: {target}"})
        report["pass"] = False
        report["critical"] = critical
        report["warnings"] = warnings
        return report

    if probe_fn is None and check_ffprobe() is None:
        warnings.append({"field": "ffprobe", "message": "缺少 ffprobe，跳过整片探测"})
        report["pass"] = True
        report["critical"] = critical
        report["warnings"] = warnings
        return report

    reader = probe_fn or probe
    try:
        info = reader(target)
    except Exception as exc:  # noqa: BLE001
        critical.append({"field": "probe", "message": f"ffprobe 失败: {exc}"})
        report["pass"] = False
        report["critical"] = critical
        report["warnings"] = warnings
        return report

    streams = info.get("streams") or []
    video = next((s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"), {})
    fmt = info.get("format") if isinstance(info.get("format"), dict) else {}
    duration = float(fmt.get("duration") or 0)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = _r_frame_rate(video.get("r_frame_rate"))
    report["probe"] = {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "video_codec": video.get("codec_name") or "",
        "audio_codec": audio.get("codec_name") or "",
        "has_audio": bool(audio),
        "size_bytes": int(fmt.get("size") or 0),
    }
    if not video:
        critical.append({"field": "video", "message": "成片没有视频流"})
    if duration <= 0:
        critical.append({"field": "duration", "message": "成片时长为 0"})
    if video and (width <= 0 or height <= 0):
        critical.append({"field": "resolution", "message": "成片分辨率无效"})
    if not audio:
        warnings.append({"field": "audio", "message": "成片没有音轨"})
    if fps is not None and (fps < 1 or fps > 120):
        warnings.append({"field": "fps", "message": f"帧率异常: {fps}"})
    if expected_duration and expected_duration > 0 and duration > 0:
        delta = abs(duration - expected_duration) / expected_duration
        if delta > 0.2:
            warnings.append({
                "field": "duration",
                "message": f"时长 {duration:.1f}s 相对目标 {expected_duration:.1f}s 偏差超过 20%",
            })
    report["pass"] = not critical
    report["critical"] = critical
    report["warnings"] = warnings
    return report


def _expected_duration(project_dir: Path) -> float | None:
    store = ArtifactStore(project_dir)
    bible = store.read("series_bible") or {}
    try:
        target = float(bible.get("target_duration_seconds") or 0)
    except (TypeError, ValueError):
        target = 0.0
    if target > 0:
        return target
    plan = store.read("compose_plan") or {}
    total = 0.0
    for shot in plan.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        try:
            total += float(shot.get("duration_seconds") or 0)
        except (TypeError, ValueError):
            continue
    return total if total > 0 else None


class FilmHealth(BaseTool):
    """发布前整片健康检查。"""

    name = "film_health"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "properties": {
            "project_dir": {"type": "string"},
            "path": {"type": "string", "description": "默认 renders/final.mp4"},
            "expected_duration": {"type": "number"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if check_ffprobe() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        root = Path(str(inputs.get("project_dir") or "")).resolve() if inputs.get("project_dir") else None
        raw = str(inputs.get("path") or "").strip()
        if raw:
            path = Path(raw)
        elif root:
            path = root / "renders" / "final.mp4"
        else:
            return ToolResult(success=False, error="需要 path 或 project_dir")
        expected = inputs.get("expected_duration")
        try:
            expected_f = float(expected) if expected not in (None, "") else None
        except (TypeError, ValueError):
            expected_f = None
        if expected_f is None and root:
            expected_f = _expected_duration(root)
        data = inspect_film(path, expected_duration=expected_f)
        if root:
            ArtifactStore(root).write("film_health", data, schema=None)
            data["artifact"] = str(root / "artifacts" / "film_health.json")
        return ToolResult(success=True, data=data)
