"""visual_prompt_builder 工具测试：首帧/视频双提示词 + 参考图。"""

import pytest

from montage.tools.visual_prompt_builder import VisualPromptBuilder

SHOT = {
    "scene_id": "sc01",
    "shot_kind": "video",
    "duration_seconds": 10,
    "hero_moment": True,
    "shot_language": {
        "shot_size": "wide",
        "camera_movement": "handheld",
        "lens_mm": 35,
        "depth_of_field": "shallow",
        "lighting_key": "neon",
        "color_temperature": "cool",
    },
    "visual_details": {
        "environment": "雨夜赛博朋克城市街道，霓虹招牌倒映在积水路面",
        "lighting": "霓虹冷色调主光，硬光",
        "cinematography": {"angle": "low", "frame_composition": "主体居中，街道纵深"},
        "subjects": [
            {
                "id": "protagonist",
                "appearance_anchor": "黑发青年，深灰连帽卫衣",
                "action": {"verb": "奔跑", "manner": "急促", "emotion": "紧张", "beat_count": 2},
            }
        ],
    },
}


def test_shot_pair_produces_both_prompts():
    tool = VisualPromptBuilder()
    result = tool.execute({
        "purpose": "shot",
        "shot": SHOT,
        "english_visual": True,
        "enrich_first_frame": True,
    })
    assert result.success
    assert "first_frame_prompt" in result.data
    assert "video_prompt" in result.data
    assert result.data["first_frame_prompt"]


def test_missing_shot_rejected():
    tool = VisualPromptBuilder()
    result = tool.execute({"purpose": "shot"})
    assert not result.success


def test_bad_purpose_rejected():
    tool = VisualPromptBuilder()
    result = tool.execute({"purpose": "nope", "shot": SHOT})
    assert not result.success


def test_portrait_reference_prompt():
    tool = VisualPromptBuilder()
    result = tool.execute({
        "purpose": "portrait",
        "character": {"name": "主角", "appearance": "黑发青年", "outfit": "深灰风衣"},
    })
    assert result.success
    assert result.data.get("first_frame_prompt") or result.data.get("prompt")


def test_turnaround_reference_prompt():
    tool = VisualPromptBuilder()
    result = tool.execute({
        "purpose": "turnaround",
        "character": {"name": "主角", "appearance": "黑发青年", "outfit": "深灰风衣"},
    })
    assert result.success
    prompt = result.data.get("first_frame_prompt") or ""
    assert "四视图" in prompt or "turnaround" in prompt.lower() or "正面" in prompt


def test_style_context_injected_from_proposal(tmp_path):
    """未传 style_context 时，从 proposal_packet.playbook 注入。"""
    from montage.engine.artifacts import ArtifactStore

    store = ArtifactStore(tmp_path)
    store.write("proposal_packet", {
        "concept": "雨夜",
        "playbook": "cyberpunk_neon",
    })
    shot = {
        "shot_kind": "video",
        "visual_details": {
            "environment": "室内走廊",
            "subjects": [{"id": "a", "action": {"verb": "站立"}}],
        },
    }
    tool = VisualPromptBuilder()
    result = tool.execute({
        "purpose": "shot",
        "shot": shot,
        "project_dir": str(tmp_path),
        "english_visual": False,
    })
    assert result.success
    assert result.meta.get("style_injected") is True
    prompt = result.data["first_frame_prompt"]
    assert "赛博朋克" in prompt or "霓虹" in prompt


def test_explicit_style_context_not_overridden(tmp_path):
    from montage.engine.artifacts import ArtifactStore
    from montage.playbooks import get_playbook

    store = ArtifactStore(tmp_path)
    store.write("proposal_packet", {"concept": "x", "playbook": "cyberpunk_neon"})
    shot = {
        "shot_kind": "video",
        "visual_details": {
            "environment": "室内走廊",
            "subjects": [{"id": "a", "action": {"verb": "站立"}}],
        },
    }
    tool = VisualPromptBuilder()
    result = tool.execute({
        "purpose": "shot",
        "shot": shot,
        "project_dir": str(tmp_path),
        "style_context": get_playbook("healing_japanese"),
        "english_visual": False,
    })
    assert result.success
    assert result.meta.get("style_injected") is False
    prompt = result.data["first_frame_prompt"]
    assert "日系" in prompt or "清透" in prompt or "治愈" in prompt
    assert "赛博朋克" not in prompt


