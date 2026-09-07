"""shot_runner 模块级常量。

从 shot_runner.py 抽出，供 _shot_route / _shot_refs / shot_runner 三方共享，
避免拆模块后出现双向 import 环。
"""

from __future__ import annotations

MAX_ATTEMPTS = 3
_DEFAULT_IMAGE_TOOL = "jimeng_image"
_DEFAULT_VIDEO_TOOL = "jimeng_video"
_MAX_PROPS = 3
_AGNES_FLASH_MAX_IMAGES = 5
_AGNES_FLASH_IMAGE_RANK = {
    "turnaround": 0,
    "portrait": 1,
    "scene_ref": 2,
    "prop": 3,
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
    "portrait": "角色外貌",
    "turnaround": "角色体态",
    "scene_ref": "场景",
    "prop": "道具",
    "style_anchor": "风格",
}
_REFINE_HINT = "延续上一镜运动、补时长"
