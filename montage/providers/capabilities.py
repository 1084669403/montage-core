"""供应商能力表 — 谁支持参考图 / 首帧 / 尾帧 / 多镜 / 原生音频。

编排器按表填字段、按表降级：无参考则纯文生，无尾帧则只传首帧。
不要在 shot_runner 里按供应商名猜参数。本模块是数据 + 填字段助手。

VIDEO_BY_TOOL 是当前接线（jimeng=v30、kling=3.0 Omni、agnes=2.5 Flash）。
VIDEO_SURFACES 按 api_id 建档（含尚未接线的 2.5 / Omni）；doctor 读后者。
"""

from __future__ import annotations

import math
import os
from typing import Any

# image_reference: 生图时能否喂定妆照/参考图
# first_frame / last_frame: 视频能否图生（首帧）以及首尾帧约束
# 下列布尔/空缺省必须 False/0/""：叠到旧工具表时不得改变现网 first/last/duration_policy
_NO_IMAGE: dict[str, Any] = {
    "image_reference": False,
    "ref_url_fields": (),
    "ref_path_fields": (),
    "reference_operation": "",
    "turnaround": False,
}
_NO_VIDEO: dict[str, Any] = {
    "first_frame": False,
    "last_frame": False,
    "first_url_fields": (),
    "first_path_fields": (),
    "last_url_fields": (),
    "last_path_fields": (),
    "mode": "",
    "multi_shot": False,
    "multi_shot_max": 0,
    "native_audio": False,
    "lipsync": False,
    "camera_control": False,
    "max_duration": 0,
    "duration_policy": {"kind": "none"},
    "negative_prompt": False,
    "edit_clip": False,
    "extend_clip": False,
    "citation_syntax": "",
    "turnaround": False,
    "requires_first_frame": False,
    "last_frame_requires_first": False,
    "ratio_adaptive_when_frames_locked": False,
    "watermark_default": False,
    "api_id": "",
    "passthrough": False,
    "continuity_mode": "",
    "max_ref_images": 0,
    "video_ref": False,
}

# 按工具名（选型器真正执行的 name）
IMAGE_BY_TOOL: dict[str, dict[str, Any]] = {
    "jimeng_image": {
        "image_reference": True,
        "ref_url_fields": ("image_urls",),
        "ref_path_fields": ("image_paths",),
        "reference_operation": "",
        "turnaround": True,
    },
    "agnes_image": {
        "image_reference": True,
        "ref_url_fields": ("reference_urls",),
        "ref_path_fields": (),
        "reference_operation": "image_reference",
        "max_ref_images": 6,  # 来源待证（官方 Flash 文档只写明视频 images ≤ 5）
        "turnaround": True,
    },
    "dashscope_image": dict(_NO_IMAGE),
    "kling_image": {
        "image_reference": True,
        "ref_url_fields": ("image_list",),
        "ref_path_fields": (),
        "reference_operation": "",
        "turnaround": True,
    },
    "seedream_image": {
        "image_reference": True,
        "ref_url_fields": ("image",),
        "ref_path_fields": (),
        "reference_operation": "",
        "turnaround": True,
    },
}

VIDEO_BY_TOOL: dict[str, dict[str, Any]] = {
    "jimeng_video": {
        "first_frame": True,
        "last_frame": True,
        "first_url_fields": ("image_url", "first_frame_url"),
        "first_path_fields": ("image_path", "first_frame_path"),
        "last_url_fields": ("last_frame_url",),
        "last_path_fields": ("last_frame_path",),
        "mode": "i2v_first_tail",
        "duration_policy": {"kind": "enum", "values": [5, 10]},
        "camera_control": True,
        "max_duration": 10,
        "negative_prompt": True,
        "api_id": "jimeng_v30",
    },
    "seedance_video": {
        "first_frame": True,
        "last_frame": True,
        "first_url_fields": (),
        "first_path_fields": (),
        "last_url_fields": (),
        "last_path_fields": (),
        "mode": "seedance_content",
        "duration_policy": {"kind": "range", "min": 4, "max": 30, "step": 1},
        "multi_shot": True,
        "native_audio": True,
        "max_duration": 30,
        "negative_prompt": True,
        "edit_clip": True,
        "extend_clip": True,
        "citation_syntax": "@图片N",
        "ratio_adaptive_when_frames_locked": True,
        "watermark_default": False,
        "api_id": "seedance_25",
    },
    "agnes_video": {
        # 2.5 Flash 三模式全开（text / keyframe / reference）：keyframe 至少一帧由
        # 适配器 _payload_v25 运行时判定；requires_first_frame 仍 False（纯文生合法）。
        "first_frame": True,
        "last_frame": True,
        "first_url_fields": ("first_frame",),
        "first_path_fields": (),
        "last_url_fields": ("last_frame",),
        "last_path_fields": (),
        "requires_first_frame": False,
        # mode 逐请求决定（text/keyframe/reference），表上不锁死。
        "mode": "",
        "continuity_mode": "image_ref",
        "max_ref_images": 5,  # 官方 Flash 文档：images ≤ 5
        "max_ref_audios": 3,  # 官方 Flash 文档：audios ≤ 3（agnes._payload_v25 实际消费）
        "video_ref": False,
        "keyframe_chain": False,
        "keyframe_field": "first_frame",
        "modes": ("text", "keyframe", "reference"),
        "duration_policy": {"kind": "range", "min": 4, "max": 12, "step": 1},
        "native_audio": True,
        "max_duration": 12,
        "negative_prompt": False,
        "api_id": "agnes_v25",
        "passthrough": False,
    },
    "wan_video": {
        **_NO_VIDEO,
        "duration_policy": {"kind": "none"},
    },
    "cogvideo_video": {
        "first_frame": True,
        "last_frame": False,
        "first_url_fields": ("image_url",),
        "first_path_fields": (),
        "last_url_fields": (),
        "last_path_fields": (),
        "mode": "",
        "duration_policy": {"kind": "none"},
    },
    "kling_video": {
        "first_frame": True,
        "last_frame": True,
        "first_url_fields": ("image_url",),
        "first_path_fields": (),
        "last_url_fields": ("last_frame_url", "tail_image_url"),
        "last_path_fields": (),
        "mode": "",
        "duration_policy": {"kind": "range", "min": 3, "max": 15, "step": 1},
        "max_duration": 15,
        "api_id": "kling_omni_30",
        "native_audio": True,
        "multi_shot": True,
    },
    "hunyuan_video": {
        "first_frame": True,
        "last_frame": False,
        "first_url_fields": ("image_url",),
        "first_path_fields": (),
        "last_url_fields": (),
        "last_path_fields": (),
        "mode": "",
        "duration_policy": {"kind": "none"},
    },
}

