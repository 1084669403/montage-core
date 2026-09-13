"""narration — 配音装配（全新原创代码）。

把脚本各段的旁白音频按时间轴装配成一条 narration 轨：
- 每段旁白文件之间插入可选静音间隔（gap_seconds）
- 返回每段的起止时间（字幕/剪辑对齐用）
- 用 FFmpeg concat 拼接；不修改原始素材
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_engine import (
    ComposError,
    check_ffmpeg,
    concat_file_line,
    probe,
    run_ffmpeg,
)

_SILENCE_GAP_DEFAULT = 0.4


def plan_narration(
    sections: list[dict[str, Any]],
    *,
    gap_seconds: float = _SILENCE_GAP_DEFAULT,
) -> list[dict[str, Any]]:
    """规划配音时间轴（不写文件）：返回每段 {id, audio, start, end, speaker_id, voice}。

    sections: [{"id": "sc01", "narration_audio": "path.mp3", "speaker_id": "...", "voice": "..."}, ...]
    多角色 = 多段不同 speaker_id/voice，仍按顺序拼接（不重叠）。
    """
    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    for section in sections:
        audio = section.get("narration_audio")
        if not audio:
            continue
        duration = _audio_duration(Path(audio))
        timeline.append(
            {
                "id": section.get("id", "?"),
                "audio": str(audio),
                "start_seconds": round(cursor, 3),
                "end_seconds": round(cursor + duration, 3),
                "duration_seconds": round(duration, 3),
                "speaker_id": str(section.get("speaker_id") or ""),
                "voice": str(section.get("voice") or ""),
            }
        )
        cursor += duration + gap_seconds
    return timeline


def _audio_duration(path: Path) -> float:
    if not path.exists():
        raise ComposError(f"旁白文件不存在: {path}")
    info = probe(path)
    return float((info.get("format") or {}).get("duration", 0))


def assemble_narration(
    sections: list[dict[str, Any]],
    output: Path,
    *,
    gap_seconds: float = _SILENCE_GAP_DEFAULT,
) -> dict[str, Any]:
    """拼接各段旁白为一条完整音轨，返回 {output, timeline, total_seconds}。"""
    if check_ffmpeg() is None:
        raise ComposError("缺少 ffmpeg")
    timeline = plan_narration(sections, gap_seconds=gap_seconds)
    if not timeline:
        raise ComposError("没有可装配的旁白段（sections 需含 narration_audio）")

    output.parent.mkdir(parents=True, exist_ok=True)
    list_file = output.with_suffix(".concat.txt")
    lines = [concat_file_line(t["audio"]) for t in timeline]
    if gap_seconds > 0:
        # 每段之间插入静音
        silence = output.parent / "_silence.wav"
        _run_silence(silence, gap_seconds)
        for i in range(len(lines) - 1, 0, -1):
            lines.insert(i, concat_file_line(silence))
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cmd = [
        check_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", str(output),
    ]
    try:
        run_ffmpeg(cmd, timeout=1800)
    except ComposError:
        # 参数不一致时回退重编码
        cmd = [
            check_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c:a", "aac", "-b:a", "192k", str(output),
        ]
        run_ffmpeg(cmd, timeout=1800, error_prefix="配音装配失败")
    total = float((probe(output).get("format") or {}).get("duration", 0))
    return {"output": str(output), "timeline": timeline, "total_seconds": round(total, 3)}


def _run_silence(path: Path, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        check_ffmpeg(), "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=stereo",
        "-t", f"{seconds:.2f}", "-c:a", "pcm_s16le", str(path),
    ]
    run_ffmpeg(cmd, timeout=120, error_prefix="生成静音失败")
