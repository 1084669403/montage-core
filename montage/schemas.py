"""schemas — 规范产物 JSON Schema（精简、原创）。

故意保持"松校验"：只约束每个产物的契约字段，不约束表达细节——
细节由导演技能/LLM 决定，schema 只保证"下一阶段能读"。

本文件为全新原创代码。
"""

from __future__ import annotations

from typing import Any

# 身份参考图类型：portrait（单张定妆，默认） / turnaround（四视图拼板）。
# 三级优先级：form.cast_ref_kind > character.cast_ref_kind > proposal_packet.cast_ref_kind。
CAST_REF_KIND_VALUES: list[str] = ["portrait", "turnaround"]

# 人物形态（form）：同一角色在不同场景/状态的独立外观（如画皮鬼的人皮形/鬼形）。
# 缺省（无 forms[]）= 单隐式形态 form_id=""，行为与旧版一致。
CHARACTER_FORM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id"],
    "properties": {
        "id": {
            "type": "string",
            "description": "角色内唯一；非空。隐式形态不写此字段（form_id=\"\"）",
        },
        "name": {"type": "string", "description": "形态名（如「人皮形」）；缺省回落角色 name"},
        "appearance": {"type": "string", "description": "该形态外貌锚点；缺省回落角色 appearance"},
        "outfit_anchor": {"type": "string", "description": "该形态服装锚点；缺省回落角色 outfit"},
        "skip_turnaround": {
            "type": "boolean",
            "description": "该形态跳过四视图；缺省回落 characters[].skip_turnaround",
        },
        "cast_ref_kind": {
            "type": "string",
            "enum": CAST_REF_KIND_VALUES,
            "description": "该形态的默认身份参考图类型；缺省回落 character/packet",
        },
        "default": {
            "type": "boolean",
            "description": "镜头未声明形态时的默认形态；每角色至多一个 true",
        },
    },
}

# 剧本环境：结构化对象（抬到镜头时拍扁成字符串）。也允许纯字符串以免旧写法炸 schema。
ENVIRONMENT_SCHEMA: dict[str, Any] = {
    "description": "主环境。对象字段全 optional；喂提示词前用 flatten_environment 拍扁成一句",
    "anyOf": [
        {"type": "string"},
        {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "地点"},
                "space": {"type": "string", "description": "室内/室外/空间尺度"},
                "time": {"type": "string", "description": "晨/日/暮/夜 或具体时刻；驱动 sensory_by_time 选句"},
                "lighting": {"type": "string", "description": "光线基调"},
                "color_tone": {"type": "string", "description": "色调"},
                "era": {"type": "string", "description": "时代"},
                "atmosphere": {"type": "string", "description": "氛围"},
            },
        },
    ],
}

# visual_prompt_builder 消费的 visual_details 约定（全部 optional，松校验）
VISUAL_DETAILS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "environment": ENVIRONMENT_SCHEMA,
        "lighting": {"type": "string"},
        "cinematography": {
            "type": "object",
            "properties": {
                "angle": {"type": "string"},
                "frame_composition": {"type": "string"},
                "camera_path": {"type": "string"},
            },
        },
        "subjects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "form_id": {
                        "type": "string",
                        "description": "该镜出场形态（引用 character.forms[].id）；缺省=默认形态",
                    },
                    "appearance_anchor": {"type": "string"},
                    "position": {"type": "string"},
                    "action": {
                        "type": "object",
                        "properties": {
                            "verb": {"type": "string"},
                            "manner": {"type": "string"},
                            "emotion": {"type": "string"},
                            "body_part": {"type": "string", "description": "可见身体部位"},
                            "contact": {"type": "string", "description": "接触点（衣领/墙/手等）"},
                        },
                    },
                },
            },
        },
        "objects": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "appearance": {"type": "string"},
                    "position": {"type": "string", "description": "物体在画面中的位置"},
                },
            },
        },
        "action_density": {"type": "object"},
    },
}

AUDIO_PROMPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "镜头声音设计（约定字段，全部 optional）",
    "properties": {
        "dialogue": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "role": {"type": "string"},
                    "speaker_id": {
                        "type": "string",
                        "description": "与 script.lines[].speaker_id 同义；builder 认 role 或本字段",
                    },
                    "dialogue_ref": {"type": "string"},
                    "dialogue_text": {"type": "string"},
                    "text": {
                        "type": "string",
                        "description": "与 dialogue_text 同义；手写剧本常用",
                    },
                    "delivery": {"type": "string"},
                    "volume": {"type": "number"},
                },
            },
        },
        "sfx": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "action_ref": {"type": "string"},
                    "sound": {"type": "string"},
                    "onset": {"type": "string"},
                },
            },
        },
        "bgm": {
            "type": "object",
            "properties": {
                "mood": {"type": "string"},
                "tempo": {"type": "string"},
                "instrument": {"type": "string"},
                "level": {"type": "string"},
            },
        },
        "audio_ref": {
            "type": "array",
            "description": "音频参考（v8.2 P1-audio-ref）：喂 Agnes reference 模式 audios[]；"
            "官方上限 3 段，节奏参考（BGM）优先于音色参考（人声）。条目可为公网 URL 或"
            "本地路径/资产 id（URL 之外须仓库可解析）",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "资产 id（assets 音频表或 reference_assets）"},
                    "url": {"type": "string", "description": "公网 http(s) URL，直传 audios"},
                    "path": {"type": "string", "description": "本地路径（无上传器，先解 URL 才能发）"},
                    "role": {
                        "type": "string",
                        "description": "rhythm=BGM 节奏参考 | timbre=人声音色参考；排序依据",
                    },
                },
            },
        },
    },
}

# P0-8 特效条目（特效指导制定；prompt 层自由文本 / post 层受限枚举）。
# 与 effects[] 的边界：effects = ken_burns 等结构化管线操作（compose_planner 生成），
# vfx = 特效指导的观感特效（时间点事件；整镜常驻视觉状态写 visual_details）。
VFX_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "layer": {
            "type": "string",
            "enum": ["prompt", "post"],
            "description": "prompt=AI 生成画面内特效（写进提示词）；post=后期 ffmpeg 特效（impact_flash/zoom_punch/camera_shake）",
        },
        "kind": {
            "type": "string",
            "description": "prompt 层自由文本（剑气/粒子/能量…具体视觉语言）；post 层受限枚举 impact_flash|zoom_punch|camera_shake",
        },
        "onset": {
            "type": "number",
            "description": "镜内相对秒（0=镜头起点）；自审校验 onset ∈ [0, duration_seconds]；audio_prompt.sfx.onset 是 string 语义不同",
        },
        "duration": {"type": "number", "description": "特效持续秒数（post 层必填，如闪白 0.12）"},
        "intensity": {"type": "number", "description": "强度 0-1（post 层生效；prompt 层作散文参考）"},
        "note": {"type": "string", "description": "美术审风格用的一句话备注"},
    },
}

