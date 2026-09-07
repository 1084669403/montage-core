"""script_to_scene_plan — 剧本 → 分镜骨架（原创，确定性纯函数）。

契约：
- 返回 scene_plan 对象，默认不写磁盘。
- 1 个 sections[] → 1 个 scenes[]；该段拆成 N 个嵌套 shots[]。
- 各镜 duration 之和 = 场景 end_seconds - start_seconds。
- 嵌套 shot 形状对齐 visual_prompt_builder 的 shot 对象。
- character_registry 逐字复制 appearance / outfit。
- 已有 scene_plan 且 overwrite 不为 true → 拒绝覆盖。
"""

from __future__ import annotations

import math
import re
from typing import Any

from montage.engine.shot_language import fill_shot_language as apply_shot_language, language_for_shot
from montage.providers.capabilities import (
    policy_for_loop,
    policy_max,
    policy_step,
    snap_duration_seconds,
)
from montage.script_fields import flatten_environment, section_spoken_text
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_DEFAULT_WPS = 5.0
_GRID = 5.0
_MAX_SHOTS = 4
_SENT_SPLIT = re.compile(r"(?<=[。！？!?\n])")


def _playbook_dict(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict) and raw.get("id"):
        return raw
    if isinstance(raw, str) and raw.strip():
        from montage.playbooks import get_playbook

        return get_playbook(raw.strip())
    return None


def _section_duration(
    section: dict[str, Any],
    wps: float,
    policy: dict[str, Any] | None,
) -> float:
    given = float(section.get("duration_seconds") or 0)
    if given > 0:
        return given
    chars = len(section_spoken_text(section))
    needed = chars / wps if wps else chars / _DEFAULT_WPS
    return snap_duration_seconds(needed, policy)


def _narrative_role(index: int, total: int) -> str:
    if total <= 1:
        return "hook"
    frac = (index + 0.5) / total
    if frac < 0.15:
        return "hook"
    if frac < 0.65:
        return "escalation"
    if frac < 0.80:
        return "reveal"
    return "landing"


def _emotion(script: dict[str, Any], playbook: dict[str, Any] | None) -> str:
    tone = str(script.get("tone") or "").strip()
    if tone:
        return tone.replace("，", "、").split("、")[0].strip()[:16]
    mood = str(((playbook or {}).get("identity") or {}).get("mood") or "").strip()
    if mood:
        return mood.split("，")[0].strip()[:16]
    return ""


def _character_registry(script: dict[str, Any]) -> list[dict[str, Any]]:
    registry: list[dict[str, Any]] = []
    for char in script.get("characters") or []:
        if not isinstance(char, dict) or not char.get("id"):
            continue
        registry.append({
            "id": char["id"],
            "appearance": str(char.get("appearance") or ""),
            "outfit_anchor": str(char.get("outfit") or ""),
        })
    return registry


def _objects(script: dict[str, Any]) -> list[dict[str, str]]:
    objects: list[dict[str, str]] = []
    for prop in script.get("props") or []:
        if isinstance(prop, dict):
            appearance = str(prop.get("appearance") or prop.get("name") or prop.get("id") or "").strip()
            if appearance:
                objects.append({
                    "id": str(prop.get("id") or prop.get("name") or appearance),
                    "appearance": appearance,
                })
        elif isinstance(prop, str) and prop.strip():
            objects.append({"id": prop.strip(), "appearance": prop.strip()})
    return objects


