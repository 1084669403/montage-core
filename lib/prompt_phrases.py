"""镜头提示词常量与短语表（纯数据，无依赖）。

从 shot_prompt_builder.py 抽出，供核心构建器与英文画质层共享，
避免拆模块后出现双向 import 环。
"""

from __future__ import annotations

import re

# 把 shot_language 枚举映射为用于提示词的自然语言（2026-09 全中文政策：值一律中文）。
_SHOT_SIZE_PHRASES = {
    "extreme_wide": "大远景，展现广阔环境全貌",
    "wide": "全景，人物与环境同框",
    "medium_wide": "中全景，主体连同周围环境入画",
    "medium": "中景，取腰部以上半身",
    "medium_close": "中近景，取胸部以上",
    "close_up": "近景，聚焦面部或细节",
    "extreme_close_up": "特写，聚焦细微局部",
    "over_shoulder": "过肩视角",
    "insert": "插入镜头，交代具体细节",
    "establishing": "建立镜头，交代地点与环境",
    "close": "特写，聚焦局部细节",
}

_MOVEMENT_PHRASES = {
    "static": "固定机位，画面稳定",
    "pan_left": "向左平稳摇镜",
    "pan_right": "向右平稳摇镜",
    "tilt_up": "镜头上摇",
    "tilt_down": "镜头下摇",
    "dolly_in": "缓慢向主体推进",
    "dolly_out": "缓慢拉远离开主体",
    "tracking_left": "向左跟拍主体",
    "tracking_right": "向右跟拍主体",
    "crane_up": "镜头升起",
    "crane_down": "镜头降下",
    "handheld": "手持拍摄，带自然晃动",
    "steadicam": "稳定器跟随移动",
    "whip_pan": "快速甩摇",
    "orbital": "环绕主体运镜",
    "zoom_in": "缓慢推近",
    "zoom_out": "缓慢拉远",
    "rack_focus": "焦点在前景与后景之间切换",
}

_LIGHTING_PHRASES = {
    "high_key": "明亮高调布光，阴影很少",
    "low_key": "低调布光，阴影深重",
    "natural": "自然环境光",
    "golden_hour": "暖色黄昏光",
    "blue_hour": "冷色蓝调暮光",
    "tungsten_warm": "暖色钨丝灯室内光",
    "neon": "霓虹光，彩色光晕外溢",
    "silhouette": "逆光剪影",
    "rim_lit": "轮廓光勾边",
    "volumetric": "体积光，可见光束",
    "overcast_soft": "阴天柔和漫射光",
}

_DOF_PHRASES = {
    "shallow": "浅景深，背景虚化",
    "medium": "中等景深",
    "deep": "大景深，前后景均清晰",
}

_COLOR_TEMP_PHRASES = {
    "cool": "冷色调，偏蓝",
    "neutral": "中性色调，色彩均衡",
    "warm": "暖色调，偏琥珀色",
    "mixed": "冷暖色调混用，形成对比",
}

_SHOT_SIZE_ZH = {
    "extreme_wide": "大远景",
    "wide": "全景",
    "medium_wide": "中全景",
    "medium": "中景",
    "medium_close": "中近景",
    "close_up": "近景",
    "extreme_close_up": "特写",
    "over_shoulder": "过肩",
    "insert": "插入特写",
    "establishing": "建立镜头",
    "close": "特写",
}

_MOVEMENT_ZH = {
    "static": "固定机位",
    "pan_left": "向左摇",
    "pan_right": "向右摇",
    "tilt_up": "上摇",
    "tilt_down": "下摇",
    "dolly_in": "缓慢向主体推进",
    "dolly_out": "缓慢拉远",
    "tracking_left": "向左跟拍",
    "tracking_right": "向右跟拍",
    "crane_up": "升起",
    "crane_down": "降下",
    "handheld": "手持微晃",
    "steadicam": "稳定器跟随",
    "whip_pan": "快速甩摇",
    "orbital": "环绕主体",
    "zoom_in": "缓慢推近",
    "zoom_out": "缓慢拉远",
    "rack_focus": "焦点在前景与后景间切换",
}

_BLOCKING_X_ZH = {
    "left": "左侧", "center": "中间", "right": "右侧",
    "左": "左侧", "中": "中间", "右": "右侧",
}
_BLOCKING_Z_ZH = {
    "near": "近处", "mid": "中景", "far": "远处", "farther": "更远处",
    "近": "近处", "中": "中景", "远": "远处", "更远": "更远处",
}
_BLOCKING_Y_ZH = {
    "high": "上方", "mid": "中段", "low": "下方",
    "上": "上方", "中": "中段", "下": "下方",
}
_CLOSE_SIZES = frozenset({
    "close", "close_up", "extreme_close_up", "medium_close", "insert",
})
_WIDE_SIZES = frozenset({
    "extreme_wide", "wide", "establishing", "medium_wide",
})



_DEFAULT_EAST_ASIAN_APPEARANCE = "东亚人面孔，自然的亚洲面部特征"

