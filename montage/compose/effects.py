"""effects — 剪辑特效模块（100% 自创实现）。

全部基于 ffmpeg **标准滤镜**（官方文档），不参照任何第三方源码：
- ``normalize_clip``   参数归一化（分辨率/fps/像素格式统一，拼接前必做）
- ``spatial_compose``  空间布局（分屏 / 竖排 / 画中画 PiP）
- ``blend_layers``     混合图层（blend 模式：screen/overlay/multiply/add...）
- ``change_speed``     变速（视频 setpts + 音频 atempo，支持 >2x/ <0.5x 串联）
- ``showcase_card``    展示卡片（9:16 letterbox + 底部标题）
- ``cut_silence``      静音剪切/跳切（silencedetect 分析 + trim/concat）
- ``auto_reframe``     智能重构图（比例转换；人脸追踪 OpenCV 可选，缺失降级居中）

所有函数零第三方依赖（人脸追踪为可选 OpenCV，缺失自动降级）。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_engine import ComposError, check_ffmpeg, probe

_ASPECTS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:3": (1440, 1080),
    "3:4": (1080, 1440),
    "21:9": (2520, 1080),
}

_BLEND_MODES = (
    "normal", "screen", "overlay", "multiply", "add", "softlight",
    "hardlight", "dodge", "burn", "darken", "lighten", "difference",
)

_PIP_POSITIONS = {
    "top_left": (20, 20),
    "top_right": ("W-w-20", 20),
    "bottom_left": (20, "H-h-20"),
    "bottom_right": ("W-w-20", "H-h-20"),
}

_DRAWTEXT_DANGER = frozenset(":\\',")


def sanitize_drawtext(text: str) -> str:
    """drawtext 转义：``: ' , \\`` 与换行。过滤后为空则返回空串（调用方应 skip）。"""
    collapsed = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())
    visible = "".join(ch for ch in collapsed if ch not in _DRAWTEXT_DANGER)
    if not visible.strip():
        return ""
    return (
        collapsed.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
    )


def _even(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    number = max(2, number)
    return number if number % 2 == 0 else number - 1


def _media_layout(path: str | Path | None) -> dict[str, Any]:
    """从成片探测宽高/帧率/采样率；失败则 1920x1080@30 + 48k stereo。"""
    width, height, fps = 1920, 1080, 30
    rate, layout = 48000, "stereo"
    if path:
        try:
            info = probe(Path(path))
        except ComposError:
            info = {}
        for stream in info.get("streams") or []:
            if not isinstance(stream, dict):
                continue
            kind = stream.get("codec_type")
            if kind == "video":
                width = _even(stream.get("width") or width, 1920)
                height = _even(stream.get("height") or height, 1080)
                raw = str(stream.get("r_frame_rate") or stream.get("avg_frame_rate") or "")
                if "/" in raw:
                    num, den = raw.split("/", 1)
                    try:
                        if float(den):
                            fps = max(1, round(float(num) / float(den)))
                    except (TypeError, ValueError):
                        pass
                elif raw:
                    try:
                        fps = max(1, round(float(raw)))
                    except (TypeError, ValueError):
                        pass
            elif kind == "audio":
                try:
                    rate = int(float(stream.get("sample_rate") or rate))
                except (TypeError, ValueError):
                    pass
                ch_layout = str(stream.get("channel_layout") or "").strip()
                channels = stream.get("channels")
                if ch_layout:
                    layout = ch_layout
                elif channels == 1:
                    layout = "mono"
                else:
                    layout = "stereo"
    return {
        "width": _even(width, 1920),
        "height": _even(height, 1080),
        "fps": int(fps) if fps else 30,
        "rate": max(8000, int(rate)),
        "layout": layout or "stereo",
    }


def _ffmpeg() -> str:
    ff = check_ffmpeg()
    if not ff:
        raise ComposError("缺少 ffmpeg")
    return ff


def _run(cmd: list[str], timeout: int = 1800) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
    if proc.returncode != 0:
        tail = proc.stderr[-800:] if proc.stderr else ""
        raise ComposError(f"FFmpeg 失败(exit {proc.returncode}): {tail}")


def _out_dir(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# 参数归一化
# ---------------------------------------------------------------------------


def normalize_clip(
    input_path: str | Path,
    output: str | Path,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    crf: int = 18,
) -> Path:
    """统一分辨率/fps/像素格式（拼接前归一化，提高 concat 成功率）。"""
    output = Path(output)
    _out_dir(output)
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}"
    )
    _run([
        _ffmpeg(), "-y", "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(output),
    ])
    return output