def test_compress_keeps_environment_drops_sound():
    """【环境】高于动作/镜头；超长压缩必须留环境、丢掉声音。"""
    from lib.shot_prompt_builder import _compress_prompt

    filler = "雨夜霓虹积水倒映金属质感层叠细节，" * 250
    sections = [
        "【台词】站住，别再往前走。",
        "【角色与外貌】黑发青年，深灰连帽卫衣，面容清晰。",
        "【环境】SCENE_ANCHOR_KEEP 雨夜赛博朋克街道，霓虹招牌倒映积水。",
        "【姿态】微微前倾，重心在前脚。",
        "【动作】" + filler,
        "【镜头】低机位缓慢推进，浅景深。",
        "【风格】电影级写实，冷暖对比。",
        "【光线】霓虹主光，侧面硬光。",
        "【声音】SFX_MUST_DROP 脚步溅水、远处汽笛、雨声密集。",
    ]
    raw = "。".join(sections)
    assert len(raw) > 3000
    out = _compress_prompt(raw, sections, 3000)
    assert "【环境】" in out
    assert "SCENE_ANCHOR_KEEP" in out
    assert "【声音】" not in out
    assert "SFX_MUST_DROP" not in out
    assert "【台词】" in out


def test_shot_language_fills_empty_cinematography():
    from lib.shot_prompt_builder import build_shot_prompt_pair

    shot = {
        "shot_kind": "video",
        "shot_language": {"shot_size": "wide", "camera_movement": "handheld"},
        "visual_details": {
            "environment": "雨夜街道",
            "subjects": [{"id": "a", "appearance_anchor": "黑发青年", "action": {"verb": "走"}}],
        },
        "audio_prompt": {"dialogue": [{"text": "站住"}]},
    }
    pair = build_shot_prompt_pair(shot, agnes_audio=True, provider_max_chars=3000)
    assert "【镜头】" in (pair["video_prompt"] or "") or "【镜头】" in (pair["first_frame_prompt"] or "")


def test_dialogue_fields_three_spellings_same_line():
    from lib.shot_prompt_builder import (
        build_shot_prompt_pair,
        dialogue_line_role,
        dialogue_line_text,
    )

    line = "站住，别再往前走。"
    variants = [
        {"dialogue_text": line, "role": "女子"},
        {"text": line, "speaker_id": "女子"},
        {"content": line, "role": "女子"},
    ]
    for item in variants:
        assert dialogue_line_text(item) == line
        assert dialogue_line_role(item) == "女子"

    def _shot(dialogue):
        return {
            "shot_kind": "video",
            "visual_details": {
                "environment": "雨夜街道",
                "subjects": [{"id": "a", "action": {"verb": "停"}}],
            },
            "audio_prompt": {"dialogue": [dialogue]},
        }

    prompts = [
        build_shot_prompt_pair(_shot(item), agnes_audio=True, provider_max_chars=3000)["video_prompt"]
        for item in variants
    ]
    assert len(set(prompts)) == 1
    assert '女子 说出："站住，别再往前走。"' in (prompts[0] or "")
    assert "说出台词（）" not in (prompts[0] or "")

def test_agnes_v25_spatial_prompt_plaza():
    from lib.shot_prompt_builder import build_agnes_v25_prompt, crop_location_sensory

    sensory = (
        "古风石板广场。左侧近处有仙气般的薄云贴着地面缓缓流动；"
        "画面中下方是湿润石板倒映天光；右侧远处是宏伟高大的中国宫殿，屋脊入雾；"
        "正中更远处天际还有一层淡金霞光"
    )
    shot = {
        "duration_seconds": 8,
        "location_sensory": sensory,
        "shot_language": {"shot_size": "medium_wide", "camera_movement": "orbital"},
        "visual_details": {
            "subjects": [{
                "id": "女子",
                "blocking": {"x": "left", "z": "mid"},
                "action": {"verb": "沿石板缓慢走向右侧远处宫殿方向", "manner": "从容"},
            }],
            "cinematography": {"orbit": "顺时针环绕她一周"},
        },
        "audio_prompt": {
            "bgm": {"mood": "清淡古风丝竹"},
            "sfx": [{"sound": "远处空旷回声"}, {"sound": "衣料轻摩"}],
            "dialogue": [],
        },
    }
    refs = [
        {"kind": "turnaround", "name": "女子"},
        {"kind": "scene_ref", "name": "古风广场"},
    ]
    text = build_agnes_v25_prompt(shot, refs=refs)
    assert "【剧情】" in text
    assert "【全局音频】" in text
    assert "左侧近处" in text
    assert "右侧远处" in text or "更远处" in text
    assert "环绕" in text
    assert "dolly_in" not in text
    assert "orbital" not in text
    assert "<Picture 1>" in text
    assert "说出：" not in text
    cropped = crop_location_sensory(sensory, "close_up")
    assert "近处" in cropped or "中下方" in cropped
    assert cropped.count("更远处") <= 1


