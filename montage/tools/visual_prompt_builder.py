"""visual_prompt_builder — 镜头 → 首帧图/视频动态双提示词。

确定性封装 lib.shot_prompt_builder：
- purpose="shot"：build_shot_prompt_pair —— 产出 first_frame_prompt + video_prompt
  + 负向提示词（含中英分层、压缩预算、词库兜底注入 enrich_first_frame）。
- purpose="portrait" / "turnaround" / "scene_ref"：build_reference_image_prompt —— 定妆照/四视图/场景参考图。

本文件为全新原创代码（仅依赖用户自有模块 lib.shot_prompt_builder）。
"""

from __future__ import annotations

from typing import Any

from lib.shot_prompt_builder import (
    build_reference_image_prompt,
    build_shot_prompt_pair,
)
from montage.engine.prompt_contract import annotate_prompt_pair
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_BOOL_KEYS = (
    "agnes_audio", "agnes_v25", "kling_prompt", "english_visual", "enrich_first_frame", "enrich_video_frame",
    "keep_reference", "dense", "jimeng_prompt",
)


def _resolve_style_context(inputs: dict[str, Any]) -> Any:
    """已有 style_context 优先；否则从 proposal_packet.playbook 注入。缺失则 None。"""
    ctx = inputs.get("style_context")
    if isinstance(ctx, dict) and ctx:
        return ctx
    proj = inputs.get("project_dir")
    if not proj:
        return None
    try:
        from montage.engine.artifacts import ArtifactStore
        from montage.playbooks import get_playbook

        packet = ArtifactStore(proj).read("proposal_packet") or {}
        name = str(packet.get("playbook") or "").strip()
        if not name:
            return None
        return get_playbook(name)
    except (OSError, TypeError, ValueError):
        return None


