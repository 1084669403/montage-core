"""idea_developer — 收编想法：校验 bible、编译 script/scene_plan（无 LLM）。

operation:
- skeleton：只出 format_card 骨架（调 style_matcher cascade）
- validate：purpose=bible 门禁，不编假剧情
- compile：bible → script + scene_plan，注入 library_hit_ids
"""

from __future__ import annotations

from typing import Any

from montage.engine.artifacts import ArtifactStore
from montage.engine.bible import compile_bible, write_compiled
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus
from montage.tools.script_validator import validate_bible
from montage.tools.style_matcher import cascade_styles


def merge_library_hits(bible: dict[str, Any], format_card: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(bible)
    ids: list[str] = []
    for raw in (out.get("library_hit_ids") or []) + ((format_card or {}).get("library_hit_ids") or []):
        text = str(raw or "").strip()
        if text and text not in ids:
            ids.append(text)
    out["library_hit_ids"] = ids
    return out


def write_format_card(project_dir: str, card: dict[str, Any]) -> None:
    ArtifactStore(project_dir).write("format_card", card)


class IdeaDeveloper(BaseTool):
    name = "idea_developer"
    version = "0.1.0"
    capability = "prompt_engineering"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["skeleton", "validate", "compile"],
                "default": "skeleton",
            },
            "idea": {"type": "string"},
            "bible": {"type": "object"},
            "format_card": {"type": ["object", "null"]},
            "episode_plan": {"type": ["object", "null"]},
            "project_dir": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        op = str(inputs.get("operation") or "skeleton")
        idea = str(inputs.get("idea") or "").strip()
        card = inputs.get("format_card") if isinstance(inputs.get("format_card"), dict) else None
        bible = inputs.get("bible") if isinstance(inputs.get("bible"), dict) else None
        project_dir = str(inputs.get("project_dir") or "").strip()

        if op == "skeleton":
            if not idea:
                return ToolResult(success=False, error="'idea' 必填（operation=skeleton）")
            card = cascade_styles(idea)
            if project_dir:
                write_format_card(project_dir, card)
            return ToolResult(
                success=True,
                data={
                    "format_card": card,
                    "status": "need_bible",
                    "findings": card.get("findings") or [{
                        "severity": "warning",
                        "stage": "bible",
                        "field": "series_bible",
                        "message": "无 bible，只写出 format_card 骨架，不编假剧情",
                        "proposed_fix": "由 Agent 写入 artifacts/series_bible.json 后再 compile",
                    }],
                },
                meta={"operation": "skeleton"},
            )

        if not isinstance(bible, dict) and op == "compile" and project_dir:
            # V29 直读回退：compile 时 bible 缺省且 project_dir 存在 → 从项目目录直读
            # series_bible（复用 format_card 同款回退惯例）。用于停点改 bible 后的手动
            # 重编译（inputs 仅 {"operation":"compile"}，免手抄数万字 JSON）。
            # validate 分支不改：其语义就是校验显式传入的稿。
            stored = ArtifactStore(project_dir).read("series_bible")
            if isinstance(stored, dict):
                bible = stored
                self._bible_from_project_dir = True
            else:
                self._bible_from_project_dir = False
        else:
            self._bible_from_project_dir = False
        if not isinstance(bible, dict):
            return ToolResult(success=False, error="'bible' 必填")
        if card is None and idea:
            card = cascade_styles(idea)
        if card is None and project_dir:
            stored = ArtifactStore(project_dir).read("format_card")
            card = stored if isinstance(stored, dict) else None
        bible = merge_library_hits(bible, card)
        report = validate_bible(bible, format_card=card, playbook=bible.get("playbook"))
        if op == "validate":
            return ToolResult(
                success=True,
                data={"bible": bible, "format_card": card, **report},
                meta={"operation": "validate"},
            )

        if not report["pass"]:
            return ToolResult(
                success=False,
                error="bible 未通过门禁，拒绝编译",
                data={"bible": bible, "format_card": card, **report},
            )
        episode = inputs.get("episode_plan") if isinstance(inputs.get("episode_plan"), dict) else None
        compiled = compile_bible(bible, episode_plan=episode, project_dir=project_dir or None)
        if project_dir:
            write_compiled(project_dir, compiled["script"], compiled["scene_plan"])
            if card:
                write_format_card(project_dir, card)
        return ToolResult(
            success=True,
            data={
                "bible": bible,
                "format_card": card,
                "script": compiled["script"],
                "scene_plan": compiled["scene_plan"],
                "findings": (report.get("findings") or []) + (compiled.get("findings") or []),
                "pass": True,
                "bible_source": "project_dir" if self._bible_from_project_dir else "inputs",
            },
            meta={"operation": "compile"},
        )
