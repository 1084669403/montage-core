"""chase_comedy — 单向追逐·喜剧物理 playbook（原创）。

对应母带模式 ``chase_axis``（参考 4：废墟厨房单向追逐）。
核心纪律：屏幕方向恒左→右不越轴、三次抓捕升级、唯一一处慢动作、
甩出物件真实物理、现场音效无对白。
"""

PLAYBOOK: dict = {
    "id": "chase_comedy",
    "title": "单向追逐·喜剧物理",
    "identity": {
        "name": "Chase Comedy",
        "category": "comedy",
        "mood": "俏皮，荒诞，紧迫，滑稽",
        "pace": "快",
        "best_for": "单向追逐、喜剧打闹、物理笑话、厨房/室内动作戏",
    },
    "master_pattern": "chase_axis",
    "visual_language": {
        "aesthetic": "照片级真人实拍，真实材质，实体电影镜头，冷调高对比室内光，60:30:10 色彩比例",
        "composition": "屏幕方向恒定：猎物恒前右、追赶者恒左后，绝不越轴、绝不并排",
        "color_palette": ["#8A9BA8", "#7A5C3E", "#3B4A63", "#5C5C5C", "#D8D2C2"],
        "texture": "35mm 胶片颗粒，180° 快门运动模糊，真实灰尘/蒸汽/瓦砾",
    },
    "motion": {
        "transitions": ["cut"],
        "animation_style": "连续手持微抖，横向跟随追逐，甩镜（whip-pan）跟随被甩物件，实时速度",
        "pacing_rules": {
            "min_scene_hold_seconds": 5.0,
            "max_scene_hold_seconds": 7.0,
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
        "voice_style": "无对白，情绪靠动作与表情",
        "music_mood": "无音乐，或一段轻快俏皮的喜剧配乐、音量压得极低",
        "music_volume": 0.05,
        "skip_bgm": True,
        "sfx_style": "慌乱小碎步、沉爪步、甩刀呼啸、擦脸慢镜呼啸、苹果闷响、酱油瓶碎裂、打滑与水花",
    },
    "asset_generation": {
        "character_appearance_default": "原创拟人角色，海军蓝棒球帽配米白袖学院夹克 / 裂壳绿芽条形码种子",
        "image_negative_prompt": "3d render, game engine, cel shading, cartoon, comic effect, halftone, pop art, subtitle, watermark, slow motion",
        "consistency_anchors": [
            "屏幕方向恒左→右推进，猎物右前、追赶者左后，不越轴",
            "三次抓捕一次比一次接近（半米→擦及→几乎按住）",
            "每次甩回物件恰在将抓到之际落下、打断抓捕",
            "无对白，喜剧全靠动作、表情与肢体表演",
        ],
    },
    "quality_rules": [
        "仅使用硬切，避免叠化",
        "实时速度；仅允许唯一一处慢动作（第二镜刀擦脸瞬间），撞击升回全速",
        "甩出物件按真实弧线/质量/惯性飞行旋转",
        "21:9 变形宽银幕；单一场景不换地点",
        "原创角色，无 logo、无真实品牌；无字幕无水印；无血腥",
    ],
    "script_style": {
        "template_id": "scripts/elements-cinematic",
        "dialogue_wps": 0.0,
        "require_characters": True,
        "require_structure": True,
        "require_environment": True,
        "require_speakers": False,
    },
}
