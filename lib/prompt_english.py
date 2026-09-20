"""英文画质层 + 首帧/视频关键词富化 + 静态镜头文案。

从 shot_prompt_builder.py 抽出，原文件保留显式重导出 shim。
只依赖 lib.prompt_phrases（常量）与标准库，不 import 核心构建器。
"""

from __future__ import annotations

import re
from typing import Any

from lib.prompt_phrases import (
    _AUDIO_LOCK_NO_DIALOGUE,
    _AUDIO_LOCK_WITH_DIALOGUE,
    _ENGLISH_NEGATIVE_PROMPT,
    _ENGLISH_VISUAL_BASELINE,
    _FRAME_CONSISTENCY_EN,
    _IMAGE_ENGLISH_GUARD,
    _IMAGE_ENGLISH_NEGATIVE,
    _IMAGE_ENGLISH_VISUAL_BASELINE,
    _MANNER_TIME_RE,
    _MOVEMENT_PHRASES,
    _SHOT_SIZE_PHRASES,
)

def contains_cjk(text: str) -> bool:
    """若字符串包含任意 CJK 字符则返回 True。

    2026-09 全中文政策下的门禁护栏：提示词全链路写死中文，画质字段
    预期是中文（builder 常量与词条均已中文化）。本函数用于校验
    画质/提示词文本确实含中文（含 CJK 为正常）；若文本为纯英文，
    可据此标记「疑似英文回潮」，资产导演的质检门可拒绝该输出。
    绝不要经由翻译器重写量化动作。
    """
    if not text:
        return False
    return any("\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf" for ch in text)


def _append_jimeng_guard(
    prompt: str,
    shot: dict[str, Any],
    provider_max_chars: int | None,
) -> str:
    """追加即梦专用的护栏句（画面禁字一句）。

    即梦路径的收尾追加（在压缩之后）：只加一句「无字幕无文字无水印」。
    - 当 provider 有 ``template_id`` 时不再写运镜（运镜由 recamera 参数控制），
      这里只负责画面禁字。
    - 追加受 ``provider_max_chars`` 保护：超限则整体丢弃，绝不溢出。
    """
    if not prompt:
        return prompt
    guard = "【画面】无字幕无文字无水印"
    candidate = prompt + "。" + guard
    if provider_max_chars is not None and len(candidate) > provider_max_chars:
        return prompt
    return candidate


def _append_english_visual_layer(
    prompt: str,
    shot: dict[str, Any],
    *,
    english_visual: bool,
    agnes_audio: bool,
    provider_max_chars: int | None,
) -> tuple[str, str]:
    """追加压缩后的中文层 + 音频锁定。

    在两个 builder 的最末尾、压缩之后调用，因此注入的元素
    绝不会被丢段处理，也绝不经过 ``_section_assembler`` 的段落
    （这样 ``_split_first_frame_sections`` 无法静默丢弃它们 ——
    首帧图片天然不含它们，使它们仅作用于视频动态侧）。

    追加顺序（最不易被丢弃的放最后）：
      1. 【画质基底】 固定中文画质基线（仅 ``english_visual``）。
      2. 【音频锁定】 中文音频锁定（仅 ``agnes_audio``，无条件）：
         有对白 -> 标准普通话 / 无外语；无对白 ->
         仅环境音 / 无旁白。与【台词】段的存在与否无关
         （无对白的镜头没有【台词】段）。
      3. 【衔接】 中文帧一致性句（仅 ``english_visual``，
         视频镜头），强化末帧交接稳定性。

    长度护栏：追加项是低优先级。若整层会超过
    ``provider_max_chars``，则从最不重要的一项开始裁减
    （【衔接】→【画质基底】→【音频锁定】），直到符合限制。在极端
    限制下，甚至连【音频锁定】也会被丢弃而非溢出 —— 英文
    ``negative_prompt`` 的语音护栏仍作为第二道独立的语音锁定
    防线保留。追加层绝不会把提示词推过 ``provider_max_chars``。

    返回 ``(prompt, negative_prompt)``。当该层激活时
    （``english_visual`` 或 ``agnes_audio``），``negative_prompt``
    是固定的中文护栏字符串，否则为 ``""``。
    """
    if not (english_visual or agnes_audio):
        return prompt, ""

    shot_kind = shot.get("shot_kind", "video")
    ap = shot.get("audio_prompt") or {}
    has_dialogue = bool(ap.get("dialogue"))

    appends: list[str] = []
    if english_visual:
        appends.append(_ENGLISH_VISUAL_BASELINE)
    if agnes_audio:
        appends.append(
            _AUDIO_LOCK_WITH_DIALOGUE if has_dialogue else _AUDIO_LOCK_NO_DIALOGUE
        )
    if english_visual and shot_kind == "video":
        appends.append(_FRAME_CONSISTENCY_EN)

    if provider_max_chars is not None:
        # 从最不重要的追加项开始裁减（【衔接】→【画质基底】→【音频锁定】），
        # 直到整个候选符合限制。极端情况下音频锁定也会被丢弃而非溢出 ——
        # negative_prompt 的语音护栏仍是第二道语音锁定防线。
        # 该层绝不会超出预算。优先级：保留【音频锁定】>【画质基底】>【衔接】。
        def _drop_priority(s: str) -> int:
            if s.startswith("【音频锁定】"):
                return 0  # 保留优先级最高（最后才丢弃）
            if s.startswith("【画质基底】"):
                return 1
            return 2  # 【衔接】最先丢弃

        while appends:
            candidate = prompt + ("。" + "。".join(appends) if appends else "")
            if len(candidate) <= provider_max_chars:
                break
            # 丢弃剩余追加项里最不重要的（【衔接】最先，
            # 【音频锁定】最后）。升序排序 → 优先级最高的值在末尾。
            appends.sort(key=_drop_priority)
            appends.pop()

    joined = "。".join(appends)
    result = prompt + ("。" + joined if joined else "")

    negative = _ENGLISH_NEGATIVE_PROMPT if (english_visual or agnes_audio) else ""
    return result, negative