# Phase 6/7 additive contracts.
TRANSITION_CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "decision": {
            "type": "string",
            "enum": [
                "fade",
                "xfade",
                "match_cut",
                "audio_bridge",
                "shared_element",
                "establishing_shot",
                "user_accepted_hard_cut",
            ],
        },
        "reason": {"type": "string", "description": "必须说明叙事/时空/视听理由"},
        "accepted_by": {"type": "string"},
        "evidence": {"type": "string"},
    },
}

PROMPT_CONTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source": {"type": "string", "enum": ["derived", "explicit"]},
        "required_characters": {"type": "array", "items": {"type": "string"}},
        "required_outfits": {"type": "array", "items": {"type": "string"}},
        "required_props": {"type": "array", "items": {"type": "string"}},
        "required_locations": {"type": "array", "items": {"type": "string"}},
        "forbidden_objects": {"type": "array", "items": {"type": "string"}},
        "forbidden_text": {"type": "array", "items": {"type": "string"}},
    },
}

# 嵌套镜头骨架（scene_plan.scenes[].shots[]）：形状对齐 visual_prompt_builder 的 shot
NESTED_SHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "shot_id": {"type": "string"},
        "scene_id": {"type": "string", "description": "可缺省，lift 时从父 scene 补"},
        "shot_kind": {"type": "string", "enum": ["image", "video"]},
        "duration_seconds": {"type": "number"},
        "hero_moment": {"type": "boolean"},
        "transition_contract": TRANSITION_CONTRACT_SCHEMA,
        "prompt_contract": PROMPT_CONTRACT_SCHEMA,
        "vfx": {
            "type": "array",
            "description": "P0-8 特效指导制定的观感特效（时间点事件；常驻视觉写 visual_details）；特效师制定、美术审风格",
            "items": VFX_ITEM_SCHEMA,
        },
        "visual_details": VISUAL_DETAILS_SCHEMA,
        "audio_prompt": AUDIO_PROMPT_SCHEMA,
        "presence": {
            "type": "object",
            "description": (
                "B2.5 逐镜在场清单：location{id,zone,time_of_day,light} / "
                "characters[{id,zone,state,costume_state,enters,exits}] / "
                "props[{id,holder,zone}] / empty_reason（空镜必填）"
            ),
        },
        "continuity": {
            "type": "object",
            "description": "B2.5 承接表（编译器生成）：from_shot / must_keep / changed / missing",
        },
        "reference_asset_ids": {"type": "array", "items": {"type": "string"}},
        "shot_language": {"type": "object"},
        "blocking": {
            "type": "object",
            "description": "站位；编译时写入 subjects[].position",
            "properties": {
                    "x": {"type": "string", "description": "left|center|right 或 左/中/右"},
                    "z": {"type": "string", "description": "near|mid|far|farther 或 近/中/远/更远"},
                    "y": {"type": "string", "description": "high|mid|low 或 上/中/下"},
            },
        },
        "shot_budget_class": {
            "type": "string",
            "description": "hero|talk|establishing；W1 只抄字段，不执法 30%",
        },
        "render_kind": {
            "type": "string",
            "description": "默认 ai_clip；P6 图形镜预留，本轮不实现第二运行时",
        },
        "cut": {
            "type": "string",
            "enum": ["hard", "bridge"],
            "description": "缺省 bridge；hard 才清镜间尾帧桥",
        },
        "location_id": {
            "type": "string",
            "description": "双方都有且不等时清桥；不要用环境全文不等断桥",
        },
        "location_sensory": {
            "type": "string",
            "description": "从 bible.locations[].sensory 抄来的交叉方位环境句",
        },
        "dialogue_audio_mode": {
            "type": "string",
            "enum": ["native", "tts", "lipsync"],
            "description": "P2 消费；对白镜优先 native。P0 只入契约",
        },
        "gen_strategy": {
            "type": "string",
            "enum": ["shot_by_shot", "single_call_multi_shot"],
            "description": "P2 消费；multi_shot 且同场且非 hard cut 才允许单次多镜。P0 只入契约",
        },
        "title": {"type": "string", "description": "镜头短标题（导演分镜表）"},
        "start_seconds": {"type": "number"},
        "end_seconds": {"type": "number"},
        "rework_mode": {
            "type": "string",
            "enum": ["edit", "extend", "splice", "regenerate", "feature"],
            "description": "P5/可灵：retry 时覆盖启发式；feature 仅 Omni 且 ≤10s；非法值回落 regenerate",
        },
        "agnes_mode": {
            "type": "string",
            "enum": ["text", "keyframe", "reference"],
            "description": "per-shot 显式生成模式（v8.2 按需分配）；空=自动兜底。显式值最高优先，"
            "运行时真源经 overlay_plan_rework 盖进 shot dict；keyframe 只认 first/last 各一帧",
        },
        "tail_frame_state": {
            "type": "string",
            "description": "尾帧状态意图（如 门开→门关 / 转场匹配）；非空触发 keyframe(first,last) 兜底",
        },
        "seed": {
            "type": "integer",
            "description": "显式种子锁定；仅导演显式声明才透传（重试不换种），自动路径仍 1000+attempt",
        },
        "revision_note": {"type": "string", "description": "P5：局部编辑一句修正"},
        "retake_segment": {
            "type": "object",
            "description": "P5：ffmpeg 兜底换段",
            "properties": {
                "start_seconds": {"type": "number"},
                "duration_seconds": {"type": "number"},
            },
        },
    },
}