# 选型器只暴露 provider；按闭环锁定查表
IMAGE_BY_PROVIDER: dict[str, dict[str, Any]] = {
    "volcengine": IMAGE_BY_TOOL["jimeng_image"],
    "agnes": IMAGE_BY_TOOL["agnes_image"],
    "dashscope": IMAGE_BY_TOOL["dashscope_image"],
    "kling": IMAGE_BY_TOOL["kling_image"],
    "ark": IMAGE_BY_TOOL["seedream_image"],
}

VIDEO_BY_PROVIDER: dict[str, dict[str, Any]] = {
    "volcengine": VIDEO_BY_TOOL["jimeng_video"],
    "agnes": VIDEO_BY_TOOL["agnes_video"],
    "dashscope": VIDEO_BY_TOOL["wan_video"],
    "kling": VIDEO_BY_TOOL["kling_video"],
    "zhipu": VIDEO_BY_TOOL["cogvideo_video"],
    "tencent": VIDEO_BY_TOOL["hunyuan_video"],
    "ark": VIDEO_BY_TOOL["seedance_video"],
}

# 按 API 面建档（不是按公司名）。P2 起 produce 可经 video_loop=ark / kling 面选中
# 2.5 / Omni / 2.1；默认 volcengine 仍是 jimeng_v30。禁止把 2.5 网格写进 jimeng_video。
VIDEO_SURFACES: dict[str, dict[str, Any]] = {
    "seedance_25": {
        "api_id": "seedance_25",
        "label": "Seedance 2.5",
        "provider": "ark",
        "tool": "seedance_video",
        "wired": True,
        "fallback_api_id": "jimeng_v30",
        "first_frame": True,
        "last_frame": True,
        "multi_shot": True,
        "multi_shot_max": 0,
        "native_audio": True,
        "lipsync": False,
        "camera_control": False,
        "max_duration": 30,
        "duration_policy": {"kind": "range", "min": 4, "max": 30, "step": 1},
        "negative_prompt": True,
        "edit_clip": True,
        "extend_clip": True,
        "citation_syntax": "@图片N",
        "requires_first_frame": False,
        "last_frame_requires_first": False,
        "ratio_adaptive_when_frames_locked": True,
        "watermark_default": False,
        "passthrough": False,
        "prompt_profile": "seedance_25",
        "models": [],
        "env_keys_hint": ("ARK_API_KEY",),
    },
    "seedance_20_pro": {
        "api_id": "seedance_20_pro",
        "label": "Seedance 2.0 Pro",
        "provider": "ark",
        "tool": "seedance_video",
        "wired": True,
        "fallback_api_id": "jimeng_v30",
        "first_frame": True,
        "last_frame": True,
        "multi_shot": True,
        "multi_shot_max": 0,
        "native_audio": True,
        "lipsync": False,
        "camera_control": False,
        "max_duration": 15,
        "duration_policy": {"kind": "range", "min": 4, "max": 15, "step": 1},
        "negative_prompt": True,
        "edit_clip": False,
        "extend_clip": False,
        "citation_syntax": "@图片N",
        "requires_first_frame": False,
        "last_frame_requires_first": False,
        "ratio_adaptive_when_frames_locked": True,
        "watermark_default": False,
        "passthrough": False,
        "prompt_profile": "seedance_20_pro",
        "models": [],
        "env_keys_hint": ("ARK_API_KEY",),
    },
    "jimeng_v30": {
        "api_id": "jimeng_v30",
        "label": "即梦视觉智能 v30",
        "provider": "volcengine",
        "tool": "jimeng_video",
        "wired": True,
        "fallback_api_id": "",
        "first_frame": True,
        "last_frame": True,
        "multi_shot": False,
        "multi_shot_max": 0,
        "native_audio": False,
        "lipsync": False,
        "camera_control": True,
        "max_duration": 10,
        "duration_policy": {"kind": "enum", "values": [5, 10]},
        "negative_prompt": True,
        "edit_clip": False,
        "extend_clip": False,
        "citation_syntax": "",
        "requires_first_frame": False,
        "last_frame_requires_first": False,
        "ratio_adaptive_when_frames_locked": False,
        "watermark_default": False,
        "passthrough": False,
        "prompt_profile": "jimeng_v30",
        "models": [],
        "env_keys_hint": ("VOLC_ACCESSKEY", "VOLC_SECRETKEY"),
    },
    "kling_omni_30": {
        "api_id": "kling_omni_30",
        "label": "Kling 3.0 Omni",
        "provider": "kling",
        "tool": "kling_video",
        "wired": True,
        "fallback_api_id": "kling_v1",
        "first_frame": True,
        "last_frame": True,
        "multi_shot": True,
        "multi_shot_max": 6,
        "native_audio": True,
        "lipsync": False,
        "camera_control": False,
        "max_duration": 15,
        "duration_policy": {"kind": "range", "min": 3, "max": 15, "step": 1},
        "negative_prompt": False,
        "edit_clip": True,
        "extend_clip": False,
        "citation_syntax": "@image_N",
        "requires_first_frame": False,
        "last_frame_requires_first": True,
        "ratio_adaptive_when_frames_locked": False,
        "watermark_default": False,
        "passthrough": False,
        "prompt_profile": "kling_omni_30",
        "models": [],
        "env_keys_hint": ("KLING_API_KEY", "KLING_API_SECRET"),
    },
    "kling_t2v_30": {
        "api_id": "kling_t2v_30",
        "label": "Kling 3.0 文生",
        "provider": "kling",
        "tool": "kling_video",
        "wired": True,
        "fallback_api_id": "kling_omni_30",
        "first_frame": False,
        "last_frame": False,
        "multi_shot": False,
        "multi_shot_max": 0,
        "native_audio": False,
        "lipsync": False,
        "camera_control": False,
        "max_duration": 15,
        "duration_policy": {"kind": "range", "min": 3, "max": 15, "step": 1},
        "negative_prompt": False,
        "edit_clip": False,
        "extend_clip": False,
        "citation_syntax": "",
        "requires_first_frame": False,
        "last_frame_requires_first": False,
        "ratio_adaptive_when_frames_locked": False,
        "watermark_default": False,
        "passthrough": False,
        "prompt_profile": "kling_t2v_30",
        "models": [],
        "env_keys_hint": ("KLING_API_KEY", "KLING_API_SECRET"),
    },
    "kling_i2v_30": {
        "api_id": "kling_i2v_30",
        "label": "Kling 3.0 图生",
        "provider": "kling",
        "tool": "kling_video",
        "wired": True,
        "fallback_api_id": "kling_omni_30",
        "first_frame": True,
        "last_frame": True,
        "first_url_fields": ("image_url",),
        "last_url_fields": ("last_frame_url",),
        "multi_shot": False,
        "multi_shot_max": 0,
        "native_audio": False,
        "lipsync": False,
        "camera_control": False,
        "max_duration": 15,
        "duration_policy": {"kind": "range", "min": 3, "max": 15, "step": 1},
        "negative_prompt": False,
        "edit_clip": False,
        "extend_clip": False,
        "citation_syntax": "",
        "requires_first_frame": True,
        "last_frame_requires_first": True,
        "ratio_adaptive_when_frames_locked": False,
        "watermark_default": False,
        "passthrough": False,
        "prompt_profile": "kling_i2v_30",
        "models": [],
        "env_keys_hint": ("KLING_API_KEY", "KLING_API_SECRET"),
    },
    "kling_motion_30": {
        "api_id": "kling_motion_30",
        "label": "Kling 3.0 动作控制",
        "provider": "kling",
        "tool": "kling_video",
        "wired": True,
        "fallback_api_id": "kling_omni_30",
        "first_frame": True,
        "last_frame": False,
        "first_url_fields": ("image_url",),
        "last_url_fields": (),
        "multi_shot": False,
        "multi_shot_max": 0,
        "native_audio": False,
        "lipsync": False,
        "camera_control": False,
        "max_duration": 10,
        "duration_policy": {"kind": "range", "min": 3, "max": 10, "step": 1},
        "negative_prompt": False,
        "edit_clip": False,
        "extend_clip": False,
        "citation_syntax": "",
        "requires_first_frame": True,
        "last_frame_requires_first": False,
        "ratio_adaptive_when_frames_locked": False,
        "watermark_default": False,
        "passthrough": False,
        "prompt_profile": "kling_motion_30",
        "models": [],
        "env_keys_hint": ("KLING_API_KEY", "KLING_API_SECRET"),
    },
    "kling_i2v_21_pro": {
        "api_id": "kling_i2v_21_pro",
        "label": "Kling 2.1 Pro",
        "provider": "kling",
        "tool": "kling_video",
        "wired": True,
        "fallback_api_id": "kling_v1",
        "first_frame": True,
        "last_frame": True,
        "multi_shot": False,
        "multi_shot_max": 0,
        "native_audio": False,
        "lipsync": False,
        "camera_control": False,
        "max_duration": 10,
        "duration_policy": {"kind": "enum", "values": [5, 10]},
        "negative_prompt": True,
        "edit_clip": False,
        "extend_clip": False,
        "citation_syntax": "",
        "requires_first_frame": True,
        "last_frame_requires_first": False,
        "ratio_adaptive_when_frames_locked": False,
        "watermark_default": False,
        "passthrough": False,
        "prompt_profile": "kling_i2v_21_pro",
        "models": [],
        "env_keys_hint": ("KLING_API_KEY", "KLING_API_SECRET"),
    },
    "kling_v1": {
        "api_id": "kling_v1",
        "label": "Kling v1（兜底）",
        "provider": "kling",
        "tool": "kling_video",
        "wired": True,
        "fallback_api_id": "",
        "first_frame": True,
        "last_frame": False,
        "multi_shot": False,
        "multi_shot_max": 0,
        "native_audio": False,
        "lipsync": False,
        "camera_control": False,
        "max_duration": 10,
        "duration_policy": {"kind": "enum", "values": [5, 10]},
        "negative_prompt": False,
        "edit_clip": False,
        "extend_clip": False,
        "citation_syntax": "",
        "requires_first_frame": False,
        "last_frame_requires_first": False,
        "ratio_adaptive_when_frames_locked": False,
        "watermark_default": False,
        "passthrough": False,
        "prompt_profile": "kling_v1",
        "models": [],
        "env_keys_hint": ("KLING_API_KEY",),
    },
    "agnes_v20": {
        "api_id": "agnes_v20",
        "label": "Agnes 2.0（退役，环境变量回滚）",
        "provider": "agnes",
        "tool": "agnes_video",
        "wired": False,
        "fallback_api_id": "",
        "first_frame": True,
        "last_frame": True,
        "first_url_fields": ("image_url", "image_urls"),
        "last_url_fields": ("last_frame_url",),
        "multi_shot": False,
        "multi_shot_max": 0,
        "native_audio": True,
        "lipsync": False,
        "camera_control": False,
        "max_duration": 18,
        "duration_policy": {"kind": "enum", "values": [5, 10, 18]},
        "negative_prompt": True,
        "edit_clip": False,
        "extend_clip": False,
        "citation_syntax": "",
        "requires_first_frame": False,
        "last_frame_requires_first": False,
        "ratio_adaptive_when_frames_locked": False,
        "watermark_default": False,
        "passthrough": True,
        "prompt_profile": "agnes_v20",
        "models": ["agnes-video-v2.0"],
        "env_keys_hint": ("AGNES_CN_API_KEY", "AGNES_API_KEY"),
    },
    "agnes_v25": {
        "api_id": "agnes_v25",
        "label": "Agnes 2.5 Flash（中国站默认）",
        "provider": "agnes",
        "tool": "agnes_video",
        "wired": True,
        "fallback_api_id": "",
        # 2.5 Flash 支持 keyframe 模式（first/last 至少一帧）；requires_first_frame
        # 仍 False：text / reference 不需要帧。适配器 _payload_v25 按请求实际改 mode。
        "first_frame": True,
        "last_frame": True,
        "first_url_fields": ("first_frame",),
        "first_path_fields": (),
        "last_url_fields": ("last_frame",),
        "last_path_fields": (),
        "multi_shot": False,
        "multi_shot_max": 0,
        "native_audio": True,
        "lipsync": False,
        "camera_control": False,
        "max_duration": 12,
        "duration_policy": {"kind": "range", "min": 4, "max": 12, "step": 1},
        "negative_prompt": False,
        "edit_clip": False,
        "extend_clip": False,
        "citation_syntax": "<Picture N>",
        "requires_first_frame": False,
        "last_frame_requires_first": False,
        "ratio_adaptive_when_frames_locked": False,
        "watermark_default": False,
        "passthrough": False,
        "prompt_profile": "agnes_v25",
        "models": ["agnes-video-2.5-flash"],
        "continuity_mode": "image_ref",
        "max_ref_images": 5,  # 官方 Flash 文档：images ≤ 5
        "max_ref_audios": 3,  # 官方 Flash 文档：audios ≤ 3
        "video_ref": False,
        "modes": ("text", "keyframe", "reference"),
        "env_keys_hint": ("AGNES_CN_API_KEY", "AGNES_API_KEY"),
    },
}