def _is_stylized_playbook(style_context: dict[str, Any] | None) -> bool:
    """风格化图片 playbook 判定：显式 ``stylized_image`` 优先，字符串兜底。

    2026-09 全中文政策：负面词已中文化，「拒绝写实/3D」的中文表述
    （``写实照片`` / ``3D渲染``）也须被识别；同时保留英文旧值兜底，
    以兼容未打标志的旧项目产物。
    """
    gen = (style_context or {}).get("asset_generation") or {}
    if "stylized_image" in gen:
        return bool(gen["stylized_image"])
    neg = str(gen.get("image_negative_prompt") or "").lower()
    return (
        "photorealistic" in neg
        or "3d render" in neg
        or "写实照片" in neg
        or "3d渲染" in neg
    )


def _image_english_layer_text(style_context: dict[str, Any] | None = None) -> str:
    """实际追加的图片画质层（基线 ± 风格化剔除 + 护栏）。

    2026-09 全中文政策：基线已中文化。常量 ``_IMAGE_ENGLISH_VISUAL_BASELINE``
    绝不被修改。风格化 playbook 只从 *这一次追加* 中剔除「真实质感」/
    「人体结构准确」（原 photorealistic texture / accurate anatomy 的
    中文对应词）。护栏词合并进同一句【画质基底】。
    """
    body = _IMAGE_ENGLISH_VISUAL_BASELINE
    if _is_stylized_playbook(style_context):
        body = body.replace("真实质感、", "").replace("人体结构准确、", "")
    return f"{body}，{_IMAGE_ENGLISH_GUARD}"


def _image_negative_text(
    style_context: dict[str, Any] | None = None,
    *,
    english_visual: bool = False,
) -> str:
    """仅图片的负面提示词。中文图片层关闭时为空。"""
    if not english_visual:
        return ""
    parts = [_IMAGE_ENGLISH_NEGATIVE]
    gen = (style_context or {}).get("asset_generation") or {}
    extra = str(gen.get("image_negative_prompt") or "").strip()
    if extra:
        parts.append(extra)
    return "，".join(parts)


