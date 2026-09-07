"""script_fields — 剧本结构化字段的确定性读写（原创）。

- flatten_environment：script.environment（对象或字符串）→ 一句字符串，
  供 visual_details.environment / 提示词构建器消费。
- section_spoken_text：对白预算用的口播文本；优先 lines[].text，回退 narration。
"""

from __future__ import annotations

from typing import Any

_ENV_ORDER = ("location", "space", "lighting", "color_tone", "era", "atmosphere")


def flatten_environment(env: Any) -> str:
    """把 environment 拍扁成一句中文描述；空输入返回空串。"""
    if env is None:
        return ""
    if isinstance(env, str):
        return env.strip()
    if not isinstance(env, dict):
        return str(env).strip()
    parts: list[str] = []
    seen: set[str] = set()
    for key in _ENV_ORDER:
        value = str(env.get(key) or "").strip()
        if value and value not in seen:
            parts.append(value)
            seen.add(value)
    for key, raw in env.items():
        if key in _ENV_ORDER:
            continue
        value = str(raw or "").strip()
        if value and value not in seen:
            parts.append(value)
            seen.add(value)
    return "，".join(parts)


def section_spoken_text(section: dict[str, Any]) -> str:
    """口播核算文本：lines[].text 拼接优先，否则 narration。"""
    lines = section.get("lines") or []
    if isinstance(lines, list) and lines:
        chunks: list[str] = []
        for item in lines:
            if isinstance(item, dict):
                chunks.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                chunks.append(item)
        joined = "".join(chunks).strip()
        if joined:
            return joined
    return str(section.get("narration") or "")
