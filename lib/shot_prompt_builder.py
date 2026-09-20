"""镜头提示词构建器 —— 把结构化镜头语言转换成针对各生成器优化的提示词。

基于专业电影摄影提示词研究，使用 5 层框架：
  第 1 层：镜头（焦距、景深）
  第 2 层：运动（景别、运镜）
  第 3 层：主体（描述 + 质感关键词）
  第 4 层：光线（布光基调、色温）
  第 5 层：风格（取自 playbook，非逐字照搬）

它取代了旧方案 —— 旧方案给每个场景描述前都拼接一段固定的 playbook
image_prompt_prefix，导致所有镜头看起来千篇一律。
"""

from __future__ import annotations

import re
from typing import Any


from lib.prompt_phrases import (
    _ABSTRACT_WORD_RE,
    _AUDIO_LOCK_NO_DIALOGUE,
    _AUDIO_LOCK_WITH_DIALOGUE,
    _BLOCKING_X_ZH,
    _BLOCKING_Y_ZH,
    _BLOCKING_Z_ZH,
    _CLOSE_SIZES,
    _COLOR_TEMP_PHRASES,
    _DEFAULT_EAST_ASIAN_APPEARANCE,
    _DOF_PHRASES,
    _ENGLISH_NEGATIVE_PROMPT,
    _ENGLISH_VISUAL_BASELINE,
    _FRAME_CONSISTENCY_EN,
    _IMAGE_ENGLISH_GUARD,
    _IMAGE_ENGLISH_NEGATIVE,
    _IMAGE_ENGLISH_VISUAL_BASELINE,
    _LIGHTING_PHRASES,
    _MANNER_TIME_RE,
    _MOVEMENT_PHRASES,
    _MOVEMENT_ZH,
    _SECTION_ORDER,
    _SHOT_SIZE_PHRASES,
    _SHOT_SIZE_ZH,
    _WIDE_SIZES,
)
from lib.prompt_english import (
    contains_cjk,
    default_english_negative_prompt,
    _append_english_visual_layer,
    _append_first_frame_english_layer,
    _append_jimeng_guard,
    _camera_motion_text,
    _camera_static_text,
    _classify_depth_layer,
    _DYNAMIC_SECTION_KEYS,
    _ENRICH_CAT_RANK,
    _ENRICH_FRAG_LONG,
    _ENRICH_FRAG_SHORT,
    _ENRICH_LEAK_RE,
    _ENRICH_SOFT_MAX_CHARS,
    _ENRICH_SOFT_MAX_PHRASES,
    _enrich_first_frame_with_library,
    _enrich_video_with_library,
    _extract_first_frame_keywords,
    _extract_video_keywords,
    _image_depth_text,
    _image_english_layer_text,
    _image_negative_text,
    _IMAGE_QUALITY_TEXT,
    _IMAGE_SECTION_ORDER,
    _image_style_text,
    _is_stylized_playbook,
    _KEEP_REFERENCE_TEXT,
    _objects_first_frame_text,
    _opening_pose_text,
    _order_image_sections,
    _pick_subject_beat,
    _section_key,
    _split_first_frame_sections,
    _STATIC_SECTION_KEYS,
    _strip_manner_timing,
    _subject_beats,
)

def build_shot_prompt(
    scene: dict[str, Any],
    style_context: dict[str, Any] | None = None,
) -> str:
    """把带有结构化镜头语言的场景转换成生成提示词。

    Args:
        scene: 来自 scene_plan 的场景 dict（含 shot_language、description、
               texture_keywords 等）
        style_context: 可选的、源自 playbook 的风格信息，键形如
                       'generation_prefix'、'visual_language'、'mood'。

    Returns:
        针对图片/视频生成优化过的自然语言提示词。
    """
    sl = scene.get("shot_language", {})
    layers: list[str] = []

    # 第 1 层：镜头 —— 焦距与景深
    camera_parts = []
    if sl.get("lens_mm"):
        camera_parts.append(f"{sl['lens_mm']}mm 焦段")
    if sl.get("depth_of_field"):
        camera_parts.append(_DOF_PHRASES.get(sl["depth_of_field"], ""))
    if camera_parts:
        layers.append("，".join(filter(None, camera_parts)))

    # 第 2 层：运动 —— 景别与运镜
    movement_parts = []
    if sl.get("shot_size"):
        movement_parts.append(_SHOT_SIZE_PHRASES.get(sl["shot_size"], sl["shot_size"]))
    if sl.get("camera_movement") and sl["camera_movement"] != "static":
        movement_parts.append(_MOVEMENT_PHRASES.get(sl["camera_movement"], sl["camera_movement"]))
    if movement_parts:
        layers.append("，".join(movement_parts))

    # 第 3 层：主体 —— 场景描述 + 质感关键词
    description = scene.get("description", "")
    texture = scene.get("texture_keywords", [])
    subject_parts = [description]
    if texture:
        subject_parts.append("，".join(texture))
    layers.append("。".join(filter(None, subject_parts)))

    # 第 4 层：光线 —— 布光基调与色温
    lighting_parts = []
    if sl.get("lighting_key"):
        lighting_parts.append(_LIGHTING_PHRASES.get(sl["lighting_key"], sl["lighting_key"]))
    if sl.get("color_temperature"):
        lighting_parts.append(_COLOR_TEMP_PHRASES.get(sl["color_temperature"], ""))
    if lighting_parts:
        layers.append("，".join(filter(None, lighting_parts)))

    # 第 5 层：风格 —— 取自 playbook（非逐字前缀）
    if style_context:
        mood = style_context.get("mood", "")
        visual_lang = style_context.get("visual_language", {})
        style_hint = visual_lang.get("aesthetic", "") or mood
        if style_hint:
            layers.append(f"风格：{style_hint}")

    return "。".join(filter(None, layers))


