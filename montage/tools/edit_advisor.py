"""edit_advisor — 剪辑转场/节奏确定性顾问（全新原创实现）。

输入 scene_plan 的 scenes[]（narrative_role / hero_moment / shot_language /
时长），输出每个切点（junction）的转场建议：

- documentary 风格：仅允许克制转场（cut / crossfade / fade）。
- cinematic 风格：允许负空隙（negative gap）重叠表达张力，hero 镜头建议 punch-in。

本顾问是确定性规则，与提示词库的 edits 分类互为补充：
规则给出建议，LLM 结合 edits 词条做最终剪辑决策。
"""

from __future__ import annotations

from typing import Any

from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

# 纪录片风格禁止的花哨转场
_DOCUMENTARY_FORBIDDEN = {"wipe", "push", "zoom", "zoom_punch", "blur", "glitch", "rgb_split"}

# 高能运镜 → 配更利落的转场
_HIGH_ENERGY_CAMERA = {"whip_pan", "handheld", "dolly_in", "crane_up", "zoom_in", "orbital"}

# 张力升级 → 硬切或叠化
_TENSION_BUILDERS = {"build_tension", "conflict", "climax", "deliver_payload", "hero_moment"}


def suggest_transitions(scenes: list[dict[str, Any]], style: str = "cinematic") -> list[dict[str, Any]]:
    """对每个相邻切点给出转场建议。"""
    suggestions: list[dict[str, Any]] = []
    for i in range(len(scenes) - 1):
        cur, nxt = scenes[i], scenes[i + 1]
        cur_id = cur.get("id") or f"scene_{i}"
        nxt_id = nxt.get("id") or f"scene_{i + 1}"
        energy = "high" if _camera_energy(cur) or cur.get("hero_moment") else "low"

        if style == "documentary":
            transition = _documentary_choice(cur, nxt)
        else:
            transition = _cinematic_choice(cur, nxt)

        suggestion: dict[str, Any] = {
            "from_scene": cur_id,
            "to_scene": nxt_id,
            "style": style,
            "energy": energy,
            "suggested_transition": transition,
        }
        if style == "cinematic" and energy == "high" and transition != "cut":
            suggestion["negative_gap_seconds"] = 0.4
            suggestion["note"] = "高能切点：建议负空隙重叠制造张力"
        elif transition == "cut":
            # cut 与负空隙互斥：cut 就是零重叠硬切，再挂 negative_gap 会让
            # 装配端误判成"需要转场"。高能但判定为 cut 时只提示，不给重叠。
            if energy == "high":
                suggestion["note"] = "高能硬切：不加重叠，靠剪辑节奏制造张力"
        else:
            suggestion["note"] = "情绪/时间过渡"
        suggestions.append(suggestion)
    return suggestions


def _camera_energy(scene: dict[str, Any]) -> bool:
    movement = (scene.get("shot_language") or {}).get("camera_movement")
    return movement in _HIGH_ENERGY_CAMERA


def _documentary_choice(cur: dict[str, Any], nxt: dict[str, Any]) -> str:
    cur_role = cur.get("narrative_role", "")
    nxt_role = nxt.get("narrative_role", "")
    if cur_role == "resolution" or nxt_role == "resolution":
        return "fade"
    if cur_role == "transition" or nxt_role == "transition":
        return "crossfade"
    return "cut"


def _cinematic_choice(cur: dict[str, Any], nxt: dict[str, Any]) -> str:
    cur_role = cur.get("narrative_role", "")
    nxt_role = nxt.get("narrative_role", "")
    if nxt.get("hero_moment") or nxt_role in _TENSION_BUILDERS:
        return "zoom_punch" if not _camera_energy(cur) else "cut"
    if cur_role == "emotional_beat" and nxt_role == "resolution":
        return "fade_black"
    if nxt.get("type") == "transition":
        return "crossfade"
    return "cut"


class EditAdvisor(BaseTool):
    name = "edit_advisor"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["scenes"],
        "properties": {
            "scenes": {
                "type": "array",
                "items": {"type": "object"},
                "description": "scene_plan 的 scenes[]（含 narrative_role/hero_moment/shot_language）",
            },
            "style": {"type": "string", "enum": ["cinematic", "documentary"], "default": "cinematic"},
            "project_dir": {"type": "string", "description": "未传 style 时读取管线 edit_style"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        scenes = inputs.get("scenes")
        if not isinstance(scenes, list) or len(scenes) < 2:
            return ToolResult(success=False, error="'scenes' 至少需要 2 个镜头")
        style = inputs.get("style")
        if not style and inputs.get("project_dir"):
            from montage.engine.policy import load_pipeline_settings

            style = load_pipeline_settings(inputs["project_dir"]).get("edit_style")
        if style not in ("cinematic", "documentary"):
            style = "cinematic"
        suggestions = suggest_transitions(scenes, style)
        return ToolResult(
            success=True,
            data={
                "style": style,
                "junctions": suggestions,
                "count": len(suggestions),
                "usage": "建议写入 edit_decisions.cuts[].transition_in/out；最终决策由 LLM 结合 edits 词条确定",
            },
        )