_DOCTOR_SURFACE_KEYS = (
    "api_id",
    "label",
    "provider",
    "tool",
    "wired",
    "fallback_api_id",
    "native_audio",
    "multi_shot",
    "multi_shot_max",
    "max_duration",
    "duration_policy",
    "citation_syntax",
    "negative_prompt",
    "edit_clip",
    "extend_clip",
    "passthrough",
    "requires_first_frame",
    "camera_control",
    "models",
    "continuity_mode",
    "max_ref_images",
    "video_ref",
    "modes",
)


def _merge_caps(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    out.update(overlay)
    policy = out.get("duration_policy")
    if isinstance(policy, dict):
        out["duration_policy"] = dict(policy)
    return out


def image_caps(*, tool: str = "", provider: str = "") -> dict[str, Any]:
    if tool and tool in IMAGE_BY_TOOL:
        return _merge_caps(_NO_IMAGE, IMAGE_BY_TOOL[tool])
    if provider and provider in IMAGE_BY_PROVIDER:
        return _merge_caps(_NO_IMAGE, IMAGE_BY_PROVIDER[provider])
    return dict(_NO_IMAGE)


def video_caps(*, tool: str = "", provider: str = "") -> dict[str, Any]:
    if tool and tool in VIDEO_BY_TOOL:
        return _merge_caps(_NO_VIDEO, VIDEO_BY_TOOL[tool])
    if provider and provider in VIDEO_BY_PROVIDER:
        return _merge_caps(_NO_VIDEO, VIDEO_BY_PROVIDER[provider])
    return dict(_NO_VIDEO)


def video_surface(api_id: str) -> dict[str, Any]:
    """按 API 面取能力。未知 id 返回缺省（全部 False/空）。"""
    raw = VIDEO_SURFACES.get(str(api_id or "").strip())
    if not raw:
        out = dict(_NO_VIDEO)
        out["api_id"] = str(api_id or "")
        out["wired"] = False
        out["label"] = ""
        out["provider"] = ""
        out["tool"] = ""
        out["fallback_api_id"] = ""
        out["prompt_profile"] = ""
        out["models"] = []
        out["env_keys_hint"] = ()
        return out
    return _merge_caps(_NO_VIDEO, raw)


def list_video_surfaces() -> list[dict[str, Any]]:
    return [video_surface(api_id) for api_id in VIDEO_SURFACES]


def doctor_video_surfaces() -> list[dict[str, Any]]:
    """doctor --json 用的缩略面（不含密钥值）。"""
    rows: list[dict[str, Any]] = []
    for surface in list_video_surfaces():
        row = {key: surface.get(key) for key in _DOCTOR_SURFACE_KEYS}
        policy = row.get("duration_policy")
        if isinstance(policy, dict):
            row["duration_policy"] = dict(policy)
        rows.append(row)
    return rows


def _first_filled(items: list[str]) -> list[str]:
    return [str(x) for x in items if x]


def _kling_image_omni_from_refs(
    refs: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[str]]:
    """工牌进 element_list；无工牌的场景/参考图进 image_list。合计 ≤10。"""
    notes: list[str] = []
    elements: list[dict[str, str]] = []
    seen_el: set[str] = set()
    images: list[dict[str, str]] = []
    seen_img: set[str] = set()
    for item in refs or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or item.get("type") or "").strip().lower()
        eid = item.get("element_id")
        if eid is not None and str(eid).strip() != "":
            key = str(eid).strip()
            if key not in seen_el:
                seen_el.add(key)
                elements.append({"element_id": key})
            if kind in ("element", "portrait", "turnaround", "prop", ""):
                continue
        url = str(item.get("url") or item.get("image") or "").strip()
        if url and url not in seen_img:
            seen_img.add(url)
            images.append({"image": url})
    while len(elements) + len(images) > 10:
        if images:
            images.pop()
        elif elements:
            elements.pop()
        else:
            break
        if "Image Omni 参考+主体超过 10，已截断" not in notes:
            notes.append("Image Omni 参考+主体超过 10，已截断")
    return elements, images, notes


