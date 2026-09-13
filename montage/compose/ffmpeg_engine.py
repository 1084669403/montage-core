"""ffmpeg_engine — FFmpeg 本地合成引擎（全新原创代码）。

能力：
- concat        多片段拼接（concat demuxer，先尝试流拷贝，失败回退重编码）
- trim          按时间裁剪
- burn_subtitles 烧录 SRT 字幕
- mix_audio     视频 + 旁白 + 配乐 混音（旁白优先，配乐压低音量）
- assemble      按 edit_decisions 的 cuts[] 顺序拼接 + 混音，产出 render_report

所有操作都通过 ffprobe 校验输出。ffmpeg 缺失时工具状态为 UNAVAILABLE。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_compat import (
    capabilities_snapshot,
    ffmpeg_version,
    supports,
)
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus


class ComposError(RuntimeError):
    """合成错误（可读信息）。"""


# 超时口径（全仓唯一来源）：ffprobe 探测是本地的、秒级；渲染/滤镜链可能很久。
FFPROBE_TIMEOUT = 60
FFMPEG_TIMEOUT = 1800


def check_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def check_ffprobe() -> str | None:
    return shutil.which("ffprobe")


def run_ffmpeg(
    cmd: list[str],
    timeout: int = FFMPEG_TIMEOUT,
    *,
    check: bool = True,
    error_prefix: str = "FFmpeg 失败",
) -> subprocess.CompletedProcess:
    """共享执行器：所有 ffmpeg/ffprobe 子进程都经此，避免"修一处漏一处"。

    ``check=True`` 失败抛 ``ComposError``（附 stderr 尾部）；``check=False``
    返回原始 ``CompletedProcess`` 供调用方自行解析（probe / silencedetect）。
    """
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)  # noqa: S603
    if check and proc.returncode != 0:
        tail = proc.stderr[-800:] if proc.stderr else ""
        raise ComposError(f"{error_prefix}(exit {proc.returncode}): {tail}")
    return proc


def _run(cmd: list[str], timeout: int = FFMPEG_TIMEOUT) -> None:
    """向后兼容入口：测试常 patch 本名断言命令；实现委托 ``run_ffmpeg``。"""
    run_ffmpeg(cmd, timeout=timeout)


def probe(path: str | Path) -> dict[str, Any]:
    """ffprobe 读时长/大小/流信息；失败抛 ComposError。"""
    ffprobe = check_ffprobe()
    if not ffprobe:
        raise ComposError("缺少 ffprobe（安装 FFmpeg）")
    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_entries",
        "format=duration,size:stream=codec_type,codec_name,width,height,"
        "sample_rate,channels,channel_layout,r_frame_rate",
        str(path),
    ]
    proc = run_ffmpeg(
        cmd, timeout=FFPROBE_TIMEOUT, check=False, error_prefix="ffprobe 失败",
    )
    if proc.returncode != 0:
        raise ComposError(f"ffprobe 失败: {proc.stderr[:300]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise ComposError(f"ffprobe 输出无法解析: {proc.stdout[:200]}") from exc


def concat_file_line(path: str | Path) -> str:
    """concat demuxer 一行：单引号包裹，内部 ' 写成 '\\''。"""
    posix = Path(path).resolve().as_posix().replace("'", r"'\''")
    return f"file '{posix}'"


def _stream_size(info: dict[str, Any]) -> tuple[int, int] | None:
    for stream in info.get("streams") or []:
        if stream.get("codec_type") != "video":
            continue
        try:
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if width > 0 and height > 0:
            return (width, height)
    return None


def probe_size(path: str | Path) -> tuple[int, int] | None:
    """探测画面尺寸；失败返回 None（调用方自行兜底，禁止默认 1280x720）。"""
    if not path or not Path(path).exists():
        return None
    try:
        info = probe(Path(path))
    except ComposError:
        return None
    return _stream_size(info)


def _first_video_size(clips: list[Path]) -> tuple[int, int] | None:
    """取第一个可探测片段的画面尺寸（偶数），作为重编码拼接的目标画布。

    ``concat_videos`` 的回退重编码原来硬编码 ``1920x1080``：竖屏项目一旦走到
    这条路径，整片会被 letterbox 成横屏。这里按实际片段推导；**全部探测失败
    返回 None**（由调用方决定用 profile 推导的兜底尺寸还是直接报错），不再
    静默回落横屏。
    """
    for clip in clips:
        size = probe_size(clip)
        if size:
            width, height = size
            return (width - width % 2, height - height % 2)
    return None


def concat_videos(
    clips: list[Path],
    output: Path,
    *,
    timeout: int = 1800,
    fallback_size: tuple[int, int] | None = None,
) -> Path:
    """按顺序拼接片段。输出统一 H.264 + AAC。

    ``fallback_size``：所有片段都探测不到尺寸时的兜底画布（应由调用方按
    output_profile 推导）。不传则直接报错，避免静默 letterbox 竖屏项目。
    """
    if len(clips) == 0:
        raise ComposError("concat 需要至少一个片段")
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)

    if any(_clip_has_audio(c) for c in clips) and not all(_clip_has_audio(c) for c in clips):
        work = output.parent / f"{output.stem}.pad"
        work.mkdir(parents=True, exist_ok=True)
        clips = [_ensure_audio_track(c, work) for c in clips]

    # concat demuxer 需要列表文件
    list_file = output.with_suffix(".concat.txt")
    lines = [concat_file_line(p) for p in clips]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 先尝试流拷贝（快），失败回退重编码（兼容不同参数）
    cmd_copy = [
        ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart", str(output),
    ]
    try:
        _run(cmd_copy, timeout=timeout)
    except ComposError:
        size = _first_video_size(clips) or fallback_size
        if size is None:
            raise ComposError(
                "concat 回退重编码无法确定画布尺寸（所有片段探测失败），"
                "请传 fallback_size（按 output_profile 推导）"
            )
        size_w, size_h = size
        cmd_reencode = [
            ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-vf", f"scale={size_w}:{size_h}:force_original_aspect_ratio=decrease,pad={size_w}:{size_h}:(ow-iw)/2:(oh-ih)/2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output),
        ]
        _run(cmd_reencode, timeout=timeout)
    return output


def trim_clip(input_path: Path, output: Path, start: float, duration: float) -> Path:
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg, "-y", "-ss", f"{start:.2f}", "-i", str(input_path),
        "-t", f"{duration:.2f}", "-c", "copy", str(output),
    ])
    return output


def retake_segment(
    input_path: Path,
    replacement: Path,
    output: Path,
    start: float,
    duration: float,
) -> Path:
    """用 replacement 替换 input 的 [start, start+duration)，其余段尽量 copy。"""
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    if not input_path.exists():
        raise ComposError(f"原片不存在: {input_path}")
    if not replacement.exists():
        raise ComposError(f"替换片段不存在: {replacement}")
    start = max(float(start or 0), 0.0)
    duration = float(duration or 0)
    if duration <= 0:
        raise ComposError("retake_segment 需要 duration_seconds > 0")
    info = probe(input_path)
    total = float((info.get("format") or {}).get("duration") or 0)
    if total <= 0:
        raise ComposError("原片时长无效")
    end = min(start + duration, total)
    work = output.parent / f"{output.stem}.retake"
    work.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    try:
        if start > 0.05:
            head = work / "head.mp4"
            trim_clip(input_path, head, 0.0, start)
            parts.append(head)
        parts.append(replacement)
        tail_dur = total - end
        if tail_dur > 0.05:
            tail = work / "tail.mp4"
            trim_clip(input_path, tail, end, tail_dur)
            parts.append(tail)
        return concat_videos(parts, output)
    finally:
        for leftover in work.glob("*"):
            try:
                leftover.unlink()
            except OSError:
                pass
        try:
            work.rmdir()
        except OSError:
            pass


def burn_subtitles(input_path: Path, srt_path: Path, output: Path) -> Path:
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg, "-y", "-i", str(input_path),
        "-vf", f"subtitles={srt_path.as_posix()}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "copy", str(output),
    ])
    return output


def filter_path(path: Path) -> str:
    """把磁盘路径转成 filtergraph 里安全的内联值。

    filtergraph 解析器与滤镜选项各吃一层转义，所以字面量的冒号/空格需要写
    成**双重**反斜杠（``\\\\:`` / ``\\\\ ``）才能在 ffmpeg 侧还原成一个字符。
    Windows 盘符（``D:\\...``）不双重转义会报 "No option name near ..."。
    统一用正斜杠，避免反斜杠在 filtergraph 里被当成转义符。
    """
    posix = path.resolve().as_posix()
    return posix.replace("\\", "\\\\").replace(":", "\\\\:").replace(" ", "\\\\ ")


def lut3d_filter(lut_path: Path, interp: str = "tetrahedral") -> str:
    """构造 lut3d 滤镜片段。

    选项名由 ``ffmpeg_compat.supports("lut3d_file")`` 决定：现代 ffmpeg 用
    ``file``（vf_lut3d.c 自 2.4 起即如此），写 ``filename=`` 会得到
    "Option not found"；极老版本才回落到 ``filename``。
    """
    option = "file" if supports("lut3d_file") else "filename"
    return f"lut3d={option}={filter_path(lut_path)}:interp={interp}"


def apply_lut(
    input_path: Path,
    lut_path: Path,
    output: Path,
    *,
    strength: float = 1.0,
    interp: str = "tetrahedral",
    crf: int = 18,
) -> Path:
    """统一调色：对整片应用 .cube 3D LUT（ffmpeg lut3d 滤镜）。

    - ``strength`` ∈ [0,1]：LUT 与原片混合强度（1.0 完全应用；<1 用
      blend normal 叠加，便于轻微润色）。
    - ``interp``：tetrahedral（默认，质量最好）/ trilinear / nearest。
    - 输出统一 H.264（crf 可调，默认 18 比合成端默认 20 略好）+ AAC。

    用于成片装配后统一调色，实现跨镜头色调一致（"电影感"关键），
    配合 assets/luts/ 的原创 .cube（见 asset_retriever / scripts/make_luts.py）。
    """
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    if not lut_path.exists():
        raise ComposError(f"LUT 文件不存在: {lut_path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    lut_filter = lut3d_filter(lut_path, interp)
    strength = max(0.0, min(1.0, float(strength)))

    if strength >= 1.0:
        vf = lut_filter
        cmd = [
            ffmpeg, "-y", "-i", str(input_path),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(output),
        ]
    else:
        fc = (
            f"[0:v]{lut_filter}[g];"
            f"[g][0:v]blend=all_mode=normal:all_opacity={strength:.2f}[v]"
        )
        cmd = [
            ffmpeg, "-y", "-i", str(input_path),
            "-filter_complex", fc,
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(output),
        ]
    _run(cmd, timeout=3600)
    return output


def apply_profile(
    input_path: Path,
    profile_name: str,
    output: Path,
) -> Path:
    """按平台档案输出：保比例缩放+补边、统一 fps/crf/码率（见 profiles.py）。

    profile_name 对应 montage.compose.profiles 的档案名
    （youtube_landscape / douyin_vertical / bilibili_horizontal / wechat_vertical
    / cinematic_21_9 / youtube_4k）。未知档案抛 ComposError。
    """
    from montage.compose.profiles import get_profile

    profile = get_profile(profile_name)
    if profile is None:
        raise ComposError(f"未知渲染档案: {profile_name}")
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg, "-y", "-i", str(input_path),
        "-vf", f"{profile.scale_filter()},fps={profile.fps}",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(profile.crf),
    ]
    if profile.video_bitrate:
        cmd += ["-b:v", profile.video_bitrate]
    cmd += [
        "-c:a", "aac", "-b:a", profile.audio_bitrate,
        "-movflags", "+faststart", str(output),
    ]
    _run(cmd, timeout=3600)
    return output


def extract_last_frame(src: Path, output: Path) -> Path:
    """抽出视频最后一帧为静态图（即梦 first_tail 用 last_frame_path）。

    优先 ``-sseof``；失败再按 duration-ε seek。失败条件是无视频流或空文件，
    不以 JPEG 体积过小作为判定。
    """
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    if not src.exists():
        raise ComposError(f"输入不存在: {src}")
    output.parent.mkdir(parents=True, exist_ok=True)

    def _ok(path: Path) -> bool:
        if not path.exists() or path.stat().st_size < 1:
            return False
        try:
            info = probe(path)
        except ComposError:
            return False
        streams = info.get("streams") or []
        return any(s.get("codec_type") == "video" for s in streams)

    cmd_sseof = [
        ffmpeg, "-y", "-sseof", "-0.05", "-i", str(src),
        "-frames:v", "1", "-q:v", "2", str(output),
    ]
    try:
        _run(cmd_sseof, timeout=60)
        if _ok(output):
            return output
    except ComposError:
        pass

    duration = 0.0
    try:
        info = probe(src)
        duration = float((info.get("format") or {}).get("duration") or 0)
    except (ComposError, TypeError, ValueError):
        duration = 0.0
    ss = max(duration - 0.05, 0.0)
    cmd_ss = [
        ffmpeg, "-y", "-ss", f"{ss:.3f}", "-i", str(src),
        "-frames:v", "1", "-q:v", "2", str(output),
    ]
    _run(cmd_ss, timeout=60)
    if not _ok(output):
        raise ComposError("extract_last_frame 失败：输出没有视频流或为空")
    return output


def _force_cut(cut: dict[str, Any]) -> dict[str, Any]:
    """cut_only 策略：把转场折成硬切。"""
    forced = dict(cut)
    forced["transition"] = "cut"
    forced["transition_in"] = "cut"
    forced["transition_duration"] = 0
    forced["negative_gap_seconds"] = 0
    return forced


# ---------------------------------------------------------------------------
# 转场拼接（xfade 族）：让 edit_decisions 的转场建议真正渲染出来
# ---------------------------------------------------------------------------

# 剪辑转场名 → ffmpeg xfade transition 值。
# zoom_punch / blur 无直接对应，第一版以短叠化近似（需 zoompan 的冲击感属后续增强）。
_XFADE_TRANSITIONS = {
    # "cut" 仅作哨兵值保留：TRANSITION_NAMES 用它当公开白名单（StylePack /
    # AutoEditor 消费），删键会改公开 API。ffmpeg 的 xfade **没有** cut
    # transition，真实拼接由 stitch_with_transitions 拆成 concat 完成。
    "cut": "cut",
    "crossfade": "fade",
    "dissolve": "fade",
    "fade": "fade",
    "fade_black": "fadeblack",
    "fadeblack": "fadeblack",
    "wipe": "wipeleft",
    "wipe_left": "wipeleft",
    "wipe_right": "wiperight",
    "wipe_up": "wipeup",
    "wipe_down": "wipedown",
    "push": "slideleft",
    "push_left": "slideleft",
    "push_right": "slideright",
    "push_up": "slideup",
    "push_down": "slidedown",
    "zoom_punch": "fade",
    "blur": "fade",
}

# 公开转场名（StylePack / AutoEditor 白名单）。与上表键一致，避免外部 import 私有常量。
TRANSITION_NAMES: frozenset[str] = frozenset(_XFADE_TRANSITIONS)


def _normalize_transition(cut: dict[str, Any]) -> tuple[str, float]:
    """从一条 cut 提取 (转场名, 时长秒)。

    ``transition_in``/``transition`` 为转场名；``transition_duration`` 与
    ``negative_gap_seconds`` 为重叠时长（>0 表示负空隙）。缺省 cut。
    """
    tname = str(cut.get("transition_in") or cut.get("transition") or "cut")
    tdur = float(
        cut.get("transition_duration")
        or cut.get("negative_gap_seconds")
        or 0.0
    )
    if tname == "cut" or tdur <= 0:
        return "cut", 0.0
    return tname, tdur


def needs_transition_at(cut: dict[str, Any]) -> bool:
    """该切点是否真的需要 xfade 转场。

    ``cut`` 与零时长一律按硬切（False）；只有非 cut 且时长 >0 才需要转场。
    装配端据此在 ``concat_videos`` 与 ``stitch_with_transitions`` 之间选择，
    避免给 cut 切点误判转场。
    """
    tname, _tdur = _normalize_transition(cut)
    return tname != "cut"


def _clip_duration(path: Path) -> float:
    try:
        info = probe(path)
        return float((info.get("format") or {}).get("duration") or 0)
    except ComposError:
        return 0.0


def _ensure_audio_track(path: Path, work_dir: Path) -> Path:
    """无音轨片段补静音轨，避免拼接时整片 -an。"""
    if _clip_has_audio(path):
        return path
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        return path
    dur = max(_clip_duration(path), 0.1)
    dest = work_dir / f"{path.stem}.silent.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run([
            ffmpeg, "-y", "-i", str(path),
            "-f", "lavfi", "-t", f"{dur:.3f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            "-movflags", "+faststart", str(dest),
        ], timeout=120)
    except ComposError:
        return path
    return dest if dest.is_file() else path


def _clip_has_audio(path: Path) -> bool:
    """片段是否含音轨（探测 streams）。"""
    try:
        info = probe(path)
    except ComposError:
        return False
    return any(s.get("codec_type") == "audio" for s in info.get("streams") or [])


def _acrossfade_chain(clips: list[Path], transitions: list[dict[str, Any]]) -> tuple[str, str]:
    """构造音频 acrossfade 链，返回 (filter_text, 输出标签)。

    所有输入先统一采样率/声道再 acrossfade 串联。
    本函数只服务 ``_xfade_stitch``，调用方已保证链上没有 cut 转场
    （cut 由 ``stitch_with_transitions`` 拆成 concat，不用 acrossfade 近似）。
    """
    norm = _audio_norm_filter()
    parts = [f"[0:a]{norm}[ar0]"]
    prev = "ar0"
    for i in range(1, len(clips)):
        _tname, tdur = _normalize_transition(transitions[i - 1] if i - 1 < len(transitions) else {})
        dur = max(0.05, tdur)
        # 每条输入必须先各自 norm，再接 acrossfade：aformat 只吃 1 路输入，
        # 写成 ``[prev][i:a]{norm},acrossfade`` 会得到
        # "More input link labels specified for filter 'aformat' than it has inputs"。
        parts.append(f"[{i}:a]{norm}[ar{i}]")
        parts.append(f"[{prev}][ar{i}]acrossfade=d={dur:.3f}[a{i}]")
        prev = f"a{i}"
    return ";".join(parts), f"[a{len(clips) - 1}]"


def _audio_norm_filter() -> str:
    """统一到 48kHz 立体声。

    经 ``ffmpeg_compat.supports("aformat")`` 选择：aformat 的
    ``channel_layouts`` 最稳定；仅在探测不到时回落 aresample 的 ``ochl``。
    此前写 ``aresample=48000:cl=stereo`` 会得到 "Error applying option 'cl'
    to filter 'aresample': Option not found"。
    """
    if supports("aformat"):
        return "aformat=sample_rates=48000:channel_layouts=stereo"
    return "aresample=48000:ochl=stereo"


def _xfade_stitch(
    clips: list[Path],
    transitions: list[dict[str, Any]],
    output: Path,
    *,
    keep_audio: bool = True,
    timeout: int = 1800,
) -> Path:
    """把一段**全部为非 cut 转场**的片段用一条 xfade 链拼起来。

    cut 转场不能交给 xfade：ffmpeg 没有 ``transition=cut``，会报
    "const_values array too small for transition" / "Not yet implemented in
    FFmpeg"。调用方需先把 cut 处拆开。
    """
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)

    durations = [
        float((probe(c).get("format") or {}).get("duration", 0)) for c in clips
    ]
    if any(d <= 0 for d in durations):
        raise ComposError("存在无法探测时长的片段（转场拼接需要各片段时长）")

    if keep_audio:
        any_audio = any(_clip_has_audio(c) for c in clips)
        all_audio = all(_clip_has_audio(c) for c in clips)
        if any_audio and not all_audio:
            work = output.parent / f"{output.stem}.pad"
            work.mkdir(parents=True, exist_ok=True)
            clips = [_ensure_audio_track(c, work) for c in clips]

    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]

    fc: list[str] = []
    prev_label = "[0:v]"
    cum_dur = 0.0
    for i in range(1, len(clips)):
        tname, tdur = _normalize_transition(transitions[i - 1] if i - 1 < len(transitions) else {})
        xf = _XFADE_TRANSITIONS.get(tname, "fade")
        if xf == "cut" and not supports("xfade_cut"):
            # 调用方已把 cut 拆成 concat；此处仅防御，绝不把 transition=cut
            # 发给 ffmpeg（9.x 会直接失败）。
            xf = "fade"
        cum_dur += durations[i - 1]
        offset = cum_dur - tdur  # 负空隙：两片段重叠 tdur 秒
        dur = tdur
        fc.append(
            f"{prev_label}[{i}:v]xfade=transition={xf}:duration={dur:.3f}:offset={offset:.3f}[x{i}]"
        )
        prev_label = f"[x{i}]"
    filter_complex = ";".join(fc)

    audio_ok = keep_audio and all(_clip_has_audio(c) for c in clips)

    if audio_ok:
        afc, aout = _acrossfade_chain(clips, transitions)
        cmd = [
            ffmpeg, "-y", *inputs,
            "-filter_complex", f"{filter_complex};{afc}",
            "-map", f"[x{len(clips) - 1}]", "-map", aout,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(output),
        ]
    else:
        cmd = [
            ffmpeg, "-y", *inputs,
            "-filter_complex", filter_complex,
            "-map", f"[x{len(clips) - 1}]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-an", "-movflags", "+faststart", str(output),
        ]
    _run(cmd, timeout=timeout)
    return output


def stitch_with_transitions(
    clips: list[Path],
    transitions: list[dict[str, Any]],
    output: Path,
    *,
    keep_audio: bool = True,
    timeout: int = 1800,
) -> Path:
    """按转场定义拼接片段：非 cut 用 xfade，cut 用真硬切（concat）。

    - ``clips``: 按顺序的片段；``transitions``: 长度 = len(clips)-1，
      ``transitions[j]`` 描述 clips[j] → clips[j+1] 的转场
      （对应 edit_decisions.cuts[j+1] 的 transition 字段）。
    - 转场名见 ``_XFADE_TRANSITIONS``；非 cut 且时长 >0 时两片段重叠
      （负空隙），时长即转场时长；cut 与时长 <=0 一律按硬切处理。
    - 实现上先按 cut 把片段切成若干"连续转场段"，段内用 ``_xfade_stitch``
      走 xfade 链，段间用 ``concat_videos`` 真硬拼。xfade 没有 ``cut``
      这个 transition，早期版本对 cut 仍发 ``transition=cut`` 导致整片渲染
      直接失败；也不能用 ``d=0.05`` 的淡入淡出近似——那既不是硬切，还会
      让每一处 cut 都吃掉 0.05s 造成时间轴漂移。
    - ``keep_audio=True``（默认）：所有片段有音轨时输出带音频
      （段内 acrossfade 交叉淡化，段间 concat 硬接）；任一片段无音轨则
      降级为无声视频轨（assemble 会接 mix_audio 叠加旁白/配乐）。
    - 输出 H.264（crf 18）；纯硬切时 concat 会优先尝试流拷贝。
    """
    if len(clips) < 2:
        raise ComposError("stitch 需要至少 2 个片段")
    if not check_ffmpeg():
        raise ComposError("缺少 ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)

    # 按 cut 切段：段内保留转场，段间硬切。
    runs: list[tuple[list[Path], list[dict[str, Any]]]] = [([clips[0]], [])]
    for i in range(1, len(clips)):
        raw = transitions[i - 1] if i - 1 < len(transitions) else {}
        tname, _tdur = _normalize_transition(raw)
        if tname == "cut":
            runs.append(([clips[i]], []))
        else:
            run_clips, run_trans = runs[-1]
            runs[-1] = (run_clips + [clips[i]], run_trans + [raw])

    if len(runs) == 1:
        return _xfade_stitch(
            runs[0][0], runs[0][1], output, keep_audio=keep_audio, timeout=timeout,
        )

    work = output.parent / f"{output.stem}.trans"
    work.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for idx, (run_clips, run_trans) in enumerate(runs):
        if len(run_clips) == 1:
            parts.append(run_clips[0])
            continue
        dest = work / f"run{idx:02d}.mp4"
        _xfade_stitch(
            run_clips, run_trans, dest, keep_audio=keep_audio, timeout=timeout,
        )
        parts.append(dest)
    concat_videos(parts, output, timeout=timeout)
    return output


def ken_burns(
    image_path: Path,
    output: Path,
    duration: float = 5.0,
    *,
    zoom: str = "in",
    pan: str = "center",
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    audio_path: Path | None = None,
    timeout: int = 600,
) -> Path:
    """静态图 → 缓慢运镜视频（Ken Burns，zoompan）。

    过渡/空镜/环境镜头用静态图 + 运镜代替视频生成，成本趋近于零、
    效果接近（见 DIRECTOR_GUIDE 镜头分层策略）。

    - ``zoom``: in（推近 1.0→1.25）/ out（拉远 1.25→1.0）/ none。
    - ``pan``: center / left（右→左）/ right（左→右）/ up / down。
    - ``width``/``height``：输出画布。默认 1920x1080 只是直调兜底；compose 管线
      （``compose_planner.realize_ken_burns``）按 output_profile 或源图实测传入，
      不假设横屏 1080p。
    - 输出 H.264（crf 18）yuv420p；``audio_path`` 可选叠加环境音/背景音。
    """
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    if not image_path.exists():
        raise ComposError(f"图片不存在: {image_path}")
    output.parent.mkdir(parents=True, exist_ok=True)

    frames = max(1, int(round(duration * fps)))
    zoom_step = 0.25 / frames
    zoom_expr = {
        "in": f"min(zoom+{zoom_step:.6f},1.25)",
        "out": f"if(eq(on,0),1.25,max(zoom-{zoom_step:.6f},1.0))",
        "none": "1.0",
    }.get(zoom, f"min(zoom+{zoom_step:.6f},1.25)")
    x_expr, y_expr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if pan == "left":
        x_expr = f"(iw-iw/zoom)*(1-on/{frames})"
    elif pan == "right":
        x_expr = f"(iw-iw/zoom)*(on/{frames})"
    elif pan == "up":
        y_expr = f"(ih-ih/zoom)*(1-on/{frames})"
    elif pan == "down":
        y_expr = f"(ih-ih/zoom)*(on/{frames})"

    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={frames}:s={width}x{height}:fps={fps}"
    )
    cmd = [ffmpeg, "-y", "-loop", "1", "-i", str(image_path)]
    if audio_path is not None:
        if not audio_path.exists():
            raise ComposError(f"音轨不存在: {audio_path}")
        cmd += ["-i", str(audio_path)]
    cmd += [
        "-vf", vf,
        "-t", f"{duration:.2f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
    ]
    if audio_path is not None:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    cmd += ["-movflags", "+faststart", str(output)]
    _run(cmd, timeout=timeout)
    return output


def _has_audio_stream(path: Path) -> bool:
    try:
        info = probe(path)
    except (ComposError, OSError, TypeError, ValueError):
        return False
    for stream in info.get("streams") or []:
        if isinstance(stream, dict) and stream.get("codec_type") == "audio":
            return True
    return False


def resolve_lut_file(lut_id: str | Path | None) -> Path | None:
    """catalog id（``luts/dark-moody``）或 ``.cube`` 路径 → 磁盘文件。"""
    raw = str(lut_id or "").strip()
    if not raw:
        return None
    p = Path(raw)
    if p.is_file():
        return p
    try:
        from lib.asset_catalog import get_catalog

        cat = get_catalog()
        stem = p.stem if p.suffix else raw.replace("\\", "/").rsplit("/", 1)[-1]
        for hit in cat.by_category("luts"):
            hid = str(hit.get("id") or "").strip()
            file_val = str(hit.get("file") or "").strip()
            if hid == raw or hid.endswith("/" + stem) or Path(file_val).stem == stem:
                dest = cat.root / file_val
                if dest.is_file():
                    return dest
    except (OSError, TypeError, ValueError):
        pass
    return None


_FONT_EXTS = (".ttf", ".otf", ".ttc")

# 平台系统字体兜底：drawtext 不给 fontfile 时依赖 fontconfig，Windows 上
# 常直接报 "Fontconfig error: Cannot load default config file" 而没有片头。
_SYSTEM_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",          # 微软雅黑（简体中文）
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",        # 黑体
    "C:/Windows/Fonts/simsun.ttc",        # 宋体
    "C:/Windows/Fonts/Deng.ttf",          # 等线
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def resolve_font_file(fontfile: str | Path | None = None) -> Path | None:
    """字体解析：显式路径 → 资产库 ``fonts`` → 平台系统字体 → None。

    ``None`` 表示找不到任何可用字体，调用方应给出可读错误而不是让 ffmpeg
    去撞 fontconfig（``assets/scripts/fetch_assets.py --fonts`` 可补齐资产库字体）。
    """
    raw = str(fontfile or "").strip()
    if raw:
        p = Path(raw)
        if p.is_file():
            return p
    try:
        from lib.asset_catalog import get_catalog

        cat = get_catalog()
        for hit in cat.by_category("fonts"):
            if not hit.get("available"):
                continue
            cand = cat.root / str(hit.get("file") or "")
            if cand.suffix.lower() in _FONT_EXTS and cand.is_file():
                return cand
    except (OSError, TypeError, ValueError):
        pass
    for cand in _SYSTEM_FONT_CANDIDATES:
        p = Path(cand)
        if p.is_file():
            return p
    return None


def font_filter_path(fontfile: Path) -> str:
    """drawtext ``fontfile=`` 的值：filtergraph 内需要双重转义（同 LUT）。

    保留成独立名字是为了让 drawtext 的调用点自解释；实现与 ``filter_path`` 相同。
    """
    return filter_path(fontfile)


def _media_duration(path: Path | None) -> float:
    if path is None:
        return 0.0
    try:
        return float((probe(path).get("format") or {}).get("duration") or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _bgm_volume_chain(in_label: str, out_label: str, volume: float, *, video_dur: float, music_dur: float) -> str:
    """裁到 min(片长, 曲长)，头尾淡化；时长未知则只做 volume（兼容假文件测试）。"""
    target = 0.0
    if video_dur > 0 and music_dur > 0:
        target = min(video_dur, music_dur)
    elif video_dur > 0:
        target = video_dur
    elif music_dur > 0:
        target = music_dur
    if target <= 0:
        return f"{in_label}volume={volume}{out_label}"
    fade = min(1.5, max(target / 4.0, 0.05))
    fade_out_at = max(target - fade, 0.0)
    return (
        f"{in_label}atrim=0:{target:.3f},asetpts=PTS-STARTPTS,volume={volume},"
        f"afade=t=in:st=0:d={fade:.2f},afade=t=out:st={fade_out_at:.3f}:d={fade:.2f}{out_label}"
    )


def mix_audio(
    video: Path,
    narration: Path | None,
    music: Path | None,
    output: Path,
    *,
    music_volume: float = 0.25,
    narration_volume: float = 1.0,
    ducking: bool = False,
    loudnorm: bool = False,
    loudness_target: float = -14.0,
    mix_source_audio: bool = False,
    source_audio_volume: float = 1.0,
    music_segments: list[dict[str, Any]] | None = None,
) -> Path:
    """视频 + 旁白 + 配乐混音：旁白为主轨，配乐自动压低。

    - ``ducking=True``：用 sidechaincompress 做**旁白触发式音乐闪避**
      （旁白起 → 音乐自动降 -6~-9dB，旁白停 → 恢复），优于固定压低。
    - ``loudnorm=True``：输出前做响度标准化（loudnorm，默认 -14 LUFS，
      与 YouTube/B 站/抖音标准一致）。
    - ``mix_source_audio=True``：把视频自带音轨（place_audio 叠的 SFX）混进成品。
    - ``music_segments``：≥2 条带时间窗的 BGM 用 adelay+amix ``duration=longest``。
      此时不要再传 ``music``（同一床会叠两遍）。
    - 单条 ``music``：按成片/乐曲较短者裁切，头尾淡化；曲短于片则不循环。
    - 默认（ducking=False, loudnorm=False）保持原有固定音量行为，向后兼容。
    """
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    output.parent.mkdir(parents=True, exist_ok=True)
    video_dur = _media_duration(video)
    music_dur = _media_duration(Path(music) if music else None)

    segments = [
        s for s in (music_segments or [])
        if isinstance(s, dict) and s.get("path") and Path(str(s["path"])).exists()
    ]
    if len(segments) >= 2:
        music = None

    inputs: list[str] = ["-y", "-i", str(video)]
    narr_i: int | None = None
    music_i: int | None = None
    seg_indices: list[int] = []
    next_i = 1
    if narration:
        inputs += ["-i", str(narration)]
        narr_i = next_i
        next_i += 1
    if len(segments) >= 2:
        for seg in segments:
            inputs += ["-i", str(seg["path"])]
            seg_indices.append(next_i)
            next_i += 1
    elif music:
        inputs += ["-i", str(music)]
        music_i = next_i

    use_bed = bool(
        mix_source_audio
        and (narration or music or len(seg_indices) >= 2)
        and _has_audio_stream(video)
    )

    parts: list[str] = []
    maps = ["-map", "0:v:0"]
    has_seg_music = len(seg_indices) >= 2
    if has_seg_music:
        labels: list[str] = []
        for i, (seg, idx) in enumerate(zip(segments, seg_indices)):
            delay_ms = max(int(float(seg.get("start_seconds") or 0) * 1000), 0)
            vol = float(seg["volume"]) if seg.get("volume") not in (None, "") else music_volume
            start = float(seg.get("start_seconds") or 0)
            end_raw = seg.get("end_seconds")
            try:
                dur = float(end_raw) - start if end_raw not in (None, "") else 0.0
            except (TypeError, ValueError):
                dur = 0.0
            chain: list[str] = []
            if dur > 0:
                chain.append(f"atrim=0:{dur:.3f}")
                chain.append("asetpts=PTS-STARTPTS")
            chain.append(f"volume={vol}")
            if delay_ms:
                chain.append(f"adelay={delay_ms}|{delay_ms}")
            parts.append(f"[{idx}:a]" + ",".join(chain) + f"[bgm{i}]")
            labels.append(f"[bgm{i}]")
        parts.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0[m]")

    if use_bed:
        parts.append(f"[0:a]volume={source_audio_volume}[bed]")
        if narr_i is not None:
            parts.append(f"[{narr_i}:a]volume={narration_volume}[n]")
        if has_seg_music:
            music_lab = "[m]"
            if ducking and narr_i is not None:
                parts.append(
                    "[m][n]sidechaincompress="
                    "threshold=0.02:ratio=8:attack=20:release=400[duck]"
                )
                music_lab = "[duck]"
        elif music_i is not None:
            parts.append(
                _bgm_volume_chain(
                    f"[{music_i}:a]", "[m]", music_volume,
                    video_dur=video_dur, music_dur=music_dur,
                )
            )
            if ducking and narr_i is not None:
                parts.append(
                    "[m][n]sidechaincompress="
                    "threshold=0.02:ratio=8:attack=20:release=400[duck]"
                )
                music_lab = "[duck]"
            else:
                music_lab = "[m]"
        else:
            music_lab = ""
        mix_in = ["[bed]"]
        if narr_i is not None:
            mix_in.append("[n]")
        if music_lab:
            mix_in.append(music_lab)
        parts.append("".join(mix_in) + f"amix=inputs={len(mix_in)}:duration=first:dropout_transition=2[a]")
        maps += ["-map", "[a]"]
    elif narration and (music_i is not None or has_seg_music):
        parts.append(f"[{narr_i}:a]volume={narration_volume}[n]")
        if not has_seg_music:
            parts.append(
                _bgm_volume_chain(
                    f"[{music_i}:a]", "[m]", music_volume,
                    video_dur=video_dur, music_dur=music_dur,
                )
            )
        if ducking:
            parts.append(
                "[m][n]sidechaincompress="
                "threshold=0.02:ratio=8:attack=20:release=400[duck]"
            )
            parts.append("[n][duck]amix=inputs=2:duration=first:dropout_transition=2[a]")
        else:
            parts.append("[n][m]amix=inputs=2:duration=first:dropout_transition=2[a]")
        maps += ["-map", "[a]"]
    elif narration:
        parts.append(f"[{narr_i}:a]volume={narration_volume}[a]")
        maps += ["-map", "[a]"]
    elif music_i is not None:
        parts.append(
            _bgm_volume_chain(
                f"[{music_i}:a]", "[a]", music_volume,
                video_dur=video_dur, music_dur=music_dur,
            )
        )
        maps += ["-map", "[a]"]
    elif has_seg_music:
        parts.append("[m]anull[a]")
        maps += ["-map", "[a]"]
    else:
        maps += ["-map", "0:a:0?"]

    if loudnorm and narration and parts:
        parts[-1] = (
            parts[-1].rsplit("[a]", 1)[0]
            + f"[tmp];[tmp]loudnorm=I={loudness_target}:TP=-1.5:LRA=11[a]"
        )

    cmd: list[str] = [ffmpeg, *inputs]
    if parts:
        cmd += ["-filter_complex", ";".join(parts)]
    cmd += [*maps, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(output)]
    _run(cmd)
    return output


def overlay_clip_audio(
    video: Path,
    events: list[dict[str, Any]],
    output: Path,
) -> Path:
    """把带 delay 的音效叠到单条 clip。events: path / delay_seconds / volume。"""
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    usable = [e for e in events if e.get("path") and Path(str(e["path"])).exists()]
    output.parent.mkdir(parents=True, exist_ok=True)
    if not usable:
        shutil.copy2(video, output)
        return output

    cmd: list[str] = [ffmpeg, "-y", "-i", str(video)]
    for ev in usable:
        cmd += ["-i", str(ev["path"])]
    parts: list[str] = []
    labels: list[str] = []
    if _has_audio_stream(video):
        parts.append("[0:a]volume=1[bed]")
        labels.append("[bed]")
    for i, ev in enumerate(usable):
        delay_ms = max(int(float(ev.get("delay_seconds") or 0) * 1000), 0)
        vol = float(ev.get("volume") or 0.8)
        idx = i + 1
        if delay_ms:
            parts.append(f"[{idx}:a]volume={vol},adelay={delay_ms}|{delay_ms}[s{i}]")
        else:
            parts.append(f"[{idx}:a]volume={vol}[s{i}]")
        labels.append(f"[s{i}]")
    if len(labels) == 1:
        parts.append(f"{labels[0]}volume=1[a]")
    else:
        parts.append("".join(labels) + f"amix=inputs={len(labels)}:duration=first:dropout_transition=0[a]")
    cmd += [
        "-filter_complex", ";".join(parts),
        "-map", "0:v:0", "-map", "[a]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", str(output),
    ]
    _run(cmd)
    return output


class FFmpegCompose(BaseTool):
    """合成工具：按 operation 分派到上面的引擎函数。"""

    name = "ffmpeg_compose"
    version = "0.1.0"
    capability = "compose"
    provider = "ffmpeg"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["operation"],
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "concat", "trim", "burn_subtitles", "apply_lut", "apply_profile",
                    "mix_audio", "assemble", "plan_narration", "assemble_narration",
                    "ken_burns", "normalize", "spatial", "blend_layer", "speed",
                    "showcase_card", "cut_silence", "auto_reframe", "extract_last_frame",
                    "title_card", "lower_third", "retake_segment",
                ],
            },
            "clips": {"type": "array", "items": {"type": "string"}},
            "input_path": {"type": "string"},
            "image_path": {"type": "string", "description": "ken_burns 的静态图输入"},
            "audio_path": {"type": "string", "description": "ken_burns 可选叠加的环境音/背景音"},
            "duration_seconds": {"type": "number", "description": "ken_burns 输出时长（秒）"},
            "zoom": {"type": "string", "enum": ["in", "out", "none"], "default": "in"},
            "pan": {"type": "string", "enum": ["center", "left", "right", "up", "down"], "default": "center"},
            "layout": {"type": "string", "enum": ["side_by_side", "vertical_stack", "picture_in_picture"], "default": "side_by_side"},
            "pip_position": {"type": "string", "enum": ["top_left", "top_right", "bottom_left", "bottom_right"], "default": "bottom_right"},
            "overlay_path": {"type": "string", "description": "blend_layer 的叠加层输入"},
            "blend_mode": {"type": "string", "default": "screen", "description": "blend 模式：screen/overlay/multiply/add/softlight/hardlight/dodge/burn..."},
            "blend_opacity": {"type": "number", "default": 1.0},
            "speed_factor": {"type": "number", "default": 1.5, "description": "变速因子（>1 加速，<1 慢放）"},
            "title": {"type": "string", "description": "showcase_card / title_card / lower_third 文本"},
            "start_seconds": {"type": "number", "description": "lower_third 起始秒（片头结束后）"},
            "card_width": {"type": "integer", "default": 1080},
            "card_height": {"type": "integer", "default": 1920},
            "width": {"type": "integer", "description": "ken_burns 输出宽（缺省 1920；compose 管线按 output_profile 或源图实测传）"},
            "height": {"type": "integer", "description": "ken_burns 输出高（缺省 1080；compose 管线按 output_profile 或源图实测传）"},
            "fontfile": {"type": "string", "description": "showcase_card 标题字体文件路径（可选）"},
            "silence_threshold_db": {"type": "number", "default": -35.0},
            "silence_min_duration": {"type": "number", "default": 0.5},
            "silence_action": {"type": "string", "enum": ["remove", "mark"], "default": "remove"},
            "reframe_target": {"type": "string", "enum": ["9:16", "16:9", "1:1", "4:3", "3:4", "21:9"], "default": "9:16"},
            "reframe_mode": {"type": "string", "enum": ["center", "face"], "default": "center"},
            "output_path": {"type": "string"},
            "srt_path": {"type": "string"},
            "lut_path": {"type": "string", "description": "apply_lut 的 .cube 调色表路径"},
            "lut_strength": {"type": "number", "default": 1.0, "description": "LUT 混合强度 0-1"},
            "profile": {"type": "string", "description": "apply_profile 的平台档案名（youtube_landscape 等）"},
            "project_dir": {"type": "string", "description": "未传 profile 时读取管线 default_profile"},
            "allow_non_cut": {"type": "boolean", "description": "cut_only 管线下允许非硬切"},
            "transition_policy": {"type": "string", "description": "覆盖管线 transition_policy"},
            "edit_decisions_path": {"type": "string", "description": "assemble 的 edit_decisions 产物路径（与 edit_decisions 二选一）"},
            "sections": {"type": "array", "items": {"type": "object"}, "description": "plan_narration/assemble_narration 的旁白段 [{id, narration_audio}]"},
            "gap_seconds": {"type": "number", "default": 0.4, "description": "配音段间静音间隔（秒）"},
            "narration_path": {"type": "string"},
            "music_path": {"type": "string"},
            "music_segments": {
                "type": "array",
                "items": {"type": "object"},
                "description": "≥2 条带时间窗的 BGM；与 music_path 互斥，amix duration=longest",
            },
            "music_volume": {"type": "number", "default": 0.25},
            "ducking": {"type": "boolean", "default": False, "description": "旁白触发式音乐闪避。mix_audio 默认关；assemble 默认开"},
            "loudnorm": {"type": "boolean", "default": False, "description": "响度标准化（-14 LUFS）。mix_audio 默认关；assemble 默认开"},
            "mix_source_audio": {"type": "boolean", "default": False, "description": "把视频自带音轨（place_audio 的 SFX）混进 assemble"},
            "start_seconds": {"type": "number"},
            "duration_seconds": {"type": "number"},
            "edit_decisions": {"type": "object"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if check_ffmpeg() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        operation = inputs.get("operation")
        if check_ffmpeg() is None:
            return ToolResult(success=False, error="缺少 ffmpeg（安装 FFmpeg 并加入 PATH）")
        try:
            return self._dispatch(operation, inputs)
        except ComposError as exc:
            return ToolResult(success=False, error=str(exc))

    def _dispatch(self, operation: str, inputs: dict[str, Any]) -> ToolResult:
        if operation == "concat":
            clips = [Path(p) for p in inputs.get("clips", [])]
            missing = [str(p) for p in clips if not p.exists()]
            if missing:
                return ToolResult(success=False, error=f"片段不存在: {missing[:3]}")
            out = concat_videos(clips, Path(inputs.get("output_path", "concat.mp4")))
            return ToolResult(success=True, data={"output": str(out), **probe(out)})

        if operation == "trim":
            src, out = Path(inputs["input_path"]), Path(inputs.get("output_path", "trim.mp4"))
            if not src.exists():
                return ToolResult(success=False, error=f"输入不存在: {src}")
            trim_clip(src, out, float(inputs.get("start_seconds", 0)), float(inputs.get("duration_seconds", 5)))
            return ToolResult(success=True, data={"output": str(out), **probe(out)})

        if operation == "retake_segment":
            src = Path(inputs.get("input_path") or "")
            repl = Path(inputs.get("replacement_path") or "")
            out = Path(inputs.get("output_path") or "retake.mp4")
            if not src.exists():
                return ToolResult(success=False, error=f"原片不存在: {src}")
            if not repl.exists():
                return ToolResult(success=False, error=f"替换片段不存在: {repl}")
            retake_segment(
                src, repl, out,
                float(inputs.get("start_seconds") or 0),
                float(inputs.get("duration_seconds") or 0),
            )
            return ToolResult(success=True, data={"output": str(out), **probe(out)})

        if operation == "burn_subtitles":
            src, srt, out = Path(inputs["input_path"]), Path(inputs["srt_path"]), Path(inputs.get("output_path", "subs.mp4"))
            burn_subtitles(src, srt, out)
            return ToolResult(success=True, data={"output": str(out), **probe(out)})

        if operation == "apply_lut":
            src, out = Path(inputs["input_path"]), Path(inputs.get("output_path", "graded.mp4"))
            lut_resolved = resolve_lut_file(inputs.get("lut_path"))
            if lut_resolved is None:
                return ToolResult(success=False, error=f"LUT 不存在: {inputs.get('lut_path')}")
            apply_lut(
                src, lut_resolved, out,
                strength=float(inputs.get("lut_strength", 1.0)),
            )
            return ToolResult(success=True, data={"output": str(out), "lut": str(lut_resolved), **probe(out)})

        if operation == "apply_profile":
            src, out = Path(inputs["input_path"]), Path(inputs.get("output_path", "profiled.mp4"))
            profile = inputs.get("profile")
            if not profile:
                from montage.engine.policy import infer_project_dir, load_pipeline_settings

                proj = inputs.get("project_dir")
                if not proj:
                    inferred = infer_project_dir(inputs.get("input_path"), inputs.get("output_path"))
                    proj = str(inferred) if inferred else None
                if proj:
                    profile = load_pipeline_settings(proj).get("default_profile")
            if not profile:
                return ToolResult(
                    success=False,
                    error="apply_profile 需要 profile，或 project_dir（读取管线 default_profile）",
                )
            apply_profile(src, profile, out)
            return ToolResult(success=True, data={"output": str(out), "profile": profile, **probe(out)})

        if operation == "mix_audio":
            video = Path(inputs["input_path"])
            narration = Path(inputs["narration_path"]) if inputs.get("narration_path") else None
            music = Path(inputs["music_path"]) if inputs.get("music_path") else None
            segs = inputs.get("music_segments") if isinstance(inputs.get("music_segments"), list) else None
            out = mix_audio(
                video, narration, music, Path(inputs.get("output_path", "mixed.mp4")),
                music_volume=float(inputs.get("music_volume", 0.25)),
                ducking=bool(inputs.get("ducking", False)),
                loudnorm=bool(inputs.get("loudnorm", False)),
                mix_source_audio=bool(inputs.get("mix_source_audio", False)),
                music_segments=segs,
            )
            return ToolResult(success=True, data={"output": str(out), **probe(out)})

        if operation == "assemble":
            return self._assemble(inputs)

        if operation in ("plan_narration", "assemble_narration"):
            return self._narration(operation, inputs)

        if operation == "ken_burns":
            src = Path(inputs["image_path"])
            if not src.exists():
                return ToolResult(success=False, error=f"图片不存在: {src}")
            out = ken_burns(
                src,
                Path(inputs.get("output_path", "renders/kenburns.mp4")),
                float(inputs.get("duration_seconds", 5)),
                zoom=inputs.get("zoom", "in"),
                pan=inputs.get("pan", "center"),
                audio_path=Path(inputs["audio_path"]) if inputs.get("audio_path") else None,
                **{k: int(inputs[k]) for k in ("width", "height") if inputs.get(k)},
            )
            return ToolResult(success=True, data={"output": str(out), "mode": "ken_burns", **probe(out)})

        if operation in ("normalize", "spatial", "blend_layer", "speed", "showcase_card",
                         "cut_silence", "auto_reframe", "title_card", "lower_third"):
            return self._effects(operation, inputs)

        if operation == "extract_last_frame":
            src = Path(inputs["input_path"])
            out = Path(inputs.get("output_path") or src.with_name(src.stem + "_last.jpg"))
            extract_last_frame(src, out)
            return ToolResult(success=True, data={"output": str(out), **probe(out)})

        return ToolResult(success=False, error=f"未知 operation: {operation}")

    def _effects(self, operation: str, inputs: dict[str, Any]) -> ToolResult:
        """剪辑特效分发（自创实现，见 montage/compose/effects.py）。"""
        from montage.compose import effects

        out = Path(inputs.get("output_path") or f"renders/{operation}.mp4")
        try:
            if operation == "normalize":
                if not inputs.get("input_path"):
                    return ToolResult(success=False, error="'input_path' 必填")
                p = effects.normalize_clip(
                    inputs["input_path"], out,
                    width=int(inputs.get("card_width", 1920) or 1920),
                    height=int(inputs.get("card_height", 1080) or 1080),
                    fps=30,
                )
                return ToolResult(success=True, data={"output": str(p), **probe(p)})

            if operation == "spatial":
                clips = [Path(p) for p in inputs.get("clips", [])]
                if len(clips) < 2:
                    return ToolResult(success=False, error="spatial 需要至少 2 个 clips")
                missing = [str(p) for p in clips if not p.exists()]
                if missing:
                    return ToolResult(success=False, error=f"片段不存在: {missing[:3]}")
                p = effects.spatial_compose(
                    clips, out,
                    layout=inputs.get("layout", "side_by_side"),
                    pip_position=inputs.get("pip_position", "bottom_right"),
                )
                return ToolResult(success=True, data={"output": str(p), "layout": inputs.get("layout"), **probe(p)})

            if operation == "blend_layer":
                if not (inputs.get("input_path") and inputs.get("overlay_path")):
                    return ToolResult(success=False, error="blend_layer 需要 input_path 与 overlay_path")
                p = effects.blend_layers(
                    inputs["input_path"], inputs["overlay_path"], out,
                    mode=inputs.get("blend_mode", "screen"),
                    opacity=float(inputs.get("blend_opacity", 1.0)),
                )
                return ToolResult(success=True, data={"output": str(p), "mode": inputs.get("blend_mode"), **probe(p)})

            if operation == "speed":
                if not inputs.get("input_path"):
                    return ToolResult(success=False, error="'input_path' 必填")
                p = effects.change_speed(
                    inputs["input_path"], out,
                    factor=float(inputs.get("speed_factor", 1.5)),
                )
                return ToolResult(success=True, data={"output": str(p), "factor": inputs.get("speed_factor"), **probe(p)})

            if operation == "showcase_card":
                if not inputs.get("input_path"):
                    return ToolResult(success=False, error="'input_path' 必填")
                p = effects.showcase_card(
                    inputs["input_path"], out,
                    title=inputs.get("title", ""),
                    width=int(inputs.get("card_width", 1080)),
                    height=int(inputs.get("card_height", 1920)),
                    fontfile=inputs.get("fontfile"),
                )
                return ToolResult(success=True, data={"output": str(p), **probe(p)})

            if operation == "cut_silence":
                if not inputs.get("input_path"):
                    return ToolResult(success=False, error="'input_path' 必填")
                result = effects.cut_silence(
                    inputs["input_path"], out,
                    threshold_db=float(inputs.get("silence_threshold_db", -35.0)),
                    min_duration=float(inputs.get("silence_min_duration", 0.5)),
                    action=inputs.get("silence_action", "remove"),
                )
                if inputs.get("silence_action") == "mark":
                    return ToolResult(success=True, data=result)
                return ToolResult(success=True, data={**result, "output": str(out), **probe(out)})

            if operation == "auto_reframe":
                if not inputs.get("input_path"):
                    return ToolResult(success=False, error="'input_path' 必填")
                result = effects.auto_reframe(
                    inputs["input_path"], out,
                    target=inputs.get("reframe_target", "9:16"),
                    mode=inputs.get("reframe_mode", "center"),
                )
                return ToolResult(success=True, data={**result, **probe(out)})

            if operation == "title_card":
                p = effects.title_card(
                    inputs.get("input_path") or "",
                    out,
                    title=str(inputs.get("title") or ""),
                    duration=float(inputs.get("duration_seconds") or 2),
                    fontfile=inputs.get("fontfile"),
                )
                return ToolResult(success=True, data={"output": str(p), **probe(p)})

            if operation == "lower_third":
                if not inputs.get("input_path"):
                    return ToolResult(success=False, error="'input_path' 必填")
                p = effects.lower_third(
                    inputs["input_path"], out,
                    text=str(inputs.get("title") or inputs.get("text") or ""),
                    start_seconds=float(inputs.get("start_seconds") or 0),
                    duration=float(inputs.get("duration_seconds") or 4),
                    fontfile=inputs.get("fontfile"),
                )
                return ToolResult(success=True, data={"output": str(p), **probe(out)})

        except ComposError as exc:
            return ToolResult(success=False, error=str(exc))
        return ToolResult(success=False, error=f"未知特效: {operation}")

    def _narration(self, operation: str, inputs: dict[str, Any]) -> ToolResult:
        """配音装配：plan_narration（规划时间轴，不写盘）/ assemble_narration（拼接音轨）。"""
        from montage.compose import narration

        sections = inputs.get("sections") or []
        if not sections:
            return ToolResult(success=False, error="'sections' 必填（[{id, narration_audio}]）")
        gap = float(inputs.get("gap_seconds", 0.4))
        try:
            if operation == "plan_narration":
                timeline = narration.plan_narration(sections, gap_seconds=gap)
                return ToolResult(
                    success=True,
                    data={"timeline": timeline, "count": len(timeline), "gap_seconds": gap},
                )
            out = narration.assemble_narration(
                sections,
                Path(inputs.get("output_path", "renders/narration.mp3")),
                gap_seconds=gap,
            )
            return ToolResult(success=True, data=out)
        except narration.ComposError as exc:
            return ToolResult(success=False, error=str(exc))

    def _assemble(self, inputs: dict[str, Any]) -> ToolResult:
        """assemble：edit_decisions.cuts[] 顺序拼接 + 可选转场 + 旁白/配乐混音。

        数据来源二选一：直接传 ``edit_decisions``，或传 ``edit_decisions_path``
        （compose 阶段先产出 ``edit_decisions`` 产物，再由 assemble 读取）。
        cuts[].transition 定义片段间转场（cut/crossfade/fade_black/wipe...），
        有转场定义时走 ``stitch_with_transitions``（xfade 链），否则 concat 硬拼。
        """
        decisions = inputs.get("edit_decisions") or {}
        if not decisions:
            epath = inputs.get("edit_decisions_path")
            if not epath:
                return ToolResult(
                    success=False,
                    error="缺少 edit_decisions 或 edit_decisions_path（compose 阶段先产出 edit_decisions 产物）",
                )
            p = Path(epath)
            if not p.exists():
                return ToolResult(success=False, error=f"edit_decisions 文件不存在: {p}")
            try:
                decisions = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return ToolResult(success=False, error=f"edit_decisions 解析失败: {p}")

        runtime = decisions.get("render_runtime")
        if runtime and runtime != "ffmpeg":
            return ToolResult(
                success=False,
                error=f"assemble 仅支持 render_runtime=ffmpeg，收到 {runtime!r}",
            )

        cuts = decisions.get("cuts") or []
        if not cuts:
            return ToolResult(success=False, error="edit_decisions.cuts 为空")

        allow_non_cut = bool(inputs.get("allow_non_cut") or decisions.get("allow_non_cut"))
        if not allow_non_cut:
            from montage.engine.policy import infer_project_dir, load_pipeline_settings

            policy_name = inputs.get("transition_policy")
            proj = inputs.get("project_dir")
            if not proj:
                inferred = infer_project_dir(
                    inputs.get("edit_decisions_path"),
                    inputs.get("output_path"),
                    *[c.get("clip_path") or c.get("path") or c.get("output") for c in cuts],
                )
                proj = str(inferred) if inferred else None
            if not policy_name and proj:
                policy_name = load_pipeline_settings(proj).get("transition_policy")
            if policy_name == "cut_only":
                cuts = [_force_cut(c) for c in cuts]

        clips = []
        for cut in cuts:
            path = cut.get("clip_path") or cut.get("path") or cut.get("output")
            if not path:
                return ToolResult(success=False, error=f"cut 缺少 clip_path: {cut}")
            clips.append(Path(path))
        missing = [str(p) for p in clips if not p.exists()]
        if missing:
            return ToolResult(success=False, error=f"片段不存在: {missing[:3]}")

        out_dir = Path(inputs.get("output_path") or "renders/final.mp4")
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        joined = out_dir.with_suffix(".joined.mp4")
        # cut 切点即便带着误写的 negative_gap 也只算硬切，避免整片走 xfade
        # 后因 ffmpeg 没有 transition=cut 而直接失败。
        needs_transition = any(needs_transition_at(c) for c in cuts)
        if needs_transition and len(clips) >= 2:
            # cuts[j] 的转场描述 cuts[j-1] → cuts[j]（stitch 内 transitions[j-1]=cuts[j]）
            stitch_with_transitions(clips, cuts, joined)
        else:
            concat_videos(clips, joined)

        segs = inputs.get("music_segments")
        if not isinstance(segs, list):
            segs = decisions.get("music_segments") if isinstance(decisions.get("music_segments"), list) else None
        if isinstance(segs, list) and len(segs) >= 2:
            music_path = None
        else:
            music_path = Path(inputs["music_path"]) if inputs.get("music_path") else None
            segs = None
        final = mix_audio(
            joined,
            Path(inputs["narration_path"]) if inputs.get("narration_path") else None,
            music_path,
            out_dir,
            music_volume=float(inputs.get("music_volume", 0.25)),
            ducking=bool(inputs["ducking"]) if "ducking" in inputs else True,
            loudnorm=bool(inputs["loudnorm"]) if "loudnorm" in inputs else True,
            mix_source_audio=bool(inputs.get("mix_source_audio", False)),
            music_segments=segs,
        )
        info = probe(final)
        duration = 0.0
        fmt = info.get("format") if isinstance(info, dict) else None
        if isinstance(fmt, dict):
            try:
                duration = float(fmt.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0
        # 每镜实测尺寸：Agnes 720P 实为 1280x704、图片 2K 非 1920x1080，
        # 落盘实际像素，下游不得按 1280x720/1920x1080 反推。
        clip_rows: list[dict[str, Any]] = []
        for cut, clip in zip(cuts, clips):
            size = probe_size(clip)
            clip_rows.append({
                "shot_id": str(cut.get("shot_id") or cut.get("to_scene") or ""),
                "scene_id": str(cut.get("scene_id") or ""),
                "path": str(clip),
                "width": size[0] if size else 0,
                "height": size[1] if size else 0,
            })
        out_size = _stream_size(info) or (0, 0)
        report: dict[str, Any] = {
            "output_path": str(final),
            "duration_seconds": duration,
            "encoding": "h264",
            "width": out_size[0],
            "height": out_size[1],
            "clips": clip_rows,
            "ffmpeg_version": ffmpeg_version(),
            "ffmpeg_capabilities": capabilities_snapshot()["capabilities"],
        }
        proj = inputs.get("project_dir")
        if proj:
            from montage.engine.artifacts import ArtifactStore

            store = ArtifactStore(proj)
            # 每图实测尺寸：注册进 manifest 的定妆/首帧/场景/道具图。
            image_rows: list[dict[str, Any]] = []
            manifest = store.read("asset_manifest") or {}
            for item in manifest.get("items") or []:
                if not isinstance(item, dict) or str(item.get("kind") or "") != "image":
                    continue
                path = str(item.get("path") or "")
                size = probe_size(path) if path else None
                image_rows.append({
                    "id": str(item.get("id") or ""),
                    "shot_id": str(item.get("shot_id") or ""),
                    "path": path,
                    "width": size[0] if size else 0,
                    "height": size[1] if size else 0,
                })
            report["images"] = image_rows
            store.write("render_report", report)
        return ToolResult(
            success=True,
            data={
                "output": str(final),
                "clip_count": len(clips),
                "render_runtime": "ffmpeg",
                "render_report": report,
                **info,
            },
        )