def _append_first_frame_english_layer(
    first_frame_prompt: str,
    provider_max_chars: int | None,
    style_context: dict[str, Any] | None = None,
) -> str:
    """把仅图片的中文画质层追加到首帧提示词。

    当 ``english_visual=True`` 时，在压缩之后、``enrich_first_frame``
    词库注入之后对 ``first_frame_prompt``（视频镜头和图片镜头）调用，
    因此中文画质词叠加在已压缩的中文主体之上，
    绝不会被词库的（参考：...）后缀吞掉。

    追加 ``_image_english_layer_text``（A/B 基线，无「动作自然稳定」，
    外加风格中性护栏）。故意不追加中文【音频锁定】
    （图片没有音频）也不追加视频帧一致性【衔接】句（静态图片）。

    长度护栏：追加层是低优先级 —— 若结果会超过
    ``provider_max_chars``，则整层被丢弃而非挤占首帧图片预算。
    调用方（``build_shot_prompt_pair``）在压缩期间已为这层预留
    空间，因此实际中它会被保留。
    """
    if not first_frame_prompt:
        return first_frame_prompt
    layer = _image_english_layer_text(style_context)
    candidate = first_frame_prompt + "。" + layer
    if provider_max_chars is not None and len(candidate) > provider_max_chars:
        return first_frame_prompt
    return candidate


def default_english_negative_prompt() -> str:
    """返回固定的中文负面提示词（画质 + 语音护栏）。

    2026-09 全中文政策：原英文负面词已逐项中文化，函数名保留兼容
    （agnes.py 调用点不改）。
    Agnes 的 ``negative_prompt`` 字段（最大 500 字符）是第二道独立的
    语音锁定防线 —— 对无对白镜头尤其关键，因为正面提示词里的
    【音频锁定】句可能无法完全阻止英文旁白。在资产导演层引用该常量，
    并把它传给 ``video_selector``/``agnes_video``。
    """
    return _ENGLISH_NEGATIVE_PROMPT




_STATIC_SECTION_KEYS = {"角色与外貌", "在场清单", "空镜", "物体与道具", "环境", "光线"}
# P0-8 「特效」是动态专属段（时间点事件，只进 video_prompt；首帧静态图是
# 特效发生前的状态，不携带）。
_DYNAMIC_SECTION_KEYS = {"动作", "台词", "背景音乐", "衔接", "承接", "画外", "声音", "特效"}

_IMAGE_QUALITY_TEXT = (
    "电影级写实质感：皮肤保留微纹理与自然高光（不磨皮），"
    "布料纤维与褶皱清晰，器物有真实反光与使用痕迹；高视觉密度，次要细节可辨"
)
_KEEP_REFERENCE_TEXT = "保留身份、轮廓与构图，只改本镜姿态与光线"
_IMAGE_SECTION_ORDER = (
    "构图",
    "角色与外貌",
    "在场清单",
    "空镜",
    "画外",
    "姿态",
    "物体与道具",
    "环境",
    "风格",
    "光线",
    "层次",
    "镜头",
    "质量",
    "保留",
)
_ENRICH_LEAK_RE = re.compile(r"半身|胸部|对白|肩部|dolly|推近|快切", re.IGNORECASE)
_ENRICH_SOFT_MAX_PHRASES = 6
_ENRICH_SOFT_MAX_CHARS = 1000
_ENRICH_FRAG_SHORT = 180
_ENRICH_FRAG_LONG = 400
_ENRICH_CAT_RANK = {"scenes": 0, "lighting": 1, "styles": 2, "shots": 3}


def _camera_static_text(shot: dict[str, Any]) -> str:
    """用于首帧图片的静态镜头文本：机位角度 + 画面构图。

    显式排除 camera_path（视频运动）和 generation_feasibility
    （可执行动作描述），让首帧图片保持纯静态。
    """
    cine = shot.get("visual_details", {}).get("cinematography") or {}
    parts = []
    angle = cine.get("angle")
    comp = cine.get("frame_composition")
    if angle:
        parts.append(angle)
    if comp:
        parts.append(comp)
    if not parts:
        sl = shot.get("shot_language") if isinstance(shot.get("shot_language"), dict) else {}
        size = sl.get("shot_size")
        if size:
            parts.append(_SHOT_SIZE_PHRASES.get(str(size), str(size)))
    return "，".join(parts)


def _camera_motion_text(shot: dict[str, Any]) -> str:
    """仅视频的运镜文本：camera_path / generation_feasibility。

    用于视频动态提示词，驱动图生视频模型。
    """
    cine = shot.get("visual_details", {}).get("cinematography") or {}
    parts = []
    path = cine.get("camera_path")
    feas = cine.get("generation_feasibility")
    if path:
        parts.append(path)
    if feas:
        parts.append(feas)
    if not parts:
        sl = shot.get("shot_language") if isinstance(shot.get("shot_language"), dict) else {}
        move = sl.get("camera_movement")
        if move:
            parts.append(_MOVEMENT_PHRASES.get(str(move), str(move)))
    return "，".join(parts)