SCRIPT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "sections"],
    "properties": {
        "title": {"type": "string"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "narration"],
                "properties": {
                    "id": {"type": "string"},
                    "narration": {
                        "type": "string",
                        "description": "旁白或由 lines 拼出的朗读稿；对白/TTS 以 lines[] 为准",
                    },
                    "duration_seconds": {"type": "number"},
                    "lines": {
                        "type": "array",
                        "description": "对白/TTS 单一事实源",
                        "items": {
                            "type": "object",
                            "properties": {
                                "speaker_id": {
                                    "type": "string",
                                    "description": "引用 characters[].id，旁白可用 narrator",
                                },
                                "text": {"type": "string"},
                                "delivery": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
        # 人物卡（叙事片）：单一事实源，scene_plan.character_registry 按 id 逐字引用
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "role": {
                        "type": "string",
                        "enum": ["protagonist", "antagonist", "supporting", "functional", "narrator"],
                    },
                    "appearance": {"type": "string", "description": "外貌锚点，逐字复制到 character_registry.appearance"},
                    "outfit": {"type": "string", "description": "服装锚点，逐字复制到 character_registry.outfit_anchor"},
                    "personality": {"type": "string"},
                    "age": {"type": "string", "description": "年龄段（导演大纲；自由文本）"},
                    "speech_style": {"type": "string", "description": "用词/口癖/语速，对白须与其一致"},
                    "voice_id": {"type": "string", "description": "音色预留（P4 voice_director 消费）"},
                    "do_not_change": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "定妆后禁止改的锚点",
                    },
                    "contrast_notes": {"type": "string", "description": "与同场其他角色如何区分"},
                    "relationships": {"type": "array", "items": {"type": "string"}},
                    "arc": {"type": "string", "description": "本片人物变化一句话"},
                    "skip_turnaround": {
                        "type": "boolean",
                        "description": "导演定妆步跳过该角色四视图；默认出",
                    },
                    "cast_ref_kind": {
                        "type": "string",
                        "enum": CAST_REF_KIND_VALUES,
                        "description": "该角色身份参考图类型；缺省回落 proposal_packet.cast_ref_kind",
                    },
                    "forms": {
                        "type": "array",
                        "description": (
                            "该角色的多个形态（可选）。缺省=单隐式形态，行为与旧版一致；"
                            "可灵环忽略 forms"
                        ),
                        "items": CHARACTER_FORM_SCHEMA,
                    },
                    "cast_note": {
                        "type": "string",
                        "description": "定妆重抽时附带的一句修正",
                    },
                },
            },
        },
        # 故事节拍地图（可选）：钩子→升级→揭示→落地
        "structure": {
            "type": "object",
            "properties": {
                "hook": {"type": "string"},
                "escalation": {"type": "string"},
                "reveal": {"type": "string"},
                "landing": {"type": "string"},
            },
        },
        # 长片分章（v8.2 P0-0，可选）：四拍在章内起作用；短篇不声明走旧全局四拍。
        # start_scene 是章首场景，从该场到下一章 start_scene 前的场都属于本章。
        "chapters": {
            "type": "array",
            "description": "章节结构（长片主锚）：按日拆批次=章节粒度；章内四拍；"
            "bgm_id 作章节默认曲（音乐锚降章节内辅助，显式 scene.bgm_id 仍最高优先）",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "章节 id，缺省 chNN 自动编号"},
                    "title": {"type": "string", "description": "章标题"},
                    "role": {"type": "string", "description": "章节作用一句话"},
                    "hook": {"type": "string", "description": "章钩子"},
                    "start_scene": {"type": "string", "description": "章首场景 id（归属锚）"},
                    "bgm_id": {"type": "string", "description": "章节默认曲 id；无显式 scene.bgm_id 的场兜底"},
                    "target_duration_seconds": {"type": "number", "description": "章节目标时长段（秒）"},
                },
            },
        },
        "environment": ENVIRONMENT_SCHEMA,
        "props": {
            "type": "array",
            "description": "关键道具",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "appearance": {"type": "string"},
                    "purpose": {"type": "string", "description": "情感作用一句"},
                    "appears_in_location_id": {"type": "string"},
                    "cast_note": {
                        "type": "string",
                        "description": "道具静物重抽时附带的一句修正",
                    },
                },
            },
        },
        "tone": {"type": "string", "description": "色调与情绪基调（可引用 playbook mood）"},
    },
}

SCENE_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["scenes"],
    "properties": {
        "locations": {
            "type": "array",
            "items": {"type": "object"},
            "description": "compile 从 bible.locations[] 抄来，供提示词按 location_id 反查",
        },
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "description"],
                "properties": {
                    "id": {"type": "string"},
                    "description": {"type": "string"},
                    "title": {"type": "string", "description": "幕标题"},
                    "sound_notes": {"type": "string"},
                    "keyframe_blurb": {"type": "string"},
                    "type": {"type": "string"},
                    "narrative_role": {"type": "string"},
                    "chapter_id": {
                        "type": "string",
                        "description": "所属章节 id（v8.2 P0-0；compile 从 bible.chapters 区间归属，四拍在章内重置）",
                    },
                    "hero_moment": {"type": "boolean"},
                    "environment": ENVIRONMENT_SCHEMA,
                    "start_seconds": {"type": "number"},
                    "end_seconds": {"type": "number"},
                    "character_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "本场出镜角色 id（引用 script.characters[].id）",
                    },
                    "shot_language": {
                        "type": "object",
                        "properties": {
                            "shot_size": {"type": "string"},
                            "camera_movement": {"type": "string"},
                            "lens_mm": {"type": "number"},
                            "depth_of_field": {"type": "string"},
                            "lighting_key": {"type": "string"},
                            "color_temperature": {"type": "string"},
                        },
                    },
                    "emotion": {
                        "type": "string",
                        "description": "情绪短词，对齐 playbook audio.music_mood；不是曲线",
                    },
                    "bgm_id": {
                        "type": "string",
                        "description": "显式曲目 id（对齐 assets/bgm）；有编译进去的才按场换歌",
                    },
                    "location_id": {
                        "type": "string",
                        "description": "对齐 bible.locations[].id；compile 时抄到镜头",
                    },
                    "shots": {
                        "type": "array",
                        "description": "嵌套镜头骨架（P2 转换器产出）；shot_prompts 仍在 assets 阶段写出",
                        "items": NESTED_SHOT_SCHEMA,
                    },
                },
            },
        },
        # 章节快照（v8.2 P0-0）：compile 从 bible.chapters 归属后落这里，供
        # 按日拆批次（章节粒度）与审核读取；四拍在章内起作用。
        "chapters": {
            "type": "array",
            "description": "compile 落的章节快照；来源 bible.chapters[]（显式）或长片自动等距分章",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "role": {"type": "string"},
                    "hook": {"type": "string"},
                    "start_scene": {"type": "string"},
                    "bgm_id": {"type": "string"},
                    "target_duration_seconds": {"type": "number"},
                },
            },
        },
        # 角色注册表：逐字复制 script.characters[].appearance/outfit（禁止在分镜阶段改写）
        "character_registry": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string"},
                    "appearance": {"type": "string"},
                    "outfit_anchor": {"type": "string"},
                    "forms": {
                        "type": "array",
                        "items": CHARACTER_FORM_SCHEMA,
                        "description": "逐字镜像 script.characters[].forms；分镜阶段禁止改写",
                    },
                    "ethnicity_default": {"type": "string"},
                    "voice_id": {"type": "string", "description": "音色 id（voice_director / voices 表）"},
                },
            },
        },
    },
}

