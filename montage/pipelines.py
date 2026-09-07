"""pipelines — 管线配置（Python 字典）。

每个管线描述阶段序列：gated（是否需要人类审批才能 completed）、
produces（规范产物）、tools（该阶段可用的工具名）。

顶层字段（assemble / edit_advisor / apply_profile 会读取）：
- default_playbook / default_profile / edit_style / transition_policy
三条管线必须可区分，且这些字段会被消费（不是文档装饰）。
"""

from __future__ import annotations

from typing import Any

_ASSETS_CORE = [
    "visual_prompt_builder",
    "prompt_library_retriever",
    "asset_retriever",
    "image_selector",
    "video_selector",
    "tts_selector",
    "shot_runner",
    "voice_director",
]

_COMPOSE_CORE = [
    "ffmpeg_compose",
    "edit_advisor",
    "soundtrack_planner",
    "asset_retriever",
    "place_audio",
    "compose_planner",
]


def _stage(name: str, gated: bool, produces: list[str], tools: list[str]) -> dict[str, Any]:
    """构造单阶段配置（与 cinematic 同阶段序列，避免重复书写）。"""
    return {"name": name, "gated": gated, "produces": produces, "tools": tools}


CINEMATIC: dict[str, Any] = {
    "name": "cinematic",
    "version": "1.0",
    "description": "情绪驱动的剧情/宣传片管线（中文生产主用）",
    "default_playbook": "",
    "default_profile": "youtube_landscape",
    "edit_style": "cinematic",
    "transition_policy": "cinematic_xfade",
    "stages": [
        _stage("research", False, ["research_brief"], []),
        _stage("proposal", True, ["proposal_packet"], ["style_matcher"]),
        _stage("script", True, ["script"], ["prompt_library_retriever", "script_validator"]),
        _stage(
            "scene_plan",
            True,
            ["scene_plan"],
            ["prompt_library_retriever", "visual_prompt_builder", "edit_advisor", "script_to_scene_plan"],
        ),
        _stage("assets", True, ["asset_manifest", "shot_prompts"], list(_ASSETS_CORE) + ["vlm_reviewer"]),
        _stage(
            "compose",
            False,
            ["edit_decisions", "render_report"],
            list(_COMPOSE_CORE),
        ),
        _stage("publish", True, ["publish_log"], ["export_bundle", "film_health"]),
    ],
}

DOCUMENTARY: dict[str, Any] = {
    "name": "documentary",
    "version": "1.0",
    "description": "纪实/访谈/口播管线：克制编辑（仅硬切）、竖屏优先、环境声保留",
    "default_playbook": "documentary_restraint",
    "default_profile": "douyin_vertical",
    "edit_style": "documentary",
    "transition_policy": "cut_only",
    "stages": [
        _stage("research", False, ["research_brief"], []),
        _stage("proposal", True, ["proposal_packet"], ["style_matcher"]),
        _stage("script", True, ["script"], ["prompt_library_retriever", "script_validator"]),
        _stage(
            "scene_plan",
            True,
            ["scene_plan"],
            ["prompt_library_retriever", "visual_prompt_builder", "edit_advisor", "script_to_scene_plan"],
        ),
        _stage(
            "assets",
            True,
            ["asset_manifest", "shot_prompts"],
            [*_ASSETS_CORE, "asset_quality_gate", "vlm_reviewer"],
        ),
        _stage(
            "compose",
            False,
            ["edit_decisions", "render_report"],
            list(_COMPOSE_CORE),
        ),
        _stage("publish", True, ["publish_log"], ["export_bundle", "film_health"]),
    ],
}

CLIP_FACTORY: dict[str, Any] = {
    "name": "clip_factory",
    "version": "1.0",
    "description": "切片工厂：从长片素材提取短竖屏切片（script 阶段由导演写 clip_plan）",
    "default_playbook": "",
    "default_profile": "douyin_vertical",
    "edit_style": "documentary",
    "transition_policy": "cut_only",
    "stages": [
        _stage("research", False, ["research_brief"], []),
        _stage("proposal", True, ["proposal_packet"], ["style_matcher"]),
        _stage(
            "script",
            True,
            ["script", "clip_plan"],
            ["prompt_library_retriever", "script_validator", "scene_detect"],
        ),
        _stage(
            "scene_plan",
            True,
            ["scene_plan"],
            ["prompt_library_retriever", "visual_prompt_builder", "edit_advisor"],
        ),
        _stage(
            "assets",
            True,
            ["asset_manifest", "shot_prompts"],
            [
                "visual_prompt_builder",
                "prompt_library_retriever",
                "asset_retriever",
                "video_selector",
                "tts_selector",
                "scene_detect",
            ],
        ),
        _stage(
            "compose",
            False,
            ["edit_decisions", "render_report"],
            list(_COMPOSE_CORE),
        ),
        _stage("publish", True, ["publish_log"], ["export_bundle", "film_health"]),
    ],
}

PIPELINES: dict[str, dict[str, Any]] = {
    "cinematic": CINEMATIC,
    "documentary": DOCUMENTARY,
    "clip_factory": CLIP_FACTORY,
}


def get_pipeline(name: str) -> dict[str, Any] | None:
    return PIPELINES.get(name)


def list_pipelines() -> list[str]:
    return sorted(PIPELINES)