def test_agnes_v25_picture_numbering_matches_final_plan():
    """图例逐下标与最终有序表一致：每个 <Picture i> 恰好一次、无越界，adapter 不重复。"""
    from lib.shot_prompt_builder import build_agnes_v25_prompt
    from montage.providers.prompt_adapter import _citation_lines
    from montage.tools._shot_refs import _agnes_flash_image_plan, agnes_plan_adapter_refs

    identity = [
        {"url": "https://x/turn.png", "kind": "turnaround", "character_id": "c1"},
        {"url": "https://x/port.png", "kind": "portrait", "character_id": "c1"},
        {"url": "https://x/scene.png", "kind": "scene_ref", "location_id": "alley"},
        {"url": "/local/prop.png", "kind": "prop", "prop_id": "p1"},
        {"url": "https://x/p2.png", "kind": "prop", "prop_id": "p2"},
    ]
    plan = _agnes_flash_image_plan(identity)
    assert plan["urls"] == [
        "https://x/port.png",
        "https://x/scene.png",
        "https://x/p2.png",
        "https://x/turn.png",
    ]
    shot = {
        "duration_seconds": 6,
        "location_sensory": "夜巷",
        "shot_language": {"shot_size": "medium"},
        "visual_details": {"subjects": [{"id": "c1"}]},
    }
    text = build_agnes_v25_prompt(shot, refs=plan["entries"])
    for i in range(1, len(plan["urls"]) + 1):
        assert f"参考图{i}（<Picture {i}>）" in text
    n = len(plan["urls"])
    assert f"参考图{n + 1}（<Picture {n + 1}>）" not in text
    # 锁外貌的句子也须指向人物参考的下标，不能写死 <Picture 1>
    assert "<Picture 1> 一致" in text
    assert _citation_lines("<Picture N>", agnes_plan_adapter_refs(plan), body=text) == ""


def test_image_ref_legend_matches_sent_order():
    """图片侧多图合成图例：逐条按实发顺序说明每张参考图的角色。"""
    from lib.shot_prompt_builder import image_ref_legend
    from montage.providers.capabilities import agnes_image_ref_entries, image_caps

    refs = [
        {"kind": "portrait", "name": "王生", "url": "https://x/p.png"},
        {"kind": "scene_ref", "name": "老宅", "path": "local/scene.png"},
        {"kind": "prop", "name": "铜镜", "path": "local/mirror.png"},
    ]
    entries, _ = agnes_image_ref_entries(refs, image_caps(tool="agnes_image"))
    legend = image_ref_legend(entries)
    assert legend.startswith("【参考图角色】")
    assert "参考图1（<Picture 1>）为王生基础形象的定妆照" in legend
    assert "参考图2（<Picture 2>）为老宅的场景环境参考" in legend
    assert "参考图3（<Picture 3>）为铜镜的道具参考" in legend
    # url 项在前、path 兜底项在后；图例顺序与实发一致
    assert legend.index("王生") < legend.index("老宅") < legend.index("铜镜")


def test_image_ref_legend_empty_when_no_role_info():
    from lib.shot_prompt_builder import image_ref_legend
    assert image_ref_legend(None) == ""
    assert image_ref_legend([{"kind": "unknown"}]) == ""