SHOT_PROMPTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["version", "shots"],
    "properties": {
        "version": {"type": "string"},
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["scene_id", "shot_kind"],
                "properties": {
                    "scene_id": {"type": "string"},
                    "shot_kind": {"type": "string", "enum": ["image", "video"]},
                    "shot_id": {"type": "string"},
                    "duration_seconds": {"type": "number"},
                    "hero_moment": {"type": "boolean"},
                    "vfx": {
                        "type": "array",
                        "description": "P0-8 特效条目（lift 后路由侧仍可读）",
                        "items": VFX_ITEM_SCHEMA,
                    },
                    "cut": NESTED_SHOT_SCHEMA["properties"]["cut"],
                    "location_id": NESTED_SHOT_SCHEMA["properties"]["location_id"],
                    "visual_details": VISUAL_DETAILS_SCHEMA,
                    "audio_prompt": AUDIO_PROMPT_SCHEMA,
                    "reference_asset_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
            "audio_source": {
                "type": "string",
                "enum": ["agnes_prompt", "jimeng_prompt", "kling_prompt", "doubao_seed_audio", "none"],
            },
            "dialogue_audio_mode": NESTED_SHOT_SCHEMA["properties"]["dialogue_audio_mode"],
            "gen_strategy": NESTED_SHOT_SCHEMA["properties"]["gen_strategy"],
            "rework_mode": NESTED_SHOT_SCHEMA["properties"]["rework_mode"],
            "agnes_mode": NESTED_SHOT_SCHEMA["properties"]["agnes_mode"],
            "tail_frame_state": NESTED_SHOT_SCHEMA["properties"]["tail_frame_state"],
            "seed": NESTED_SHOT_SCHEMA["properties"]["seed"],
            "revision_note": NESTED_SHOT_SCHEMA["properties"]["revision_note"],
            "retake_segment": NESTED_SHOT_SCHEMA["properties"]["retake_segment"],
                },
            },
        },
        "metadata": {"type": "object"},
    },
}

COMPOSE_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "每镜合成方案（P5）；assemble 不读此文件，须先 compile 成 edit_decisions",
    "properties": {
        "version": {"type": "string"},
        "render_runtime": {
            "type": "string",
            "enum": ["ffmpeg"],
        },
        "playbook": {"type": "string"},
        "style_pack": {"type": "string"},
        "lut": {"type": "string"},
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "shot_id": {"type": "string"},
                    "scene_id": {"type": "string"},
                    "clip_path": {"type": "string"},
                    "duration_seconds": {"type": "number"},
                    "transition": {"type": "string"},
                    "transition_duration": {"type": "number"},
                    "negative_gap_seconds": {"type": "number"},
                    "transition_contract": TRANSITION_CONTRACT_SCHEMA,
                    "transition_reason": {"type": "string"},
                    "prompt_contract": PROMPT_CONTRACT_SCHEMA,
                    "presence": {
                        "type": "object",
                        "description": "B2.5 逐镜在场清单（人物/道具/场景方位，供提示词与承接表使用）",
                    },
                    "continuity": {
                        "type": "object",
                        "description": "B2.5 承接表：must_keep / changed / missing（编译器生成）",
                    },
                    "prompt_hash": {"type": "string"},
                    "anchor_coverage": {"type": "object"},
                    "reference_requirements": {
                        "type": "array",
                        "items": {"type": "object"},
                    },
                    "forbidden_text_hits": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "lut": {"type": "string"},
                    "subtitle_cues": {"type": "array", "items": {"type": "object"}},
                    "audio_events": {"type": "array", "items": {"type": "object"}},
                    "effects": {"type": "array", "items": {"type": "object"}},
                    "vfx": {
                        "type": "array",
                        "description": "P0-8 特效条目（compose_planner 从 scene_plan shot.vfx 透传；compile_compose_plan 复制进 cuts）",
                        "items": VFX_ITEM_SCHEMA,
                    },
                    "render_kind": {
                        "type": "string",
                        "enum": [
                            "ai_clip",
                            "title_card",
                            "kinetic_caption",
                            "manga_panel",
                            "chart",
                        ],
                    },
                    "hero_moment": {"type": "boolean"},
                },
            },
        },
        "assemble_hints": {"type": "object"},
    },
}

EDIT_DECISIONS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["cuts"],
    "properties": {
        "cuts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_scene": {"type": "string", "description": "来源镜头 id（edit_advisor junctions 对齐）"},
                    "to_scene": {"type": "string", "description": "目标镜头 id"},
                    "clip_path": {"type": "string", "description": "片段文件路径（优先）"},
                    "path": {"type": "string", "description": "片段文件路径（兼容别名）"},
                    "output": {"type": "string", "description": "片段文件路径（兼容别名）"},
                    "shot_id": {"type": "string", "description": "镜头 id（compose_planner 写入；place_audio 对齐）"},
                    "scene_id": {"type": "string"},
                    "transition": {"type": "string", "description": "入转场（与 transition_in 同义）"},
                    "transition_in": {"type": "string", "description": "入转场：cut/crossfade/fade_black/wipe/zoom_punch"},
                    "transition_out": {"type": "string", "description": "出转场"},
                    "transition_duration": {"type": "number", "description": "转场时长（秒）；>0 即负空隙重叠"},
                    "negative_gap_seconds": {"type": "number", "description": "负空隙（秒），转场重叠的等价表达"},
                    "transition_contract": TRANSITION_CONTRACT_SCHEMA,
                    "transition_reason": {"type": "string"},
                    "vfx": {
                        "type": "array",
                        "description": "P0-8 后期特效（compose_planner 从 shot.vfx[] 的 post 层透传；assemble 拼接前逐 cut 应用）",
                        "items": VFX_ITEM_SCHEMA,
                    },
                },
            },
        },
        "render_runtime": {
            "type": "string",
            "enum": ["ffmpeg"],
            "description": "渲染运行时（本项目仅支持 ffmpeg）",
        },
        "allow_non_cut": {
            "type": "boolean",
            "description": "documentary/clip_factory 的 cut_only 策略下，显式允许非硬切",
        },
        "music_segments": {
            "type": "array",
            "description": "≥2 条带时间窗的 BGM（place_audio 写入；assemble 读此字段，不再传 music_path）",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_seconds": {"type": "number"},
                    "end_seconds": {"type": "number"},
                    "volume": {"type": "number"},
                    "scene_id": {"type": "string"},
                },
            },
        },
    },
}

RENDER_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["output_path"],
    "properties": {
        "output_path": {"type": "string"},
        "duration_seconds": {"type": "number"},
        "encoding": {"type": "string"},
        # 实测分辨率：Agnes 720P 实为 1280x704，图片 2K 非 1920x1080；
        # 落盘实际像素供下游按需缩放，禁止反推 1280x720/1920x1080。
        "width": {"type": "integer"},
        "height": {"type": "integer"},
        "clips": {"type": "array", "items": {"type": "object"}},
        "images": {"type": "array", "items": {"type": "object"}},
        "ffmpeg_version": {"type": "string"},
        "ffmpeg_capabilities": {"type": "object"},
    },
}

