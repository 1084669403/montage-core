"""subtitle_builder — 字幕生成（时间戳 → SRT/ASS，原创实现）。

豆包 TTS / DashScope ASR 返回字符级时间戳（sentences: [{text, start_seconds,
end_seconds}]），本工具把它们变成可烧录的字幕文件：

- ``format=srt``：标准 SRT。
- ``format=ass``：ASS 样式字幕（思源黑体 + 白字 + 描边 + 安全区），
  直接传给 ``ffmpeg_compose burn_subtitles`` 即可获得成片级排版
  （libass 按扩展名自动识别 .ass）。

自动断行：中文按 ``max_chars_per_line`` 断行，优先在标点后断，避免单行过长。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_DEFAULT_FONT = "Source Han Sans SC"
_DEFAULT_FONT_SIZE = 72
_DEFAULT_MAX_CHARS = 20

# 中文断行优先标点（断在标点之后）
_BREAK_AFTER = set("，。！？；：、）】》…—")
_NO_BREAK_AFTER = set("（【《‘“")


def format_timestamp(seconds: float, *, ass: bool = False) -> str:
    """格式化时间戳：SRT → HH:MM:SS,mmm；ASS → H:MM:SS.cc。"""
    if seconds < 0:
        seconds = 0.0
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    if ass:
        return f"{h}:{m:02d}:{s:02d}.{ms // 10:02d}"
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def wrap_text(text: str, max_chars: int = _DEFAULT_MAX_CHARS) -> list[str]:
    """中文断行：每行 ≤ max_chars，优先在标点后断；标点不落行首。"""
    if not text:
        return []
    if max_chars <= 0:
        return [text]
    lines: list[str] = []
    current = ""
    for ch in text:
        if len(current) >= max_chars and ch not in _NO_BREAK_AFTER:
            # 当前行已满：若 ch 是结尾标点则并入本行，否则开新行
            if ch in _BREAK_AFTER:
                lines.append(current + ch)
                current = ""
                continue
            lines.append(current)
            current = ch
            continue
        # 满行且 ch 是禁行首标点：并入上一行或本行
        if len(current) >= max_chars and ch in _NO_BREAK_AFTER:
            lines.append(current)
            current = ch
            continue
        current += ch
    if current:
        lines.append(current)
    return lines


def timestamps_to_srt(
    sentences: list[dict[str, Any]],
    *,
    max_chars_per_line: int = _DEFAULT_MAX_CHARS,
) -> str:
    """[{text, start_seconds, end_seconds}] → SRT 文本。"""
    blocks: list[str] = []
    for idx, sent in enumerate(sentences, start=1):
        text = str(sent.get("text") or "").strip()
        if not text:
            continue
        start = float(sent.get("start_seconds", 0) or 0)
        end = float(sent.get("end_seconds", start) or start)
        body = "\n".join(wrap_text(text, max_chars_per_line))
        blocks.append(
            f"{idx}\n"
            f"{format_timestamp(start)} --> {format_timestamp(end)}\n"
            f"{body}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def timestamps_to_ass(
    sentences: list[dict[str, Any]],
    *,
    max_chars_per_line: int = _DEFAULT_MAX_CHARS,
    font: str = _DEFAULT_FONT,
    font_size: int = _DEFAULT_FONT_SIZE,
) -> str:
    """[{text, start_seconds, end_seconds}] → ASS 文本（样式字幕）。

    样式：白字 + 黑描边 + 底部安全区；字体默认思源黑体（见 assets/fonts）。
    """
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: 1920\n"
        f"PlayResY: 1080\n"
        "ScaledBorderAndShadow: yes\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{font_size},&H00FFFFFF,&H000000FF,&H00101010,"
        f"&H80000000,0,0,0,0,100,100,0,0,1,3,2,2,80,80,100,1\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    events: list[str] = []
    for sent in sentences:
        text = str(sent.get("text") or "").strip()
        if not text:
            continue
        start = float(sent.get("start_seconds", 0) or 0)
        end = float(sent.get("end_seconds", start) or start)
        body = "\\N".join(wrap_text(text, max_chars_per_line))
        # ASS 转义：{} 与 \n
        body = body.replace("\\", "\\\\").replace("{", "（").replace("}", "）")
        events.append(
            f"Dialogue: 0,{format_timestamp(start, ass=True)},"
            f"{format_timestamp(end, ass=True)},Default,,0,0,0,,{body}"
        )
    return header + "\n".join(events) + "\n"


class SubtitleBuilder(BaseTool):
    """时间戳 → SRT/ASS 字幕（含断行与样式）。"""

    name = "subtitle_builder"
    version = "0.1.0"
    capability = "subtitle"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["sentences"],
        "properties": {
            "sentences": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["text", "start_seconds", "end_seconds"],
                },
                "description": "时间戳句段（豆包 TTS / DashScope ASR 输出格式）",
            },
            "format": {"type": "string", "enum": ["srt", "ass"], "default": "srt"},
            "font": {"type": "string", "default": _DEFAULT_FONT},
            "font_size": {"type": "integer", "default": _DEFAULT_FONT_SIZE},
            "max_chars_per_line": {"type": "integer", "default": _DEFAULT_MAX_CHARS},
            "output_path": {"type": "string", "description": "可选：写盘路径（.srt/.ass）"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        sentences = inputs.get("sentences")
        if not isinstance(sentences, list) or not sentences:
            return ToolResult(success=False, error="'sentences' 必填（[{text, start_seconds, end_seconds}]）")
        fmt = inputs.get("format", "srt")
        kwargs = {"max_chars_per_line": int(inputs.get("max_chars_per_line", _DEFAULT_MAX_CHARS))}
        if fmt == "ass":
            kwargs["font"] = inputs.get("font", _DEFAULT_FONT)
            kwargs["font_size"] = int(inputs.get("font_size", _DEFAULT_FONT_SIZE))
            text = timestamps_to_ass(sentences, **kwargs)
        else:
            text = timestamps_to_srt(sentences, **kwargs)

        output = None
        if inputs.get("output_path"):
            path = Path(inputs["output_path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            output = str(path)
        return ToolResult(
            success=True,
            data={
                "format": fmt,
                "text": text,
                "lines": len([l for l in text.splitlines() if l.strip()]),
                "output": output,
                "usage": "ASS 字幕直接传给 ffmpeg_compose 的 burn_subtitles（libass 按扩展名识别）",
            },
        )
