"""guochao_anime_real — 中国式动漫真实风 playbook（原创）。

定位：**国产三维动画电影质感**（半写实动漫）——写实比例与材质（次表面散射皮肤、
有重量的布料、雨雾体积光），保留动画的表演幅度与构图夸张；**明确排除水墨/宣纸/
手绘线稿/2D 平涂**（用户 2026-09-19 指定：不要沿用上一版的手绘、水墨风）。

结构与 `shot_prompt_builder` 的 style_context 消费点对齐：
identity.mood / visual_language.aesthetic / asset_generation.* / quality_rules。
"""

PLAYBOOK: dict = {
    "id": "guochao_anime_real",
    "title": "中国式动漫真实风（国产三维动画电影质感）",
    "identity": {
        "name": "Guochao Anime Realism",
        "category": "cinematic",
        "mood": "清冷，克制，宿命感，雨夜静谧",
        "pace": "舒缓",
        "best_for": "国风仙侠叙事、聊斋题材、人鬼情缘、东方奇幻长片",
    },
    "visual_language": {
        "aesthetic": (
            "中国式三维动画电影质感（写实材质，非照片写实）：真实比例与解剖，"
            "皮肤有次表面散射 + 可见微纹理（不磨皮），布料有纤维与自然褶皱，"
            "木器/金属有真实反射与磨损；雨雾与体积光按物理光照落影；"
            "青灰底色为主，蓝白冷光与暖金烛光对照"
        ),
        "composition": "以三分法与对角线建立空间层次，前景遮挡物营造纵深；人物不居中留白",
        "color_palette": ["#2B3A42", "#5C7A8A", "#A8C6D8", "#E8DCC8", "#C08A3E"],
        "texture": (
            "电影级细节：皮肤可见毛孔/细纹与汗湿高光，布料纤维与缝线可见，"
            "木材纹理与铜器氧化痕迹可辨；雨滴挂面与地面真实反射；"
            "光晕柔和但边缘干净，无颗粒噪点"
        ),
    },
    "motion": {
        "transitions": ["cut", "dissolve", "fade"],
        "animation_style": "动画表演幅度自然，动作有预备与收势；运镜平稳，慢推与横移为主",
        "pacing_rules": {
            "min_scene_hold_seconds": 3.0,
            "max_scene_hold_seconds": 12,
            "transition_duration_seconds": 0.6,
        },
    },
    "audio": {
        "voice_style": "温润克制，中低音，语速偏慢；对白留白多",
        "music_mood": "古琴、箫、琵琶与低频氛围，克制不煽情",
        "music_volume": 0.18,
        "skip_bgm": True,
        "sfx_style": "雨声、屋檐滴水、琴弦余韵、衣料摩擦、脚步踩湿石",
    },
    "asset_generation": {
        # 2026-09-19 用户要求"质感更真实"：不再按"风格化"处理，否则英文画质层
        # 会把"真实质感/人体结构准确"这类写实护栏删掉（见 _is_stylized_playbook）。
        "stylized_image": False,
        "character_appearance_default": (
            "中国古风人物，动画电影式造型：五官立体但不夸张，皮肤有次表面散射质感，"
            "发丝成束可见，服装有布料厚度与自然褶皱"
        ),
        "image_negative_prompt": (
            "水墨, 宣纸, 手绘线稿, 2D平涂, 赛璐璐, 漫画网点, "
            "磨皮, 塑料皮肤, 蜡像感, 手办感, 玩具质感, 平涂色块, 低多边形, 低细节, "
            "霓虹, 现代服饰, 3D粗糙渲染, 低画质, 畸形手, "
            # 用户实测反馈：画面里生成匾额/对联书法文字、古琴被画成琵琶/月琴
            "画面文字, 匾额, 对联, 书法, 招牌, 印章, 水印, "
            "琵琶, 月琴, 古筝, 四视图拼版当人物参考"
        ),
        "consistency_anchors": [
            "青灰底 + 蓝白冷光 + 暖金烛光的固定色板",
            "写实材质的三维动画电影质感：皮肤不磨皮、布料有纤维、器物有磨损，禁止水墨/手绘平涂",
            "同一角色的发型、服装厚度与配色在全部镜头保持一致",
        ],
    },
    "quality_rules": [
        "严禁水墨/宣纸/手绘线稿/2D 平涂等上一版风格特征",
        "材质要「写实」：皮肤保留微纹理与自然高光，布料纤维/褶皱可见，器物有真实反光与使用痕迹；严禁磨皮、塑料感、蜡像感、手办感",
        "夜景以蓝白冷光为主光源，烛火仅作暖色点缀",
        "建景镜头保持 ≥3.0s，转场间隔 ≥2.0s",
        "人物默认外观使用 character_appearance_default",
        "每镜必须在场清单齐备：场景方位 + 人物 + 道具归属（B2.5）",
    ],
    "script_style": {
        "template_id": "scripts/elements-cinematic",
        "dialogue_wps": 4.5,
        "require_characters": True,
        "require_structure": True,
        "require_environment": True,
        "require_speakers": True,
    },
}