AUTO_EDIT_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema_version", "session_id", "style_pack", "source_decl", "segments"],
    "properties": {
        "schema_version": {"type": "string"},
        "session_id": {"type": "string"},
        "stage": {"type": "string"},
        "updated_at": {"type": "string"},
        "style_pack": {"type": "string"},
        "source_decl": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "has_bgm": {"type": "boolean"},
                "is_speech": {"type": "boolean"},
                "language": {"type": "string"},
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "sha256": {"type": "string"},
                            "duration": {"type": "number"},
                        },
                    },
                },
            },
        },
        "params": {"type": "object"},
        "segments": {"type": "array", "items": {"type": "object"}},
        "edits": {"type": "array", "items": {"type": "object"}},
    },
}

AUTO_EDIT_REPORT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["output_path"],
    "properties": {
        "output_path": {"type": "string"},
        "duration_seconds": {"type": "number"},
        "encoding": {"type": "string"},
        "source_integrity": {"type": "object"},
        "hardware_profile": {"type": "object"},
        "render_log": {"type": "array"},
        "failed_step": {"type": "string"},
        "partial_outputs": {"type": "object"},
        "error": {"type": "string"},
        "note": {"type": "string"},
    },
}

RESEARCH_BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["topic"],
    "properties": {
        "topic": {"type": "string"},
        "audience": {"type": "string"},
        "style_direction": {"type": "string"},
        "references": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "title": {"type": "string"},
                    "note": {"type": "string"},
                },
            },
        },
        "notes": {"type": "string"},
    },
}

PROPOSAL_PACKET_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["concept"],
    "properties": {
        "concept": {"type": "string"},
        "playbook": {"type": "string"},
        "output_profile": {"type": "string"},
        "frames_mode": {
            "type": "string",
            "enum": ["preview", "reference_first", "keyframe"],
            "description": (
                "首帧如何参与 Agnes 2.5 视频生成。preview（默认）不把首帧喂给模型，"
                "只作审图；reference_first 把首帧作为 images[0] 参考；"
                "keyframe 走 mode=keyframe 真 I2V（角色一致性下降）。"
            ),
        },
        "ref_overflow_mode": {
            "type": "string",
            "enum": ["single", "segment"],
            "description": (
                "参考图超出名额时的策略。segment（默认）把同一 shot 按时间轴切多段，"
                "段间以尾帧续接，段数封顶 4 后回退 single；single 直接丢弃多余参考"
                "并出 finding（旧行为）。keyframe 首帧模式强制 single。"
            ),
        },
        "render_runtime": {"type": "string", "enum": ["ffmpeg"]},
        "video_loop": {
            "type": "string",
            "enum": ["agnes", "volcengine", "dashscope", "kling", "mixed", "ark", "seedance"],
        },
        "video_surface": {
            "type": "string",
            "description": "可选 API 面覆盖（须属于当前 video_loop 家族）",
        },
        "allowed_providers": {"type": "array", "items": {"type": "string"}},
        "lock_preferred_provider": {"type": "boolean"},
        "budget_ceiling_usd": {"type": "number"},
        "cost_estimate_usd": {"type": "number"},
        "cast_ref_kind": {
            "type": "string",
            "enum": CAST_REF_KIND_VALUES,
            "description": (
                "全局默认身份参考图类型；被 character/form 级 cast_ref_kind 覆盖。"
                "可灵环强制等价 turnaround"
            ),
        },
        "quality_mode": {
            "type": "string",
            "enum": ["full", "strict", "degraded", "manual_only"],
            "description": (
                "P0-7/P0-4 quality policy: full requires VLM verification; "
                "strict additionally blocks VLM critical; degraded allows a skipped "
                "VLM but must remain explicitly unverified; manual_only is explicit "
                "human verification."
            ),
        },
    },
}

ASSET_MANIFEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["image", "video", "audio", "music", "other"],
                    },
                    "path": {"type": "string"},
                    "scene_id": {"type": "string"},
                    "shot_id": {"type": "string"},
                    "provider": {"type": "string"},
                    "license": {"type": "string"},
                    "url": {"type": "string"},
                    # 实测媒体参数：Agnes 720P 实为 1280x704、图片 2K 非 1920x1080。
                    "duration_seconds": {"type": "number"},
                    "width": {"type": "integer"},
                    "height": {"type": "integer"},
                },
            },
        },
        "reference_assets": {
            "type": "array",
            "description": "定妆照/场景参考/风格锚点（optional；P3 shot_runner 写入）",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["portrait", "turnaround", "scene_ref", "style_anchor", "prop"],
                    },
                    "character_id": {"type": "string"},
                    "scene_id": {"type": "string"},
                    "location_id": {
                        "type": "string",
                        "description": "空镜对齐 bible.locations[].id",
                    },
                    "prop_id": {"type": "string", "description": "对齐 script.props[].id"},
                    "form_id": {
                        "type": "string",
                        "description": "该参考图所属角色形态（form）；缺省/空=单隐式形态",
                    },
                    "path": {"type": "string"},
                    "url": {"type": "string"},
                    "provider": {"type": "string"},
                    "seed": {"type": "integer"},
                    "canonical": {
                        "type": "boolean",
                        "description": "身份记忆库 canonical 锚（P0-identity-memory）：同角色/形态唯一真源，_ref_index 优先取",
                    },
                    "identity_key": {
                        "type": "string",
                        "description": "对应 artifacts/identity_memory.json 的键：cid 或 cid:form",
                    },
                    "views": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "四视图转面所含方位（文档；不触发多次生图）",
                    },
                },
            },
        },
    },
}

PUBLISH_LOG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {"type": "string", "enum": ["draft", "exported", "published"]},
        "output_path": {"type": "string"},
        "profile": {"type": "string"},
        "platform": {"type": "string"},
        "notes": {"type": "string"},
        "exported_at": {"type": "string"},
    },
}

FILM_HEALTH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pass": {"type": "boolean"},
        "path": {"type": "string"},
        "path_source": {
            "type": "string",
            "enum": ["explicit", "renders", "auto_edit"],
            "description": "P0-7：成片来自显式路径 / renders/final.mp4 / auto_edit/final.mp4",
        },
        "probe": {"type": "object"},
        "critical": {"type": "array", "items": {"type": "object"}},
        "warnings": {"type": "array", "items": {"type": "object"}},
        "duration_check": {
            "type": "object",
            "description": "P0-7：时长偏差实值（delta/tolerance/longform），不只给一句「超过 x%」",
        },
        "audio_consistency": {
            "type": "object",
            "description": "P0-7：段间响度一致性（分块均值/中位/落差），测量失败只记 reason",
        },
        "continuity": {
            "type": "object",
            "description": "P0-7：镜连续性抽检（VLM，默认关；skipped=未执行，永不进 critical）",
        },
        "artifact": {"type": "string"},
    },
}

CONTINUITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "outfit": {"type": "string"},
                    "location_id": {"type": "string"},
                    "held_prop_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "props": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "present": {"type": "boolean"},
                },
            },
        },
        "locations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
            },
        },
        "last_shot_id": {"type": "string"},
    },
}