def agnes_image_ref_entries(
    refs: list[dict[str, Any]],
    caps: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Agnes 图片多图合成的实发有序表（URL 优先、本地路径兜底、去重保序、按上限截断）。

    与 ``apply_image_refs`` 的 agnes 分支共用，保证提示词里的角色图例与
    实际塞进 ``extra_body.image`` 的顺序逐条第对齐。返回的每项都是原 ref
    dict 并附 ``_sent``（真正送出的 url/path），供图例按同序说明角色。
    """
    notes: list[str] = []
    limit = int(caps.get("max_ref_images") or 0)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in refs:
        if not isinstance(item, dict):
            continue
        val = str(item.get("url") or "").strip()
        if val and val not in seen:
            seen.add(val)
            entries.append({**item, "_sent": val})
    for item in refs:
        if not isinstance(item, dict) or str(item.get("url") or "").strip():
            continue
        val = str(item.get("path") or "").strip()
        if val and val not in seen:
            seen.add(val)
            entries.append({**item, "_sent": val})
    if limit > 0 and len(entries) > limit:
        entries = entries[:limit]
        notes.append(f"参考图超过上限 {limit}，已截断保留前 {limit} 张")
    return entries, notes


def apply_image_refs(
    payload: dict[str, Any],
    refs: list[dict[str, Any]],
    caps: dict[str, Any],
) -> list[str]:
    """按能力表把定妆照/参考图填进生图 payload。返回降级说明（空=已填或无需填）。"""
    notes: list[str] = []
    if not caps.get("image_reference"):
        if refs:
            notes.append("当前图片供应商不支持参考图，降级纯文生")
        return notes
    url_fields = tuple(caps.get("ref_url_fields") or ())
    if url_fields and url_fields[0] == "image_list":
        elements, images, extra = _kling_image_omni_from_refs(refs)
        notes.extend(extra)
        if images:
            payload["image_list"] = images
        if elements:
            payload["elements"] = elements
        if images or elements:
            return notes
    if url_fields and url_fields[0] == "image":
        # seedream：合并 url 与本地 path（去重保序，URL 优先），cap 14；
        # 本地路径装箱由 seedream_image.execute 的 pack_seedream_image 完成。
        # 只填 URL 会让仅存本地路径的 refs 走「降级纯文生」——与本工具硬失败语义冲突。
        entries: list[str] = []
        seen: set[str] = set()
        for item in refs:
            if not isinstance(item, dict):
                continue
            for key in ("url", "path", "image"):
                val = str(item.get(key) or "").strip()
                if val and val not in seen:
                    seen.add(val)
                    entries.append(val)
        if entries:
            payload["image"] = entries[:14]
            return notes
    urls = _first_filled([str(r.get("url") or "") for r in refs])
    paths = _first_filled([str(r.get("path") or "") for r in refs])
    path_fields = tuple(caps.get("ref_path_fields") or ())
    op = str(caps.get("reference_operation") or "")
    if op:
        # agnes_image（image_reference）：实发有序表见 agnes_image_ref_entries，
        # 提示词侧的角色图例复用同一函数，杜绝编号与实发错位。
        entries_info, extra = agnes_image_ref_entries(refs, caps)
        notes.extend(extra)
        if not entries_info:
            notes.append(f"{op} 需要参考图 URL 或本地路径，两者皆无，降级纯文生")
            return notes
        payload["operation"] = op
        payload[url_fields[0] if url_fields else "reference_urls"] = [
            e["_sent"] for e in entries_info
        ]
        return notes
    if urls and url_fields:
        field = url_fields[0]
        if field == "image_list":
            payload[field] = [{"image": u} for u in urls[:10]]
        else:
            payload[field] = urls if field.endswith("s") else urls[0]
    elif paths and path_fields:
        field = path_fields[0]
        payload[field] = paths if field.endswith("s") else paths[0]
    elif refs:
        notes.append("参考图未填入（无匹配字段），降级纯文生")
    return notes


def apply_video_frames(
    payload: dict[str, Any],
    *,
    first_path: str = "",
    first_url: str = "",
    last_path: str = "",
    last_url: str = "",
    caps: dict[str, Any],
) -> list[str]:
    """按能力表填首帧/尾帧。无能力则不填（纯文生视频）。"""
    notes: list[str] = []
    if caps.get("first_frame"):
        path_fields = tuple(caps.get("first_path_fields") or ())
        url_fields = tuple(caps.get("first_url_fields") or ())
        attached = False
        if first_path and path_fields:
            payload[path_fields[0]] = first_path
            attached = True
        if first_url and url_fields:
            field = url_fields[0]
            payload[field] = [first_url] if field.endswith("s") else first_url
            attached = True
        if first_path and not first_url and url_fields and not path_fields:
            notes.append("视频供应商只要 URL 首帧，本地路径无法上传，降级文生视频")
        elif attached:
            payload.setdefault("operation", "image_to_video")
        elif first_path or first_url:
            notes.append("首帧未填入（无匹配字段），降级文生视频")
    elif first_path or first_url:
        notes.append("当前能力表未声明首帧，已忽略 first_frame（其余生成路径不变）")

    if last_path or last_url:
        if not caps.get("last_frame"):
            notes.append("当前视频供应商不支持尾帧，已忽略 last_frame")
        else:
            if last_path and caps.get("last_path_fields"):
                payload[list(caps["last_path_fields"])[0]] = last_path
            elif last_url and caps.get("last_url_fields"):
                payload[list(caps["last_url_fields"])[0]] = last_url
            else:
                notes.append("尾帧未填入（无匹配字段）")
    return notes


def apply_seedance_content(
    payload: dict[str, Any],
    *,
    text: str = "",
    first_url: str = "",
    last_url: str = "",
    refs: list[dict[str, Any]] | None = None,
    video_urls: list[str] | None = None,
    audio_urls: list[str] | None = None,
    caps: dict[str, Any] | None = None,
) -> list[str]:
    """填方舟 content[]。不要用 apply_video_frames 硬扩。生产默认无水印。"""
    notes: list[str] = []
    content: list[dict[str, Any]] = []
    body = str(text or "").strip()
    if body:
        content.append({"type": "text", "text": body})
    first = str(first_url or "").strip()
    last = str(last_url or "").strip()
    extra_refs = [r for r in (refs or []) if isinstance(r, dict) and str(r.get("url") or "").strip()]
    videos = [str(u).strip() for u in (video_urls or []) if str(u).strip()]
    audios = [str(u).strip() for u in (audio_urls or []) if str(u).strip()]
    if last and not first:
        notes.append("方舟首尾帧锁定需要同时有首帧，已忽略尾帧")
        last = ""
    frame_lock = bool(first or last)
    if frame_lock and (extra_refs or videos or audios):
        notes.append("方舟首尾帧与 reference_* 互斥，已忽略参考素材")
        extra_refs, videos, audios = [], [], []
    if first:
        content.append({
            "type": "image_url",
            "image_url": {"url": first},
            "role": "first_frame",
        })
    if last:
        content.append({
            "type": "image_url",
            "image_url": {"url": last},
            "role": "last_frame",
        })
    for item in extra_refs:
        content.append({
            "type": "image_url",
            "image_url": {"url": str(item["url"]).strip()},
            "role": "reference_image",
        })
    for url in videos:
        content.append({
            "type": "video_url",
            "video_url": {"url": url},
            "role": "reference_video",
        })
    for url in audios:
        content.append({
            "type": "audio_url",
            "audio_url": {"url": url},
            "role": "reference_audio",
        })
    payload["content"] = content
    payload["watermark"] = False
    flags = caps if isinstance(caps, dict) else {}
    if first and last and flags.get("ratio_adaptive_when_frames_locked", True):
        payload["ratio"] = "adaptive"
    return notes


_KLING_VIDEO_REF = ("feature_video", "base_video")
_KLING_FRAME_REF = ("first_frame", "last_frame")


def kling_omni_elements_cited(prompt: str, element_count: int) -> tuple[bool, list[str]]:
    """contents 有 element 时 prompt 必须出现对应 @element_N。"""
    notes: list[str] = []
    n = max(0, int(element_count or 0))
    if n <= 0:
        return True, notes
    text = str(prompt or "")
    missing = [f"@element_{i}" for i in range(1, n + 1) if f"@element_{i}" not in text]
    if missing:
        notes.append("工牌未点名: " + "、".join(missing))
        return False, notes
    return True, notes


def apply_kling_omni_image_refs(
    payload: dict[str, Any],
    refs: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Image Omni：element_list + image_list[].image（https 或已装箱的 base64）。"""
    notes: list[str] = []
    elements, image_list, extra = _kling_image_omni_from_refs(refs)
    notes.extend(extra)
    if image_list:
        payload["image_list"] = image_list
    if elements:
        payload["elements"] = elements
    payload.setdefault("watermark_info", {"enabled": False})
    payload.setdefault("result_type", payload.get("result_type") or "single")
    payload.setdefault("resolution", payload.get("resolution") or "2k")
    return notes


def apply_kling_omni_refs(
    payload: dict[str, Any],
    *,
    prompt: str = "",
    first_url: str = "",
    last_url: str = "",
    refs: list[dict[str, Any]] | None = None,
    elements: list[dict[str, Any]] | None = None,
) -> list[str]:
    """填 Omni contents[]。@image_N 只给 refer_image；first/last 不写 id。"""
    notes: list[str] = []
    first = str(first_url or "").strip()
    last = str(last_url or "").strip()
    existing = [c for c in (payload.get("contents") or []) if isinstance(c, dict)]
    has_video = any(c.get("type") in _KLING_VIDEO_REF for c in existing)
    prompt_text = str(prompt or "").strip()
    if not prompt_text:
        for item in existing:
            if item.get("type") == "prompt":
                prompt_text = str(item.get("text") or "")
                break
    contents: list[dict[str, Any]] = []
    if prompt_text:
        contents.append({"type": "prompt", "text": prompt_text})
    if has_video:
        for item in existing:
            if item.get("type") in _KLING_VIDEO_REF:
                contents.append(item)
        if first or last:
            notes.append("Omni 视频参考与首尾帧互斥，已忽略首尾帧")
        first, last = "", ""
    if last and not first:
        notes.append("可灵 Omni 有尾帧必须同时有首帧，已忽略尾帧")
        last = ""
    if first:
        contents.append({"type": "first_frame", "url": first})
    if last:
        contents.append({"type": "last_frame", "url": last})
    image_n = 0
    extra = 0
    for item in refs or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or item.get("kind") or "").strip().lower()
        if kind in _KLING_FRAME_REF or kind in ("end_frame", "first", "last"):
            continue
        eid = item.get("element_id") if item.get("element_id") is not None else item.get("id")
        if kind == "element" or (eid is not None and str(eid).strip() != "" and kind in ("element", "portrait", "turnaround")):
            continue
        url = str(item.get("url") or "").strip()
        if not url or url in {first, last}:
            continue
        image_n += 1
        extra += 1
        contents.append({"type": "refer_image", "url": url, "id": f"image_{image_n}"})
    element_items: list[dict[str, Any]] = []
    for item in list(elements or []) + list(refs or []):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or item.get("kind") or "").strip().lower()
        eid = item.get("element_id") if item.get("element_id") is not None else None
        if eid is None and kind == "element":
            eid = item.get("id")
        if eid is None or str(eid).strip() == "":
            continue
        if kind and kind not in ("element", "portrait", "turnaround", ""):
            if kind in ("subject", "refer_image", "scene_ref", "prop"):
                continue
        element_items.append(item)
    seen_el: set[str] = set()
    el_n = 0
    for item in element_items:
        eid = item.get("element_id") if item.get("element_id") is not None else item.get("id")
        key = str(eid)
        if key in seen_el:
            continue
        seen_el.add(key)
        el_n += 1
        extra += 1
        contents.append({"type": "element", "element_id": str(eid), "id": f"element_{el_n}"})
    cap = 4 if has_video else 7
    keep_prompt = 1 if prompt_text else 0
    keep_frames = int(bool(first)) + int(bool(last))
    keep_video = sum(1 for c in contents if c.get("type") in _KLING_VIDEO_REF)
    budget = cap
    trimmed: list[dict[str, Any]] = contents[: keep_prompt + keep_video + keep_frames]
    extras = [c for c in contents[keep_prompt + keep_video + keep_frames:]]
    if len(extras) > budget:
        notes.append(f"可灵 Omni 图+元素超过 {cap}，已截断")
        extras = extras[:budget]
    contents = trimmed + extras
    payload["contents"] = contents
    payload.pop("image_list", None)
    settings = payload.setdefault("settings", {})
    if not any(c.get("type") == "feature_video" for c in contents):
        settings.setdefault("multi_shot", False)
    if any(c.get("type") in _KLING_FRAME_REF or c.get("type") in _KLING_VIDEO_REF for c in contents):
        settings.pop("aspect_ratio", None)
    else:
        settings.setdefault("aspect_ratio", "16:9")
    options = payload.setdefault("options", {})
    options.setdefault("watermark_info", {"enabled": False})
    payload.pop("watermark_info", None)
    cited, cite_notes = kling_omni_elements_cited(prompt_text, el_n)
    notes.extend(cite_notes)
    if not cited:
        payload["_kling_cite_invalid"] = True
    return notes