def _units(section: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    """切分对白单元。无 lines 时按标点切 narration，并标记降级。"""
    lines = section.get("lines") or []
    usable = [
        item for item in lines
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    if usable:
        units = [{"kind": "line", "line": item, "text": str(item.get("text") or "")} for item in usable]
        return units, False
    narration = str(section.get("narration") or "").strip()
    if not narration:
        return [{"kind": "empty", "text": "", "line": None}], False
    parts = [p.strip() for p in _SENT_SPLIT.split(narration) if p and p.strip()]
    if not parts:
        parts = [narration]
    units = [{"kind": "narration", "text": p, "line": None} for p in parts]
    return units, True


def _group_units(units: list[dict[str, Any]], n_shots: int) -> list[list[dict[str, Any]]]:
    n_shots = max(1, n_shots)
    if not units:
        return [[{"kind": "empty", "text": "", "line": None}]]
    groups: list[list[dict[str, Any]]] = [[] for _ in range(n_shots)]
    for i, unit in enumerate(units):
        groups[min(i * n_shots // len(units), n_shots - 1)].append(unit)
    filled: list[list[dict[str, Any]]] = []
    last = list(units)
    for group in groups:
        if group:
            last = group
            filled.append(group)
        else:
            filled.append(list(last))
    return filled or [units]


def _shot_durations(total: float, n: int, step: float | None) -> list[float]:
    n = max(1, n)
    if n == 1:
        return [round(total, 3)]
    if step is None or step <= 0:
        base = total / n
        slots = [base] * (n - 1)
        slots.append(total - sum(slots))
        return [round(x, 3) for x in slots]
    slots = [step] * n
    remaining = total - step * n
    i = 0
    while remaining >= step - 1e-6:
        slots[i % n] += step
        remaining -= step
        i += 1
    slots[-1] = round(slots[-1] + remaining, 3)
    drift = round(total - sum(slots), 3)
    slots[-1] = round(slots[-1] + drift, 3)
    return [round(x, 3) for x in slots]


def _n_shots(duration: float, n_units: int, policy: dict[str, Any] | None) -> int:
    unit_n = max(1, n_units)
    if policy is None:
        max_by_grid = max(1, int(duration // _GRID))
        return min(_MAX_SHOTS, max_by_grid, unit_n)
    kind = str(policy.get("kind") or "")
    if kind == "none":
        return min(_MAX_SHOTS, unit_n)
    step = policy_step(policy)
    max_one = policy_max(policy)
    max_by_grid = max(1, int(duration // step)) if step else unit_n
    need = 1
    if max_one and duration > max_one + 1e-9:
        need = max(1, math.ceil(duration / max_one - 1e-9))
    return min(_MAX_SHOTS, max(need, min(max_by_grid, unit_n)))


def _shot_language(
    index: int,
    total: int,
    narrative_role: str = "",
    playbook: dict[str, Any] | None = None,
    *,
    fill: bool = True,
    defer: bool = False,
) -> dict[str, Any]:
    return language_for_shot(
        index, total, narrative_role, playbook, fill=fill, defer=defer,
    )


def _action_for_units(units: list[dict[str, Any]]) -> dict[str, str]:
    if any(u.get("kind") == "line" for u in units):
        return {"verb": "说话", "manner": "口型清晰"}
    return {"verb": "站立", "manner": "可见停顿"}


def convert_script_to_scene_plan(
    script: dict[str, Any],
    playbook: dict[str, Any] | None = None,
    *,
    words_per_second: float = _DEFAULT_WPS,
    duration_policy: dict[str, Any] | None = None,
    fill_shot_language: bool = True,
    defer_camera_fill: bool = False,
) -> dict[str, Any]:
    """纯函数：script → {scene_plan, findings}。

    duration_policy=None 未传 → 保持 5/10 网格。显式 {kind:none} 才不 snap。
    fill_shot_language=False 时运镜回落 static（冻结用词）。
    defer_camera_fill=True 时只写景别，留给 overlay 后再 fill（compile 用）。
    """
    findings: list[dict[str, str]] = []
    pb = playbook if isinstance(playbook, dict) else None
    env_text = flatten_environment(script.get("environment"))
    lighting = ""
    env_obj = script.get("environment")
    if isinstance(env_obj, dict):
        lighting = str(env_obj.get("lighting") or "").strip()
    objects = _objects(script)
    chars = [c for c in (script.get("characters") or []) if isinstance(c, dict) and c.get("id")]
    char_by_id = {c["id"]: c for c in chars}
    emotion = _emotion(script, pb)
    sections = script.get("sections") or []
    if not sections:
        findings.append({
            "severity": "warning",
            "stage": "scene_plan",
            "field": "sections",
            "message": "剧本无 sections，无法拆镜头",
            "proposed_fix": "至少写一段 id + narration/lines",
        })

    scenes: list[dict[str, Any]] = []
    cursor = 0.0
    total = len(sections)
    for idx, section in enumerate(sections):
        if not isinstance(section, dict):
            continue
        sid = str(section.get("id") or f"sc{idx + 1:02d}")
        # 每场可自带 environment：优先用本场环境覆盖全局/首场环境，避免
        # 首场（如室外黄昏）污染室内夜戏的视觉细节与灯光。
        sec_env = section.get("environment")
        sec_env_text = flatten_environment(sec_env)
        sec_lighting = ""
        if isinstance(sec_env, dict):
            sec_lighting = str(sec_env.get("lighting") or "").strip()
        section_env_text = sec_env_text or env_text
        section_lighting = sec_lighting or lighting
        duration = _section_duration(section, words_per_second, duration_policy)
        units, degraded = _units(section)
        if degraded:
            findings.append({
                "severity": "warning",
                "stage": "scene_plan",
                "field": f"sections[{idx}].lines",
                "message": f"{sid} 无 lines[]，按 narration 标点降级切分",
                "proposed_fix": "补 lines[{speaker_id, text}] 后再转换",
            })
        n_shots = _n_shots(duration, len(units), duration_policy)
        groups = _group_units(units, n_shots)
        durs = _shot_durations(duration, len(groups), policy_step(duration_policy))
        role = _narrative_role(idx, total)
        lang_role = role if fill_shot_language else ""
        lang_pb = pb if fill_shot_language else None
        speakers: list[str] = []
        for unit in units:
            line = unit.get("line") or {}
            sp = str(line.get("speaker_id") or "").strip()
            if sp and sp != "narrator":
                speakers.append(sp)
        character_ids = []
        for sp in speakers:
            if sp not in character_ids:
                character_ids.append(sp)
        if not character_ids:
            character_ids = [c["id"] for c in chars]

        shots: list[dict[str, Any]] = []
        spoken_bits = [u.get("text") or "" for u in units]
        description = section_env_text or str(section.get("narration") or sid)
        if spoken_bits:
            description = (section_env_text + "。" if section_env_text else "") + spoken_bits[0]

        for ji, group in enumerate(groups):
            shot_dur = durs[ji] if ji < len(durs) else durs[-1]
            dialogue: list[dict[str, Any]] = []
            for unit in group:
                line = unit.get("line")
                if isinstance(line, dict) and str(line.get("text") or "").strip():
                    dialogue.append({
                        "role": str(line.get("speaker_id") or "narrator"),
                        "dialogue_text": str(line.get("text") or ""),
                        "delivery": str(line.get("delivery") or ""),
                    })
            subjects: list[dict[str, Any]] = []
            seen_sub: set[str] = set()
            for unit in group:
                line = unit.get("line") or {}
                cid = str(line.get("speaker_id") or "").strip()
                if cid and cid != "narrator" and cid not in seen_sub:
                    char = char_by_id.get(cid) or {}
                    subjects.append({
                        "id": cid,
                        "appearance_anchor": str(char.get("appearance") or ""),
                        "action": _action_for_units(group),
                    })
                    seen_sub.add(cid)
            if not subjects:
                for cid in character_ids[:2]:
                    char = char_by_id.get(cid) or {}
                    subjects.append({
                        "id": cid,
                        "appearance_anchor": str(char.get("appearance") or ""),
                        "action": _action_for_units(group),
                    })
            vd: dict[str, Any] = {
                "environment": section_env_text,
                "subjects": subjects,
            }
            if section_lighting:
                vd["lighting"] = section_lighting
            if objects:
                vd["objects"] = objects
            shot: dict[str, Any] = {
                "shot_id": f"{sid}_{ji + 1:02d}",
                "shot_kind": "video",
                "duration_seconds": shot_dur,
                "visual_details": vd,
                "shot_language": _shot_language(
                    ji, len(groups), lang_role, lang_pb,
                    fill=fill_shot_language, defer=defer_camera_fill,
                ),
                "cut": "bridge",
            }
            if dialogue:
                shot["audio_prompt"] = {"dialogue": dialogue}
            shots.append(shot)

        start = round(cursor, 3)
        end = round(cursor + duration, 3)
        scenes.append({
            "id": sid,
            "description": description,
            "narrative_role": role,
            "start_seconds": start,
            "end_seconds": end,
            "character_ids": character_ids,
            "shot_language": _shot_language(
                0, len(shots), lang_role, lang_pb,
                fill=fill_shot_language, defer=defer_camera_fill,
            ),
            "emotion": emotion,
            "shots": shots,
        })
        cursor = end

    scene_plan = {
        "scenes": scenes,
        "character_registry": _character_registry(script),
    }
    if fill_shot_language and not defer_camera_fill:
        findings.extend(apply_shot_language(scene_plan, pb, enabled=True))
    return {"scene_plan": scene_plan, "findings": findings}


class ScriptToScenePlan(BaseTool):
    name = "script_to_scene_plan"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["script"],
        "properties": {
            "script": {"type": "object"},
            "playbook": {
                "description": "playbook id 或 dict",
            },
            "scene_plan": {
                "type": "object",
                "description": "已有分镜；存在且 overwrite 不为 true 时拒绝覆盖",
            },
            "overwrite": {"type": "boolean", "default": False},
            "words_per_second": {"type": "number", "default": 5.0},
            "duration_policy": {"type": "object"},
            "video_loop": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        script = inputs.get("script")
        if not isinstance(script, dict):
            return ToolResult(success=False, error="'script' 必填")
        existing = inputs.get("scene_plan")
        overwrite = bool(inputs.get("overwrite"))
        policy = inputs.get("duration_policy")
        if not isinstance(policy, dict):
            if "video_loop" in inputs:
                policy = policy_for_loop(inputs.get("video_loop"))
            else:
                policy = None
        wps = float(inputs.get("words_per_second") or _DEFAULT_WPS)
        if isinstance(existing, dict) and (existing.get("scenes") or existing.get("character_registry")) and not overwrite:
            proposed = convert_script_to_scene_plan(
                script,
                _playbook_dict(inputs.get("playbook")),
                words_per_second=wps,
                duration_policy=policy,
            )
            old_ids = [s.get("id") for s in (existing.get("scenes") or []) if isinstance(s, dict)]
            new_ids = [s.get("id") for s in proposed["scene_plan"].get("scenes") or []]
            return ToolResult(
                success=False,
                error="scene_plan 已存在，拒绝覆盖（传入 overwrite=true 才重算）",
                data={
                    "refused": True,
                    "existing_scene_ids": old_ids,
                    "proposed_scene_ids": new_ids,
                    "findings": proposed.get("findings") or [],
                },
            )
        result = convert_script_to_scene_plan(
            script,
            _playbook_dict(inputs.get("playbook")),
            words_per_second=wps,
            duration_policy=policy,
        )
        return ToolResult(
            success=True,
            data=result,
            meta={"scenes": len(result["scene_plan"].get("scenes") or [])},
        )