def build_batch_prompts(
    scenes: list[dict[str, Any]],
    style_context: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """为场景计划里的所有视觉场景构建提示词。

    返回 {scene_id, prompt} dict 的列表。
    """
    results = []
    for scene in scenes:
        # 跳过非视觉类场景
        scene_type = scene.get("type", "")
        if scene_type in ("transition",):
            continue
        prompt = build_shot_prompt(scene, style_context)
        results.append({
            "scene_id": scene.get("id", "unknown"),
            "prompt": prompt,
            "hero_moment": scene.get("hero_moment", False),
        })
    return results


# ---------------------------------------------------------------------------
# 逐镜头画面提示词流程（详细版）
# ---------------------------------------------------------------------------


def _strip_abstract_words(section: str) -> str:
    """从提示词段落中剔除抽象套话词（仅 dense 模式）。

    先将被匹配的套话替换为占位符再删除，这样没有 ASCII 边界的
    中文词（电影感 / 高质量）即使被 CJK 包围也能被干净剔除。
    其余字节原样保留。
    """
    if not section:
        return section
    return _ABSTRACT_WORD_RE.sub("", section)


def _find_form(char: dict[str, Any], form_id: str) -> dict[str, Any] | None:
    """角色 forms[] 里按 id 找形态；找不到返回 None。"""
    fid = str(form_id or "").strip()
    if not fid:
        return None
    for form in char.get("forms") or []:
        if isinstance(form, dict) and str(form.get("id") or "").strip() == fid:
            return form
    return None


def _form_appearance(char: dict[str, Any], form: dict[str, Any]) -> str:
    """形态外观文本：form.appearance/outfit 优先，缺省回落角色同名字段。"""
    app = str(form.get("appearance") or char.get("appearance") or "").strip()
    outfit = str(
        form.get("outfit_anchor")
        or form.get("outfit")
        or char.get("outfit_anchor")
        or char.get("outfit")
        or ""
    ).strip()
    if not app and not outfit:
        return ""
    return app + (f", {outfit}" if outfit else "")


def _subject_display_name(
    subject: dict[str, Any],
    character_registry: list[dict[str, Any]] | None,
) -> str:
    """身份行显示名：声明形态且有形态名时用「角色·形态」。"""
    cid = str(subject.get("id") or "")
    fid = str(subject.get("form_id") or "").strip()
    if not cid:
        return cid
    # 有中文名就用中文名（形态名前缀保持 id·形态 兼容旧行为）。
    if not fid:
        for char in character_registry or []:
            if char.get("id") == cid:
                return str(char.get("name") or cid)
        return cid
    if not character_registry:
        return cid
    for char in character_registry:
        if char.get("id") != cid:
            continue
        form = _find_form(char, fid)
        if form is None:
            return cid
        fname = str(form.get("name") or "").strip()
        return f"{cid}·{fname}" if fname else f"{cid}（{fid}）"
    return cid


def _resolve_appearance(
    subject: dict[str, Any],
    character_registry: list[dict[str, Any]] | None,
    style_context: dict[str, Any] | None,
) -> str:
    """解析某个主体的外貌文本。

    优先级：显式 subject.appearance_anchor（作者覆写）
    > 声明形态的 form.appearance/outfit（缺省回落角色）
    > subject.appearance_anchor（编译期自动抄的角色基础外貌）
    > character_registry[subjects[].id].appearance
    > style_context character_appearance_default
    > 硬编码的东亚默认值。
    """
    cid = subject.get("id")
    fid = str(subject.get("form_id") or "").strip()
    anchor = str(subject.get("appearance_anchor") or "").strip()
    char: dict[str, Any] | None = None
    if character_registry:
        for row in character_registry:
            if row.get("id") == cid:
                char = row
                break
    # 声明了形态：形态外观优先。编译期会把角色基础外貌写进 appearance_anchor，
    # 因此只有作者显式写了不同 anchor 时才让 anchor 压过形态。
    if char is not None and fid:
        form = _find_form(char, fid)
        if form is not None:
            form_text = _form_appearance(char, form)
            base = str(char.get("appearance") or "").strip()
            if form_text and (not anchor or anchor == base):
                return form_text
    if anchor:
        return anchor
    if char is not None:
        reg_appearance = char.get("appearance")
        if reg_appearance:
            return reg_appearance
        # ethnicity_default 覆盖 playbook 默认值
        ethnicity = char.get("ethnicity_default")
        outfit = char.get("outfit_anchor") or ""
        body = ethnicity or _DEFAULT_EAST_ASIAN_APPEARANCE
        return f"{body}" + (f", {outfit}" if outfit else "")

    if style_context:
        gen = style_context.get("asset_generation") or {}
        default = gen.get("character_appearance_default")
        if default:
            return default

    return _DEFAULT_EAST_ASIAN_APPEARANCE


def _beat_timeline_text(subject: dict[str, Any], *, name: str = "") -> str:
    """渲染某个主体的节拍级动作时间线。

    当 ``subject.action_sequence`` 非空时，返回时间线字符串：
        "{name}（{emotion?}）：{start:.1f}-{end:.1f}s {verb}（{manner?}）；..."
    节拍严格按 ``start_seconds`` 排序。当 ``action_sequence``
    缺失或为空时返回 ""（调用方回退到扁平 ``action``）。
    """
    seq = subject.get("action_sequence") or []
    if not seq:
        return ""
    name = str(name or subject.get("id") or "")
    beats = sorted(seq, key=lambda b: float(b.get("start_seconds", 0.0)))
    parts = []
    for b in beats:
        start = float(b.get("start_seconds", 0.0))
        end = float(b.get("end_seconds", 0.0))
        verb = b.get("verb") or ""
        manner = b.get("manner") or ""
        emotion = b.get("emotion") or ""
        seg = f"{name}：{start:.1f}-{end:.1f}s {verb}"
        if manner:
            seg += f"（{manner}）"
        if emotion:
            seg += f"，情绪：{emotion}"
        parts.append(seg)
    return "；".join(parts)


def dialogue_line_text(item: Any) -> str:
    """台词：``dialogue_text`` → ``text`` → ``content``。"""
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return str(item or "").strip()
    for key in ("dialogue_text", "text", "content"):
        val = str(item.get(key) or "").strip()
        if val:
            return val
    return ""


def dialogue_line_role(item: Any) -> str:
    """角色：``role`` → ``speaker_id``。"""
    if not isinstance(item, dict):
        return ""
    for key in ("role", "speaker_id"):
        val = str(item.get(key) or "").strip()
        if val:
            return val
    return ""


def _dialogue_segment(
    item: Any,
    *,
    use_full_text: bool,
    names: dict[str, str] | None = None,
) -> str:
    """一行对白的提示词片段。无全文且无 dialogue_ref 时返回空，禁止 ``说出台词（）``。"""
    if not isinstance(item, dict):
        text = dialogue_line_text(item)
        if not text:
            return ""
        return f"说出：\"{text}\"" if use_full_text else text
    role = dialogue_line_role(item)
    # 说话人写中文名（台词段此前会把 huan_niang 这类 id 写进提示词）。
    if role and names:
        role = names.get(role, role)
    full = dialogue_line_text(item)
    ref = str(item.get("dialogue_ref") or "").strip()
    if use_full_text and full:
        seg = f"{role} 说出：\"{full}\"" if role else f"说出：\"{full}\""
    elif ref:
        seg = f"{role} 说出台词（{ref}）" if role else f"说出台词（{ref}）"
    else:
        return ""
    delivery = str(item.get("delivery") or "").strip()
    volume = item.get("volume")
    if delivery:
        seg += f"，语气：{delivery}"
    if volume not in (None, ""):
        seg += f"，音量：{volume}"
    return seg.strip()


def _dialogue_section_text(
    shot: dict[str, Any],
    *,
    use_full_text: bool = False,
    names: dict[str, str] | None = None,
) -> str:
    """把镜头的对白（台词）渲染成散文式【台词】段。

    对白必须逐字保留：每一行通过 ``{{对白:...}}`` 占位符引用脚本文本
    （全文存放在 script.sections[].text，在 TTS/Seed-Audio 前替换）。
    该段在压缩时被视为最高优先级，绝不被截断。

    当 ``use_full_text=True``（Agnes 路径 —— 音频由视频提示词文本生成）时，
    每一行渲染完整口播文本，让生成器能真正说出这句台词。接受
    ``dialogue_text`` / ``text`` / ``content`` 以及 ``role`` / ``speaker_id``。
    仅当完整台词缺失时才回退到 ``dialogue_ref`` —— 绝不输出 ``说出台词（）``。
    """
    ap = shot.get("audio_prompt") or {}
    parts: list[str] = []
    for d in ap.get("dialogue") or []:
        seg = _dialogue_segment(d, use_full_text=use_full_text, names=names)
        if seg:
            parts.append(seg)
    return "；".join(parts)


def blocking_to_zh(blocking: Any) -> str:
    """``x/z/y`` 或中文别名 →「左侧中景」；缺字段就跳过。"""
    if isinstance(blocking, str):
        return blocking.strip()
    if not isinstance(blocking, dict):
        return ""
    x = _BLOCKING_X_ZH.get(str(blocking.get("x") or "").strip().lower()) or _BLOCKING_X_ZH.get(
        str(blocking.get("x") or "").strip()
    )
    z = _BLOCKING_Z_ZH.get(str(blocking.get("z") or "").strip().lower()) or _BLOCKING_Z_ZH.get(
        str(blocking.get("z") or "").strip()
    )
    y = _BLOCKING_Y_ZH.get(str(blocking.get("y") or "").strip().lower()) or _BLOCKING_Y_ZH.get(
        str(blocking.get("y") or "").strip()
    )
    bits = [p for p in (x, y, z) if p]
    return "".join(bits) if bits else ""


def crop_location_sensory(text: str, shot_size: str = "") -> str:
    """地点全图按景别裁切。无方位词则原样返回，不编造地标。"""
    raw = str(text or "").strip()
    if not raw:
        return ""
    size = str(shot_size or "").strip().lower()
    chunks = [c.strip(" 。；;") for c in re.split(r"[；;。]", raw) if c.strip(" 。；;")]
    if len(chunks) <= 1 or size in _WIDE_SIZES or not size:
        return raw if raw.endswith(("。", "；")) else raw + "。"
    far_mark = ("更远处", "远处", "天际")
    near_mark = ("近处", "眼前", "贴地", "中景", "中段", "下方")
    has_axis = any(m in raw for m in (*near_mark, *far_mark, "左侧", "右侧"))
    if not has_axis:
        return raw if raw.endswith(("。", "；")) else raw + "。"
    if size in _CLOSE_SIZES:
        near = [c for c in chunks if any(m in c for m in near_mark)]
        far = [c for c in chunks if any(m in c for m in far_mark)]
        kept = list(near or chunks[:1])
        if far:
            piece = far[0]
            if "轮廓" not in piece:
                piece = piece.rstrip("。") + "轮廓"
            kept.append(piece)
        return "；".join(kept) + "。"
    return "；".join(chunks) + "。"


def _zh_shot_size(shot: dict[str, Any]) -> str:
    sl = shot.get("shot_language") if isinstance(shot.get("shot_language"), dict) else {}
    key = str(sl.get("shot_size") or "").strip()
    return _SHOT_SIZE_ZH.get(key, "") or (key if key and not re.search(r"[A-Za-z]", key) else "")


def _zh_camera(shot: dict[str, Any]) -> str:
    sl = shot.get("shot_language") if isinstance(shot.get("shot_language"), dict) else {}
    key = str(sl.get("camera_movement") or "").strip()
    return _MOVEMENT_ZH.get(key, "") or (key if key and not re.search(r"[A-Za-z]", key) else "")


def _location_sensory_text(
    shot: dict[str, Any],
    locations: list[dict[str, Any]] | None = None,
) -> str:
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    for raw in (shot.get("location_sensory"), vd.get("environment")):
        text = str(raw or "").strip()
        if text:
            return text
    lid = str(shot.get("location_id") or "").strip()
    for loc in locations or []:
        if not isinstance(loc, dict):
            continue
        if lid and str(loc.get("id") or loc.get("name") or "").strip() == lid:
            text = str(loc.get("sensory") or loc.get("appearance") or "").strip()
            if text:
                return text
    return str(shot.get("description") or "").strip()


def _subject_blocking_zh(
    shot: dict[str, Any],
    character_registry: list[dict[str, Any]] | None = None,
) -> list[str]:
    lines: list[str] = []
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    shot_block = blocking_to_zh(shot.get("blocking"))
    for subj in vd.get("subjects") or []:
        if not isinstance(subj, dict):
            continue
        # 中文显示名优先（registry.name），否则回落 id —— 提示词里不该出现
        # wen_ruchun 这类内部 id（2026-09-19 用户实测反馈）。
        cid = str(subj.get("id") or "").strip()
        display = ""
        for row in character_registry or []:
            if isinstance(row, dict) and str(row.get("id") or "") == cid:
                display = str(row.get("name") or "")
                break
        name = str(subj.get("name") or display or cid or "").strip() or "角色"
        pos = blocking_to_zh(subj.get("blocking") or subj.get("position")) or shot_block
        act = subj.get("action") if isinstance(subj.get("action"), dict) else {}
        verb = str(act.get("verb") or "").strip()
        manner = str(act.get("manner") or "").strip()
        path = str(act.get("path") or shot.get("blocking_path") or "").strip()
        bits = [name]
        if pos:
            bits.append(f"在{pos}")
        if path:
            bits.append(path if path.startswith("从") else f"从{path}")
        elif verb:
            bits.append(verb)
            if manner:
                bits.append(manner)
        lines.append("，".join(bits) + "。")
    return lines


def _ref_header_lines(refs: list[dict[str, Any]] | None) -> list[str]:
    lines: list[str] = []
    for i, ref in enumerate(refs or [], 1):
        if not isinstance(ref, dict):
            continue
        kind = str(ref.get("kind") or "").strip()
        name = str(ref.get("name") or ref.get("id") or "").strip()
        if kind == "first_frame":
            if ref.get("bridge"):
                lines.append(
                    f"参考图{i}（<Picture {i}>）为续接上一段的起始帧，"
                    "锁构图、景别与人物姿态连续。"
                )
            else:
                lines.append(
                    f"参考图{i}（<Picture {i}>）为本镜首帧，锁构图、景别与人物姿态。"
                )
        elif kind in ("portrait", "turnaround") or str(ref.get("role") or "") in ("角色外貌", "角色体态"):
            who = name or "角色"
            kind_zh = "角色四视图" if kind == "turnaround" or "体态" in str(ref.get("role") or "") else "定妆照"
            line = f"参考图{i}（<Picture {i}>）为{who}基础形象的{kind_zh}，锁外貌与体态"
            if kind == "turnaround":
                # 四视图是分格拼板：**必须显式说明这四个角度是同一个人**，
                # 否则 I2V 会把四格读成多个人（实测：宦娘被画成两个女主）。
                line += (
                    "；这张参考图是**同一个人**的四个角度（正面/侧面/背面/四分之三侧），"
                    "**不是四个不同的人、也不是多胞胎**；"
                    "画面中该角色只能出现一次，禁止分格/拼贴、禁止复制成多个个体，"
                    "只输出单幅画面"
                )
            lines.append(line + "。")
        elif kind == "scene_ref" or "场景" in str(ref.get("role") or ""):
            place = name or "场景"
            lines.append(
                f"参考图{i}（<Picture {i}>）为{place}的场景环境参考，锁空间结构、陈设、材质和光影，不锁镜头机位。"
            )
        elif kind == "prop":
            # 道具参考也是四视图拼板：必须说明"同一件物品的四个角度"，
            # 否则 I2V 会把四格读成多件道具（与人物四视图同理）。
            lines.append(
                f"参考图{i}（<Picture {i}>）为{name or '道具'}的**道具四视图**"
                "（正面/侧面/背面/俯视四个角度，**是同一件物品**，不是四件物品、"
                "也不是多件同类道具），锁外形；画面中该道具只按一件来画。"
            )
        else:
            continue
    return lines


def image_ref_legend(refs: list[dict[str, Any]] | None) -> str:
    """图片多图合成的角色图例：说明每张输入参考图是什么。

    顺序必须与实发 ``extra_body.image`` 完全一致（见
    ``capabilities.agnes_image_ref_entries``），否则模型会把角色图当场景用。
    """
    lines = _ref_header_lines(refs)
    if not lines:
        return ""
    return "【参考图角色】" + "".join(lines)


CONTINUATION_FRAME_HINT = (
    "续接上一段尾帧的画面：保持同一人物、服装、构图、光线与场景，"
    "只在此基础上推进下一段动作；只输出单幅画面，禁止分格/拼贴。"
)


def build_bridge_frame_prompt(refs: list[dict[str, Any]] | None = None) -> str:
    """段间桥接首帧的生图提示词：以「接尾帧继续」为核心，附本段参考图例。

    不沿用整镜 ``first_frame_prompt``（那会把画面拉回镜头起始姿态），
    也不复刻 v2.0 的 refine hint。
    """
    legend = image_ref_legend(refs)
    return f"{CONTINUATION_FRAME_HINT}\n{legend}" if legend else CONTINUATION_FRAME_HINT


def _v25_audio_line(shot: dict[str, Any]) -> str:
    ap = shot.get("audio_prompt") if isinstance(shot.get("audio_prompt"), dict) else {}
    bgm = ap.get("bgm") if isinstance(ap.get("bgm"), dict) else {}
    mood = str(bgm.get("mood") or bgm.get("instrument") or "").strip()
    sfx_bits: list[str] = []
    for item in ap.get("sfx") or []:
        if isinstance(item, dict):
            sound = str(item.get("sound") or "").strip()
            if sound:
                sfx_bits.append(sound)
        elif str(item or "").strip():
            sfx_bits.append(str(item).strip())
    parts = []
    if mood:
        parts.append(f"全片统一BGM：{mood}，音量低于环境")
    if sfx_bits:
        parts.append("本镜环境音：" + "、".join(sfx_bits[:4]))
    parts.append("禁止静音；无字幕无屏显文字")
    return "；".join(parts) + "。"


def _compress_agnes_v25(text: str, max_chars: int) -> str:
    """先砍 BGM 形容词与参考图长句；**动作与对白永不删**（用户拍板）。

    只做两类压缩：①"全片统一BGM：…" 收成"压低"；②参考图说明句超过 80 字的
    截到 70 字。方位/走位/动作/对白一律原样保留——剧情可以简洁，但动作与台词
    不能省。
    """
    body = text
    if len(body) <= max_chars:
        return body
    body = re.sub(r"全片统一BGM：[^；]+", "全片统一BGM：压低", body)
    if len(body) <= max_chars:
        return body
    body = re.sub(r"参考图\d+（<Picture \d+>）为[^。]{80,}。", lambda m: m.group(0)[:70] + "。", body)
    if len(body) <= max_chars:
        return body
    return body


def build_agnes_v25_prompt(
    shot: dict[str, Any],
    *,
    character_registry: list[dict[str, Any]] | None = None,
    locations: list[dict[str, Any]] | None = None,
    refs: list[dict[str, Any]] | None = None,
    duration_seconds: float | None = None,
    max_chars: int = 3000,
    presence_names: dict[str, str] | None = None,
    style_context: dict[str, Any] | None = None,
) -> str:
    """2.5 视频提示词：薄骨架 + 交叉方位散文。不发明地点卡里没有的地标。"""
    # 外貌锁在参考图行、正文不复述（以免和四视图打架）；但**显示名**要用 registry，
    # 否则正文里会出现 wen_ruchun 这类 id。
    seconds = duration_seconds
    if seconds is None:
        try:
            seconds = float(shot.get("duration_seconds") or 8)
        except (TypeError, ValueError):
            seconds = 8
    seconds_i = max(4, min(12, int(round(float(seconds)))))
    sl = shot.get("shot_language") if isinstance(shot.get("shot_language"), dict) else {}
    size_key = str(sl.get("shot_size") or "").strip()
    env_full = _location_sensory_text(shot, locations)
    env = crop_location_sensory(env_full, size_key)
    size_zh = _zh_shot_size(shot) or "中全景"
    cam_zh = _zh_camera(shot)
    cin = (shot.get("visual_details") or {}).get("cinematography") if isinstance(shot.get("visual_details"), dict) else {}
    extra_cam = ""
    if isinstance(cin, dict):
        extra_cam = str(cin.get("move") or cin.get("orbit") or "").strip()

    lines = _ref_header_lines(refs)
    story: list[str] = ["【剧情】"]
    # 中文名映射：角色取 registry，道具/场景取 presence_names（visual_prompt_builder 注入）。
    # 在场清单、承接表、台词说话人都用它，避免把 wen_ruchun / loc_court / prop_guqin 写进提示词。
    names: dict[str, str] = {}
    for row in character_registry or []:
        if isinstance(row, dict) and row.get("id"):
            names[str(row["id"])] = str(row.get("name") or row["id"])
    names.update(presence_names or {})
    # B2.5：在场清单 / 画外 / 承接也进视频提示词——I2V 最需要知道"谁在场、
    # 谁只在画外、上镜哪些必须保留"，否则会凭空多出人物或丢掉承接物。
    presence = shot.get("presence") if isinstance(shot.get("presence"), dict) else None
    if presence:
        from lib.shot_presence import presence_prompt_lines

        continuity = shot.get("continuity") if isinstance(shot.get("continuity"), dict) else None
        story.extend(presence_prompt_lines(presence, continuity, names=names))
    if env:
        story.append(f"场景：{env.rstrip('。')}。")
    if (
        isinstance(presence, dict)
        and str(presence.get("empty_reason") or "").strip()
        and not ((shot.get("visual_details") or {}).get("subjects") or [])
    ):
        # 空镜：I2V 同样不能"补人"（片尾山道空镜实测会自己加两个角色）。
        story.append("【空镜】画面内不出现任何人物、人影或动物，只保留场景与指定道具。")
    # 2026-09-19 用户要求：视频提示词也要带质感要求（写实材质），
    # 否则 I2V 会把材质渲染成塑料/磨皮感。取 playbook 的 texture 描述。
    texture = ""
    if isinstance(style_context, dict):
        vl = style_context.get("visual_language")
        if isinstance(vl, dict):
            texture = str(vl.get("texture") or "").strip()
    story.append(
        "【质感】中国式三维动画电影质感、写实材质："
        + (texture + "；" if texture else "")
        + "皮肤保留微纹理与自然高光（不磨皮），布料纤维与褶皱可见，"
        "器物有真实反光与使用痕迹；光照按物理规律落影。"
    )
    beat = f"[0-{seconds_i}s] {size_zh}。"
    body_bits = _subject_blocking_zh(shot, character_registry)
    if cam_zh:
        cam_sentence = f"镜头{cam_zh}"
        if extra_cam:
            cam_sentence += f"，{extra_cam}"
        cam_sentence += "。"
        body_bits.append(cam_sentence)
    dialogue = _dialogue_section_text(shot, use_full_text=True, names=names)
    if dialogue:
        body_bits.append(dialogue + "。")
    pic_lock = ""
    if refs:
        # 编号不能写死 1：refs 是最终有序表，首位未必是人物定妆。
        rows = [r for r in refs if isinstance(r, dict)]
        portrait_idx = next(
            (i for i, r in enumerate(rows, 1)
             if str(r.get("kind") or "") in ("portrait", "turnaround")),
            0,
        )
        scene_idx = next(
            (i for i, r in enumerate(rows, 1) if str(r.get("kind") or "") == "scene_ref"),
            0,
        )
        if portrait_idx and scene_idx:
            pic_lock = (
                f"保持人物外貌与 <Picture {portrait_idx}> 一致，"
                f"场景结构与 <Picture {scene_idx}> 一致。"
            )
        elif portrait_idx:
            pic_lock = f"保持外观与 <Picture {portrait_idx}> 一致。"
        elif scene_idx:
            pic_lock = f"保持场景结构与 <Picture {scene_idx}> 一致。"
    if pic_lock:
        body_bits.append(pic_lock)
    story.append(beat + "".join(body_bits))
    audio = "【全局音频】" + _v25_audio_line(shot)
    text = "\n".join(lines + story + [audio]).strip()
    return text


KLING_LOOK_SHEET_MAX = 2500
KLING_LOOK_SHEET_GRID = (
    "16:9纯白摄影棚，均匀柔光，地面无接缝，无文字无水印无家具；"
    "整图同一人同一套服装同一发型体态，无第二人无叙事场景；"
    "左半格：正面全身定妆，人物朝镜头站定，头到脚完整可见；"
    "右上左格：近景小正脸，五官朝镜头，仅锁长相不锁身体；"
    "右上右格：右侧身全身，人物朝右，可见右脸轮廓与右肩；"
    "右下左格：背面全身，后脑勺与后背朝镜头；"
    "右下右格：右前方四分之三侧身，面朝右前"
)


def _kling_look_sheet_identity(character: dict[str, Any]) -> list[str]:
    """只抄人物卡已有字段，不编造地标或五官。"""
    bits: list[str] = []
    seen: set[str] = set()
    for key in (
        "appearance",
        "outfit_anchor",
        "outfit",
        "ethnicity_default",
        "hair",
        "body",
    ):
        raw = str(character.get(key) or "").strip().rstrip("。；")
        if not raw or raw in seen:
            continue
        seen.add(raw)
        bits.append(raw)
    return bits


def build_kling_look_sheet_prompt(character: dict[str, Any] | None = None) -> str:
    """可灵环一张白底拼板。格线先锁，再填外形；不超过 2500 字。"""
    char = character if isinstance(character, dict) else {}
    bits = [KLING_LOOK_SHEET_GRID]
    identity = _kling_look_sheet_identity(char)
    if identity:
        bits.append("五格均为：" + "；".join(identity))
    text = "；".join(bits)
    if len(text) <= KLING_LOOK_SHEET_MAX:
        return text
    return text[:KLING_LOOK_SHEET_MAX]


KLING_PROP_SHEET_GRID = (
    "16:9纯白底摄影棚，均匀柔光，地面无接缝，无文字无水印无家具；"
    "整图同一件道具，孤立静物，无人无手无人体无第二件；"
    "左半格：正面，器物朝镜头，整体完整可见；"
    "右上左格：近景特写或轻微俯视，只锁局部细节，不另造第二件；"
    "右上右格：右侧，器物朝右，可见右侧轮廓；"
    "右下左格：背面，器物朝后；"
    "右下右格：右前方四分之三侧"
)


def _kling_prop_sheet_identity(item: dict[str, Any]) -> list[str]:
    """只抄道具卡已有字段，不编造材质或结构。"""
    bits: list[str] = []
    seen: set[str] = set()
    for key in ("name", "id", "description", "appearance", "material"):
        raw = str(item.get(key) or "").strip().rstrip("。；")
        if not raw or raw in seen:
            continue
        seen.add(raw)
        bits.append(raw)
    return bits


def build_kling_prop_prompt(prop: dict[str, Any] | None = None) -> str:
    """可灵环道具四视拼板。格线先锁，再填外形；不超过 2500 字。"""
    item = prop if isinstance(prop, dict) else {}
    bits = [KLING_PROP_SHEET_GRID]
    identity = _kling_prop_sheet_identity(item)
    if identity:
        bits.append("五格均为：" + "；".join(identity))
    text = "；".join(bits)
    if len(text) <= KLING_LOOK_SHEET_MAX:
        return text
    return text[:KLING_LOOK_SHEET_MAX]


def _kling_light_text(shot: dict[str, Any]) -> str:
    """只抄镜头卡已有光，不编造布光。"""
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    cin = vd.get("cinematography") if isinstance(vd.get("cinematography"), dict) else {}
    for raw in (vd.get("lighting"), vd.get("light"), cin.get("lighting"), cin.get("light")):
        text = str(raw or "").strip().rstrip("。；")
        if text:
            return text
    return ""


def _kling_registry_identity(
    shot: dict[str, Any],
    character_registry: list[dict[str, Any]] | None,
) -> list[str]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in character_registry or []:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("id") or "").strip()
        if cid:
            by_id[cid] = row
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    bits: list[str] = []
    seen: set[str] = set()
    for subj in vd.get("subjects") or []:
        if not isinstance(subj, dict):
            continue
        cid = str(subj.get("id") or "").strip()
        char = by_id.get(cid)
        if not char:
            continue
        identity = _kling_look_sheet_identity(char)
        if not identity:
            continue
        key = "；".join(identity)
        if key in seen:
            continue
        seen.add(key)
        bits.append(f"{cid}外形：" + key)
    return bits