def apply_seedance_rework(
    payload: dict[str, Any],
    *,
    text: str = "",
    video_url: str = "",
    refs: list[dict[str, Any]] | None = None,
    mode: str = "edit",
    extra_seconds: float = 5,
    caps: dict[str, Any] | None = None,
) -> list[str]:
    """方舟 edit/extend：只填参考视频，禁止首尾帧（否则 generate 注入器会丢掉成片）。"""
    notes: list[str] = []
    url = str(video_url or "").strip()
    if not url.startswith("http"):
        notes.append("edit/extend 需要公网视频 URL")
        return notes
    content: list[dict[str, Any]] = []
    body = str(text or "").strip()
    if body:
        content.append({"type": "text", "text": body})
    content.append({
        "type": "video_url",
        "video_url": {"url": url},
        "role": "reference_video",
    })
    for item in refs or []:
        if not isinstance(item, dict):
            continue
        ref = str(item.get("url") or "").strip()
        if not ref:
            continue
        content.append({
            "type": "image_url",
            "image_url": {"url": ref},
            "role": "reference_image",
        })
    payload["content"] = content
    payload["watermark"] = False
    payload["ratio"] = "adaptive"
    kind = str(mode or "edit").strip().lower()
    if kind == "extend":
        policy = (caps or {}).get("duration_policy") if isinstance(caps, dict) else None
        payload["duration"] = int(snap_duration_seconds(extra_seconds or 5, policy))
    else:
        payload["duration"] = -1
    for key in ("first_frame_url", "last_frame_url", "image_url"):
        payload.pop(key, None)
    return notes