def test_build_kling_prompt_plaza_and_look_sheet():
    from lib.shot_prompt_builder import (
        build_kling_look_sheet_prompt,
        build_kling_prompt,
        build_kling_prop_prompt,
    )

    sensory = (
        "古风石板广场。左侧近处有仙气般的薄云贴着地面缓缓流动；"
        "画面中下方是湿润石板倒映天光；右侧远处是宏伟高大的中国宫殿，屋脊入雾；"
        "正中更远处天际还有一层淡金霞光"
    )
    shot = {
        "duration_seconds": 8,
        "location_sensory": sensory,
        "shot_language": {"shot_size": "medium_wide", "camera_movement": "orbital"},
        "visual_details": {
            "subjects": [{
                "id": "女子",
                "blocking": {"x": "left", "z": "mid"},
                "action": {"verb": "沿石板缓慢走向右侧远处宫殿方向"},
            }],
            "cinematography": {"orbit": "顺时针环绕她一周"},
        },
        "audio_prompt": {"dialogue": [{"role": "女子", "text": "站住"}]},
    }
    refs = [
        {"kind": "turnaround", "name": "女子", "element_id": 1},
        {"kind": "scene_ref", "name": "古风广场"},
    ]
    text = build_kling_prompt(shot, refs=refs, cite="omni")
    assert "左侧" in text
    assert "@element_1" in text
    assert "@image_1" in text
    assert "对白：女子：站住" in text
    assert "镜头" in text
    assert "定格静帧" not in text
    assert "【剧情】" not in text
    assert "<Picture" not in text
    assert "<<<image_" not in text
    still = build_kling_prompt(shot, refs=refs, cite="omni", still=True)
    assert "定格静帧" in still
    assert "对白：" not in still
    assert "@image_" not in still
    assert "@element_1" in still
    assert "站定" in still
    shot["visual_details"]["lighting"] = "左侧逆光勾边"
    filled = build_kling_prompt(
        shot,
        refs=refs,
        cite="omni",
        character_registry=[{"id": "女子", "appearance": "青衫长发"}],
    )
    assert "光：左侧逆光勾边" in filled
    assert "女子外形：青衫长发" in filled
    from lib.shot_prompt_builder import build_shot_prompt_pair

    pair = build_shot_prompt_pair(
        {**shot, "shot_kind": "video"},
        kling_prompt=True,
        kling_cite="omni",
        refs=refs,
        english_visual=False,
        character_registry=[{"id": "女子", "appearance": "青衫长发"}],
    )
    assert "定格静帧" in (pair["first_frame_prompt"] or "")
    assert "<<<object_1>>>" in (pair["first_frame_prompt"] or "")
    assert "<<<image_1>>>" in (pair["first_frame_prompt"] or "")
    assert "@element_1" not in (pair["first_frame_prompt"] or "")
    assert "对白：女子：站住" in (pair["video_prompt"] or "")
    assert "@element_1" in (pair["video_prompt"] or "")
    assert "<<<object_" not in (pair["video_prompt"] or "")
    assert "<<<image_" not in (pair["video_prompt"] or "")
    fat = {
        "location_sensory": "甲" * 4000,
        "shot_language": {"shot_size": "wide"},
        "visual_details": {"subjects": []},
    }
    assert len(build_kling_prompt(fat, max_chars=2500)) == 2500
    sheet = build_kling_look_sheet_prompt({
        "appearance": "黑发圆框眼镜",
        "outfit": "蓝外套",
    })
    assert "16:9" in sheet
    assert "纯白" in sheet
    assert "左半" in sheet
    assert "右上左" in sheet
    assert "右上右" in sheet
    assert "右下左" in sheet
    assert "右下右" in sheet
    assert "朝镜头" in sheet
    assert "黑发圆框眼镜" in sheet
    assert "蓝外套" in sheet
    assert "<<<image_" not in sheet
    assert "@element" not in sheet
    assert "studio" not in sheet.lower()
    assert 120 < len(sheet) <= 2500
    huge = "甲" * 4000
    clipped = build_kling_look_sheet_prompt({"appearance": huge})
    assert len(clipped) == 2500
    assert clipped.startswith("16:9")
    prop = build_kling_prop_prompt({"name": "打火机"})
    assert "纯白底" in prop
    assert "打火机" in prop
    assert "左半" in prop
    assert "右上右" in prop
    assert "右下左" in prop
    assert "右下右" in prop
    assert "四分之三" in prop
    assert "无人无手" in prop
    assert "<<<image_" not in prop
    assert 120 < len(prop) <= 2500
    clipped_prop = build_kling_prop_prompt({"name": "杯", "description": "乙" * 4000})
    assert len(clipped_prop) == 2500
    assert clipped_prop.startswith("16:9")