def _kling_join_budget(core: list[str], extra: list[str], max_chars: int) -> str:
    text = "；".join(b.rstrip("；") for b in core if b).strip()
    limit = max(32, int(max_chars or KLING_LOOK_SHEET_MAX))
    for piece in extra:
        chunk = str(piece or "").strip().rstrip("；")
        if not chunk:
            continue
        cand = f"{text}；{chunk}" if text else chunk
        if len(cand) <= limit:
            text = cand
            continue
        room = limit - len(text) - 1
        if room > 8:
            text = f"{text}；{chunk[:room]}" if text else chunk[:limit]
        break
    return text[:limit]


def _kling_dialogue_line(shot: dict[str, Any]) -> str:
    ap = shot.get("audio_prompt") if isinstance(shot.get("audio_prompt"), dict) else {}
    parts: list[str] = []
    for row in ap.get("dialogue") or []:
        if not isinstance(row, dict):
            continue
        speaker = str(row.get("role") or row.get("speaker_id") or row.get("name") or "").strip()
        text = str(
            row.get("dialogue_text") or row.get("text") or row.get("content") or ""
        ).strip()
        if not text:
            continue
        parts.append(f"{speaker}：{text}" if speaker else text)
    if not parts:
        return ""
    return "对白：" + "；".join(parts)


