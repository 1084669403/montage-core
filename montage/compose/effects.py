"""effects — 剪辑特效模块（100% 自创实现）。

全部基于 ffmpeg **标准滤镜**（官方文档），不参照任何第三方源码：
- ``normalize_clip``   参数归一化（分辨率/fps/像素格式统一，拼接前必做）
- ``spatial_compose``  空间布局（分屏 / 竖排 / 画中画 PiP）
- ``blend_layers``     混合图层（blend 模式：screen/overlay/multiply/add...）
- ``change_speed``     变速（视频 setpts + 音频 atempo，支持 >2x/ <0.5x 串联）
- ``showcase_card``    展示卡片（9:16 letterbox + 底部标题）
- ``cut_silence``      静音剪切/跳切（silencedetect 分析 + trim/concat）
- ``auto_reframe``     智能重构图（比例转换；人脸追踪 OpenCV 可选，缺失降级居中）
- ``impact_flash``     P0-8 冲击闪白（eq timeline 时间窗亮度脉冲，时长守恒）
- ``zoom_punch``       P0-8 缩放冲击（crop 表达式急推回弹，不用 zoompan，时长守恒）
- ``camera_shake``     P0-8 镜头震动（crop x/y 正弦抖动，时长守恒）
- ``apply_post_vfx``   P0-8 post 层 vfx 分发器（按 onset 链式应用；MONTAGE_NO_VFX=1 直通）

所有函数零第三方依赖（人脸追踪为可选 OpenCV，缺失自动降级）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_engine import (
    ComposError,
    check_ffmpeg,
    font_filter_path,
    probe,
    resolve_font_file,
    run_ffmpeg,
)

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


def font_param(fontfile: str | None = None) -> str:
    """drawtext 的 ``fontfile=...:`` 片段。

    不给 fontfile 时解析资产库/系统字体；都没有则抛可读错误——否则 ffmpeg
    会退到 fontconfig，在没配 config 的机器（尤其 Windows）上直接失败，
    片头被静默跳过。

    值不加引号、按 filtergraph 规则双重转义（与 lut3d 的 ``file=`` 一致）：
    ``fontfile='C\\\\:/...'`` 这种"带引号 + 双转义"会被解析成字面 ``\\\\``，
    ffmpeg 报 "No option name near '/Windows/Fonts/...'"。
    """
    resolved = resolve_font_file(fontfile)
    if resolved is None:
        raise ComposError(
            "找不到可用字体：请跑 python assets/scripts/fetch_assets.py --fonts，"
            "或显式传 fontfile="
        )
    return f"fontfile={font_filter_path(resolved)}:"


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
    """向后兼容入口：测试常 patch 本名断言命令；实现委托共享执行器。"""
    run_ffmpeg(cmd, timeout=timeout)


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
            font = font_param(fontfile)
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
    proc = run_ffmpeg(
        [ff, "-hide_banner", "-i", str(input_path),
         "-af", f"silencedetect=noise={threshold_db:.1f}dB:d={min_duration:.2f}",
         "-f", "null", "-"],
        timeout=600, check=False,
    )
    starts: list[float] = []
    ends: list[float] = []
    for kind, val in _SILENCE_RE.findall(proc.stderr):
        (ends if kind == "end" else starts).append(float(val))
    # 成对匹配（silence_end 对应最近的 silence_start）
    ranges: list[tuple[float, float]] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else s + min_duration
        ranges.append((s, e))
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
    font = font_param(fontfile)
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
    font = font_param(fontfile)
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


# ---------------------------------------------------------------------------
# P0-8 后期特效（post 层 vfx）：冲击闪白 / 缩放冲击 / 镜头震动
# ---------------------------------------------------------------------------
# 时长守恒是硬约束：三个特效都不改变输出时长（保护 film_health.duration_check
# 与 edit_metrics 不被自家特效打破）。滤镜选型约束：
# - 亮度脉冲用 eq（有 timeline 支持）；curves 无 enable，会全程生效，不可用。
# - 缩放冲击用 zoompan d=1（每输入帧出 1 帧，时长守恒；ot 驱动窗口）——
#   crop 动态 w/h 会触发 filter 重初始化失败（ffmpeg 实测）。
# - 震动用 crop x/y 表达式（x/y 动态求值不触发重初始化；w/h 固定才安全）。

POST_VFX_KINDS: tuple[str, ...] = ("impact_flash", "zoom_punch", "camera_shake")


def _post_vfx_guard(input_path: str | Path, output: str | Path) -> Path:
    if not Path(input_path).exists():
        raise ComposError(f"特效源文件不存在: {input_path}")
    output = Path(output)
    _out_dir(output)
    return output


def _post_vfx_cmd(
    input_path: str | Path, output: Path, *, vf: str
) -> list[str]:
    """后期特效统一编码参数：重编码 + 音频直通 + 时长不显式裁剪（滤镜全部时长守恒）。"""
    return [
        _ffmpeg(), "-y", "-i", str(input_path),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart", str(output),
    ]


def impact_flash(
    input_path: str | Path,
    output: str | Path,
    *,
    onset: float,
    duration: float = 0.12,
    intensity: float = 0.6,
) -> Path:
    """冲击闪白：[onset, onset+duration] 时间窗内亮度脉冲（eq 有 timeline 支持）。

    intensity 0-1 映射 brightness 0~0.5；窗外亮度增益为 0（enable 时间窗）。
    """
    if not 0.0 <= float(intensity) <= 1.0:
        raise ComposError(f"impact_flash intensity 须在 [0,1]: {intensity}")
    if float(duration) <= 0:
        raise ComposError(f"impact_flash duration 须 > 0: {duration}")
    if float(onset) < 0:
        raise ComposError(f"impact_flash onset 须 >= 0: {onset}")
    output = _post_vfx_guard(input_path, output)
    start, end = float(onset), float(onset) + float(duration)
    bright = 0.5 * float(intensity)
    vf = (
        f"eq=brightness={bright:.3f}"
        f":enable='between(t,{start:.3f},{end:.3f})'"
    )
    _run(_post_vfx_cmd(input_path, output, vf=vf))
    return output


def zoom_punch(
    input_path: str | Path,
    output: str | Path,
    *,
    onset: float,
    duration: float = 0.25,
    intensity: float = 0.5,
) -> Path:
    """缩放冲击：[onset, onset+duration] 内急推放大后回弹（zoompan d=1）。

    ffmpeg 实测约束：crop 动态 w/h 触发 filter 重初始化失败，不可用；
    zoompan 配 ``d=1``（每输入帧恰好出 1 输出帧）时长天然守恒，用
    ``ot``（out_time 秒）驱动三角波：0→1→0（上升急推、回弹），窗外 z=1 直通。
    """
    if not 0.0 < float(intensity) <= 1.0:
        raise ComposError(f"zoom_punch intensity 须在 (0,1]: {intensity}")
    if float(duration) <= 0:
        raise ComposError(f"zoom_punch duration 须 > 0: {duration}")
    if float(onset) < 0:
        raise ComposError(f"zoom_punch onset 须 >= 0: {onset}")
    output = _post_vfx_guard(input_path, output)
    start, end = float(onset), float(onset) + float(duration)
    lay = _media_layout(input_path)
    zoom = 1.0 + 0.25 * float(intensity)
    window = (
        f"max(0\\,min(1\\,1-abs((ot-{start:.3f})/{duration:.3f}*2-1)))"
    )
    z_expr = (
        f"if(between(ot\\,{start:.3f}\\,{end:.3f})"
        f"\\,1+{zoom - 1:.4f}*{window}\\,1)"
    )
    vf = (
        f"zoompan=z='{z_expr}'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={lay['width']}x{lay['height']}:fps={lay['fps']}"
    )
    _run(_post_vfx_cmd(input_path, output, vf=vf))
    return output


def camera_shake(
    input_path: str | Path,
    output: str | Path,
    *,
    onset: float,
    duration: float = 0.3,
    intensity: float = 0.5,
) -> Path:
    """镜头震动：[onset, onset+duration] 内 crop x/y 正弦抖动，scale 回原尺寸。

    幅度 = 帧高 * 0.03 * intensity；窗外位移 0。crop 表达式逐帧求值，
    输出尺寸先锁定（偶数），时长天然守恒。
    """
    if not 0.0 < float(intensity) <= 1.0:
        raise ComposError(f"camera_shake intensity 须在 (0,1]: {intensity}")
    if float(duration) <= 0:
        raise ComposError(f"camera_shake duration 须 > 0: {duration}")
    if float(onset) < 0:
        raise ComposError(f"camera_shake onset 须 >= 0: {onset}")
    output = _post_vfx_guard(input_path, output)
    start, end = float(onset), float(onset) + float(duration)
    lay = _media_layout(input_path)
    amp = max(2.0, lay["height"] * 0.03 * float(intensity))
    # 窗内 sin 抖动；窗外位移 0（等价直通）。crop 无 enable timeline（ffmpeg 实测），
    # 窗口判断内联进 x/y 表达式（if(between)），逐帧求值，时长天然守恒。
    vf = (
        f"crop=w=iw:h=ih"
        f":x='if(between(t,{start:.3f},{end:.3f})\\,{amp:.2f}*sin(t*80)\\,0)'"
        f":y='if(between(t,{start:.3f},{end:.3f})\\,{amp:.2f}*sin(t*120)\\,0)'"
        f",scale=iw:ih"
    )
    _run(_post_vfx_cmd(input_path, output, vf=vf))
    return output


def apply_post_vfx(
    input_path: str | Path,
    output: str | Path,
    vfx_list: list[dict[str, Any]] | None,
    *,
    work_dir: str | Path | None = None,
) -> Path:
    """post 层 vfx 分发器：按 onset 排序链式应用；空列表/MONTAGE_NO_VFX=1 直通。

    非法 kind 报错（确定性）；合法但源文件缺失时报错由特效函数抛出。
    work_dir 供链式中间产物落盘（缺省与 output 同目录）。
    """
    import os
    import shutil

    items = [v for v in (vfx_list or []) if isinstance(v, dict)]
    post = sorted(
        (v for v in items if str(v.get("layer") or "") == "post"),
        key=lambda v: float(v.get("onset") or 0),
    )
    if os.environ.get("MONTAGE_NO_VFX", "").strip() == "1":
        post = []
    if not post:
        # 直通：目标不存在时拷贝（调用方拿到的 output 一定存在）。
        src, dst = Path(input_path), Path(output)
        if src.resolve() != dst.resolve() and not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return Path(output)
    for v in post:
        kind = str(v.get("kind") or "").strip()
        if kind not in POST_VFX_KINDS:
            raise ComposError(
                f"未知 post 层特效 kind: {kind!r}（支持 {', '.join(POST_VFX_KINDS)}）"
            )
    work = Path(work_dir) if work_dir else Path(output).parent
    work.mkdir(parents=True, exist_ok=True)
    current = Path(input_path)
    for i, v in enumerate(post):
        kind = str(v["kind"])
        last = i == len(post) - 1
        target = Path(output) if last else work / f"_vfx_{i}_{kind}.mp4"
        kwargs: dict[str, Any] = {
            "onset": float(v.get("onset") or 0),
        }
        if v.get("duration") is not None:
            kwargs["duration"] = float(v["duration"])
        if v.get("intensity") is not None:
            kwargs["intensity"] = max(0.0, min(1.0, float(v["intensity"])))
        fn = {
            "impact_flash": impact_flash,
            "zoom_punch": zoom_punch,
            "camera_shake": camera_shake,
        }[kind]
        current = fn(current, target, **kwargs)
    return Path(output)