def test_agnes_v25_prompt_not_silently_compressed():
    from lib.shot_prompt_builder import apply_agnes_prompt_limit, build_agnes_v25_prompt

    shot = {
        "duration_seconds": 8,
        "location_sensory": "左侧近处" + ("甲" * 4000),
        "shot_language": {"shot_size": "wide", "camera_movement": "static"},
        "visual_details": {"subjects": []},
        "audio_prompt": {"bgm": {"mood": "静"}, "sfx": [], "dialogue": []},
    }
    text = build_agnes_v25_prompt(shot)
    assert len(text) > 3000
    kept, over = apply_agnes_prompt_limit(text, fallback=False)
    assert over is True
    assert kept == text
    clipped, over2 = apply_agnes_prompt_limit(text, fallback=True)
    assert over2 is False
    assert len(clipped) <= 3000


def test_location_scene_uses_chinese_sensory():
    from montage.tools.shot_runner import _location_scene

    scene = _location_scene({
        "id": "plaza",
        "name": "广场",
        "sensory": "左侧近处薄云；右侧更远处宫殿",
    })
    assert "左侧近处" in scene["description"]
    assert "无人空镜" in scene["description"]
    assert "empty establishing" not in scene["description"]


def test_resolve_appearance_prefers_declared_form():
    from lib.shot_prompt_builder import _resolve_appearance

    registry = [{
        "id": "g",
        "appearance": "基础人形",
        "outfit_anchor": "素衣",
        "forms": [
            {"id": "human", "appearance": "清秀书生"},
            {"id": "ghost", "name": "鬼形", "appearance": "青面獠牙", "outfit_anchor": "破红嫁衣"},
        ],
    }]
    # 未声明形态 → 角色基础外观
    assert _resolve_appearance({"id": "g"}, registry, None) == "基础人形"
    # 声明形态 → 形态外观；形态无 outfit 时回落角色 outfit
    assert _resolve_appearance(
        {"id": "g", "form_id": "human", "appearance_anchor": "基础人形"}, registry, None,
    ) == "清秀书生, 素衣"
    # 形态 outfit 覆盖角色
    assert _resolve_appearance(
        {"id": "g", "form_id": "ghost", "appearance_anchor": "基础人形"}, registry, None,
    ) == "青面獠牙, 破红嫁衣"
    # 作者显式改写 anchor（≠ 角色基础外貌）→ anchor 优先
    assert _resolve_appearance(
        {"id": "g", "form_id": "ghost", "appearance_anchor": "戴斗笠"}, registry, None,
    ) == "戴斗笠"


def test_subject_display_name_uses_form_name():
    from lib.shot_prompt_builder import _subject_display_name

    registry = [{"id": "g", "forms": [{"id": "ghost", "name": "鬼形"}, {"id": "human"}]}]
    assert _subject_display_name({"id": "g", "form_id": "ghost"}, registry) == "g·鬼形"
    assert _subject_display_name({"id": "g", "form_id": "human"}, registry) == "g（human）"
    assert _subject_display_name({"id": "g"}, registry) == "g"


def test_ref_legend_form_name_and_turnaround_single_frame():
    from lib.shot_prompt_builder import _ref_header_lines, image_ref_legend

    refs = [
        {"kind": "turnaround", "name": "画皮鬼·鬼形", "url": "https://x/t.png"},
        {"kind": "portrait", "name": "画皮鬼·人皮形", "url": "https://x/p.png"},
    ]
    legend = image_ref_legend(refs)
    assert "画皮鬼·鬼形" in legend
    assert "画皮鬼·人皮形" in legend
    # 四视图附单幅/禁分格约束；定妆照不带
    assert "禁止分格/拼贴" in legend
    portrait_line = _ref_header_lines([{"kind": "portrait", "name": "画皮鬼·人皮形"}])[0]
    assert "禁止分格" not in portrait_line