def build_kling_prompt(
    shot: dict[str, Any],
    *,
    character_registry: list[dict[str, Any]] | None = None,
    locations: list[dict[str, Any]] | None = None,
    refs: list[dict[str, Any]] | None = None,
    duration_seconds: float | None = None,
    max_chars: int = 2500,
    cite: str = "omni",
    still: bool = False,
    master_prompt: str | None = None,
    master_pattern: str | None = None,
) -> str:
    """可灵中文方位骨架 + 官方 @ / 对白：。静帧无对白无运镜。不写【剧情】<Picture>。

    ``master_prompt``：playbook/模式给出的全局母带句（画幅/快门/胶片/调色/铁律），
    非空时前置为「母带块」。``master_pattern``：lib.kling_master 模式 id，
    取其母带句与逐镜规约（镜头类型/状态演进/卡司/人群/声画同步/能量曲线），
    合并进母带块。静帧（``still=True``，首帧图）不带母带块——图片生成器
    不需要全局节奏信息。
    """
    cite = str(cite or "omni").strip().lower() or "omni"

    # —— 母带块（仅视频运动提示词；静帧不带）——
    master_bits: list[str] = []
    if not still:
        mp = str(master_prompt or "").strip()
        pattern = _kling_master_pattern(master_pattern)
        master_sentences: list[str] = []
        if pattern:
            # 模式的硬约束 + 音频原则总是并入；master_prompt 显式给出时覆盖模式母带句
            for hc in pattern.get("hard_constraints") or []:
                if str(hc or "").strip():
                    master_sentences.append(str(hc).rstrip("。；"))
            audio_principle = str(pattern.get("audio_principle") or "").strip()
            if audio_principle:
                master_sentences.append("声音：" + audio_principle.rstrip("。；"))
        if mp:
            master_sentences.insert(0, mp.rstrip("。；"))
        elif pattern:
            master_sentences.insert(
                0, str(pattern.get("master_prompt") or "").rstrip("。；")
            )
        if master_sentences:
            master_bits.append("；".join(master_sentences))
        contract_bits = _kling_contract_block(master_pattern, shot)
        if contract_bits:
            master_bits.append("；".join(contract_bits))

    sl = shot.get("shot_language") if isinstance(shot.get("shot_language"), dict) else {}
    size_key = str(sl.get("shot_size") or "").strip()
    env = crop_location_sensory(_location_sensory_text(shot, locations), size_key)
    size_zh = _zh_shot_size(shot) or "中全景"
    cam_zh = _zh_camera(shot)
    cin = (shot.get("visual_details") or {}).get("cinematography") if isinstance(shot.get("visual_details"), dict) else {}
    extra_cam = ""
    if isinstance(cin, dict):
        extra_cam = str(cin.get("move") or cin.get("orbit") or "").strip()

    bits: list[str] = []
    if master_bits:
        bits.append("；".join(b.rstrip("；") for b in master_bits if b))
    if not still:
        ts = _kling_shot_timestamp(master_pattern, shot, duration_seconds)
        if ts:
            bits.append(ts)
    if env:
        bits.append(env.rstrip("。；"))
    bits.append(str(size_zh).rstrip("。；") + "。")
    if still:
        bits.append("定格静帧，人物停在该方位，无运动模糊")


    elem_n = 0
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    shot_block = blocking_to_zh(shot.get("blocking"))
    for subj in vd.get("subjects") or []:
        if not isinstance(subj, dict):
            continue
        name = str(subj.get("name") or subj.get("id") or "").strip() or "角色"
        pos = blocking_to_zh(subj.get("blocking") or subj.get("position")) or shot_block
        act = subj.get("action") if isinstance(subj.get("action"), dict) else {}
        verb = str(act.get("verb") or "").strip()
        path = str(act.get("path") or shot.get("blocking_path") or "").strip()
        if cite in ("omni", "image_omni"):
            elem_n += 1
            who = f"<<<object_{elem_n}>>>" if cite == "image_omni" else f"@element_{elem_n}"
        elif cite == "i2v":
            who = f"@{name}" if not name.startswith("@") else name
        else:
            who = name
        piece = who
        if pos:
            piece += f"在{pos}"
        if still:
            if pos:
                piece += "站定"
        elif path:
            piece += path if path.startswith("从") else f"从{path}"
        elif verb:
            piece += verb
        bits.append(piece.rstrip("。；") + "。")

    if not still and cam_zh:
        cam_sentence = f"镜头{cam_zh}"
        if extra_cam:
            cam_sentence += f"，{extra_cam}"
        bits.append(cam_sentence.rstrip("。；") + "。")

    image_n = 0
    if cite == "omni" and not still:
        for ref in refs or []:
            if not isinstance(ref, dict):
                continue
            kind = str(ref.get("kind") or ref.get("type") or "").strip().lower()
            if kind == "scene_ref" or "场景" in str(ref.get("role") or ""):
                image_n += 1
                bits.append(f"@image_{image_n}锁场景结构")
            elif kind == "prop":
                image_n += 1
                bits.append(f"@image_{image_n}锁道具外形")
    elif cite == "image_omni":
        for ref in refs or []:
            if not isinstance(ref, dict):
                continue
            kind = str(ref.get("kind") or ref.get("type") or "").strip().lower()
            eid = ref.get("element_id")
            if eid is not None and str(eid).strip() != "" and kind in (
                "element", "portrait", "turnaround", "prop", "",
            ):
                continue
            if kind == "scene_ref" or "场景" in str(ref.get("role") or ""):
                image_n += 1
                bits.append(f"<<<image_{image_n}>>>锁场景结构")
            elif kind == "prop":
                image_n += 1
                bits.append(f"<<<image_{image_n}>>>锁道具外形")

    if not still:
        dialogue = _kling_dialogue_line(shot)
        if dialogue:
            bits.append(dialogue)

    extra: list[str] = []
    light = _kling_light_text(shot)
    if light:
        extra.append("光：" + light)
    # P0-8：prompt 层特效（仅动态侧；静帧是特效发生前的状态）。复用主
    # builder 的 dense 语义——只保主特效 1 条，极简措辞进 extra 块。
    if not still:
        vfx = _vfx_section_text(shot, dense=True)
        if vfx:
            extra.append("特效：" + vfx)
    extra.extend(_kling_registry_identity(shot, character_registry))
    return _kling_join_budget(bits, extra, max_chars)