def _strip_manner_timing(manner: str) -> str:
    """从方式中丢弃秒数/步频量；保留 急促/前倾 等。"""
    if not manner:
        return ""
    cleaned = _MANNER_TIME_RE.sub("", manner)
    cleaned = re.sub(r"[，,、]\s*[，,、]+", "，", cleaned)
    return cleaned.strip("，,、 ").strip()


def _subject_beats(subject: dict[str, Any]) -> list[dict[str, Any]]:
    seq = subject.get("action_sequence") or []
    return [b for b in seq if isinstance(b, dict)]


def _pick_subject_beat(
    subject: dict[str, Any],
    pose_beat_id: str | None,
    at_seconds: float | None,
) -> dict[str, Any] | None:
    """为单个主体挑选定格节拍。

    当该主体上存在该 id 时，``pose_beat_id`` 优先；否则回退到
    该主体的开场节拍（绝不抛异常）。``at_seconds`` 使用所在的
    ``[start, end)`` 区间，否则用最近的 ``start``。
    """
    beats = _subject_beats(subject)
    if pose_beat_id:
        for b in beats:
            if b.get("beat_id") == pose_beat_id:
                return b
    if at_seconds is not None and beats:
        t = float(at_seconds)
        for b in beats:
            start = float(b.get("start_seconds", 0.0))
            end = float(b.get("end_seconds", start))
            if start <= t < end:
                return b
        return min(beats, key=lambda b: abs(float(b.get("start_seconds", 0.0)) - t))
    if beats:
        return min(beats, key=lambda b: float(b.get("start_seconds", 0.0)))
    return None


def _opening_pose_text(
    shot: dict[str, Any],
    pose_beat_id: str | None = None,
    at_seconds: float | None = None,
    character_registry: list[dict[str, Any]] | None = None,
) -> str:
    """把每个主体的开场（或选定）节拍定格为静态姿势。

    不带时间线秒数。方式中的时长量会被剔除。后续的
    ``beat_type==still`` 除非经由 ``pose_beat_id`` / ``at_seconds``
    显式选定，否则绝不覆盖开场节拍。
    """
    vd = shot.get("visual_details") or {}
    parts: list[str] = []
    for subj in vd.get("subjects") or []:
        cid = (subj.get("id") or "").strip()
        # 中文显示名优先：提示词里不写 wen_ruchun 这类内部 id。
        name = cid
        for row in character_registry or []:
            if isinstance(row, dict) and str(row.get("id") or "") == cid:
                name = str(row.get("name") or cid)
                break
        beat = _pick_subject_beat(subj, pose_beat_id, at_seconds)
        if beat:
            verb = (beat.get("verb") or "").strip()
            manner = _strip_manner_timing(beat.get("manner") or "")
            emotion = (beat.get("emotion") or "").strip()
            contact = (beat.get("contact") or "").strip()
        else:
            act = subj.get("action") or {}
            verb = (act.get("verb") or "").strip()
            manner = _strip_manner_timing(act.get("manner") or "")
            emotion = (act.get("emotion") or "").strip()
            contact = (act.get("contact") or "").strip()
        if not verb and not emotion and not contact:
            continue
        seg = f"{name} {verb}".strip()
        if manner:
            seg += f"（{manner}）"
        if emotion:
            seg += f"，神情：{emotion}"
        if contact:
            seg += f"，接触：{contact}"
        parts.append(seg)
    return "；".join(parts)


def _section_key(section: str) -> str:
    return section[1:section.index("】")] if "】" in section else ""


def _order_image_sections(sections: list[str]) -> list[str]:
    """把首帧段落重排为官方的 2.1 静态图片结构。"""
    by_key: dict[str, str] = {}
    extras: list[str] = []
    for s in sections:
        key = _section_key(s)
        if key in _IMAGE_SECTION_ORDER:
            by_key[key] = s
        elif s:
            extras.append(s)
    return [by_key[k] for k in _IMAGE_SECTION_ORDER if k in by_key] + extras