def apply_kling_omni_videos(
    payload: dict[str, Any],
    *,
    video_url: str = "",
    refer_type: str = "base",
    prompt: str = "",
) -> list[str]:
    """Omni contents 只塞 base_video / feature_video，与首尾帧互斥。"""
    notes: list[str] = []
    url = str(video_url or "").strip()
    if not url.startswith("http"):
        notes.append("Omni 编辑需要公网视频 URL")
        return notes
    kind = str(refer_type or "base").strip().lower() or "base"
    if kind not in ("base", "feature"):
        notes.append(f"忽略非法 refer_type={kind}，改用 base")
        kind = "base"
    had_frames = any(
        isinstance(c, dict) and c.get("type") in _KLING_FRAME_REF
        for c in (payload.get("contents") or [])
    )
    prompt_text = str(prompt or "").strip()
    if not prompt_text:
        for item in payload.get("contents") or []:
            if isinstance(item, dict) and item.get("type") == "prompt":
                prompt_text = str(item.get("text") or "")
                break
    video_type = "feature_video" if kind == "feature" else "base_video"
    contents: list[dict[str, Any]] = []
    if prompt_text:
        contents.append({"type": "prompt", "text": prompt_text})
    contents.append({"type": video_type, "url": url, "id": "video_1"})
    payload["contents"] = contents
    payload.pop("image_list", None)
    payload.pop("video_list", None)
    payload.pop("sound", None)
    settings = payload.setdefault("settings", {})
    if kind == "feature":
        settings["multi_shot"] = True
        settings["audio"] = "off"
    else:
        settings["multi_shot"] = False
        settings["audio"] = "original"
        settings.pop("duration", None)
    settings.pop("aspect_ratio", None)
    options = payload.setdefault("options", {})
    options.setdefault("watermark_info", {"enabled": False})
    payload.pop("watermark_info", None)
    if had_frames:
        notes.append("Omni 视频参考与首尾帧互斥，已忽略首尾帧")
    return notes