# ---------------------------------------------------------------------------
# 空间布局（分屏 / 竖排 / 画中画）
# ---------------------------------------------------------------------------


def spatial_compose(
    clips: list[Path],
    output: str | Path,
    *,
    layout: str = "side_by_side",
    width: int = 1920,
    height: int = 1080,
    pip_position: str = "bottom_right",
) -> Path:
    """空间布局：side_by_side（等宽分屏）/ vertical_stack（等高竖排）/
    picture_in_picture（主片 + 小窗）。

    分屏/竖排用 xstack；画中画用 overlay。
    """
    if len(clips) < 2:
        raise ComposError("spatial_compose 需要至少 2 个片段")
    if layout not in ("side_by_side", "vertical_stack", "picture_in_picture"):
        raise ComposError(f"未知布局: {layout}（可选 side_by_side/vertical_stack/picture_in_picture）")
    output = Path(output)
    _out_dir(output)
    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]
    n = len(clips)

    if layout == "picture_in_picture":
        pos = _PIP_POSITIONS.get(pip_position, _PIP_POSITIONS["bottom_right"])
        x_expr, y_expr = pos
        pw, ph = width // 4, height // 4
        fc = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2[bg];"
            f"[1:v]scale={pw}:{ph}:force_original_aspect_ratio=decrease,"
            f"pad={pw}:{ph}:(ow-iw)/2:(oh-ih)/2[fg];"
            f"[bg][fg]overlay=x={x_expr}:y={y_expr}[v]"
        )
    else:
        if layout == "side_by_side":
            cell_w, cell_h = width // n, height
            xstack_expr = "|".join(f"{i * (width // n)}_0" for i in range(n))
        else:
            cell_w, cell_h = width, height // n
            xstack_expr = "|".join(f"0_{i * (height // n)}" for i in range(n))
        vf_parts = []
        for i in range(n):
            vf_parts.append(
                f"[{i}:v]scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
                f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2[v{i}]"
            )
        fc = ";".join(vf_parts) + (
            f";{''.join(f'[v{i}]' for i in range(n))}"
            f"xstack=inputs={n}:layout={xstack_expr}[v]"
        )

    _run([
        _ffmpeg(), "-y", *inputs,
        "-filter_complex", fc,
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(output),
    ])
    return output


# ---------------------------------------------------------------------------
# 混合图层（blend 模式）
# ---------------------------------------------------------------------------


def blend_layers(
    base: str | Path,
    overlay: str | Path,
    output: str | Path,
    *,
    mode: str = "screen",
    opacity: float = 1.0,
) -> Path:
    """用 blend 滤镜叠加图层（screen/overlay/multiply/add 等模式）。"""
    if mode not in _BLEND_MODES:
        raise ComposError(f"未知混合模式: {mode}（可选 {', '.join(_BLEND_MODES)}）")
    output = Path(output)
    _out_dir(output)
    opacity = max(0.0, min(1.0, float(opacity)))
    fc = (
        f"[0:v]format=rgba[base];[1:v]format=rgba[ovl];"
        f"[base][ovl]blend=all_mode={mode}:all_opacity={opacity:.2f}[v]"
    )
    _run([
        _ffmpeg(), "-y", "-i", str(base), "-i", str(overlay),
        "-filter_complex", fc,
        "-map", "[v]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(output),
    ])
    return output


# ---------------------------------------------------------------------------
# 变速
# ---------------------------------------------------------------------------


def _atempo_chain(factor: float) -> str:
    """atempo 滤镜只支持 0.5-2.0；超出用串联逼近。"""
    parts: list[str] = []
    f = factor
    while f > 2.0:
        parts.append("atempo=2.0")
        f /= 2.0
    while f < 0.5:
        parts.append("atempo=0.5")
        f /= 0.5
    if abs(f - 1.0) > 1e-6:
        parts.append(f"atempo={f:.3f}")
    return ",".join(parts) if parts else "atempo=1.0"


def change_speed(
    input_path: str | Path,
    output: str | Path,
    *,
    factor: float = 1.5,
) -> Path:
    """变速：factor>1 加速、<1 慢放；视频 setpts + 音频 atempo 同步。"""
    factor = float(factor)
    if factor <= 0:
        raise ComposError("变速因子必须 > 0")
    output = Path(output)
    _out_dir(output)
    _run([
        _ffmpeg(), "-y", "-i", str(input_path),
        "-vf", f"setpts=PTS/{factor:.4f}",
        "-af", _atempo_chain(factor),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(output),
    ])
    return output


# ---------------------------------------------------------------------------
# 展示卡片（9:16 letterbox + 标题）
# ---------------------------------------------------------------------------


