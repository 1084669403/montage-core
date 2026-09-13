"""documentary_restraint — 纪实克制·真实质感 playbook（原创）。"""

PLAYBOOK: dict = {
    "id": "documentary_restraint",
    "title": "纪实克制·真实质感",
    "identity": {
        "name": "Documentary Restraint",
        "category": "documentary",
        "mood": "真实，克制，客观，在场感",
        "pace": "中速",
        "best_for": "纪录片、访谈、社会观察、人物纪实、历史档案",
    },
    "master_pattern": "film_spectacle",
    "visual_language": {
        "aesthetic": "自然光纪实质感，低饱和真实色彩，手持呼吸感，不刻意构图",
        "composition": "自然取景，访谈用三分法，避免完美对称的摆拍感",
        "color_palette": ["#5C5C5C", "#8A8578", "#B7B09E", "#3D3A34", "#D8D2C2"],
        "texture": "真实颗粒，无滤镜感，宽容度高",
    },
    "motion": {
        "transitions": ["cut"],
        "animation_style": "手持轻微晃动，跟随拍摄，无炫技运镜",
        "pacing_rules": {
            "min_scene_hold_seconds": 3.0,
            "max_scene_hold_seconds": 15,
            "transition_duration_seconds": 0.0,
        },
        "beat_camera": {
            "hook": "handheld",
            "escalation": "handheld",
            "reveal": "handheld",
            "landing": "static",
            "chase": "handheld",
        },
    },
    "audio": {
        "voice_style": "自然口语、无播音腔、保留停顿与语气词",
        "music_mood": "极简 ambient 或无配乐，环境声为主",
        "music_volume": 0.08,
        "skip_bgm": True,
        "sfx_style": "环境底噪真实保留，不刻意消除",
    },
    "asset_generation": {
        "stylized_image": True,
        "character_appearance_default": "自然真实的中式面容，无妆感，日常便服",
        "image_negative_prompt": "电影滤镜, 浓重调色, 戏剧布光, 写实照片3D, 风格化, 低画质",
        "consistency_anchors": [
            "真实自然光，无舞台光",
            "低饱和中性色调贯穿全片",
            "环境音与现场感保留",
        ],
    },
    "quality_rules": [
        "禁止叠化/缩放冲击等花哨转场，只用硬切",
        "禁用人造高饱和调色（见 luts/muted-documentary）",
        "访谈镜头保持 ≥3s，避免频繁切机位",
        "音乐电平 ≤0.1，不压环境声",
    ],
    "script_style": {
        "template_id": "scripts/elements-spoken",
        "dialogue_wps": 5.0,
        "require_characters": False,
        "require_structure": False,
        "require_environment": False,
        "require_speakers": False,
    },
}
