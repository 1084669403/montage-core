"""policy — 从项目目录读取闭环锁定 / 管线默认值（不猜路径）。

project_dir 解析顺序：
1. 显式 ``inputs.project_dir``
2. 若某路径的父目录名属于已知子目录（artifacts/renders/assets/auto_edit/history），
   则取其祖父目录
3. 否则不猜测
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from montage.engine.stages import STAGE_ORDER
from montage.pipelines import get_pipeline

KNOWN_PROJECT_SUBDIRS = frozenset({"artifacts", "renders", "assets", "auto_edit", "history"})

LOCK_CAPABILITIES = frozenset({"image_generation", "video_generation"})

VIDEO_LOOP_PROVIDERS: dict[str, list[str]] = {
    "agnes": ["agnes"],
    "volcengine": ["volcengine"],
    "dashscope": ["dashscope"],
    "kling": ["kling"],
    "ark": ["ark"],
    "seedance": ["ark"],
}

NATIVE_AUDIO_SOURCES = frozenset({"agnes_prompt", "jimeng_prompt", "kling_prompt"})
NATIVE_AUDIO_LOOPS = frozenset({"agnes", "ark", "seedance"})


def infer_project_dir(*paths: Any) -> Path | None:
    """从产物/媒体路径推断项目根；无法确定时返回 None（不猜）。"""
    for raw in paths:
        if not raw:
            continue
        try:
            p = Path(str(raw))
        except (TypeError, ValueError):
            continue
        parent = p.parent
        if parent.name in KNOWN_PROJECT_SUBDIRS:
            return parent.parent
    return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def pipeline_type_for(project_dir: str | Path | None) -> str:
    if not project_dir:
        return "cinematic"
    meta = _read_json(Path(project_dir) / "project.json")
    return str(meta.get("pipeline_type") or "cinematic")


def load_pipeline_settings(project_dir: str | Path | None) -> dict[str, Any]:
    """管线顶层默认值（playbook / profile / edit_style / transition_policy）。"""
    name = pipeline_type_for(project_dir)
    pipe = get_pipeline(name) or {}
    return {
        "pipeline_type": name,
        "default_playbook": pipe.get("default_playbook") or "",
        "default_profile": pipe.get("default_profile") or "",
        "edit_style": pipe.get("edit_style") or "cinematic",
        "transition_policy": pipe.get("transition_policy") or "",
    }


def load_loop_policy(project_dir: str | Path | None) -> dict[str, Any]:
    """从 artifacts/proposal_packet.json 读图/视频闭环锁定与预算封顶。"""
    if not project_dir:
        return {}
    packet = _read_json(Path(project_dir) / "artifacts" / "proposal_packet.json")
    allowed = packet.get("allowed_providers")
    if not isinstance(allowed, list):
        allowed = None
    else:
        allowed = [str(x) for x in allowed if x]
    image_providers = packet.get("image_providers")
    if not isinstance(image_providers, list):
        image_providers = None
    else:
        image_providers = [str(x) for x in image_providers if x]
    loop = packet.get("video_loop")
    loop_key = str(loop or "").strip().lower()
    if loop_key == "seedance":
        loop_key = "ark"
    if not allowed and loop_key in VIDEO_LOOP_PROVIDERS:
        allowed = list(VIDEO_LOOP_PROVIDERS[loop_key])
    ceiling = packet.get("budget_ceiling_usd")
    try:
        ceiling_f = float(ceiling) if ceiling is not None else None
    except (TypeError, ValueError):
        ceiling_f = None
    return {
        "video_loop": loop,
        "video_surface": packet.get("video_surface"),
        "allowed_providers": allowed,
        "image_providers": image_providers,
        "lock_preferred_provider": bool(packet.get("lock_preferred_provider")),
        "budget_ceiling_usd": ceiling_f,
        "render_runtime": packet.get("render_runtime") or "ffmpeg",
        "output_profile": packet.get("output_profile"),
        "playbook": packet.get("playbook"),
    }


def shot_keeps_embedded_audio(shot: dict[str, Any] | None) -> bool:
    """单镜是否保留片内音（原生音频 / 提示词出声）。"""
    if not isinstance(shot, dict):
        return False
    if str(shot.get("audio_source") or "") in NATIVE_AUDIO_SOURCES:
        return True
    return str(shot.get("dialogue_audio_mode") or "").strip().lower() == "native"


def keep_embedded_audio(
    shot_prompts: dict[str, Any] | None = None,
    project_dir: str | Path | None = None,
) -> bool:
    """整片跳过叠音：仅 Agnes / Seedance。可灵按镜处理，kling_prompt 不在这里短路。"""
    loop = str(load_loop_policy(project_dir).get("video_loop") or "").strip().lower()
    if loop == "kling":
        return False
    for shot in (shot_prompts or {}).get("shots") or []:
        if not shot_keeps_embedded_audio(shot):
            continue
        if str(shot.get("audio_source") or "") == "kling_prompt":
            continue
        return True
    return loop in NATIVE_AUDIO_LOOPS


def lock_applies(tool: Any) -> bool:
    """锁定只作用于图/视频生成（含选型器的 target_capability）。TTS/compose 永不锁定。"""
    target = getattr(tool, "target_capability", None) or getattr(tool, "capability", "")
    return target in LOCK_CAPABILITIES


def resolve_allowed_providers(
    policy: dict[str, Any],
    inputs: dict[str, Any],
    *,
    capability: str = "",
) -> list[str] | None:
    cap = str(capability or "")
    # 图/视频解耦：image_providers 只作用于生图（inputs 显式 > policy 配置）
    if cap == "image_generation":
        img_raw = inputs.get("image_providers")
        if isinstance(img_raw, list) and img_raw:
            return [str(x) for x in img_raw if x]
        img_policy = policy.get("image_providers")
        if isinstance(img_policy, list) and img_policy:
            return list(img_policy)
    raw = inputs.get("allowed_providers")
    if isinstance(raw, list) and raw:
        allowed = [str(x) for x in raw if x]
    else:
        allowed = policy.get("allowed_providers")
        if isinstance(allowed, list) and allowed:
            allowed = list(allowed)
        else:
            loop = inputs.get("video_loop") or policy.get("video_loop")
            key = str(loop or "").strip().lower()
            if key == "seedance":
                key = "ark"
            elif key == "jimeng":
                key = "volcengine"
            allowed = list(VIDEO_LOOP_PROVIDERS[key]) if key in VIDEO_LOOP_PROVIDERS else None
    if not allowed:
        return None
    return allowed


def enforce_concrete_provider_lock(tool: Any, inputs: dict[str, Any], policy: dict[str, Any]) -> str | None:
    """具体适配器（非选型器）：provider 不在允许列表则返回错误文案。"""
    if not lock_applies(tool):
        return None
    if getattr(tool, "target_capability", None):
        return None
    allowed = resolve_allowed_providers(
        policy, inputs, capability=getattr(tool, "capability", "") or "",
    )
    if not allowed:
        return None
    provider = getattr(tool, "provider", "")
    if provider not in allowed:
        return (
            f"供应商锁定：{getattr(tool, 'name', '?')} provider={provider} "
            f"不在允许列表 {allowed}（仅约束 image/video 生成，不影响 TTS/compose）"
        )
    return None


_PRODUCE_SKILL_PATHS = (
    "docs/skills/meta/produce.md",
    ".cursor/skills/montage-produce/SKILL.md",
)


def skill_paths_for_pipeline(name: str) -> list[str]:
    """导演技能相对仓库根的路径（成片 Skill 在前；cinematic 分阶段；另两条管线各一页 delta）。"""
    prefix = list(_PRODUCE_SKILL_PATHS)
    if name == "documentary":
        return prefix + ["docs/skills/pipelines/documentary.md"]
    if name == "clip_factory":
        return prefix + ["docs/skills/pipelines/clip_factory.md"]
    return prefix + [f"docs/skills/pipelines/cinematic/{stage}.md" for stage in STAGE_ORDER]


def skill_paths_for_project(project_dir: str | Path) -> list[str]:
    return skill_paths_for_pipeline(pipeline_type_for(project_dir))