# RPM / 时长网格（编排层 pacing 用）
# durations 含 API 仍接受的 3s；转换器读 duration_policy，不含 3s。
# 仅记录，全仓无消费方（保留以便未来校验网格）。
VIDEO_META: dict[str, dict[str, Any]] = {
    "agnes_video": {"durations": list(range(4, 13)), "prompt_max": 3000},
}

# ---- 实测输出分辨率（2026-09 实测；官方只给 720P/档位，不列像素）----
# 视频 2.5 Flash：720P 硬限。16:9 实出 1280x704（上下各 8px 黑边，非 1280x720）、
# 9:16 实出 720x1280。竖屏成片 1080x1920 即 1.5x 上采样。
# 图片 2.5 Flash：2K 档 16:9=2624x1472、9:16=1472x2624、1:1=2048x2048，
# 均非 1920x1080 / 1280x720。
# 这两张表只作知识与兜底：compose/report 一律以 ffprobe 实测为准，不得据此
# 反推缩放/letterbox（也不得假设 1280x720 / 1920x1080）。
AGNES_V25_VIDEO_SIZES: dict[str, tuple[int, int]] = {
    "16:9": (1280, 704),
    "9:16": (720, 1280),
}
AGNES_IMAGE_2K_SIZES: dict[str, tuple[int, int]] = {
    "16:9": (2624, 1472),
    "9:16": (1472, 2624),
    "1:1": (2048, 2048),
}