def showcase_card(
    input_path: str | Path,
    output: str | Path,
    *,
    title: str = "",
    width: int = 1080,
    height: int = 1920,
    fontfile: str | None = None,
) -> Path:
    """展示卡片：视频 letterbox 居中 + 底部标题（适合 9:16 竖屏分发）。"""
    output = Path(output)
    _out_dir(output)
    # 视频区：占高度 ~70%，上下留背景
    pad_top = int(height * 0.06)
    vh = int(height * 0.70)
    vf = (
        f"scale={width}:{vh}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:{pad_top}:color=0x101014"
    )
    if title:
        safe = sanitize_drawtext(title)
        if safe:
            font = f"fontfile='{fontfile}':" if fontfile else ""
            vf += (
                f",drawtext={font}text='{safe}':x=(w-text_w)/2:y=h-text_h-{int(height*0.06)}:"
                f"fontsize={int(height*0.035)}:fontcolor=white:borderw=2:bordercolor=black"
            )
    _run([
        _ffmpeg(), "-y", "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(output),
    ])
    return output


# ---------------------------------------------------------------------------
# 静音剪切 / 跳切
# ---------------------------------------------------------------------------

_SILENCE_RE = re.compile(
    r"silence_(start|end):\s*(-?\d+(?:\.\d+)?)"
)


def detect_silence(
    input_path: str | Path,
    *,
    threshold_db: float = -35.0,
    min_duration: float = 0.5,
) -> list[tuple[float, float]]:
    """检测静音区间（silencedetect 分析），返回 [(start, end), ...]。"""
    ff = _ffmpeg()
    proc = subprocess.run(  # noqa: S603
        [ff, "-hide_banner", "-i", str(input_path),
         "-af", f"silencedetect=noise={threshold_db:.1f}dB:d={min_duration:.2f}",
         "-f", "null", "-"],
        capture_output=True, text=True, timeout=600,
    )
    starts: list[float] = []
    ends: list[float] = []
    for kind, val in _SILENCE_RE.findall(proc.stderr):
        (ends if kind == "end" else starts).append(float(val))
    # 成对匹配（silence_end 对应最近的 silence_start）
    ranges: list[tuple[float, float]] = []
    i = 0
    for s in starts:
        e = ends[i] if i < len(ends) else s + min_duration
        ranges.append((s, e))
        i += 1
    return ranges


