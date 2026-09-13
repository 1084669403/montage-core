"""manga_panel — 漫画分镜·高对比剪影 playbook（原创）。

画面风格（描边/网点/高对比）走 AI 生成；对话框入场动画不是本 playbook 的职责。
"""

PLAYBOOK: dict = {
    "id": "manga_panel",
    "title": "漫画分镜·高对比剪影",
    "identity": {
        "name": "Manga Panel Contrast",
        "category": "cinematic",
        "mood": "张力，停格，黑白或有限套色",
        "pace": "快",
        "best_for": "漫画改编短片、高对比对峙、信息停格、拟声词场面",
    },
    "visual_language": {
        "aesthetic": "高对比漫画分镜，清晰描边，网点或平涂阴影，剪影可读",
        "composition": "面板感构图，特写与大远景反差，信息量最大的一格可标 hero_moment",
        "color_palette": ["#111111", "#F5F5F5", "#C41E3A", "#4A4A4A", "#D9D9D9"],
        "texture": "网点、速度线、硬边阴影，避免照片噪点",
    },
    "motion": {
        "transitions": ["cut"],
        "animation_style": "切镜干脆，关键格可短暂停住，少用叠化",
        "pacing_rules": {
            "min_scene_hold_seconds": 1.2,
            "max_scene_hold_seconds": 4.5,
            "transition_duration_seconds": 0.0,
        },
    },
    "audio": {
        "voice_style": "短句，力度外放",
        "music_mood": "低音铺底或无配乐，高潮一击鼓",
        "music_volume": 0.18,
        "skip_bgm": False,
        "bgm_id": "bgm/dark-drone",
        "sfx_style": "拟声留给字幕；音效只点关键动作",
    },
    "asset_generation": {
        "stylized_image": True,
        "character_appearance_default": "漫画线稿面容，标志性发型固定，黑白或有限套色，眼睛高光明确",
        "image_negative_prompt": "写实照片, 电影调色, 柔和胶片颗粒, 水彩虚化, 多余肢体, 水印",
        "consistency_anchors": [
            "高对比描边贯穿",
            "角色剪影与发型固定",
            "避免写实布光",
        ],
    },
    "quality_rules": [
        "禁止把漫画风做成电影调色大片",
        "每段至少一个信息停格（hero_moment）",
        "拟声词不要写进 TTS 正文",
        "转场以硬切为主",
    ],
    "script_style": {
        "template_id": "scripts/elements-manga",
        "dialogue_wps": 5.0,
        "require_characters": True,
        "require_structure": True,
        "require_environment": True,
        "require_speakers": True,
    },
}