def _objects_first_frame_text(shot: dict[str, Any]) -> str:
    """合并顶层物体与主体持有的 id；位置只写一次。"""
    vd = shot.get("visual_details") or {}
    catalog: dict[str, dict[str, Any]] = {}
    for obj in vd.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        oid = str(obj.get("id") or "").strip()
        if oid:
            catalog[oid] = obj

    seen: set[str] = set()
    parts: list[str] = []

    def _add(obj: dict[str, Any], fallback_id: str = "") -> None:
        oid = str(obj.get("id") or fallback_id or "").strip()
        appearance = str(obj.get("appearance") or "").strip()
        position = str(obj.get("position") or "").strip()
        key = oid or appearance
        if not key or key in seen:
            return
        seen.add(key)
        text = appearance or oid
        if position:
            text = f"{text}（{position}）"
        if text:
            parts.append(text)

    for obj in vd.get("objects") or []:
        if isinstance(obj, dict):
            _add(obj)

    for subj in vd.get("subjects") or []:
        for ref in subj.get("objects") or []:
            if not isinstance(ref, str):
                continue
            rid = ref.strip()
            if not rid or rid in seen:
                continue
            if rid in catalog:
                _add(catalog[rid], fallback_id=rid)
            else:
                seen.add(rid)
                parts.append(rid)

    return "、".join(parts)


def _image_style_text(style_context: dict[str, Any] | None, already: str) -> str:
    """静态图用的 playbook 风格：情绪 / 美学 / 最多 2 个锚点。不加前缀。"""
    if not style_context:
        return ""
    identity = style_context.get("identity")
    mood = ""
    if isinstance(identity, dict):
        mood = str(identity.get("mood") or "").strip()
    if not mood:
        mood = str(style_context.get("mood") or "").strip()
    visual = style_context.get("visual_language")
    aesthetic = ""
    if isinstance(visual, dict):
        aesthetic = str(visual.get("aesthetic") or "").strip()
    gen = style_context.get("asset_generation")
    anchors = (gen or {}).get("consistency_anchors") if isinstance(gen, dict) else None
    if not isinstance(anchors, list):
        anchors = []

    candidates: list[str] = []
    for item in (aesthetic, mood, *[str(a).strip() for a in anchors if a]):
        text = (item or "").strip()
        if not text:
            continue
        low = text.lower()
        if "导演" in text or "director" in low:
            continue
        if text in already or text in candidates:
            continue
        if any(text in picked or picked in text for picked in candidates):
            continue
        candidates.append(text)
        if len(candidates) >= 2:
            break
    return "，".join(candidates)


def _classify_depth_layer(text: str) -> str | None:
    if not text:
        return None
    low = text.lower()
    if "前景" in text or "foreground" in low:
        return "前景"
    if "中景" in text or "midground" in low or "mid-ground" in low or "middle ground" in low:
        return "中景"
    if "背景" in text or "background" in low:
        return "背景"
    return None


def _image_depth_text(shot: dict[str, Any]) -> str:
    """把现有位置 token 映射到 前景/中景/背景。绝不凭空造出缺失的层次。"""
    vd = shot.get("visual_details") or {}
    buckets: dict[str, list[str]] = {"前景": [], "中景": [], "背景": []}

    def _put(layer: str | None, label: str) -> None:
        name = (label or "").strip()
        if not layer or not name or name in buckets[layer]:
            return
        buckets[layer].append(name)

    for subj in vd.get("subjects") or []:
        _put(_classify_depth_layer(str(subj.get("position") or "")), str(subj.get("id") or ""))
    for obj in vd.get("objects") or []:
        if not isinstance(obj, dict):
            continue
        _put(
            _classify_depth_layer(str(obj.get("position") or "")),
            str(obj.get("id") or obj.get("appearance") or ""),
        )
    _put(_classify_depth_layer(str(vd.get("environment") or "")), "环境")

    parts = [f"{key}：{'、'.join(names)}" for key, names in buckets.items() if names]
    return "；".join(parts)


