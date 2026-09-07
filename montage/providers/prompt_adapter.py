"""把 visual_prompt_builder 的输出方言化。不替换 builder。

即梦/可灵：在正文外包一层引用语法与对白符号。
Seedance：单镜时间戳 + `@图片N` + `{台词}`；默认去掉【字幕】。
可灵 Omni：`@image_N` 只给 refer_image，跳过 first/last；工牌用 `@element_N`。
Agnes 2.0：passthrough，只做长度校验，不改一字、不截断。
"""

from __future__ import annotations

from typing import Any

from montage.providers.capabilities import kling_omni_elements_cited
from montage.providers.video_prompts import prompt_profile

_SEEDANCE_SUBTITLE = "【字幕】"


def adapt_visual_prompt(
    api_id: str,
    builder: dict[str, Any] | None = None,
    *,
    refs: list[dict[str, Any]] | None = None,
    dialogue: str = "",
    duration_seconds: float | None = None,
    continuity_note: str = "",
) -> dict[str, Any]:
    """同一 builder 输出 → 目标 API 面方言。

    返回 video_prompt / first_frame_prompt / negative_prompt / notes / valid / passthrough。
    builder 缺字段时按空处理；video_prompt 为 None（静图镜）时保持 None。
    """
    profile = prompt_profile(api_id)
    src = builder if isinstance(builder, dict) else {}
    video = src.get("video_prompt")
    first = src.get("first_frame_prompt")
    negative = src.get("negative_prompt")
    notes: list[str] = []

    if profile.get("passthrough"):
        valid = True
        limit = int(profile.get("max_chars") or 0)
        if isinstance(video, str) and limit and len(video) > limit:
            notes.append(f"{api_id} 提示词超过 {limit} 字，应拆镜（本波不截断）")
            valid = False
        return {
            "api_id": profile.get("api_id") or api_id,
            "video_prompt": video,
            "first_frame_prompt": first,
            "negative_prompt": negative,
            "notes": notes,
            "valid": valid,
            "passthrough": True,
        }

    body = "" if video is None else str(video)
    first_out = "" if first is None else str(first)
    negative_out = "" if negative is None else str(negative)
    for token in profile.get("forbidden") or ():
        if token and token in body:
            body = body.replace(str(token), "")
            notes.append(f"已去掉禁忌片段 {token}")
        if token and token in first_out:
            if token in ("<<<image_", "<<<object_"):
                continue
            first_out = first_out.replace(str(token), "")

    if _SEEDANCE_SUBTITLE in body and str(profile.get("audio_syntax") or "") == "{台词}":
        body = body.replace(_SEEDANCE_SUBTITLE, "")
        notes.append("默认不写 Seedance【字幕】符号")

    body = _timestamp_wrap(profile, body, duration_seconds)

    note = str(continuity_note or "").strip()
    if note:
        for token in ("@图片", "<<<image", "{台词}", "【字幕】", "(音乐)"):
            note = note.replace(token, "")
        note = note.strip()
    if note:
        body = f"场记：{note}\n{body}" if body else f"场记：{note}"

    citations = _citation_lines(str(profile.get("citation_syntax") or ""), refs, body=body)
    audio = _audio_block(str(profile.get("audio_syntax") or ""), dialogue, body=body)
    guard = str(profile.get("watermark_guard") or "")
    if guard and guard not in body:
        body = f"{body}，{guard}" if body else guard

    parts = [p for p in (citations, body, audio) if p]
    prompt = "\n".join(parts)
    limit = int(profile.get("max_chars") or 0)
    valid = True
    if limit and len(prompt) > limit:
        notes.append(f"{api_id} 方言超过 {limit} 字，应拆镜或降级（本波不截断）")
        valid = False
    syntax = str(profile.get("citation_syntax") or "")
    if syntax == "@image_N":
        el_n = sum(1 for r in (refs or []) if isinstance(r, dict) and _is_element_ref(r))
        cited, cite_notes = kling_omni_elements_cited(prompt, el_n)
        notes.extend(cite_notes)
        if not cited:
            valid = False
    if not profile.get("negative"):
        negative_out = ""

    return {
        "api_id": profile.get("api_id") or api_id,
        "video_prompt": prompt,
        "first_frame_prompt": first_out,
        "negative_prompt": negative_out,
        "notes": notes,
        "valid": valid,
        "passthrough": False,
    }


def _timestamp_wrap(profile: dict[str, Any], body: str, duration_seconds: float | None) -> str:
    """Seedance 单镜时间戳。禁止把多场写进同一段。"""
    if str(profile.get("style") or "") != "narrative_timestamp":
        return body
    text = str(body or "").strip()
    if not text:
        return body
    stripped = text.lstrip()
    if stripped.startswith("[0s") or stripped.startswith("[0 s"):
        return body
    try:
        sec = int(max(float(duration_seconds or 5), 1))
    except (TypeError, ValueError):
        sec = 5
    return f"[0s-{sec}s] {text}"


_KLING_SKIP_IMAGE = {
    "first_frame", "last_frame", "end_frame", "first", "last",
}


def _is_frame_ref(item: dict[str, Any]) -> bool:
    kind = str(item.get("type") or item.get("kind") or "").strip().lower()
    return kind in _KLING_SKIP_IMAGE


def _is_element_ref(item: dict[str, Any]) -> bool:
    kind = str(item.get("type") or item.get("kind") or "").strip().lower()
    if kind == "element":
        return True
    return item.get("element_id") is not None and str(item.get("element_id")).strip() != ""


def _citation_lines(syntax: str, refs: list[dict[str, Any]] | None, *, body: str = "") -> str:
    items = [r for r in (refs or []) if isinstance(r, dict)]
    if not syntax or not items:
        return ""
    text = str(body or "")
    lines: list[str] = []
    if syntax == "@image_N":
        image_n = 0
        element_n = 0
        for item in items:
            if _is_frame_ref(item):
                continue
            if _is_element_ref(item):
                element_n += 1
                token = f"@element_{element_n}"
            else:
                image_n += 1
                token = f"@image_{image_n}"
            if token not in text and token not in lines:
                lines.append(token)
        return "\n".join(lines)
    if syntax == "@主体名":
        for item in items:
            if _is_frame_ref(item):
                continue
            name = str(item.get("name") or item.get("id") or "").strip()
            if not name:
                continue
            token = f"@{name}" if not name.startswith("@") else name
            if token not in text and token not in lines:
                lines.append(token)
        return "\n".join(lines)
    for idx, item in enumerate(items, start=1):
        role = str(item.get("role") or item.get("purpose") or "参考").strip() or "参考"
        if syntax == "@图片N":
            lines.append(f"@图片{idx}用于{role}，不采用背景")
        elif syntax == "<<<image_N>>>":
            lines.append(f"<<<image_{idx}>>>")
        elif syntax == "<Picture N>":
            lines.append(f"<Picture {idx}>")
    return "\n".join(lines)


def _audio_block(syntax: str, dialogue: str, *, body: str = "") -> str:
    text = str(dialogue or "").strip()
    if not text:
        return ""
    hay = str(body or "")
    if syntax == "{台词}":
        return "{" + text + "}"
    if syntax == "prompt_dialogue":
        if "对白：" in hay:
            return ""
        return f"对白：{text}"
    return ""