def measured_output_size(kind: str, *, ratio: str = "", size: str = "") -> tuple[int, int] | None:
    """实测输出像素（仅知识/兜底，不能替代 ffprobe）。未知组合返回 None。"""
    key = str(ratio or "").strip()
    if kind == "video":
        return AGNES_V25_VIDEO_SIZES.get(key)
    if kind == "image" and str(size or "").strip().upper() == "2K":
        return AGNES_IMAGE_2K_SIZES.get(key)
    return None

# Agnes 访问类型 RPM：官方 Token Plan FAQ（取「实际 RPM」，比「允许发起」更保守）。
# 同类型多密钥共享同一限制池、不叠加；default = 未声明 Token Plan/企业认证的免费用户。
# 文本 RPM（20/40/1000）不建：仓库无任何 agnes 文本调用。
AGNES_RPM: dict[str, dict[str, Any]] = {
    "default": {"video": 1, "image": {"1K": 20, "2K": 10, "3K": 1, "4K": 1}},
    "enterprise": {"video": 2, "image": {"1K": 40, "2K": 20, "3K": 1, "4K": 1}},
    "tokenplan": {"video": 5, "image": {"1K": 100, "2K": 80, "3K": 1, "4K": 1}},
}


def agnes_access_tier() -> str:
    """AGNES_ACCESS_TYPE=default|enterprise|tokenplan；未知/未设回 default（免费档）。

    未声明即免费：代码不得从密钥/额度等其它信号推断 Token Plan。
    """
    val = str(os.environ.get("AGNES_ACCESS_TYPE") or "").strip().lower()
    return val if val in AGNES_RPM else "default"


def agnes_video_rpm(tier: str | None = None) -> float:
    key = str(tier).strip().lower() if tier else agnes_access_tier()
    if key not in AGNES_RPM:
        key = "default"
    return float(AGNES_RPM[key]["video"])


def agnes_image_rpm(size: str, tier: str | None = None) -> float:
    """按 size 档取 RPM；未知 size 兜底 2K 档。"""
    key = str(tier).strip().lower() if tier else agnes_access_tier()
    if key not in AGNES_RPM:
        key = "default"
    table = AGNES_RPM[key]["image"]
    return float(table.get(str(size or "").strip().upper()) or table["2K"])


def policy_for_loop(video_loop: Any = None) -> dict[str, Any]:
    """video_loop → 转换器/校验器共用的 duration_policy。

    jimeng/volcengine → 即梦 v30 的 5/10；agnes → 2.5 的 4–12 range；
    kling → Omni 3.0 的 3–15 range（FORCE v1/2.1 仍按旧 enum 贴网格）；
    ark/seedance → Seedance 2.5 的 4–30 range；其余 → kind=none。
    """
    val = str(video_loop or "none").strip().lower()
    if val in ("jimeng", "volcengine"):
        return dict(VIDEO_BY_TOOL["jimeng_video"]["duration_policy"])
    if val == "agnes":
        return dict(VIDEO_BY_TOOL["agnes_video"]["duration_policy"])
    if val == "kling":
        return dict(VIDEO_BY_TOOL["kling_video"]["duration_policy"])
    if val in ("ark", "seedance"):
        return dict(VIDEO_SURFACES["seedance_25"]["duration_policy"])
    return {"kind": "none"}


def _enum_values(policy: dict[str, Any]) -> list[float]:
    return [float(v) for v in (policy.get("values") or []) if v is not None]


def policy_step(policy: dict[str, Any] | None) -> float | None:
    """切镜步长。None=未传政策（旧 5s）；kind=none 返回 None（均分）；enum 取最小档。"""
    if policy is None:
        return 5.0
    kind = str(policy.get("kind") or "")
    if kind == "none":
        return None
    if kind == "enum":
        values = _enum_values(policy)
        return min(values) if values else 5.0
    if kind == "range":
        step = float(policy.get("step") or 1.0)
        return step if step > 0 else 1.0
    return 5.0


def policy_max(policy: dict[str, Any] | None) -> float | None:
    if not isinstance(policy, dict):
        return None
    kind = str(policy.get("kind") or "")
    if kind == "enum":
        values = _enum_values(policy)
        return max(values) if values else None
    if kind == "range":
        try:
            return float(policy.get("max"))
        except (TypeError, ValueError):
            return None
    return None


def snap_duration_seconds(seconds: float, policy: dict[str, Any] | None) -> float:
    """所需秒数向上贴政策。policy is None → 旧 5/10 网格。kind=none → 不贴网格。"""
    needed = max(float(seconds or 0), 0.0)
    if policy is None:
        needed = max(needed, 5.0)
        if needed <= 5.0:
            return 5.0
        return float(math.ceil(needed / 10.0) * 10)
    kind = str(policy.get("kind") or "")
    if kind == "none":
        return round(needed, 3)
    if kind == "enum":
        values = sorted(_enum_values(policy))
        if not values:
            return round(needed, 3)
        for value in values:
            if value + 1e-9 >= needed:
                return value
        return values[-1]
    if kind == "range":
        try:
            lo = float(policy.get("min"))
            hi = float(policy.get("max"))
        except (TypeError, ValueError):
            return round(needed, 3)
        step = float(policy.get("step") or 1.0)
        if step <= 0:
            step = 1.0
        needed = max(needed, lo)
        if needed > hi:
            return hi
        steps = math.ceil((needed - lo) / step - 1e-9)
        return min(hi, round(lo + steps * step, 3))
    return round(needed, 3)

