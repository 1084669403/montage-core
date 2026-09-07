"""spoken_explain — 口播讲解 playbook（原创）。

不要命名为 talking_head：与数字人供应商工具重名。竖屏档案由 style pack 绑定。
"""

PLAYBOOK: dict = {
    "id": "spoken_explain",
    "title": "口播讲解·清晰构图",
    "identity": {
        "name": "Spoken Explain",
        "category": "documentary",
        "mood": "清晰，直接，信息密度高",
        "pace": "中速",
        "best_for": "口播讲解、知识科普、产品说明、竖屏信息片",
    },
    "visual_language": {
        "aesthetic": "干净背景，主体居中或三分法，光线均匀，面部清晰",
        "composition": "中近景为主，避免复杂群戏与炫技运镜",
        "color_palette": ["#F7F7F7", "#222222", "#2F80ED", "#E8EEF7", "#6B7280"],
        "texture": "低噪点，轻微锐化，无重胶片感",
    },
    "motion": {
        "transitions": ["cut"],
        "animation_style": "机位稳定，极少运动，切点对齐信息点",
        "pacing_rules": {
            "min_scene_hold_seconds": 2.5,
            "max_scene_hold_seconds": 8,
            "transition_duration_seconds": 0.0,
        },
        "beat_camera": {
            "hook": "static",
            "escalation": "static",
            "reveal": "static",
            "landing": "static",
            "chase": "static",
            "transition": "static",
        },
    },
    "audio": {
        "voice_style": "普通话清晰、中等语速、少口头禅",
        "music_mood": "轻底或无配乐，不压人声",
        "music_volume": 0.12,
        "skip_bgm": False,
        "bgm_id": "bgm/calm-piano",
        "sfx_style": "极少音效，转场可轻点",
    },
    "asset_generation": {
        "character_appearance_default": "东亚面容，干净出镜，日常得体服装，无夸张妆造",
        "image_negative_prompt": "crowded background, cinematic bokeh chaos, extra people, text overlay, watermark, low quality",
        "consistency_anchors": [
            "单一讲解主体，背景干净",
            "面部始终清晰可见",
            "避免复杂群戏",
        ],
    },
    "quality_rules": [
        "开场 3 秒内要有钩子",
        "每段只讲一个信息点",
        "字幕大且可读（由 style pack / subtitle_builder 落地）",
        "人声优先，BGM 不得压过对白",
    ],
    "script_style": {
        "template_id": "scripts/elements-spoken",
        "dialogue_wps": 5.0,
        "require_characters": False,
        "require_structure": False,
        "require_environment": True,
        "require_speakers": True,
    },
}
