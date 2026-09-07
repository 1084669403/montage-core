"""anime_shonen — 动漫·鲜明角色锚点 playbook（原创）。

日漫/国漫共用一本：外观符号写进 consistency_anchors 与 appearance_default，
负向词互斥时再拆第二本。
"""

PLAYBOOK: dict = {
    "id": "anime_shonen",
    "title": "动漫·鲜明角色锚点",
    "identity": {
        "name": "Anime Character Anchor",
        "category": "cinematic",
        "mood": "鲜明，热血或羁绊，符号清晰",
        "pace": "中快",
        "best_for": "日漫/国漫短片、角色对决、校园羁绊、热血成长",
    },
    "visual_language": {
        "aesthetic": "动画赛璐璐质感，干净线稿，角色发色瞳色标志物固定，背景可略简化",
        "composition": "角色中近景优先，关键动作用夸张透视，避免写实皮肤毛孔",
        "color_palette": ["#2B2D42", "#EF233C", "#FFD166", "#118AB2", "#F8F4E3"],
        "texture": "平涂色块，软阴影，高光色块，无照片颗粒",
    },
    "motion": {
        "transitions": ["cut", "wipe"],
        "animation_style": "动作镜利落，情绪镜可短暂定格，避免实拍手持晃动",
        "pacing_rules": {
            "min_scene_hold_seconds": 1.5,
            "max_scene_hold_seconds": 6,
            "transition_duration_seconds": 0.2,
        },
    },
    "audio": {
        "voice_style": "角色声线分明，口癖保留，情绪外放",
        "music_mood": "管弦或电子铺底，高潮可上鼓点",
        "music_volume": 0.22,
        "skip_bgm": False,
        "bgm_id": "bgm/epic-orchestral-rise",
        "sfx_style": "刀风、脚步、环境 sparingly，拟声留给字幕而非 TTS",
    },
    "asset_generation": {
        "character_appearance_default": "东亚动漫面容，标志性发色与瞳色固定，校服或战斗装有可复述的符号配件",
        "image_negative_prompt": "photorealistic, skin pores, live action, 3d render, extra fingers, watermark, realistic photograph",
        "consistency_anchors": [
            "发色瞳色与标志物全片不得改",
            "赛璐璐平涂，禁止写实皮肤",
            "角色剪影可读",
        ],
    },
    "quality_rules": [
        "跨镜不得更换发色/瞳色/标志配件",
        "禁止把角色画成真人照片",
        "对白必须装进时长网格",
        "每场至少一个可见动作节拍",
    ],
    "script_style": {
        "template_id": "scripts/elements-anime",
        "dialogue_wps": 5.0,
        "require_characters": True,
        "require_structure": True,
        "require_environment": True,
        "require_speakers": True,
    },
}