def _split_first_frame_sections(
    sections: list[str],
    shot: dict[str, Any],
    *,
    pose_beat_id: str | None = None,
    at_seconds: float | None = None,
    style_context: dict[str, Any] | None = None,
    keep_reference: bool = False,
    dense: bool = True,
    jimeng_prompt: bool = False,
    character_registry: list[dict[str, Any]] | None = None,
) -> tuple[list[str], list[str]]:
    """把组装好的段落拆分为 (首帧静态, 视频动态)。

    首帧官方顺序：
    角色 → 姿态 → 物体 → 环境 → 风格 → 光线 → 层次 → 镜头 → 质量 → 保留。
    组装器产出的【镜头】被丢弃并重建（仅 angle + composition）。
    仅图片的段落不会被加进 ``_section_assembler``。

    ``dense`` + ``jimeng_prompt`` 会丢弃固定的【质量】句（「高视觉
    密度，次要细节可辨」）—— 即梦没有英文画质层来承载图片
    画质，所以省略这句套话；画质来自实际的
    主体/环境/光线细节。Agnes 保留【质量】句
    （它的英文画质层是独立的）。
    """
    shot_kind = shot.get("shot_kind", "video")
    static: list[str] = []
    dynamic: list[str] = []

    for s in sections:
        key = _section_key(s)
        if key == "物体与道具":
            continue
        if key in _STATIC_SECTION_KEYS:
            static.append(s)
        elif key in _DYNAMIC_SECTION_KEYS:
            dynamic.append(s)

    extras: list[str] = []
    pose = _opening_pose_text(
        shot, pose_beat_id, at_seconds, character_registry=character_registry,
    )
    if pose:
        extras.append(f"【姿态】 {pose}")
    objects_text = _objects_first_frame_text(shot)
    if objects_text:
        extras.append(f"【物体与道具】 {objects_text}")
    body_so_far = "。".join(static + extras)
    style = _image_style_text(style_context, body_so_far)
    if style:
        extras.append(f"【风格】 {style}")
    depth = _image_depth_text(shot)
    if depth:
        extras.append(f"【层次】 {depth}")
    static_cam = _camera_static_text(shot)
    if static_cam:
        extras.append(f"【镜头】 {static_cam}")
    # 图片【质量】缩短：dense 且即梦时不写（画质交给英文层常量/实际细节）；
    # Agnes 保持原固定句（其英文画质层是独立追加，不冲突）。
    if not (dense and jimeng_prompt):
        extras.append(f"【质量】 {_IMAGE_QUALITY_TEXT}")
    if keep_reference:
        extras.append(f"【保留】 {_KEEP_REFERENCE_TEXT}")

    static = _order_image_sections(static + extras)

    if shot_kind == "video":
        motion_cam = _camera_motion_text(shot)
        if motion_cam:
            dynamic.append(f"【镜头】 {motion_cam}")

    return static, dynamic


def _extract_first_frame_keywords(shot: dict[str, Any]) -> str:
    """从镜头的静态（图片）关切中构建词库查询字符串。

    抽取描述 *首帧图片* 必须包含内容的字段：
    环境 / 光线 / 镜头（角度 + 构图）/ 主体情绪 / 动作密度。
    这些成为喂给 ``prompt_library.search(shot_kind="image")`` 的查询
    token，让注入的参考保持静态（场景 / 光线 / 风格 / 镜头），
    绝不带入视频运动。
    """
    vd = shot.get("visual_details", {}) or {}
    parts: list[str] = []

    env = vd.get("environment")
    if env:
        parts.append(str(env))
    lighting = vd.get("lighting")
    if lighting:
        parts.append(str(lighting))

    cine = vd.get("cinematography") or {}
    for key in ("angle", "frame_composition"):
        val = cine.get(key)
        if val:
            parts.append(str(val))

    for subj in vd.get("subjects", []) or []:
        anchor = subj.get("appearance_anchor")
        if anchor:
            parts.append(str(anchor))
        beat = _pick_subject_beat(subj, None, None)
        if beat:
            if beat.get("verb"):
                parts.append(str(beat["verb"]))
            if beat.get("emotion"):
                parts.append(str(beat["emotion"]))
            continue
        act = (subj.get("action") or {})
        emotion = act.get("emotion")
        if emotion:
            parts.append(str(emotion))
        verb = act.get("verb")
        if verb:
            parts.append(str(verb))

    for obj in vd.get("objects") or []:
        if isinstance(obj, dict) and obj.get("appearance"):
            parts.append(str(obj["appearance"]))

    density = (vd.get("action_density") or {}).get("level")
    if density:
        parts.append(str(density))

    return " ".join(p for p in parts if p)


