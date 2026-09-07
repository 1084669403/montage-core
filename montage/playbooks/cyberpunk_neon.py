"""cyberpunk_neon — 赛博朋克·霓虹雨夜 playbook（原创）。"""

PLAYBOOK: dict = {
    "id": "cyberpunk_neon",
    "title": "赛博朋克·霓虹雨夜",
    "identity": {
        "name": "Cyberpunk Neon Rain",
        "category": "cinematic",
        "mood": "冷峻，压抑，霓虹迷离，末日前夜",
        "pace": "中快",
        "best_for": "科幻都市、黑客题材、夜戏追逐、赛博叙事",
    },
    "visual_language": {
        "aesthetic": "赛博朋克都市夜景，霓虹光污染，湿润路面反射，全息广告",
        "composition": "对称与纵深构图，低角度，霓虹色带引导视线",
        "color_palette": ["#0A1A2F", "#00E5FF", "#FF2A6D", "#B388FF", "#1F3B57"],
        "texture": "细雨颗粒，玻璃反光，轻微色差（chromatic aberration）",
    },
    "motion": {
        "transitions": ["cut", "whip-pan", "glitch"],
        "animation_style": "利落干脆，小幅手持晃动，高速运镜",
        "pacing_rules": {
            "min_scene_hold_seconds": 1.8,
            "max_scene_hold_seconds": 6,
            "transition_duration_seconds": 0.3,
        },
    },
    "audio": {
        "voice_style": "冷静、低哑、略急促",
        "music_mood": "合成器波（synthwave）、低沉电子 drone、脉冲节拍",
        "music_volume": 0.25,
        "skip_bgm": False,
        "bgm_id": "bgm/dark-drone",
        "sfx_style": "雨声、电流、全息提示音、远处警笛",
    },
    "asset_generation": {
        "character_appearance_default": "东亚面容，短发或贴发，义体义眼细节，机能风外套",
        "image_negative_prompt": "photorealistic sunny day, bright daylight, low quality, blurry, watermark",
        "consistency_anchors": [
            "青蓝霓虹（#00E5FF）与品红（#FF2A6D）双主色贯穿",
            "雨夜湿润反射的路面贯穿全片",
            "夜景低光环境，无日光",
        ],
    },
    "quality_rules": [
        "夜景低光为主，避免高亮过曝",
        "霓虹主色仅用青蓝/品红两系，禁止混入暖黄",
        "追逐段落镜头 ≤3s，情绪段落可放宽至 6s",
        "所有角色带义体/科技元素锚点",
    ],
    "script_style": {
        "template_id": "scripts/elements-cinematic",
        "dialogue_wps": 5.0,
        "require_characters": True,
        "require_structure": True,
        "require_environment": True,
        "require_speakers": True,
    },
}
