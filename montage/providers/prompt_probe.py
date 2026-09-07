"""prompt_probe — Seedream 生图提示词构建器（探针五段式，结构固定、数值按需）。

风格来源：projects/huapi_liaozhai/style_probe_prompts.md（用户已认可的探针 16 条）。
Seedream 无 negative_prompt 参数，负向词以「负向：」内联在 prompt 末尾。

五段式（顺序固定）：
1. 硬性画风前置（画风类必带；缺省记 warning）
2. 人物/主体（姿势建议视线指向留白侧）
3. 服饰（从场景内取材）
4. 场景与物件（光源方向与色温；物件防歧义）
5. 构图（数值按需：探针基线 62%/头顶不裁切/六成留白仅属探针壁纸脚本；
   定妆照居中、首帧按镜位景别/视向/留白安排，由调用方传 composition，
   缺省不硬塞数值）
6. 风格锚点（渲染技术特征）
7. 负向词收尾（按画风族取 NEGATIVE_TAILS，可覆盖）

shot_runner 的 cast/首帧提示词不经过本构建器（已有 dialect 流水线）；
本模块服务于探针脚本直调出图与手工/半自动定妆、壁纸类图片。
"""

from __future__ import annotations

from typing import Any

# 段落顺序固定；构建器只按此顺序拼接，不做任何内容注入
_SECTION_ORDER = (
    "style_lock",    # 硬性画风前置
    "subject",       # 人物/主体
    "outfit",        # 服饰
    "scene",         # 场景与物件
    "composition",   # 构图（完全由调用方决定，无默认值）
    "style_anchor",  # 风格锚点
)

# 负向尾按画风族预置；可被调用方覆盖
NEGATIVE_TAILS: dict[str, str] = {
    "photoreal": "负向：卡通、动漫、插画、绘画、三维渲染、塑料皮肤、过度磨皮、水印、文字",
    "animation": "负向：照片写实、真人实拍、水印、文字、多出手指、面部扭曲",
    "lineart": "负向：彩色填色、明暗阴影、排线、剪影、三维渲染、渐变、照片写实、水印",
    "woodcut": "负向：多色套色、照片写实、三维渲染、现代数字感、渐变柔光、水印",
}


def build_seedream_prompt(sections: dict[str, Any], *, style_family: str = "") -> str:
    """按五段式顺序拼接；缺省段跳过；画风类缺 style_lock 记 warning（返回值不改）。"""
    import logging

    warnings: list[str] = []
    prompt = _compose(sections, warnings, style_family=style_family)
    for w in warnings:
        logging.getLogger(__name__).warning(w)
    return prompt


def build_seedream_prompt_checked(
    sections: dict[str, Any], *, style_family: str = ""
) -> tuple[str, list[str]]:
    """同 build_seedream_prompt，但返回 (prompt, warnings) 便于测试与调用方呈现。"""
    warnings: list[str] = []
    prompt = _compose(sections, warnings, style_family=style_family)
    return prompt, warnings


def _compose(sections: dict[str, Any], warnings: list[str], *, style_family: str) -> str:
    body = sections if isinstance(sections, dict) else {}
    if style_family and not str(body.get("style_lock") or "").strip():
        warnings.append("画风类任务缺「硬性画风前置」段（style_lock），建议补充")
    parts: list[str] = []
    for key in _SECTION_ORDER:
        text = str(body.get(key) or "").strip()
        if text:
            parts.append(text)
    prompt = "。".join(parts)
    tail = str(body.get("negative") or NEGATIVE_TAILS.get(style_family) or "").strip()
    if tail:
        prompt = f"{prompt}。{tail}" if prompt else tail
    return prompt