VLM_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pass": {"type": "boolean"},
        "skipped": {"type": "boolean"},
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "shot_id": {"type": "string"},
                    "ok": {"type": "boolean"},
                    "skipped": {"type": "boolean"},
                    "issues": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "severity": {"type": "string"},
                                "kind": {
                                    "type": "string",
                                    "enum": [
                                        "人物不一致",
                                        "道具丢失",
                                        "场景错位",
                                        "崩坏",
                                        "构图",
                                    ],
                                },
                                "message": {"type": "string"},
                                "proposed_fix": {"type": "string"},
                            },
                        },
                    },
                },
            },
        },
    },
}

CLIP_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["source_path", "cuts"],
    "properties": {
        "source_path": {"type": "string"},
        "cuts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "start_seconds": {"type": "number"},
                    "end_seconds": {"type": "number"},
                    "note": {"type": "string"},
                },
            },
        },
    },
}

# sidecar：可拍圣经。不进 pipeline produces；编译后真源仍是 script / scene_plan。
LOCATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "地点卡（空间，不是幕）。bible.scenes[] 仍是幕。",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "sensory": {
            "type": "string",
            "description": "交叉方位环境句（左侧近处/右侧更远处/画面中上方等），不是单段气氛词",
        },
        "sensory_by_time": {
            "description": (
                "按时间/光线的环境句变体；overlay 按 scene.environment.time "
                "（回落 lighting）优先选，未命中再回落 sensory。"
            ),
            "anyOf": [
                {"type": "object", "additionalProperties": {"type": "string"}},
                {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "time": {"type": "string"},
                            "lighting": {"type": "string"},
                            "sensory": {"type": "string"},
                        },
                        "required": ["sensory"],
                    },
                },
            ],
        },
        "appearance": {"type": "string", "description": "空镜可生图描述"},
        "cast_note": {
            "type": "string",
            "description": "空镜重抽时附带的一句修正",
        },
    },
}

SERIES_BIBLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "logline": {"type": "string"},
        "title": {"type": "string"},
        "synopsis": {"type": "string", "description": "梗概一段；logline 可从中压缩"},
        "theme": {"type": "string"},
        "extra_notes": {"type": "string"},
        "target_duration_seconds": {"type": "number", "description": "成片目标时长；不要写进 project.json"},
        "gold_lines": {
            "type": "array",
            "items": {"type": "string"},
            "description": "必选对白；compile 未入镜则 warning，不自动塞镜",
        },
        "music_direction": {
            "type": "object",
            "properties": {
                "instruments": {"type": "string"},
                "arc": {"type": "string"},
                "sync": {"type": "string"},
                "bgm_id": {"type": "string"},
            },
        },
        # 长片分章（v8.2 P0-0，可选）：与 structure（全局四拍描述）互补；
        # 声明后四拍在章内重置，短篇不声明行为不变。
        "chapters": {
            "type": "array",
            "description": "章节结构（长片主锚）：按日拆批次=章节粒度；章内四拍；"
            "bgm_id 作章节默认曲（音乐锚降章节内辅助，显式 scene.bgm_id 仍最高优先）",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "章节 id，缺省 chNN 自动编号"},
                    "title": {"type": "string", "description": "章标题"},
                    "role": {"type": "string", "description": "章节作用一句话"},
                    "hook": {"type": "string", "description": "章钩子"},
                    "start_scene": {"type": "string", "description": "章首场景 id（归属锚）"},
                    "bgm_id": {"type": "string", "description": "章节默认曲 id；无显式 scene.bgm_id 的场兜底"},
                    "target_duration_seconds": {"type": "number", "description": "章节目标时长段（秒）"},
                },
            },
        },
        "locations": {"type": "array", "items": LOCATION_SCHEMA},
        "structure": SCRIPT_SCHEMA["properties"]["structure"],
        "medium": {"type": "string"},
        "genres": {"type": "array", "items": {"type": "string"}},
        "playbook": {"type": "string"},
        "tone": {"type": "string"},
        "environment": ENVIRONMENT_SCHEMA,
        "library_hit_ids": {"type": "array", "items": {"type": "string"}},
        "characters": SCRIPT_SCHEMA["properties"]["characters"],
        "props": SCRIPT_SCHEMA["properties"]["props"],
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "sound_notes": {"type": "string"},
                    "keyframe_blurb": {"type": "string"},
                    "environment": ENVIRONMENT_SCHEMA,
                    "duration_seconds": {"type": "number"},
                    "narration": {"type": "string"},
                    "bgm_id": {
                        "type": "string",
                        "description": "显式曲目 id；overlay 抄到 scene_plan.scenes[].bgm_id",
                    },
                    "location_id": {
                        "type": "string",
                        "description": "幕绑定地点卡；compile 抄到 scene_plan 与镜头",
                    },
                    "lines": SCRIPT_SCHEMA["properties"]["sections"]["items"]["properties"]["lines"],
                    "blocking": NESTED_SHOT_SCHEMA["properties"]["blocking"],
                    "shots": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "shot_id": {"type": "string"},
                                "blocking": NESTED_SHOT_SCHEMA["properties"]["blocking"],
                                "shot_budget_class": NESTED_SHOT_SCHEMA["properties"]["shot_budget_class"],
                                "duration_seconds": NESTED_SHOT_SCHEMA["properties"]["duration_seconds"],
                                "cut": NESTED_SHOT_SCHEMA["properties"]["cut"],
                                "location_id": NESTED_SHOT_SCHEMA["properties"]["location_id"],
                                "subjects": VISUAL_DETAILS_SCHEMA["properties"]["subjects"],
                                "objects": VISUAL_DETAILS_SCHEMA["properties"]["objects"],
                                # V27：compile 侧 _overlay_shot 消费该字段（bible.py:193-203），
                                # 此前 schema 未声明（隐式契约）。导演执法①"换景别/角度"
                                # 的修复落点依赖它。嵌套镜头同款 dict 形状。
                                "shot_language": {"type": "object"},
                                "form_id": {"type": "string"},
                                # P0-8：同款隐式契约显式化——_overlay_shot 消费
                                # hero_moment（密度红线事实源）与 vfx（特效指导制定）。
                                "hero_moment": {"type": "boolean"},
                                "vfx": {
                                    "type": "array",
                                    "description": "P0-8 特效指导制定的观感特效；唯一事实源在此（D15），scene_plan 每次重编译从零再生",
                                    "items": VFX_ITEM_SCHEMA,
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}

EPISODE_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "本集切片；W1 不创建 episodes/ 目录",
    "properties": {
        "episode_id": {"type": "string"},
        "character_ids": {"type": "array", "items": {"type": "string"}},
        "scene_ids": {"type": "array", "items": {"type": "string"}},
    },
}

EPISODES_INDEX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "系列分集索引；不进 pipeline produces；length>=2 才物化",
    "properties": {
        "episodes": {
            "type": "array",
            "items": EPISODE_PLAN_SCHEMA,
        },
    },
}