def _extract_video_keywords(shot: dict[str, Any]) -> str:
    """从镜头的动态（视频运动）关切中构建词库查询字符串。

    抽取描述 *视频运动* 必须包含内容的字段：
    主体动作节拍 / 动作密度 / 运镜 / 转场 /
    动态光线 + 特效。这些成为喂给
    ``prompt_library.search(shot_kind="video")`` 的查询 token，
    让注入的参考保持动态（镜头 / 动作 / 光线），
    绝不带入静态的场景/风格。
    """
    vd = shot.get("visual_details", {}) or {}
    parts: list[str] = []

    density = (vd.get("action_density") or {}).get("level")
    if density:
        parts.append(str(density))

    for subj in vd.get("subjects", []) or []:
        act = (subj.get("action") or {})
        emotion = act.get("emotion")
        if emotion:
            parts.append(str(emotion))
        for beat in (act.get("action_sequence") or []) or []:
            if beat:
                parts.append(str(beat))

    cine = vd.get("cinematography") or {}
    for key in ("camera_movement", "transition_in", "transition_out"):
        val = cine.get(key)
        if val:
            parts.append(str(val))

    lighting = vd.get("lighting")
    if lighting:
        parts.append(str(lighting))

    return " ".join(p for p in parts if p)


def _enrich_first_frame_with_library(
    first_frame_prompt: str,
    shot: dict[str, Any],
    provider_max_chars: int | None,
    *,
    dense: bool = True,
) -> str:
    """确定性地把静态词库参考注入到首帧提示词。

    在 ``first_frame_prompt`` 已被压缩到 ``provider_max_chars`` 以内
    （或完整构建）之后运行。注入的参考是低优先级增强 ——
    若结果会超预算，只裁剪注入的短语，
    绝不动主体/环境/光线正文。

    护栏（对照真实词库行为测量）：
    - ``shot_kind="image"`` 搜索排除 ``video`` 动态条目。
    - 绝对下限：最高命中分低于 3 时跳过 —— 低于 3 分意味着
      没有强标签/标题重叠（标签命中带 +2 加成），因此该查询只是
      偶然匹配（例如「爆炸 火焰 冲击」→ ``grade-teal-orange``=1，
      「雪山 冷色调」→ ``grade-warm-vintage``=1）。绝不基于噪声注入。
    - 相对阈值：只保留分数不低于最高命中一半的命中
      （仅靠绝对阈值没用 —— 分数随查询 token 数线性增长，
      例如 5 token 的查询可达 60，而 3 token 的查询最高只到 5 左右）。
    - 去重：跳过提示词中已存在的短语（子串检查）。
    """
    if not first_frame_prompt:
        return first_frame_prompt

    from lib.prompt_library import search as library_search

    query = _extract_first_frame_keywords(shot)
    if not query:
        return first_frame_prompt

    try:
        hits = library_search(query, shot_kind="image", top_k=10, include_prompt=True)
    except Exception:
        # 词库不可用或无法解析 —— 优雅降级，绝不让提示词构建
        # 因一次参考查找而失败。
        return first_frame_prompt

    if not hits:
        return first_frame_prompt

    max_score = max(int(h.get("score", 0)) for h in hits)
    # 绝对下限：低于 3 分意味着没有强标签/标题命中（标签命中带
    # +2 加成），因此整个查询只是偶然重叠 —— 绝不注入。
    if max_score < 3:
        return first_frame_prompt

    # 相对阈值：保留分数不低于最高命中一半的命中。
    threshold = max(2, max_score * 0.5)
    qualifying = [h for h in hits if int(h.get("score", 0)) >= threshold]
    static_cats = {"scenes", "lighting", "styles", "shots"}
    qualifying = [h for h in qualifying if h.get("category") in static_cats]
    qualifying.sort(key=lambda h: _ENRICH_CAT_RANK.get(str(h.get("category") or ""), 9))

    if not qualifying:
        return first_frame_prompt

    leftover = (
        provider_max_chars - len(first_frame_prompt)
        if provider_max_chars is not None
        else _ENRICH_SOFT_MAX_CHARS
    )
    if leftover <= 8:
        return first_frame_prompt
    soft_budget = min(leftover, _ENRICH_SOFT_MAX_CHARS)
    # dense：词库注入只留标题或 ≤40 字关键短语（禁止长「（参考：title（prompt[:60]））」尾巴）
    frag_n = 40 if dense else (_ENRICH_FRAG_LONG if leftover >= 600 else _ENRICH_FRAG_SHORT)

    phrases: list[str] = []
    used_chars = 0
    for h in qualifying:
        if len(phrases) >= _ENRICH_SOFT_MAX_PHRASES:
            break
        title = (h.get("title") or "").strip()
        prompt = (h.get("prompt") or "").strip()
        blob = f"{title} {prompt}"
        if _ENRICH_LEAK_RE.search(blob):
            continue
        frag = prompt[:frag_n].strip()
        phrase = title if not frag else f"{title}（{frag}）"
        if not phrase or phrase in first_frame_prompt:
            continue
        trial = used_chars + len(phrase) + (1 if phrases else 0)
        wrapper = len("（参考：）")
        if trial + wrapper > soft_budget:
            continue
        phrases.append(phrase)
        used_chars = trial

    if not phrases:
        return first_frame_prompt

    suffix = "（参考：" + "；".join(phrases) + "）"
    enriched = first_frame_prompt + suffix

    if provider_max_chars is not None and len(enriched) > provider_max_chars:
        budget = provider_max_chars - len(first_frame_prompt)
        kept: list[str] = []
        for phrase in phrases:
            piece = f"（参考：{('；'.join(kept) + '；' if kept else '') + phrase}）"
            if len(piece) <= budget:
                kept.append(phrase)
            else:
                break
        if not kept:
            return first_frame_prompt
        suffix = "（参考：" + "；".join(kept) + "）"
        enriched = first_frame_prompt + suffix

    return enriched