class VisualPromptBuilder(BaseTool):
    name = "visual_prompt_builder"
    version = "0.1.0"
    capability = "prompt_engineering"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["purpose"],
        "properties": {
            "purpose": {
                "type": "string",
                "enum": ["shot", "portrait", "turnaround", "scene_ref"],
            },
            "shot": {"type": "object", "description": "scene_plan 中的镜头对象（purpose=shot 必填）"},
            "character": {"type": "object"},
            "scene": {"type": "object"},
            "character_registry": {"type": "array", "items": {"type": "object"}},
            "style_context": {"type": "object"},
            "master_pattern": {
                "type": "string",
                "description": "可选；母带模式 id（lib.kling_master），缺省从 style_context/playbook 的 master_pattern 取",
            },
            "master_prompt": {
                "type": "string",
                "description": "可选；覆盖母带字符串，缺省从 style_context/playbook 的 master_prompt 取",
            },
            "project_dir": {
                "type": "string",
                "description": "可选；style_context 为空时从 artifacts/proposal_packet.json 读 playbook",
            },
            "provider_max_chars": {"type": "integer"},
            "pose_beat_id": {"type": "string"},
            "at_seconds": {"type": "number"},
            "operation": {"type": "string"},
            **{k: {"type": "boolean"} for k in _BOOL_KEYS},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        purpose = inputs.get("purpose")
        if purpose not in ("shot", "portrait", "turnaround", "scene_ref"):
            return ToolResult(success=False, error=f"未知 purpose: {purpose}")

        style_context = _resolve_style_context(inputs)
        injected = style_context is not None and not (
            isinstance(inputs.get("style_context"), dict) and inputs.get("style_context")
        )

        if purpose == "shot":
            shot = inputs.get("shot")
            if not shot:
                return ToolResult(success=False, error="purpose=shot 需要提供 shot")
            # B2.5：在场清单里的道具/场景要用**中文名**渲染，不是 prop_xxx 这种 id。
            # 角色名由 character_registry 提供；道具名从项目 script.props 读（只读、
            # 缺项目或坏文件时静默回落 id —— 提示词质量降级但不报错）。
            presence_names: dict[str, str] = {}
            proj_dir = inputs.get("project_dir")
            if proj_dir:
                try:
                    from montage.engine.artifacts import ArtifactStore

                    store = ArtifactStore(proj_dir)
                    script_doc = store.read("script") or {}
                    bible_doc = store.read("series_bible") or {}
                    for key in ("characters", "props", "locations"):
                        for row in (script_doc.get(key) or []):
                            if isinstance(row, dict) and row.get("id"):
                                presence_names[str(row["id"])] = str(
                                    row.get("name") or row["id"]
                                )
                        for row in (bible_doc.get(key) or []):
                            if isinstance(row, dict) and row.get("id"):
                                presence_names.setdefault(
                                    str(row["id"]), str(row.get("name") or row["id"])
                                )
                    # 场景静物（灯笼/烛台这类布景道具）挂在 locations[].objects 上，
                    # 名字也要能查，否则在场清单会写 prop_lantern 这种 id。
                    for loc in (bible_doc.get("locations") or []):
                        if not isinstance(loc, dict):
                            continue
                        for obj in loc.get("objects") or []:
                            if isinstance(obj, dict) and obj.get("id"):
                                presence_names.setdefault(
                                    str(obj["id"]), str(obj.get("name") or obj["id"])
                                )
                except (OSError, ValueError, TypeError):
                    presence_names = {}
            if isinstance(inputs.get("presence_names"), dict):
                presence_names.update(
                    {str(k): str(v) for k, v in inputs["presence_names"].items()}
                )
            # 参考图图例要用中文名（"为turnaround_huan_niang…" 这种 id 会污染提示词）：
            # 逐条把 name 补成角色/道具/地点的显示名。
            raw_refs = inputs.get("refs")
            if isinstance(raw_refs, list) and presence_names:
                refs_named: list[Any] = []
                for ref in raw_refs:
                    if not isinstance(ref, dict):
                        refs_named.append(ref)
                        continue
                    row = dict(ref)
                    if not str(row.get("name") or "").strip():
                        for key in ("character_id", "prop_id", "location_id", "id"):
                            ident = str(row.get(key) or "").strip()
                            if not ident:
                                continue
                            hit = presence_names.get(ident)
                            if not hit and "_" in ident:
                                # turnaround_huan_niang / prop_guqin → 去前缀再查
                                hit = presence_names.get(ident.split("_", 1)[1])
                            if hit:
                                row["name"] = hit
                                break
                    refs_named.append(row)
                kwargs_refs = refs_named
            else:
                kwargs_refs = inputs.get("refs")
            # 母带注入：显式 inputs 优先，否则从 style_context(playbook) 取
            master_pattern = inputs.get("master_pattern") or (
                style_context.get("master_pattern") if isinstance(style_context, dict) else None
            )
            master_prompt = inputs.get("master_prompt") or (
                style_context.get("master_prompt") if isinstance(style_context, dict) else None
            )
            kwargs = {
                "shot": shot,
                "character_registry": inputs.get("character_registry"),
                "style_context": style_context,
                "agnes_audio": bool(inputs.get("agnes_audio", False)),
                "enrich_first_frame": bool(inputs.get("enrich_first_frame", False)),
                "enrich_video_frame": bool(inputs.get("enrich_video_frame", False)),
                "pose_beat_id": inputs.get("pose_beat_id"),
                "at_seconds": inputs.get("at_seconds"),
                "keep_reference": bool(inputs.get("keep_reference", False)),
                "dense": bool(inputs.get("dense", True)),
                "jimeng_prompt": bool(inputs.get("jimeng_prompt", False)),
                "operation": inputs.get("operation"),
                "provider_max_chars": inputs.get("provider_max_chars"),
                "english_visual": bool(inputs.get("english_visual", False)),
                "agnes_v25": bool(inputs.get("agnes_v25", False)),
                "kling_prompt": bool(inputs.get("kling_prompt", False)),
                "kling_cite": str(inputs.get("kling_cite") or "omni"),
                "locations": inputs.get("locations"),
                "refs": kwargs_refs,
                "master_prompt": str(master_prompt or "") or None,
                "master_pattern": str(master_pattern or "") or None,
                "presence_names": presence_names or None,
            }
            pair = build_shot_prompt_pair(**kwargs)
            pair = annotate_prompt_pair(
                shot,
                pair,
                registry=inputs.get("character_registry"),
            )
            return ToolResult(
                success=True,
                data=pair,
                meta={"purpose": "shot", "style_injected": injected},
            )

        # portrait / turnaround / scene_ref —— 定妆照、四视图、场景参考图
        kind = purpose  # "portrait" | "turnaround" | "scene_ref"
        result = build_reference_image_prompt(
            kind,
            character=inputs.get("character"),
            scene=inputs.get("scene"),
            style_context=style_context,
            provider_max_chars=inputs.get("provider_max_chars"),
            english_visual=bool(inputs.get("english_visual", True)),
            enrich_first_frame=bool(inputs.get("enrich_first_frame", True)),
            keep_reference=bool(inputs.get("keep_reference", False)),
        )
        return ToolResult(
            success=True,
            data=result,
            meta={"purpose": kind, "style_injected": injected},
        )