FORMAT_CARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "级联分析卡；不进 pipeline produces",
    "properties": {
        "chosen": {
            "type": "object",
            "properties": {
                "medium": {"type": "string"},
                "genres": {"type": "array", "items": {"type": "string"}},
                "playbook": {"type": "string"},
                "pipeline_type": {"type": "string"},
            },
        },
        "alternatives": {"type": "array", "items": {"type": "object"}},
        "user_specified": {"type": "object"},
        "library_hit_ids": {"type": "array", "items": {"type": "string"}},
        "pinned_medium": {"type": "boolean"},
        "query": {"type": "string"},
    },
}

# 图 ↔ 场景文字 冻结绑定（observe-only）。宽松 schema，便于后续扩字段。
IMAGE_BINDING_REF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "kind": {"type": "string"},
        "role": {"type": "string"},
        "form_id": {"type": "string"},
        "character_id": {"type": "string"},
        "location_id": {"type": "string"},
        "prop_id": {"type": "string"},
        "path": {"type": "string"},
        "url": {"type": "string"},
        # 实发下标；回填重算时为 null（源不可信）。
        "picture_index": {"type": ["integer", "null"]},
        "source": {"type": "string"},
    },
}

IMAGE_BINDING_SEGMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "index": {"type": "integer"},
        "seconds": {"type": "number"},
        "bridge": {"type": "boolean"},
        "refs": {"type": "array", "items": IMAGE_BINDING_REF_SCHEMA},
    },
}

IMAGE_BINDING_SHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scene_id": {"type": ["string", "null"]},
        "location_id": {"type": ["string", "null"]},
        "location_sensory": {"type": "string"},
        "environment": {"type": "string"},
        "character_forms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "character_id": {"type": "string"},
                    "form_id": {"type": "string"},
                },
            },
        },
        "prop_ids": {"type": "array", "items": {"type": "string"}},
        "refs": {"type": "array", "items": IMAGE_BINDING_REF_SCHEMA},
        "segments": {"type": "array", "items": IMAGE_BINDING_SEGMENT_SCHEMA},
        "first_frame": {"type": ["object", "null"]},
        "video": {"type": ["object", "null"]},
    },
}

IMAGE_BINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "生成的图/首帧 ↔ 场景文字/形态/实发 <Picture N> 的冻结绑定（v1 observe-only）",
    "properties": {
        "version": {"type": "integer"},
        "shots": {
            "type": "object",
            "additionalProperties": IMAGE_BINDING_SHOT_SCHEMA,
        },
        "cast": {
            "type": "object",
            "additionalProperties": IMAGE_BINDING_REF_SCHEMA,
        },
    },
    "required": ["shots", "cast"],
}

# 角色评审日志（V35）——宽松 schema，append-only .jsonl 不进 pipeline produces。
# 每行一个对象；subject 枚举见 tools/review_logger.py REVIEW_LOG_SUBJECTS（V30）。
REVIEW_LOG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "review_log.jsonl 单行；角色评审 append-only（V35 三日志分工）",
    "properties": {
        "timestamp": {"type": "string"},
        "role": {"type": "string"},
        "subject": {"type": "string"},
        "phase": {
            "type": "string",
            "enum": ["first_pass", "revise", "status_update"],
            "description": "V47：first_pass=子 Agent 关卡（不占 4 轮额度）；缺省=revise",
        },
        "round": {"type": "integer", "description": "手填参考；summary 按时间戳自动算（V30）"},
        "decision": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "finding_id": {"type": "string"},
                    "severity": {"type": "string"},
                    "field": {"type": "string"},
                    "message": {"type": "string"},
                    "proposed_fix": {"type": "string"},
                    "status": {
                        "type": "string",
                        "enum": [
                            "open", "in_progress", "fixed", "verified",
                            "waived", "invalid",
                        ],
                    },
                    "evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "metric": {
                        "type": "string",
                        "description": "P0-4 客观量规编号：m1/m2/m5/m6/drift（m3/m4 已删）",
                    },
                    "value": {"type": "number"},
                    "threshold": {"type": "number"},
                },
            },
        },
        "dissent": {"type": "string"},
        "scores": {"type": "object"},
        "self_review_findings_fixed": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["timestamp", "role", "subject", "decision"],
}

SCENE_INDEX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "P0-2 场景语义聚合：情节单元索引（时间戳均由确定性切点推导，LLM 只回索引）",
    "required": ["version", "shots", "units"],
    "properties": {
        "version": {"type": "string"},
        "grouping": {
            "type": "string",
            "description": "deterministic=VLM 未参与；vlm=边界并集含 VLM 判定",
        },
        "source": {"type": "object"},
        "params": {"type": "object"},
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["shot_id"],
                "properties": {
                    "shot_id": {"type": "string"},
                    "index": {"type": "integer"},
                    "start_seconds": {"type": "number"},
                    "end_seconds": {"type": "number"},
                    "unit_id": {"type": "string"},
                    "characters": {"type": "array", "items": {"type": "string"}},
                    "text": {"type": "string"},
                },
            },
        },
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["unit_id"],
                "properties": {
                    "unit_id": {"type": "string"},
                    "index": {"type": "integer"},
                    "narrative_role": {
                        "type": "string",
                        "enum": ["hook", "escalation", "reveal", "landing"],
                        "description": "四拍按单元位置确定性指派（与 series_bible.structure 同词表）",
                    },
                    "start_seconds": {"type": "number"},
                    "end_seconds": {"type": "number"},
                    "shot_indices": {"type": "array", "items": {"type": "integer"}},
                    "shot_ids": {"type": "array", "items": {"type": "string"}},
                    "text": {"type": "string"},
                    "summary": {"type": "string"},
                    "characters": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "unit_boundaries_seconds": {"type": "array", "items": {"type": "number"}},
        "preferred_cuts": {"type": "array", "items": {"type": "object"}},
        "similarities": {"type": "array", "items": {"type": "object"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}

PLAN_HISTORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "P0-3 版本链索引：plan.json 的历代快照 + 字段级 diff（快照在 tmp_autoedit/plan_history/）",
    "required": ["version", "current", "entries"],
    "properties": {
        "version": {"type": "string"},
        "current": {"type": "integer", "description": "最新版本号（rev 从 1 起单调增）"},
        "pruned": {"type": "integer", "description": "因超上限被裁掉的快照数"},
        "max_versions": {
            "type": ["integer", "null"],
            "description": "项目级版本上限（null=不限；显式传入才改，否则沿用）",
        },
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["rev", "path"],
                "properties": {
                    "rev": {"type": "integer"},
                    "path": {"type": "string"},
                    "at": {"type": "string"},
                    "reason": {"type": "string"},
                    "session_id": {"type": "string"},
                    "plan_sha": {"type": "string", "description": "plan_core 的 sha256 前 16 位（内容等价判定）"},
                    "overrides": {"type": "object"},
                    "diff_from_prev": {
                        "type": ["object", "null"],
                        "description": "与上一版的字段级差异（首版为 null）",
                        "properties": {
                            "unchanged": {"type": "boolean"},
                            "shots_added": {"type": "array", "items": {"type": "string"}},
                            "shots_removed": {"type": "array", "items": {"type": "string"}},
                            "shots_retimed": {"type": "array", "items": {"type": "string"}},
                            "shots_resped": {"type": "array", "items": {"type": "string"}},
                            "segments_action_changed": {"type": "object"},
                            "params_changed": {"type": "object"},
                            "edits_changed": {"type": "integer"},
                            "duration_before": {"type": "number"},
                            "duration_after": {"type": "number"},
                            "duration_delta": {"type": "number"},
                            "counts": {"type": "object"},
                        },
                    },
                },
            },
        },
    },
}