def _enrich_video_with_library(
    video_prompt: str,
    shot: dict[str, Any],
    provider_max_chars: int | None,
    *,
    dense: bool = True,
) -> str:
    """确定性地把动态词库参考注入到视频提示词。

    与 ``_enrich_first_frame_with_library`` 对称，但针对视频运动
    提示词：它在 ``video_prompt`` 被压缩到 ``provider_max_chars`` 以内
    （或完整构建）之后、英文画质层追加之前运行，
    因此注入的中文参考绝不占用英文画质层后续需要的空间。

    护栏（首帧版的镜像，按视频调校）：
    - ``shot_kind="video"`` 搜索排除静态 ``first_frame`` 条目。
    - 绝对下限：最高命中分低于 3 时跳过（噪声护栏）。
    - 相对阈值：保留分数不低于最高命中一半的命中。
    - 白名单 ``video_cats = {"shots", "actions", "lighting"}`` —— 视频
      注入关注运动 / 镜头 / 光线动态。静态场景
      （世界构建）和整片风格在这里是低价值参考，被排除。
    - 去重：跳过提示词中已存在的短语（子串检查）。
    - 长度护栏：超预算时裁剪注入的短语（低优先级）；
      绝不动正文（动作节拍 / 对白 / 衔接）。
    """
    if not video_prompt:
        return video_prompt

    from lib.prompt_library import search as library_search

    query = _extract_video_keywords(shot)
    if not query:
        return video_prompt

    try:
        hits = library_search(query, shot_kind="video", top_k=6, include_prompt=True)
    except Exception:
        # 词库不可用或无法解析 —— 优雅降级，绝不让提示词构建
        # 因一次参考查找而失败。
        return video_prompt

    if not hits:
        return video_prompt

    max_score = max(int(h.get("score", 0)) for h in hits)
    if max_score < 3:
        return video_prompt

    threshold = max(2, max_score * 0.5)
    qualifying = [h for h in hits if int(h.get("score", 0)) >= threshold]
    # 限制到携带动态信息的类别（shots/actions/lighting）。
    video_cats = {"shots", "actions", "lighting"}
    qualifying = [h for h in qualifying if h.get("category") in video_cats]

    if not qualifying:
        return video_prompt

    phrases: list[str] = []
    for h in qualifying[:4]:
        title = (h.get("title") or "").strip()
        prompt = (h.get("prompt") or "").strip()
        frag = prompt[:40].strip() if dense else prompt[:60].strip()
        phrase = title if not frag else f"{title}（{frag}）"
        if phrase and phrase not in video_prompt:
            phrases.append(phrase)

    if not phrases:
        return video_prompt

    suffix = "（参考：" + "；".join(phrases) + "）"
    enriched = video_prompt + suffix

    # 长度护栏：超预算时裁剪注入的短语（低优先级）。
    if provider_max_chars is not None and len(enriched) > provider_max_chars:
        budget = provider_max_chars - len(video_prompt)
        kept: list[str] = []
        for phrase in phrases:
            piece = f"（参考：{('；'.join(kept) + '；' if kept else '') + phrase}）"
            if len(piece) <= budget:
                kept.append(phrase)
            else:
                break
        if not kept:
            return video_prompt
        suffix = "（参考：" + "；".join(kept) + "）"
        enriched = video_prompt + suffix

    return enriched
