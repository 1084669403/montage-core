"""chinese_elegance — 中文优雅·水墨写意 playbook（原创）。

结构与 `shot_prompt_builder` 的 style_context 消费点对齐：
identity.mood / visual_language.aesthetic / asset_generation.*（外观默认、
一致性锚点、图片负向词）会被逐镜头提示词自动消费；quality_rules 供
reviewer（docs/REVIEWER.md）与导演做风格门禁。
"""

PLAYBOOK: dict = {
    "id": "chinese_elegance",
    "title": "中文优雅·水墨写意",
    "identity": {
        "name": "Chinese Elegance (Ink Wash)",
        "category": "cinematic",
        "mood": "典雅，悠远，留白，诗意",
        "pace": "舒缓",
        "best_for": "国风叙事、历史题材、茶道书法、仙侠意境、文化讲解",
    },
    "visual_language": {
        "aesthetic": "水墨写意，大量留白，低饱和宣纸质感，边缘晕染",
        "composition": "居中构图为主，留白充分，重要视觉用三分法",
        "color_palette": ["#1A1A1A", "#6B5B4F", "#9C7A5A", "#E8E0D0", "#8B0000"],
        "texture": "宣纸纹理，淡墨晕染渐变，无重颗粒",
    },
    "motion": {
        "transitions": ["dissolve", "fade", "slow-zoom"],
        "animation_style": "平滑徐缓，如笔墨流动，无弹跳",
        "pacing_rules": {
            "min_scene_hold_seconds": 3.5,
            "max_scene_hold_seconds": 12,
            "transition_duration_seconds": 0.8,
        },
    },
    "audio": {
        "voice_style": "沉稳、从容、温润，中等语速",
        "music_mood": "古筝、箫、琵琶、空灵氛围",
        "music_volume": 0.2,
        "skip_bgm": False,
        "bgm_id": "bgm/chinese-traditional",
        "sfx_style": "笔触、水滴、纸页翻动",
    },
    "asset_generation": {
        "stylized_image": True,
        "character_appearance_default": "中国古典审美面容，东方人特征，发髻或盘发，素色服饰",
        "image_negative_prompt": "写实照片, 3D渲染, 霓虹, 杂乱, 低画质, 现代数字艺术",
        "consistency_anchors": [
            "墨色为主基调，朱砂色仅作点睛",
            "宣纸留白背景贯穿全片",
            "水墨晕染质感，柔和散射光，无硬阴影",
        ],
    },
    "quality_rules": [
        "每屏颜色不超过 4 种（不含背景）",
        "建景镜头保持 ≥3.5s",
        "转场间隔 ≥2.5s，禁止快速剪辑",
        "人物默认外观使用 character_appearance_default",
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
