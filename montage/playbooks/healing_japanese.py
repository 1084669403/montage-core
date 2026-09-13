"""healing_japanese — 日系治愈·清透暖光 playbook（原创）。"""

PLAYBOOK: dict = {
    "id": "healing_japanese",
    "title": "日系治愈·清透暖光",
    "identity": {
        "name": "Healing Japanese Light",
        "category": "cinematic",
        "mood": "温柔，治愈，清透，小确幸",
        "pace": "舒缓",
        "best_for": "治愈系日常、青春成长、美食记录、乡村生活",
    },
    "visual_language": {
        "aesthetic": "日系胶片清透感，高调柔光，奶油色高光，柔和对比",
        "composition": "三分法为主，大量天空留白，生活化细节特写",
        "color_palette": ["#FDF6EC", "#FFB88C", "#A8D8EA", "#7FB069", "#4A4A4A"],
        "texture": "轻微胶片颗粒，柔焦光晕，无锐利边缘",
    },
    "motion": {
        "transitions": ["dissolve", "fade", "crossfade"],
        "animation_style": "缓慢轻柔，自然呼吸感，无剧烈运镜",
        "pacing_rules": {
            "min_scene_hold_seconds": 2.8,
            "max_scene_hold_seconds": 10,
            "transition_duration_seconds": 0.7,
        },
    },
    "audio": {
        "voice_style": "轻快、亲切、带笑意",
        "music_mood": "尤克里里、原声吉他、轻柔钢琴、鸟鸣环境",
        "music_volume": 0.18,
        "skip_bgm": False,
        "bgm_id": "bgm/gentle-ukulele",
        "sfx_style": "鸟鸣、风铃、餐具轻碰、树叶沙沙",
    },
    "asset_generation": {
        "character_appearance_default": "东亚面容，自然裸妆，棉麻日常服饰，温暖神情",
        "image_negative_prompt": "昏暗, 阴郁, 恐怖, 高饱和霓虹, 低画质, 模糊, 水印",
        "consistency_anchors": [
            "奶油白高光基调贯穿全片",
            "自然柔光，无硬阴影",
            "暖白（#FDF6EC）背景为主",
        ],
    },
    "quality_rules": [
        "整体明度高、对比柔和，禁止暗调镜头",
        "转场以叠化/淡入淡出为主，禁止硬切",
        "人物神情温暖，避免冷峻表情",
        "空镜优先生活细节（食物/植物/光影）",
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
