"""shot_runner 模块级常量。

从 shot_runner.py 抽出，供 _shot_route / _shot_refs / shot_runner 三方共享，
避免拆模块后出现双向 import 环。
"""

from __future__ import annotations

MAX_ATTEMPTS = 3
_DEFAULT_IMAGE_TOOL = "jimeng_image"
_DEFAULT_VIDEO_TOOL = "jimeng_video"
_MAX_PROPS = 3
# 每角色形态上限：超出 validation critical（防止误填导致生图/成本无界膨胀）。
_MAX_FORMS = 4
# cast 生成前按形态估算生图张数的告警阈值（不阻断，仅 finding + 卡片提示）。
_CAST_EST_IMAGE_WARN = 12
_AGNES_FLASH_MAX_IMAGES = 5
# 定妆照承载身份；reference_first 下首帧排最前（Picture 1 = 本镜首帧）。
# 四视图是网格拼板，参考生图会把网格布局一起抄，故降到最低，只在名额有剩时兜底。
_AGNES_FLASH_IMAGE_RANK = {
    "first_frame": -1,
    "portrait": 0,
    "scene_ref": 1,
    "prop": 2,
    "style_anchor": 3,
    "turnaround": 4,
}

_FAMILY_APIS: dict[str, tuple[str, ...]] = {
    "volcengine": ("jimeng_v30",),
    "ark": ("seedance_25", "seedance_20_pro"),
    "kling": (
        "kling_omni_30",
        "kling_t2v_30",
        "kling_i2v_30",
        "kling_motion_30",
        "kling_i2v_21_pro",
        "kling_v1",
    ),
    "agnes": ("agnes_v25",),
}
_SEEDANCE_APIS = frozenset({"seedance_25", "seedance_20_pro"})
_EXPLICIT_FRAME_APIS = frozenset({
    "seedance_25", "seedance_20_pro",
    "kling_omni_30", "kling_i2v_30", "kling_i2v_21_pro",
})
_REF_ROLES = {
    "first_frame": "本镜首帧",
    "portrait": "角色外貌",
    "turnaround": "角色体态",
    "scene_ref": "场景",
    "prop": "道具",
    "style_anchor": "风格",
}
_REFINE_HINT = "延续上一镜运动、补时长"