# 固定的中文画质基线（builder 常量 —— 唯一数据源，不是 prompt_library 条目）。
# 2026-09 起全中文政策：原英文基线已中文化，提示词全链路禁止英文。
# 在压缩之后追加，因此若会超过 provider_max_chars 可整体丢弃；它绝不侵占
# 已压缩的 台词/角色/环境 核心。仅在 english_visual=True 时生效。
_ENGLISH_VISUAL_BASELINE = (
    "【画质基底】超清晰、细节丰富、焦点锐利、真实质感、人体结构准确、"
    "动作自然稳定、电影级调色、专业布光、构图干净"
)

# 仅图片的中文画质基线（图片 A/B 定版后新增）—— 与视频基线同源，
# 但不含「动作自然稳定」（这是视频专用词：静态首帧图绝不能暗示
# 运动/伪影/视频感）。当 english_visual=True 时追加到
# first_frame_prompt（视频镜头和图片镜头都会）。
_IMAGE_ENGLISH_VISUAL_BASELINE = (
    "【画质基底】超清晰、细节丰富、焦点锐利、真实质感、人体结构准确、"
    "电影级调色、专业布光、构图干净"
)

# 与画质基线合并到同一句【画质基底】的风格中性静态图护栏。
# 不要加「无多余肢体」—— 那会与非人类 / 多肢体主体冲突。
_IMAGE_ENGLISH_GUARD = "无运动模糊，无文字，无水印"

# 仅图片的负面提示词（非 Agnes 生成器）。不含语音护栏，也不含多肢体 /
# 畸形手之类的措辞（对生物类主体安全）。作为 image_negative_prompt 返回。
_IMAGE_ENGLISH_NEGATIVE = (
    "低画质，模糊，水印，文字，匾额，对联，书法，招牌，印章，标志，运动模糊，"
    "琵琶，月琴，古筝"
)

# 会把运动时长泄漏进静态姿势的方式片段（保留 急促/前倾）。
_MANNER_TIME_RE = re.compile(
    r"(?:每步)?约?\d+(?:\.\d+)?\s*秒|\d+(?:\.\d+)?\s*s\b",
    re.IGNORECASE,
)

# 中文音频锁定句 —— Agnes 会根据提示词文本生成语音，所以语言必须锁定为中文。
# 在 Agnes 闭环（agnes_audio=True）上无条件追加，与【台词】段无关
# （无对白的镜头没有【台词】段）。它从不参与段落压缩；
# 它是一段压缩后追加的短句，自带长度护栏。
_AUDIO_LOCK_WITH_DIALOGUE = "【音频锁定】全程标准普通话中文发音，禁止英文及任何外语发音"
_AUDIO_LOCK_NO_DIALOGUE = "【音频锁定】全程环境音，无对白，禁止任何语言朗读或旁白"

# 追加到视频动态提示词的中文帧一致性句，让图生视频模型
# 稳住参考帧（身份/连贯性）。2026-09 全中文政策：原英文句已中文化。
_FRAME_CONSISTENCY_EN = (
    "【衔接】参考帧全程保持一致，动作平滑连贯，人物形象与光线稳定不漂移"
)

# 中文负面提示词：画质护栏 + 语音护栏。适配 Agnes 的
# negative_prompt 字段（最大 500 字符）。一旦【音频锁定】句无法出现在负面
# 字段里，语音护栏就是无对白镜头唯一的防线 —— 该字段是第二道独立的语音锁定防线。
# 2026-09 全中文政策：原英文负面词逐项中文化（语义一一对应），变量名保留兼容。
_ENGLISH_NEGATIVE_PROMPT = (
    "低画质，模糊，脱焦，肢体畸形，手指畸形，面部扭曲，画面闪烁，"
    "镜头抖动不稳，过度磨皮的AI感，塑料皮肤，英语语音，英语旁白，"
    "英文字幕，画面文字，匾额，对联，书法，外语发音，人物朗读对白，水印，标志，"
    "琵琶，月琴，古筝"
)

# 有序的提示词段落标签（组装 / 压缩 / 审核用固定顺序）。
_SECTION_ORDER = [
    ("构图", "framing"),
    ("角色与外貌", "role_appearance"),
    ("在场清单", "presence"),
    ("画外", "off_frame"),
    ("动作", "action"),
    ("特效", "vfx"),
    ("物体与道具", "objects"),
    ("环境", "environment"),
    ("光线", "lighting"),
    ("台词", "dialogue"),
    ("背景音乐", "bgm"),
    ("声音", "audio"),
    ("镜头", "camera"),
    ("衔接", "continuity"),
]

# 不携带任何像素/运动/声音信息的抽象套话词。skill 层禁止 LLM 写出这些词；
# builder 在 `dense` 下把它们当作第二道防线剔除（Agnes 与 Jimeng 精简高密度共用）。
# 以独立 token（而非子串）方式保留，这样能删掉"电影感"，
# 又不会误伤特定名词复合里的"电影感镜头"。
_ABSTRACT_WORD_RE = re.compile(
    r"(?:优雅地|很自然地|自然地|静静地|缓缓地|温柔地|电影感|高质量|高清晰|"
    r"极致|绝佳|杰作|大师级|史诗级|氛围感|艺术感|高级感|唯美|梦幻|惊艳|震撼|完美|"
    r"\b8k\b|\b4k\b|\bHD\b|ultra[ -]?quality|high[ -]?quality|masterpiece|"
    r"\b8K\b|\b4K\b|\bHDR\b)",
    re.IGNORECASE,
)