BEAT_MAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "P0-5 能量波产物（tmp_autoedit/beat_map.json）：拍网格 + 每 bar 能量 + Bar-DP 切点。"
        "顶层 feasible=切点是否真被能量波接管（量规层据此标 m5/m6 circular）"
    ),
    "required": ["version", "feasible", "bpm", "bpm_source", "cuts", "warnings"],
    "properties": {
        "version": {"type": "string"},
        "operation": {"type": "string"},
        "feasible": {"type": "boolean"},
        "bpm": {"type": "number", "description": "0=估不出拍（切点不由能量波接管）"},
        "bpm_source": {"type": "string", "enum": ["", "explicit", "estimated"]},
        "beats_per_bar": {"type": "integer"},
        "min_hold": {"type": "number"},
        "max_hold": {"type": "number"},
        "energy_source": {"type": "string", "enum": ["", "ebur128", "pcm_rms"]},
        "duration": {"type": "number"},
        "grid": {
            "type": ["object", "null"],
            "description": "P0-4 beat_grid 形状（bpm/fps/offset_seconds/start_seconds/frames_per_beat/source）+ beats_per_bar",
            "properties": {
                "bpm": {"type": "number"},
                "fps": {"type": "number"},
                "offset_seconds": {"type": "number"},
                "start_seconds": {"type": "number"},
                "frames_per_beat": {"type": "number"},
                "beats_per_bar": {"type": "integer"},
                "source": {"type": "string"},
            },
        },
        "bars": {
            "type": "array",
            "description": "每 bar 能量（start/end_seconds + mean_db + energy∈[0,1]）",
            "items": {"type": "object"},
        },
        "cuts": {"type": "array", "items": {"type": "number"}, "description": "已接管源的切点（秒）"},
        "cuts_source_index": {"type": ["integer", "null"]},
        "cuts_by_source": {"type": ["object", "null"], "description": "逐源 replaced/kept_scene_cuts 与原因"},
        "note": {"type": ["object", "null"]},
        "sources": {"type": "array", "items": {"type": "object"}, "description": "逐源明细（path/cuts/bars/feasible/warnings）"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "schema_errors": {"type": "array", "items": {"type": "string"}},
    },
}

EDIT_METRICS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "剪辑导演客观量规报告（P0-4）：DIRECT m1/m2/m5/m6 + 身份漂移；m3/m4 已删",
    "required": ["version", "metrics", "pass"],
    "properties": {
        "version": {"type": "string"},
        "fps": {"type": "number"},
        "beat_tolerance_frames": {"type": "integer"},
        "beat_grid_source": {
            "type": "string",
            "description": "拍网格来路：P0-5 能量波（energy_wave/estimated）或 soundtrack 事件",
        },
        "beat_grid_bpm": {"type": ["number", "null"]},
        "beat_grid_bpm_source": {
            "type": "string",
            "description": "bpm 来路：explicit（曲库/人给）或 estimated（自相关估拍，需人工确认）",
        },
        "circular_metrics": {
            "type": "array",
            "items": {"type": "string"},
            "description": "自我满足的指标（P0-5 Bar-DP 生成的切点使 m5/m6 必然达标）：不作质量证据",
        },
        "circular_note": {"type": "string"},
        "deleted_metrics": {"type": "array", "items": {"type": "string"}},
        "pass": {"type": "boolean"},
        "metrics": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "metric": {"type": "string"},
                    "name": {"type": "string"},
                    "value": {"type": "number"},
                    "threshold": {"type": "number"},
                    "unit": {"type": "string"},
                    "direction": {"type": "string", "enum": ["higher", "lower"]},
                    "source": {"type": "string"},
                    "pass": {"type": "boolean"},
                    "skipped": {"type": "boolean"},
                    "circular": {
                        "type": "boolean",
                        "description": "true=值自我满足（切点由该网格/能量生成），只当回归哨兵",
                    },
                    "reason": {"type": "string"},
                    "detail": {"type": "object"},
                    "findings": {"type": "array", "items": {"type": "object"}},
                },
            },
        },
        "findings": {"type": "array", "items": {"type": "object"}},
    },
}

SCHEMAS: dict[str, dict[str, Any]] = {
    "research_brief": RESEARCH_BRIEF_SCHEMA,
    "proposal_packet": PROPOSAL_PACKET_SCHEMA,
    "series_bible": SERIES_BIBLE_SCHEMA,
    "episode_plan": EPISODE_PLAN_SCHEMA,
    "episodes": EPISODES_INDEX_SCHEMA,
    "format_card": FORMAT_CARD_SCHEMA,
    "script": SCRIPT_SCHEMA,
    "scene_plan": SCENE_PLAN_SCHEMA,
    "shot_prompts": SHOT_PROMPTS_SCHEMA,
    "compose_plan": COMPOSE_PLAN_SCHEMA,
    "asset_manifest": ASSET_MANIFEST_SCHEMA,
    "edit_decisions": EDIT_DECISIONS_SCHEMA,
    "render_report": RENDER_REPORT_SCHEMA,
    "publish_log": PUBLISH_LOG_SCHEMA,
    "film_health": FILM_HEALTH_SCHEMA,
    "continuity": CONTINUITY_SCHEMA,
    "vlm_review": VLM_REVIEW_SCHEMA,
    "clip_plan": CLIP_PLAN_SCHEMA,
    "auto_edit_plan": AUTO_EDIT_PLAN_SCHEMA,
    "auto_edit_report": AUTO_EDIT_REPORT_SCHEMA,
    "image_bindings": IMAGE_BINDINGS_SCHEMA,
    "review_log": REVIEW_LOG_SCHEMA,
    "edit_metrics": EDIT_METRICS_SCHEMA,
    "beat_map": BEAT_MAP_SCHEMA,
    "scene_index": SCENE_INDEX_SCHEMA,
    "plan_history": PLAN_HISTORY_SCHEMA,
}


def get_schema(name: str) -> dict[str, Any] | None:
    return SCHEMAS.get(name)
