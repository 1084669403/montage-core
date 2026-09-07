"""schemas — 规范产物 JSON Schema（精简、原创）。

故意保持"松校验"：只约束每个产物的契约字段，不约束表达细节——
细节由导演技能/LLM 决定，schema 只保证"下一阶段能读"。

本文件为全新原创代码。
"""

from __future__ import annotations

from typing import Any

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
        "visual_details": VISUAL_DETAILS_SCHEMA,
        "audio_prompt": AUDIO_PROMPT_SCHEMA,
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
                    "hero_moment": {"type": "boolean"},
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
                    "lut": {"type": "string"},
                    "subtitle_cues": {"type": "array", "items": {"type": "object"}},
                    "audio_events": {"type": "array", "items": {"type": "object"}},
                    "effects": {"type": "array", "items": {"type": "object"}},
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
                    "path": {"type": "string"},
                    "url": {"type": "string"},
                    "provider": {"type": "string"},
                    "seed": {"type": "integer"},
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
        "probe": {"type": "object"},
        "critical": {"type": "array", "items": {"type": "object"}},
        "warnings": {"type": "array", "items": {"type": "object"}},
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
                                "cut": NESTED_SHOT_SCHEMA["properties"]["cut"],
                                "location_id": NESTED_SHOT_SCHEMA["properties"]["location_id"],
                                "subjects": VISUAL_DETAILS_SCHEMA["properties"]["subjects"],
                                "objects": VISUAL_DETAILS_SCHEMA["properties"]["objects"],
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
}


def get_schema(name: str) -> dict[str, Any] | None:
    return SCHEMAS.get(name)