def cut_silence(
    input_path: str | Path,
    output: str | Path,
    *,
    threshold_db: float = -35.0,
    min_duration: float = 0.5,
    action: str = "remove",
) -> dict[str, Any]:
    """静音剪切：remove（删除静音段，跳切）/ mark（只返回静音区间，不处理）。

    remove 用 trim+concat 保留非静音段（视频+音频同步）。
    """
    input_path = Path(input_path)
    output = Path(output)
    _out_dir(output)
    silence = detect_silence(input_path, threshold_db=threshold_db, min_duration=min_duration)
    if action == "mark":
        return {"silence_ranges": silence, "count": len(silence)}

    if not silence:
        # 无静音 → 直接复制
        _run([_ffmpeg(), "-y", "-i", str(input_path), "-c", "copy", str(output)])
        return {"silence_ranges": [], "removed_segments": 0, "action": "remove"}

    # 非静音区间 = 全长去掉静音段
    info = probe(input_path)
    duration = float((info.get("format") or {}).get("duration", 0))
    segments: list[tuple[float, float]] = []
    prev = 0.0
    for s, e in silence:
        if s > prev + 0.05:
            segments.append((prev, s))
        prev = max(prev, e)
    if prev < duration - 0.05:
        segments.append((prev, duration))
    if not segments:
        raise ComposError("静音覆盖全片，无可保留内容")

    fc_parts: list[str] = []
    v_labels: list[str] = []
    a_labels: list[str] = []
    for i, (s, e) in enumerate(segments):
        dur = e - s
        fc_parts.append(
            f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
        fc_parts.append(
            f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )
        v_labels.append(f"[v{i}]")
        a_labels.append(f"[a{i}]")
    fc_parts.append(
        f"{''.join(v_labels)}{''.join(a_labels)}"
        f"concat=n={len(segments)}:v=1:a=1[v][a]"
    )
    _run([
        _ffmpeg(), "-y", "-i", str(input_path),
        "-filter_complex", ";".join(fc_parts),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(output),
    ])
    return {
        "silence_ranges": silence,
        "kept_segments": segments,
        "removed_segments": len(silence),
        "action": "remove",
    }


# ---------------------------------------------------------------------------
# 智能重构图
# ---------------------------------------------------------------------------


def _face_center_x_ratio(input_path: Path) -> float | None:
    """人脸中心 x 比例（OpenCV 可选；缺失或未检出返回 None → 居中构图）。"""
    try:
        import cv2  # type: ignore
    except ImportError:
        return None
    try:
        cap = cv2.VideoCapture(str(input_path))
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return None
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = cascade.detectMultiScale(gray, 1.1, 5, minSize=(40, 40))
        if len(faces) == 0:
            return None
        x, _, w, _ = faces[0]
        return (x + w / 2) / frame.shape[1]
    except Exception:  # noqa: BLE001
        return None


def auto_reframe(
    input_path: str | Path,
    output: str | Path,
    *,
    target: str = "9:16",
    mode: str = "center",
) -> dict[str, Any]:
    """按目标比例重构图（scale+crop 填满）。

    ``mode=center``：居中裁切；``mode=face``：优先人脸构图
    （OpenCV 可选，缺失/未检出自动降级居中，返回 mode_used 说明）。
    """
    if target not in _ASPECTS:
        raise ComposError(f"未知比例: {target}（可选 {', '.join(_ASPECTS)}）")
    output = Path(output)
    _out_dir(output)
    tw, th = _ASPECTS[target]

    vf = f"scale={tw}:{th}:force_original_aspect_ratio=increase,crop={tw}:{th}"
    mode_used = mode
    if mode == "face":
        cx = _face_center_x_ratio(Path(input_path))
        if cx is not None:
            # 先按源高比例放大，再把人脸中心对齐到画面中心
            vf = (
                f"scale={tw}:{th}:force_original_aspect_ratio=increase,"
                f"crop={tw}:{th}:x='(iw-{tw})*{cx:.3f}':y=(ih-{th})/2"
            )
        else:
            mode_used = "center"  # 降级

    _run([
        _ffmpeg(), "-y", "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(output),
    ])
    return {"target": target, "mode": mode, "mode_used": mode_used, "output": str(output)}


# ---------------------------------------------------------------------------
# 2s 片头 / lower-third（不是 showcase_card，也不是 render_kind=title_card）
# ---------------------------------------------------------------------------


def title_card(
    input_path: str | Path,
    output: str | Path,
    *,
    title: str = "",
    duration: float = 2.0,
    fontfile: str | None = None,
) -> Path:
    """2 秒片头：纯色底 + 居中标题 + 与成片对齐的静音音轨，供 concat。"""
    output = Path(output)
    _out_dir(output)
    safe = sanitize_drawtext(title)
    if not safe:
        raise ComposError("空标题，跳过片头")
    dur = max(0.1, float(duration or 2.0))
    lay = _media_layout(input_path)
    font = f"fontfile='{fontfile}':" if fontfile else ""
    vf = (
        f"drawtext={font}text='{safe}':x=(w-text_w)/2:y=(h-text_h)/2:"
        f"fontsize={max(24, int(lay['height'] * 0.08))}:"
        f"fontcolor=white:borderw=3:bordercolor=black"
    )
    _run([
        _ffmpeg(), "-y",
        "-f", "lavfi", "-i",
        f"color=c=0x101014:s={lay['width']}x{lay['height']}:d={dur:.3f}:r={lay['fps']}",
        "-f", "lavfi", "-i",
        f"anullsrc=r={lay['rate']}:cl={lay['layout']}",
        "-vf", vf,
        "-t", f"{dur:.3f}", "-shortest",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(output),
    ])
    return output


def lower_third(
    input_path: str | Path,
    output: str | Path,
    *,
    text: str = "",
    start_seconds: float = 0.0,
    duration: float = 4.0,
    fontfile: str | None = None,
) -> Path:
    """成片左下角花字：有片头则从片头结束后起 4 秒。"""
    output = Path(output)
    _out_dir(output)
    safe = sanitize_drawtext(text)
    if not safe:
        raise ComposError("空花字，跳过 lower_third")
    start = max(0.0, float(start_seconds or 0.0))
    dur = max(0.1, float(duration or 4.0))
    end = start + dur
    lay = _media_layout(input_path)
    font = f"fontfile='{fontfile}':" if fontfile else ""
    enable = f"between(t\\,{start:.3f}\\,{end:.3f})"
    vf = (
        f"drawtext={font}text='{safe}':x={max(24, int(lay['width'] * 0.04))}:"
        f"y=h-text_h-{max(24, int(lay['height'] * 0.08))}:"
        f"fontsize={max(18, int(lay['height'] * 0.045))}:"
        f"fontcolor=white:borderw=2:bordercolor=black:enable={enable}"
    )
    _run([
        _ffmpeg(), "-y", "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(output),
    ])
    return output