def _kling_shot_timestamp(
    pattern: str | None,
    shot: dict[str, Any],
    duration_seconds: float | None,
) -> str:
    """给逐镜块算 `[0s-Ns]` 时间戳前缀。

    优先级：契约镜头类型表 duration（整片时间窗，如 0-5s / 3-6s）>
    one_take_beats.first_shot（一镜到底时间窗，如 0-11s）> shot 实际时长。
    契约表不可用或时长缺失时返回 ""（保持旧式无时间戳）。
    """
    if pattern:
        try:
            from montage.tools import _shot_contracts as sc

            shot_idx = _kling_shot_index(shot)
            stc = sc.shot_type_for(pattern, shot_idx)
            dur = ""
            if isinstance(stc, dict):
                dur = str(stc.get("duration") or "").strip()
            if not dur:
                # 契约无 duration 时兜底一镜到底时间窗
                otb = sc.one_take_beats(pattern)
                if isinstance(otb, dict):
                    dur = str(otb.get("first_shot") or "").strip()
            m = re.fullmatch(r"(\d+)s?-(\d+)s", dur)
            if m:
                return f"[{m.group(1)}s-{m.group(2)}s]"
        except Exception:  # noqa: BLE001 — 契约层缺失不阻塞
            pass
    try:
        secs = float(duration_seconds or shot.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        secs = 0
    if secs > 0:
        return f"[0s-{int(round(secs))}s]"
    return ""


def _kling_shot_index(shot: dict[str, Any]) -> int:
    """从 shot_id 尾段解析镜头序号（1 基）；解析失败回退 1。"""
    try:
        raw = shot.get("shot_id") or shot.get("id") or ""
        if isinstance(raw, str) and "_" in raw:
            tail = raw.rsplit("_", 1)[-1]
            if tail.isdigit():
                return int(tail)
    except (TypeError, ValueError):
        pass
    return 1


def _kling_master_pattern(pattern: str | None) -> dict[str, Any] | None:
    """取 lib.kling_master 中模式的母带定义；未知/缺失返回 None。"""
    try:
        from lib.kling_master import pick_master
    except Exception:  # noqa: BLE001 — 库缺失时不阻塞可灵环
        return None
    return pick_master(pattern)


def _kling_contract_block(
    pattern: str | None,
    shot: dict[str, Any],
) -> list[str]:
    """把逐镜规约层的契约信息渲染成母带块内的一组短句。

    只消费与当前镜相关的契约（镜头类型/状态演进/卡司/人群/声画同步/能量曲线），
    避免整片重复。未知模式或缺依赖时返回空列表。
    """
    if not pattern:
        return []
    try:
        from montage.tools import _shot_contracts as sc
    except Exception:  # noqa: BLE001
        return []
    bits: list[str] = []

    # 镜头类型强制（按 shot 序号，1 基）
    shot_idx = _kling_shot_index(shot)
    stc = sc.shot_type_for(pattern, shot_idx)
    if isinstance(stc, dict):
        typ = str(stc.get("type") or "").strip()
        if typ:
            dur = str(stc.get("duration") or "").strip()
            note = str(stc.get("note") or "").strip()
            seg = f"本镜类型：{typ}"
            if dur:
                seg += f"，时段{dur}"
            if note:
                seg += f"，{note}"
            bits.append(seg)

    # 一镜到底走位（首镜输出整条 beat 序列）
    otb = sc.one_take_beats(pattern)
    if isinstance(otb, dict) and shot_idx == 1:
        first_shot = str(otb.get("first_shot") or "").strip()
        beats = [str(b) for b in (otb.get("beats") or []) if str(b or "").strip()]
        seg = ""
        if first_shot:
            seg = f"一镜到底：{first_shot}"
        if beats:
            seg = (seg + "；" if seg else "") + "走位：" + "；".join(f"{i}. {b}" for i, b in enumerate(beats, 1))
        cut_at = str(otb.get("cut_at") or "").strip()
        if cut_at:
            seg = (seg + "；" if seg else "") + f"首次切镜：{cut_at}"
        if seg:
            bits.append(seg)

    # 跨镜状态演进（全镜覆盖：切换点前输出禁止态，切换点起输出新态）
    stl = sc.state_timeline(pattern)
    if isinstance(stl, dict):
        for key, entry in stl.items():
            if not isinstance(entry, dict):
                continue
            switch = entry.get("switch_shot")
            nb = entry.get("never_before_shot")
            try:
                switch_i = int(switch or 0)
            except (TypeError, ValueError):
                continue
            try:
                nb_i = int(nb or 0)
            except (TypeError, ValueError):
                nb_i = 0
            char = entry.get("character") or key
            state_a = str(entry.get("state_a") or "").strip()
            state_b = str(entry.get("state_b") or "").strip()
            note = str(entry.get("note") or "").strip()
            if nb_i and shot_idx < nb_i and state_a and state_b:
                # 切换点前：负向约束，禁止新态
                bits.append(f"{char}状态约束：仅{state_a}，禁止{state_b}（{note}）")
            elif nb_i and shot_idx >= nb_i and state_b:
                # 切换点起：新态生效
                bits.append(f"{char}状态演进：{state_b}自本镜起，{note}")

    # 角色弧线（首镜输出整条弧线）
    arc = sc.character_arcs(pattern)
    if isinstance(arc, dict) and shot_idx == 1:
        for name, entry in arc.items():
            if not isinstance(entry, dict):
                continue
            stages = entry.get("stages") or []
            if stages:
                bits.append(f"角色弧线：{name} {'→'.join(str(s) for s in stages)}")

    # 卡司构成与标签唯一（首镜输出）
    cast = sc.cast_contract(pattern)
    if isinstance(cast, dict) and shot_idx == 1:
        req = cast.get("required") or []
        forb = cast.get("forbidden") or []
        if req:
            bits.append("卡司构成：" + "、".join(str(x) for x in req))
        if forb:
            bits.append("禁止：" + "、".join(str(x) for x in forb))
        lu = cast.get("label_uniqueness")
        if isinstance(lu, dict) and lu.get("rule"):
            bits.append("标签唯一：" + str(lu["rule"]))

    # 人群规则（首镜输出）
    crowd = sc.crowd_rules(pattern)
    if isinstance(crowd, dict) and shot_idx == 1:
        segs = []
        if crowd.get("density"):
            segs.append(f"密度{crowd['density']}")
        if crowd.get("no_grid"):
            segs.append("绝不排整齐队形")
        if crowd.get("pose_variety"):
            segs.append("舞姿/姿态各异彼此有别")
        if crowd.get("no_interpenetration"):
            segs.append("互不穿模")
        if crowd.get("spacing"):
            segs.append(str(crowd["spacing"]))
        note = str(crowd.get("note") or "")
        if note and note not in segs:
            segs.append(note)
        if segs:
            bits.append("人群：" + "；".join(segs))

    # 声画同步（首镜输出）
    sync = sc.audio_syncs(pattern)
    if isinstance(sync, dict) and shot_idx == 1:
        trigger = str(sync.get("trigger") or "").strip()
        action = str(sync.get("action") or "").strip()
        timing = str(sync.get("timing") or "").strip()
        if trigger and action:
            seg = f"声画同步：{trigger}，{action}"
            if timing:
                seg += f"，时序要求：{timing}"
            bits.append(seg)

    # 情绪能量曲线（首镜输出整条曲线）
    curve = sc.energy_curves(pattern)
    if isinstance(curve, dict) and shot_idx == 1:
        segs = [str(x) for x in (
            curve.get("start"), curve.get("build"), curve.get("peak"),
            curve.get("after_peak"), curve.get("end"),
        ) if str(x or "").strip()]
        if segs:
            bits.append("情绪曲线：" + "；".join(segs))

    # 色域/尺度/方向/辉光铁律（全局，每镜输出——生成器易漂移需重申）
    pl = sc.palette_law(pattern)
    if isinstance(pl, dict):
        segs = []
        if pl.get("note"):
            segs.append(str(pl["note"]))
        for f in pl.get("forbidden") or []:
            if str(f or "").strip():
                segs.append("严禁" + str(f).rstrip("；"))
        if segs:
            bits.append("色域：" + "；".join(segs))
    sl = sc.scale_iron_law(pattern)
    if isinstance(sl, dict):
        segs = [str(sl["rule"])] if sl.get("rule") else []
        for f in sl.get("forbidden") or []:
            if str(f or "").strip():
                segs.append("严禁" + str(f).rstrip("；"))
        if segs:
            bits.append("尺度：" + "；".join(segs))
    fdl = sc.flow_direction_law(pattern)
    if isinstance(fdl, dict):
        segs = [str(fdl["rule"])] if fdl.get("rule") else []
        for f in fdl.get("forbidden") or []:
            if str(f or "").strip():
                segs.append("严禁" + str(f).rstrip("；"))
        if segs:
            bits.append("方向：" + "；".join(segs))
    gl = sc.glow_law(pattern)
    if isinstance(gl, dict):
        segs = [str(gl["rule"])] if gl.get("rule") else []
        if gl.get("note"):
            segs.append(str(gl["note"]))
        for f in gl.get("forbidden") or []:
            if str(f or "").strip():
                segs.append("严禁" + str(f).rstrip("；"))
        if segs:
            bits.append("辉光：" + "；".join(segs))

    return bits


AGNES_PROMPT_MAX = 3000


def apply_agnes_prompt_limit(
    text: str,
    *,
    fallback: bool,
    max_chars: int = AGNES_PROMPT_MAX,
) -> tuple[str, bool]:
    """未超限原样返回。超限且未 fallback 则 over=True；fallback 时先压缩再截断。"""
    body = str(text or "")
    if len(body) <= max_chars:
        return body, False
    if not fallback:
        return body, True
    body = _compress_agnes_v25(body, max_chars)
    if len(body) > max_chars:
        body = body[:max_chars]
    return body, False


def _audio_section_text(shot: dict[str, Any]) -> str:
    """渲染镜头的非对白声音设计（SFX / 环境音）。

    Agnes 会根据视频提示词文本生成匹配音频（用户已验证），
    所以每镜头的音频设计以散文式【声音】段写进视频动态提示词。
    对白单独渲染（见 ``_dialogue_section_text``），BGM 也单独渲染
    （见 ``_bgm_section_text``）；本函数只负责 SFX 和环境音。
    两者都没有时返回 ""。
    """
    ap = shot.get("audio_prompt") or {}
    parts: list[str] = []

    for s in ap.get("sfx") or []:
        act = s.get("action_ref") or ""
        sound = s.get("sound") or ""
        onset = s.get("onset")
        seg = ""
        if onset is not None:
            onset_txt = str(onset)
            if onset_txt.replace(".", "", 1).isdigit():
                seg = f"第{onset_txt}秒触发音效"
            else:
                seg = f"{onset_txt}触发音效"
        elif act:
            seg = f"{act}触发音效"
        if sound:
            seg += (f"：{sound}" if seg else f"音效：{sound}")
        if seg:
            parts.append(seg)

    amb = ap.get("ambience") or {}
    if amb.get("description"):
        lvl = amb.get("level") or ""
        seg = f"环境音：{amb['description']}"
        if lvl:
            seg += f"，电平：{lvl}"
        parts.append(seg)

    return "；".join(parts)


def _bgm_section_text(shot: dict[str, Any]) -> str:
    """把镜头的 BGM 渲染成独立受保护的【背景音乐】段。

    BGM 是用户可选功能（``bgm_choice != "none"``），因此压缩时
    它的保留优先级要高于 SFX/环境音（【声音】）—— 已选择的
    BGM 绝不因给音效腾空间而被丢弃。没有 BGM 时返回 ""。
    """
    bgm = (shot.get("audio_prompt") or {}).get("bgm") or {}
    if not bgm:
        return ""
    mood = bgm.get("mood") or ""
    tempo = bgm.get("tempo") or ""
    instrument = bgm.get("instrument") or ""
    lvl = bgm.get("level") or ""
    seg = "，".join(
        filter(None, [f"情绪：{mood}" if mood else "", f"节奏：{tempo}" if tempo else "",
                      f"乐器：{instrument}" if instrument else "", f"电平：{lvl}" if lvl else ""])
    )
    return seg or ""


def _vfx_section_text(shot: dict[str, Any], *, dense: bool = True) -> str:
    """把镜头的 prompt 层 vfx 渲染成【特效】段（P0-8）。

    只渲染 ``layer=prompt`` 条目（post 层是 ffmpeg 后期，不进生成提示词）。
    每条格式：「onset 秒处，kind（强度 X）」——onset 是镜内相对秒；
    onset 缺省按 0 处理。dense 下只保 onset 靠前的主特效 1 条
    （具体视觉语言由 VFX_DIRECTOR.md 纪律保证；抽象词由 _ABSTRACT_WORD_RE
    兜底剔除）。没有 prompt 层条目时返回 ""。
    """
    items = [v for v in (shot.get("vfx") or []) if isinstance(v, dict)]
    prompt_items = [
        v for v in items
        if str(v.get("layer") or "") == "prompt" and str(v.get("kind") or "").strip()
    ]
    if not prompt_items:
        return ""
    prompt_items.sort(key=lambda v: float(v.get("onset") or 0))
    if dense:
        prompt_items = prompt_items[:1]

    def _one(v: dict[str, Any]) -> str:
        kind = str(v.get("kind")).strip()
        onset = float(v.get("onset") or 0)
        seg = f"{onset:.1f}秒处，{kind}"
        intensity = v.get("intensity")
        if intensity is not None:
            try:
                val = float(intensity)
            except (TypeError, ValueError):
                val = None
            if val is not None:
                strength = "强" if val >= 0.66 else ("中" if val >= 0.33 else "弱")
                seg += f"（强度{strength}）"
        return seg

    return "；".join(_one(v) for v in prompt_items)


def _section_assembler(
    shot: dict[str, Any],
    character_registry: list[dict[str, Any]] | None,
    style_context: dict[str, Any] | None,
    *,
    agnes_audio: bool = False,
    dense: bool = True,
    is_i2v: bool = False,
    presence_names: dict[str, str] | None = None,
) -> list[str]:
    """把提示词组装成有序的带标签段落列表。

    返回 "【段名】 内容" 字符串列表（按 _SECTION_ORDER 排序）。空段
    会被省略。这是未压缩的原始草稿。

    ``dense`` 会剔除抽象套话词，并把 SFX 限制到 1–2 个关键音。
    完整的【角色与外貌】段总是进入首帧静态侧
    （外貌锚点属于静态图片）。视频动态侧绝不含它，
    因为 角色与外貌 是静态专属键 —— ``build_shot_prompt_pair``
    只在即梦文生视频兜底时显式把它复制进视频提示词
    （没有图片可锚定，所以必须写出外貌，否则角色会漂移）。
    保留 ``is_i2v`` 是为了与解析操作的调用方保持签名兼容。
    """
    vd = shot.get("visual_details", {})
    shot_kind = shot.get("shot_kind", "video")
    sections: list[str] = []

    # 角色与外貌：始终生成并进入 first_frame 静态段（身份锚点写在首帧图）。
    # 图生时 video_prompt 不含它（角色段属 _STATIC_SECTION_KEYS，天然只在
    # 静态侧；动态段另由 build_shot_prompt_pair 在即梦文生兜底时显式复制）。
    role_parts = []
    for subj in vd.get("subjects", []):
        appearance = _resolve_appearance(subj, character_registry, style_context)
        position = subj.get("position") or ""
        name = _subject_display_name(subj, character_registry)
        role_parts.append(f"{name}: {appearance}" + (f"，{position}" if position else ""))
    if role_parts:
        sections.append("【角色与外貌】 " + "；".join(role_parts))

    # 空镜硬约束（2026-09-20）：presence.empty_reason 写明"人已退场"的镜，
    # 提示词必须显式禁止出现人物——否则模型会往山道/庭院里"补人"（片尾空镜实测）。
    presence_row = shot.get("presence") if isinstance(shot.get("presence"), dict) else {}
    if (
        str(presence_row.get("empty_reason") or "").strip()
        and not vd.get("subjects")
    ):
        sections.append(
            "【空镜】本镜是纯环境空镜：画面内**不出现任何人物、人影、剪影或动物**；"
            "只保留场景陈设与清单里写明的道具；镜头运动只表现环境（雨雾、光、风）"
        )

    # 动作
    action_parts = []
    for subj in vd.get("subjects", []):
        # 用中文显示名，避免把 wen_ruchun 这类 id 写进生成提示词。
        name = _subject_display_name(subj, character_registry) or subj.get("id") or ""
        # 存在节拍级时间线时优先采用（向后兼容）。
        timeline = _beat_timeline_text(subj, name=name)
        if timeline:
            action_parts.append(timeline)
            continue
        act = subj.get("action") or {}
        verb = act.get("verb")
        if not verb:
            continue
        manner = act.get("manner")
        emotion = act.get("emotion")
        parts = [f"{name} {verb}"]
        if manner:
            parts.append(f"（{manner}）")
        if emotion:
            parts.append(f"，情绪：{emotion}")
        action_parts.append("".join(parts))
    if action_parts:
        sections.append("【动作】 " + "；".join(action_parts))

    # 特效（P0-8）：prompt 层 vfx 时间点事件（动态专属段——首帧静态图是特效
    # 发生前的状态，不携带）。dense 下只保主特效 1 条（即梦 400 字预算不挤爆）。
    vfx_text = _vfx_section_text(shot, dense=dense)
    if vfx_text:
        sections.append("【特效】 " + vfx_text)

    # 物体与道具
    objects = [o.get("appearance") or o.get("id") or "" for o in vd.get("objects", [])]
    objects = [o for o in objects if o]
    if objects:
        sections.append("【物体与道具】 " + "、".join(objects))

    # 环境
    env = vd.get("environment")
    if env:
        sections.append("【环境】 " + env)

    # 光线
    lighting = vd.get("lighting")
    if lighting:
        sections.append("【光线】 " + lighting)

    # 台词（verbatim：占位符 {{对白}} 完整保留，永不缩减；Agnes 路径用台词全文）→ 进视频动态提示词
    dialogue_text = _dialogue_section_text(
        shot,
        use_full_text=agnes_audio,
        names={
            **{
                str(row["id"]): str(row.get("name") or row["id"])
                for row in (character_registry or [])
                if isinstance(row, dict) and row.get("id")
            },
            **(presence_names or {}),
        },
    )
    if dialogue_text:
        sections.append("【台词】 " + dialogue_text)

    # 背景音乐（用户可选功能，压缩时保留优先级高于 SFX/环境音）
    bgm_text = _bgm_section_text(shot)
    if bgm_text:
        sections.append("【背景音乐】 " + bgm_text)

    # 声音（SFX / 环境音；Agnes 靠提示词出声音；SFX 在压缩时最优先砍）
    # dense：只保留与节拍对齐的 1–2 个关键音（极简），避免 400 字预算被音效吃光
    audio_text = _audio_section_text(shot)
    if audio_text:
        if dense:
            audio_parts = [p for p in audio_text.split("；") if p][:2]
            audio_text = "；".join(audio_parts)
        if audio_text:
            sections.append("【声音】 " + audio_text)

    # 镜头
    cine = vd.get("cinematography") or {}
    camera_parts = []
    if shot_kind == "video":
        angle = cine.get("angle")
        path = cine.get("camera_path")
        comp = cine.get("frame_composition")
        if angle:
            camera_parts.append(angle)
        if path:
            camera_parts.append(path)
        if comp:
            camera_parts.append(comp)
    else:  # 图片：仅 angle + frame_composition（无 camera_path）
        angle = cine.get("angle")
        comp = cine.get("frame_composition")
        if angle:
            camera_parts.append(angle)
        if comp:
            camera_parts.append(comp)
    if camera_parts:
        sections.append("【镜头】 " + "，".join(camera_parts))
    else:
        sl = shot.get("shot_language") if isinstance(shot.get("shot_language"), dict) else {}
        fallback: list[str] = []
        size = sl.get("shot_size")
        if size:
            fallback.append(_SHOT_SIZE_PHRASES.get(str(size), str(size)))
        move = sl.get("camera_movement")
        if shot_kind == "video" and move:
            fallback.append(_MOVEMENT_PHRASES.get(str(move), str(move)))
        if fallback:
            sections.append("【镜头】 " + "，".join(fallback))

    # 衔接
    cc = shot.get("camera_continuity") or {}
    continuity_parts = []
    if shot_kind == "video":
        if cc.get("prev"):
            continuity_parts.append(f"承接：{cc['prev']}")
        if cc.get("next"):
            continuity_parts.append(f"引出：{cc['next']}")
        if cc.get("match_on"):
            continuity_parts.append(f"衔接：{cc['match_on']}")
    # 末帧文本交接：从实际最后一帧取开场状态
    if shot.get("end_frame_state", {}).get("opening_from_actual_last_frame"):
        continuity_parts.append("从上一镜头实际末帧画面续起")
    if continuity_parts:
        sections.append("【衔接】 " + "；".join(continuity_parts))

    # 在场清单 + 承接表（B2.5）：每镜独立送给模型，模型看不到上一镜，
    # 所以"谁在场/拿什么/在哪 + 上镜哪些必须保留"必须逐镜写全。
    presence = shot.get("presence")
    if isinstance(presence, dict) and presence:
        from lib.shot_presence import presence_prompt_lines

        continuity = shot.get("continuity") if isinstance(shot.get("continuity"), dict) else None
        # 提示词里用**中文名**而不是 id：角色取自 registry，道具/场景取 presence_names
        # （visual_prompt_builder 从 script.props 注入）。缺名时回落 id，不静默删段。
        names: dict[str, str] = {}
        for row in character_registry or []:
            if isinstance(row, dict) and row.get("id"):
                names[str(row["id"])] = str(row.get("name") or row["id"])
        names.update(presence_names or {})
        sections.extend(presence_prompt_lines(presence, continuity, names=names))

    if dense:
        # 删抽象套话（共享精简门）：去掉「优雅地/电影感/高质量/8k/杰作」等
        # 不携带像素/运动/声音信息的词。角色名与台词原样保留（台词 verbatim）。
        sections = [
            s if _section_key(s) == "台词" else _strip_abstract_words(s)
            for s in sections
        ]

    return sections


def build_shot_prompt_detailed(
    shot: dict[str, Any],
    character_registry: list[dict[str, Any]] | None = None,
    style_context: dict[str, Any] | None = None,
    provider_max_chars: int | None = None,
    *,
    agnes_audio: bool = False,
    english_visual: bool = False,
    dense: bool = True,
    jimeng_prompt: bool = False,
) -> str:
    """构建一个高度详细的逐镜头生成提示词。

    Args:
        shot: 来自 shot_prompts.schema.json 的镜头 dict（visual_details、
              cinematography、camera_continuity、shot_kind、end_frame_state）。
        character_registry: scene_plan.character_registry（全局外貌）。
        style_context: 源自 playbook 的风格（asset_generation.character_appearance_default）。
        provider_max_chars: 可选的生成器提示词上限（例如即梦为 800）。
                           设置后，组装好的提示词会被压缩到该上限内，
                           且不丢弃关键段。
        agnes_audio: 为 True（Agnes 闭环）时，把完整对白文本渲染进
                     【台词】段，让 Agnes 根据提示词生成口播音频。
                     为 False（Seed-Audio/TTS 路径）时，保留
                     `{{对白:...}}` 占位符（全文在别处替换）。
        english_visual: 为 True 时，在最终提示词后追加压缩后的英文画质层
                     （视频镜头为【画质基底】+【衔接】），并且 —— 当
                     ``agnes_audio`` 也为真时 —— 追加中文【音频锁定】句。
                     返回单个 ``str``（英文 ``negative_prompt`` 只由
                     ``build_shot_prompt_pair`` 的 dict 暴露，这里不返回）。
        dense: 为 True（默认）时，剔除套话/抽象词并让 SFX 保持极简。
                     【角色与外貌】锚点留在首帧静态段（它只从视频动态侧
                     排除，动态侧从不携带静态键）。Agnes 与 Jimeng 共用。
                     为 False 时恢复 dense 之前的冗长风格。
        jimeng_prompt: 为 True 时，即使 ``english_visual`` 已设置也丢弃英文画质层，
                     保留中文结构并追加无水印护栏。硬上限通过
                     ``provider_max_chars`` 处理（推荐 400 / 硬上限 800）。

    Returns:
        最终提示词字符串（provider_max_chars 为 None 时为原始版本，否则为压缩版）。
    """
    operation = str(shot.get("operation") or shot.get("generation_function") or "text_to_video")
    is_i2v = operation == "image_to_video"
    sections = _section_assembler(
        shot,
        character_registry,
        style_context,
        agnes_audio=agnes_audio,
        dense=dense,
        is_i2v=is_i2v,
        presence_names=presence_names,
    )
    raw = "。".join(sections)

    if jimeng_prompt:
        english_visual = False

    if provider_max_chars is None or len(raw) <= provider_max_chars:
        prompt = raw
    else:
        prompt = _compress_prompt(raw, sections, provider_max_chars)

    if jimeng_prompt:
        prompt = _append_jimeng_guard(prompt, shot, provider_max_chars)
    elif english_visual or agnes_audio:
        prompt, _ = _append_english_visual_layer(
            prompt,
            shot,
            english_visual=english_visual,
            agnes_audio=agnes_audio,
            provider_max_chars=provider_max_chars,
        )

    return prompt


def _compress_prompt(raw: str, sections: list[str], max_chars: int) -> str:
    """按严格优先级顺序把提示词压缩到 max_chars 以内。

    段落按重要性排序（最重要的在前）。优先级较低的段落
    先被丢弃，直到提示词符合限制：
        台词 > 角色与外貌 > 环境 > 姿态 > 动作 > 镜头 > 风格 > 光线 > 层次 >
        物体 > 质量 > 保留 > 衔接 > 背景音乐 > 声音
    对白（【台词】）是唯一最高优先级，绝不缩减或截断 —— 无论压力多大，
    整段对白字符串都会保留（只有周围优先级更低的段落被丢弃）。
    BGM（【背景音乐】）的保留优先级高于 SFX/环境音（【声音】），
    后者最先被丢弃，让音效用最少的字。
    """
    _PRIORITY = [
        "台词", "角色与外貌", "环境", "姿态", "动作", "特效", "镜头",
        "风格", "光线", "层次", "物体与道具", "质量", "保留",
        "衔接", "背景音乐", "声音",
    ]

    def _key_of(s: str) -> str:
        return s[1:s.index("】")] if "】" in s else ""

    # 按优先级排序（稳定排序；同键内保持原始顺序）。
    ordered = sorted(
        sections,
        key=lambda s: _PRIORITY.index(_key_of(s)) if _key_of(s) in _PRIORITY else len(_PRIORITY),
    )

    def _joint(parts: list[str]) -> str:
        return "。".join(parts)

    # 从优先级最低（`ordered` 末尾）开始丢弃，直到符合限制。
    remaining = list(ordered)
    while len(remaining) > 1 and len(_joint(remaining)) > max_chars:
        remaining.pop()
    result = _joint(remaining)

    # 最终硬兜底：只有最重要的段单独就已超预算时才会走到这里。
    # 完整保留 台词；截断其他段，绝不在对白字符串中间切开。
    # 若仅 台词 就仍超预算，则整段保留（逐字）而非切割语音 ——
    # 关键内容优先于软字符上限。
    if len(result) > max_chars:
        budget = max_chars
        trimmed: list[str] = []
        for s in remaining:
            if budget <= 0 and _key_of(s) != "台词":
                break
            if _key_of(s) == "台词":
                piece = s  # 对白逐字保留，绝不截断
            else:
                piece = s[:budget]
            trimmed.append(piece)
            budget -= len(piece)
        result = "。".join(trimmed)

    return result


# ---------------------------------------------------------------------------
# 英文画质层 + 中文音频锁定（压缩后追加）
# ---------------------------------------------------------------------------



def build_shot_prompt_pair(
    shot: dict[str, Any],
    character_registry: list[dict[str, Any]] | None = None,
    style_context: dict[str, Any] | None = None,
    provider_max_chars: int | None = None,
    *,
    agnes_audio: bool = False,
    english_visual: bool = False,
    enrich_first_frame: bool = False,
    enrich_video_frame: bool = False,
    pose_beat_id: str | None = None,
    at_seconds: float | None = None,
    keep_reference: bool = False,
    dense: bool = True,
    jimeng_prompt: bool = False,
    operation: str | None = None,
    agnes_v25: bool = False,
    kling_prompt: bool = False,
    kling_cite: str = "omni",
    locations: list[dict[str, Any]] | None = None,
    refs: list[dict[str, Any]] | None = None,
    master_prompt: str | None = None,
    master_pattern: str | None = None,
    presence_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """把一个镜头/场景拆成两条提示词：首帧图片 + 视频运动。

    - ``first_frame_prompt``：静态图片提示词（外貌 / 道具 /
      环境 / 光线 / 镜头角度+构图）—— 喂给图片生成器
      （例如 万相）生成首帧。
    - ``video_prompt``：视频运动提示词（动作节拍时间线 / 运镜 /
      衔接）。当 ``shot_kind == "image"`` 时为 ``None``（无运动段）。

    ``agnes_audio=True``（Agnes 闭环）会把完整对白文本渲染进
    ``video_prompt`` 的【台词】段，让 Agnes 说出它；否则保留
    ``{{对白:...}}`` 占位符供 Seed-Audio/TTS 路径使用。

    ``english_visual=True`` 会把压缩后的英文画质层
    （【画质基底】+ 帧一致性【衔接】）以及中文音频锁定
    （【音频锁定】，当 ``agnes_audio`` 时）追加到 ``video_prompt``，
    并返回固定的英文 ``negative_prompt``。它还会把仅图片的英文
    画质层（``_IMAGE_ENGLISH_VISUAL_BASELINE``，无 ``natural stable
    motion``）追加到 ``first_frame_prompt``（视频和图片镜头都如此）——
    图片 A/B 测试显示静态图 中英混合 > 纯中文，所以首帧现在和视频一样
    享有英文画质收益。首帧层绝不携带【音频锁定】（图片没有音频）
    或视频帧一致性。当 ``shot_kind == "image"`` 时没有可追加的
    ``video_prompt``，因此跳过视频侧层；首帧层仍会追加，但
    ``negative_prompt`` 保持 ``""``（图片 API 没有负面提示词
    通道，返回一个会误导调用方去传它）。

    ``enrich_video_frame=True``（仅当 ``shot_kind == "video"`` 时有意义）
    会在压缩之后、英文画质层之前，确定性地把动态词库参考
    （镜头 / 动作 / 光线）注入 ``video_prompt`` —— 与
    ``enrich_first_frame`` 对称。当 ``english_visual=True`` 时，
    ``video_budget``（类似 ``ff_budget``）为英文层预留空间，
    让注入的中文参考绝不挤掉英文画质词。默认 ``False`` ——
    既有 ``video_prompt`` 行为不变。

    ``dense=True``（默认，Agnes 与 Jimeng 都是）：剔除套话/抽象
    词，让 SFX 保持极简（1–2 个关键音），从图生视频的
    ``video_prompt`` 中丢弃完整【角色与外貌】（身份已在首帧
    图片里）以及动态侧重复的外貌 —— 定妆照锚点
    仍留在 ``first_frame_prompt`` 里。``text_to_video`` 保留外貌
    锚点（没有图片可锚定）。``dense=False`` 恢复之前冗长的行为。

    ``jimeng_prompt=True``（即梦路径）：强制 ``english_visual=False``（无
    英文层，仅中文官方结构），追加画面禁字护栏
    「无字幕无文字无水印」，并保留硬 ``provider_max_chars`` 上限
    （推荐 400 / 硬上限 800）。当调用方知道已解析的操作时
    （例如选择器路由之后），``operation`` 会覆盖操作检测，
    让 dense 的 i2v 外貌丢弃与实际提交一致。

    对没有 ``visual_details`` 的场景级结构回退：
    首帧提示词为 ``build_shot_prompt()``（短格式），
    ``video_prompt`` 为 ``None``。

    Returns:
        {
            "first_frame_prompt": str | None,
            "video_prompt": str | None,
            "shot_kind": str,
            "provider_max_chars": int | None,
            "negative_prompt": str,
            "image_negative_prompt": str,
        }
    """
    shot_kind = shot.get("shot_kind", "video")

    if "visual_details" not in shot:
        # 场景级回退（无镜头数据的解说/动画场景）。
        short = build_shot_prompt(shot, style_context)
        return {
            "first_frame_prompt": short or None,
            "video_prompt": None,
            "shot_kind": shot_kind,
            "provider_max_chars": provider_max_chars,
            "negative_prompt": "",
            "image_negative_prompt": "",
        }

    if jimeng_prompt:
        english_visual = False

    resolved_operation = operation or str(
        shot.get("operation") or shot.get("generation_function") or "text_to_video"
    )
    is_i2v = resolved_operation == "image_to_video"

    sections = _section_assembler(
        shot,
        character_registry,
        style_context,
        agnes_audio=agnes_audio,
        dense=dense,
        is_i2v=is_i2v,
        presence_names=presence_names,
    )
    static_sections, dynamic_sections = _split_first_frame_sections(
        sections,
        shot,
        pose_beat_id=pose_beat_id,
        at_seconds=at_seconds,
        style_context=style_context,
        keep_reference=keep_reference,
        dense=dense,
        jimeng_prompt=jimeng_prompt,
        character_registry=character_registry,
    )

    first_frame_raw = "。".join(static_sections)
    ff_budget = provider_max_chars
    image_layer = _image_english_layer_text(style_context) if english_visual else ""
    if english_visual and provider_max_chars is not None:
        # 当 english_visual 激活时，为仅图片的英文层预留空间，
        # 让英文画质词绝不被压缩丢弃。预算与追加共用
        # ``_image_english_layer_text``。
        ff_budget = max(1, provider_max_chars - len(image_layer) - 20)
    if ff_budget is not None and len(first_frame_raw) > ff_budget:
        first_frame_prompt = _compress_prompt(
            first_frame_raw, static_sections, ff_budget
        )
    else:
        first_frame_prompt = first_frame_raw

    if enrich_first_frame:
        # 词库注入遵守同一预留预算，因此绝不占用图片英文层
        # 之后需要的空间。
        first_frame_prompt = _enrich_first_frame_with_library(
            first_frame_prompt, shot, ff_budget, dense=dense
        )

    if english_visual:
        first_frame_prompt = _append_first_frame_english_layer(
            first_frame_prompt, provider_max_chars, style_context
        )

    if shot_kind == "video" and dynamic_sections:
        if jimeng_prompt and not is_i2v:
            # 即梦纯文生兜底：video_prompt 必须带外貌锚点（模型看不到首帧图，
            # 人物会漂）。把 first_frame 的【角色与外貌】复制进动态侧开头。
            role_static = [s for s in static_sections if _section_key(s) == "角色与外貌"]
            dynamic_sections = role_static + dynamic_sections
        video_raw = "。".join(dynamic_sections)
        video_budget = provider_max_chars
        if english_visual and provider_max_chars is not None:
            # 当 english_visual 激活时，为视频英文层预留空间，
            # 让英文画质词绝不被压缩或词库注入的参考后缀丢弃。
            video_budget = max(1, provider_max_chars - len(_ENGLISH_VISUAL_BASELINE) - 20)
        if video_budget is not None and len(video_raw) > video_budget:
            video_prompt = _compress_prompt(video_raw, dynamic_sections, video_budget)
        else:
            video_prompt = video_raw

        if enrich_video_frame:
            # 词库注入遵守同一预留预算，因此绝不占用视频英文层
            # 之后需要的空间。
            video_prompt = _enrich_video_with_library(
                video_prompt, shot, video_budget, dense=dense
            )
    else:
        video_prompt = None

    if agnes_v25 and shot_kind == "video":
        video_prompt = build_agnes_v25_prompt(
            shot,
            character_registry=character_registry,
            locations=locations,
            refs=refs,
            duration_seconds=shot.get("duration_seconds"),
            max_chars=int(provider_max_chars or 3000),
            presence_names=presence_names,
            style_context=style_context,
        )

    if kling_prompt:
        limit = int(provider_max_chars or 2500)
        still_cite = "image_omni" if str(kling_cite or "") == "omni" else kling_cite
        first_frame_prompt = build_kling_prompt(
            shot,
            character_registry=character_registry,
            locations=locations,
            refs=refs,
            duration_seconds=shot.get("duration_seconds"),
            max_chars=limit,
            cite=still_cite,
            still=True,
        )
        if shot_kind == "video":
            video_prompt = build_kling_prompt(
                shot,
                character_registry=character_registry,
                locations=locations,
                refs=refs,
                duration_seconds=shot.get("duration_seconds"),
                max_chars=limit,
                cite=kling_cite,
                still=False,
                master_prompt=master_prompt,
                master_pattern=master_pattern,
            )

    negative_prompt = ""
    if video_prompt is not None and not agnes_v25 and not kling_prompt:
        if jimeng_prompt:
            video_prompt = _append_jimeng_guard(video_prompt, shot, provider_max_chars)
        else:
            video_prompt, negative_prompt = _append_english_visual_layer(
                video_prompt,
                shot,
                english_visual=english_visual,
                agnes_audio=agnes_audio,
                provider_max_chars=provider_max_chars,
            )

    return {
        "first_frame_prompt": first_frame_prompt or None,
        "video_prompt": video_prompt,
        "shot_kind": shot_kind,
        "provider_max_chars": provider_max_chars,
        "negative_prompt": negative_prompt,
        "image_negative_prompt": _image_negative_text(
            style_context, english_visual=english_visual
        ),
    }


def build_reference_image_prompt(
    kind: str,
    *,
    character: dict[str, Any] | None = None,
    scene: dict[str, Any] | None = None,
    style_context: dict[str, Any] | None = None,
    provider_max_chars: int | None = None,
    english_visual: bool = True,
    enrich_first_frame: bool = True,
    keep_reference: bool = False,
) -> dict[str, Any]:
    """经由同一图片栈构建 定妆照 / 场景参考图 提示词。

    返回与 ``build_shot_prompt_pair`` 相同的键结构（``video_prompt``
    恒为 ``None``，``shot_kind`` 为 ``"image"``）。当必需的主体文本
    缺失时抛出 ``ValueError``，让工具能够安全失败。
    """
    kind = (kind or "").strip()
    if kind == "portrait":
        char = character or {}
        appearance = str(char.get("appearance") or "").strip()
        ethnicity = str(char.get("ethnicity_default") or "").strip()
        outfit = str(char.get("outfit_anchor") or char.get("outfit") or "").strip()
        playbook = ""
        if style_context:
            playbook = str(
                ((style_context.get("asset_generation") or {}).get("character_appearance_default") or "")
            ).strip()
        core = appearance or ethnicity or playbook
        if not core:
            raise ValueError(
                "portrait requires character.appearance, ethnicity_default, "
                "or style_context.asset_generation.character_appearance_default"
            )
        name = str(char.get("name") or char.get("id") or "").strip()
        role = f"{name}: {core}" if name else core
        sections = [f"【角色与外貌】 {role}"]
        if outfit:
            sections.append(f"【物体与道具】 {outfit}")
        # 定妆照必须是**全身正面立像**：早前只写"主体完整"导致模型给出
        # 裁掉头部的半身/服装特写（用户实测 portrait_monk / portrait_wen_ruchun
        # 只有躯干），身份参考因此不可用。
        # 构图段放最前：Agnes 图片不支持 negative_prompt，"禁裁切"只能靠正向提示词，
        # 且模型对开头的构图句最敏感（实测把构图放末尾仍被裁掉头）。
        sections.insert(0, (
            # 2026-09-19 用户拍板 + 三次实测：定妆照走**近景胸像**——
            # "全身"与"半身到腰"都会被模型推近成"躯干+衣袍"（头顶出画，方差大）；
            # 以面部为主体的胸像构图稳定给出完整头部与脸。全身体态交给四视图。
            "【构图】 近景胸像定妆照：以人物面部为主体，画面范围从**完整发顶**到胸口；"
            "脸部清晰居中、占画面约三分之一；头部绝对完整（发际线与头顶都在画面内）；"
            "严禁裁切头顶、严禁只拍躯干或衣袍局部；纯色中性背景，无环境道具"
        ))
        sections.append(
            "【镜头】 正面胸像：再次确认发顶没有被切掉、整张脸完整可见；"
            "肩线入画；背景纯色、无环境道具"
        )
        style = _image_style_text(style_context, "。".join(sections))
        if style:
            sections.append(f"【风格】 {style}")
        sections.append(f"【质量】 {_IMAGE_QUALITY_TEXT}")
        if keep_reference:
            sections.append(f"【保留】 {_KEEP_REFERENCE_TEXT}")
        sections = _order_image_sections(sections)
        query_env = " ".join(p for p in (appearance, ethnicity, outfit, playbook) if p)
        synth = {
            "shot_kind": "image",
            "visual_details": {
                "environment": query_env,
                "subjects": [{"id": char.get("id") or name or "character", "action": {}}],
            },
        }
    elif kind == "turnaround":
        char = character or {}
        appearance = str(char.get("appearance") or "").strip()
        ethnicity = str(char.get("ethnicity_default") or "").strip()
        outfit = str(char.get("outfit_anchor") or char.get("outfit") or "").strip()
        playbook = ""
        if style_context:
            playbook = str(
                ((style_context.get("asset_generation") or {}).get("character_appearance_default") or "")
            ).strip()
        core = appearance or ethnicity or playbook
        if not core:
            raise ValueError(
                "turnaround requires character.appearance, ethnicity_default, "
                "or style_context.asset_generation.character_appearance_default"
            )
        name = str(char.get("name") or char.get("id") or "").strip()
        role = f"{name}: {core}" if name else core
        sections = [
            f"【角色与外貌】 {role}",
            "【镜头】 全身四视图转面（远距离拍摄，相机距人物约四米）："
            "正面、侧面、背面、四分之三侧，同一张图分四格等距排列；"
            "每一格都必须从头顶到脚完整入画、比例一致；严禁裁切头部或只画半身；"
            "中性背景，身份与服装锁定",
        ]
        if outfit:
            sections.append(f"【物体与道具】 {outfit}")
        style = _image_style_text(style_context, "。".join(sections))
        if style:
            sections.append(f"【风格】 {style}")
        sections.append(f"【质量】 {_IMAGE_QUALITY_TEXT}")
        if keep_reference:
            sections.append(f"【保留】 {_KEEP_REFERENCE_TEXT}")
        sections = _order_image_sections(sections)
        query_env = " ".join(p for p in (appearance, ethnicity, outfit, playbook) if p)
        synth = {
            "shot_kind": "image",
            "visual_details": {
                "environment": query_env,
                "subjects": [{"id": char.get("id") or name or "character", "action": {}}],
            },
        }
    elif kind == "scene_ref":
        sc = scene or {}
        desc = str(sc.get("description") or "").strip()
        if not desc:
            raise ValueError("scene_ref requires scene.description")
        sections = [f"【环境】 {desc}"]
        # 空镜参考图的三条硬约束（用户实测：殿内空镜被画成庭院外景+雨幕、
        # 画面里生出匾额对联、道具被画成琵琶）：
        sections.append(
            "【镜头】 纯环境空镜：画面内不出现任何人物、人影或动物；"
            "机位与空间关系严格按上述场景描述（写室内就是室内机位，"
            "不得自行改到室外庭院）；雾/烟只作薄层，不得遮蔽主体结构；"
            "只画描述里提到的陈设，不得新增匾额、对联、屏风文字等元素"
        )
        sections.append("【物体与道具】 画面内不出现乐器等无关道具（描述里没有就不画）")
        style = _image_style_text(style_context, desc)
        if style:
            sections.append(f"【风格】 {style}")
        depth = _image_depth_text({"visual_details": {"environment": desc, "subjects": [], "objects": []}})
        if depth:
            sections.append(f"【层次】 {depth}")
        sections.append(f"【质量】 {_IMAGE_QUALITY_TEXT}")
        if keep_reference:
            sections.append(f"【保留】 {_KEEP_REFERENCE_TEXT}")
        sections = _order_image_sections(sections)
        synth = {
            "shot_kind": "image",
            "visual_details": {"environment": desc, "subjects": []},
        }
    else:
        raise ValueError("kind must be 'portrait', 'turnaround', or 'scene_ref'")

    first_frame_raw = "。".join(sections)
    image_layer = _image_english_layer_text(style_context) if english_visual else ""
    ff_budget = provider_max_chars
    if english_visual and provider_max_chars is not None:
        ff_budget = max(1, provider_max_chars - len(image_layer) - 20)
    if ff_budget is not None and len(first_frame_raw) > ff_budget:
        first_frame_prompt = _compress_prompt(first_frame_raw, sections, ff_budget)
    else:
        first_frame_prompt = first_frame_raw

    if enrich_first_frame:
        first_frame_prompt = _enrich_first_frame_with_library(
            first_frame_prompt, synth, ff_budget
        )

    if english_visual:
        first_frame_prompt = _append_first_frame_english_layer(
            first_frame_prompt, provider_max_chars, style_context
        )

    return {
        "first_frame_prompt": first_frame_prompt or None,
        "video_prompt": None,
        "shot_kind": "image",
        "provider_max_chars": provider_max_chars,
        "negative_prompt": "",
        "image_negative_prompt": _image_negative_text(
            style_context, english_visual=english_visual
        ),
    }
