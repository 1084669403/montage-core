"""script_validator — 剧本/分镜确定性质量校验（原创实现）。

校验剧本产物在进入下一阶段前的硬性质量问题，思路借鉴"剧本阶段算死对白预算"
与"可拍性铁律"（详见 docs/DIRECTOR_GUIDE.md），实现为确定性规则：

- purpose=dialogue_budget 对白预算：口播密度（默认 5 字/秒，4-6 可调）核算每段
  旁白/对白是否装进时长；即梦闭环额外校验 5s/10s 时长网格，超载返回拆场建议。
- purpose=filmability 可拍性：扫描场景/动作描述中的心理动词（觉得/感到/挣扎…），
  心理状态不可拍，必须改写为可见物理动作。
- purpose=character_refs 人物引用：scene_plan 的 character_ids / character_registry
  必须能指到 script.characters[].id；外观锚点逐字复制校验（registry.appearance
  必须等于 characters[].appearance，防止分镜阶段改写人物）。
- purpose=all（默认）：跑全部已实现的检查（含 completeness；shot_completeness
  在提供 scene_plan 时跑；composition / beat_coverage 只出 warning/suggestion）。

返回 {pass, findings[]}；findings 每项含 severity(critical/suggestion/warning) /
stage / field / message / proposed_fix。critical 数量 > 0 时 pass=False。

completeness / shot_completeness 在 P0 **默认只出 warning**，不挡 completed 门禁。
P1 起才按 playbook.script_style 把部分项升为 critical。
"""

from __future__ import annotations

import re
from typing import Any

from montage.engine.bible import blocking_position, normalize_blocking
from montage.engine.shot_budget import HERO_CAP, normalize_class, shot_duration
from montage.tools._shot_constants import _MAX_FORMS
from montage.engine.shot_language import (
    CANONICAL_BEATS,
    CLOSE_SIZES,
    WIDE_SIZES,
    canonical_role,
)
from montage.providers.capabilities import policy_for_loop, policy_max
from montage.script_fields import flatten_environment, section_spoken_text
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

# 心理动词/心理状态词：出现在"动作/场景描述"里即不可拍（旁白 narration 除外）。
_PSYCH_WORDS = (
    "觉得", "感到", "认为", "想起", "害怕", "恐惧", "担心", "担忧", "挣扎",
    "犹豫", "渴望", "后悔", "意识到", "明白", "疑惑", "怀疑", "内心", "心情",
    "情绪", "心理", "回忆", "幻想", "希望", "绝望", "孤独", "愤怒地想着",
)

# 口播密度默认值（字/秒）：5s 镜段 ≈ 20-30 字；10s 镜段 ≈ 40-50 字。
_DEFAULT_WPS = 5.0

# 与 capabilities.policy_for_loop 同一张表
def _policy_grid(policy: dict[str, Any]) -> tuple[int, ...]:
    kind = str((policy or {}).get("kind") or "")
    if kind == "enum":
        return tuple(int(v) for v in (policy.get("values") or []))
    if kind == "range":
        try:
            lo = int(policy.get("min"))
            hi = int(policy.get("max"))
        except (TypeError, ValueError):
            return ()
        step = int(policy.get("step") or 1) or 1
        if hi < lo:
            return ()
        return tuple(range(lo, hi + 1, step))
    return ()


JIMENG_GRID = _policy_grid(policy_for_loop("jimeng"))
AGNES_GRID = _policy_grid(policy_for_loop("agnes"))


def _count_chars(text: str) -> int:
    """计数字符数（中文按字符，含标点——保守估算口播量）。"""
    return len(text)


def normalize_video_loop(raw: Any) -> str:
    """W1：jimeng ↔ volcengine；kling 跟 Omni 3–15；ark/seedance → ark；其余不套即梦网格。"""
    val = str(raw or "none").strip().lower()
    if val in ("jimeng", "volcengine"):
        return "jimeng"
    if val == "agnes":
        return "agnes"
    if val == "kling":
        return "kling"
    if val in ("ark", "seedance"):
        return "ark"
    return "none"


def resolve_video_loop(raw: Any, project_dir: Any = None) -> str:
    """输入优先；空则从 proposal_packet.load_loop_policy 读。"""
    text = str(raw or "").strip()
    if text and text.lower() != "none":
        return normalize_video_loop(text)
    if project_dir:
        from montage.engine.policy import load_loop_policy

        loop = load_loop_policy(project_dir).get("video_loop")
        if loop:
            return normalize_video_loop(loop)
    return "none"


def check_dialogue_budget(
    script: dict[str, Any],
    *,
    video_loop: str = "none",
    words_per_second: float = _DEFAULT_WPS,
) -> list[dict[str, str]]:
    """核算每段旁白/对白是否装进时长；即梦额外校验 5/10s 网格。"""
    findings: list[dict[str, str]] = []
    video_loop = normalize_video_loop(video_loop)
    sections = script.get("sections") or []
    for idx, sec in enumerate(sections):
        sid = sec.get("id") or f"section_{idx}"
        spoken = section_spoken_text(sec)
        narration = sec.get("narration") or ""
        duration = float(sec.get("duration_seconds") or 0)
        if not spoken:
            continue
        if duration <= 0:
            findings.append({
                "severity": "critical",
                "stage": "script",
                "field": f"sections[{idx}].duration_seconds",
                "message": f"{sid} 缺少时长，无法核算对白预算",
                "proposed_fix": f"为 {sid} 设置 duration_seconds（按口播 5 字/秒估算）",
            })
            continue

        chars = _count_chars(spoken)
        budget = duration * words_per_second
        if chars > budget:
            over = chars - budget
            findings.append({
                "severity": "critical",
                "stage": "script",
                "field": f"sections[{idx}].narration",
                "message": (
                    f"{sid} 旁白 {chars} 字超过 {duration:.0f}s 口播预算 "
                    f"（{words_per_second:.0f} 字/秒 ≈ {budget:.0f} 字），超 {over:.0f} 字"
                ),
                "proposed_fix": (
                    f"方案A：压缩 {sid} 旁白至 {budget:.0f} 字以内；"
                    f"方案B：把 {sid} 拆为两段（各 {budget / 2:.0f} 字）并加时长"
                ),
            })

        lines = sec.get("lines") or []
        if lines and narration:
            line_chars = _count_chars(section_spoken_text({"lines": lines}))
            nar_chars = _count_chars(str(narration))
            denom = max(nar_chars, line_chars, 1)
            if abs(line_chars - nar_chars) / denom > 0.3:
                findings.append({
                    "severity": "warning",
                    "stage": "script",
                    "field": f"sections[{idx}].lines",
                    "message": (
                        f"{sid} lines 字数 {line_chars} 与 narration {nar_chars} 偏离超过 30%"
                    ),
                    "proposed_fix": "以 lines[] 为准同步 narration，或删掉过时的 narration",
                })

        policy = policy_for_loop(video_loop)
        values = tuple(int(v) for v in (policy.get("values") or [])) if policy.get("kind") == "enum" else ()
        max_one = policy_max(policy)
        if video_loop == "agnes":
            if max_one and duration > max_one:
                findings.append({
                    "severity": "suggestion",
                    "stage": "script",
                    "field": f"sections[{idx}].duration_seconds",
                    "message": f"{sid} 时长 {duration:.0f}s 超过 Agnes 约 {max_one:.0f}s 上限",
                    "proposed_fix": f"拆成不超过 {max_one:.0f}s 的两段",
                })
        if video_loop == "jimeng":
            if duration not in values and max_one and duration % max_one != 0:
                findings.append({
                    "severity": "suggestion",
                    "stage": "script",
                    "field": f"sections[{idx}].duration_seconds",
                    "message": f"{sid} 时长 {duration:.0f}s 不在即梦 5/10s 网格内",
                    "proposed_fix": f"调整为 5s 或 10s 或其整数倍（当前 {duration:.0f}s → 10s 或拆段）",
                })
            if max_one and chars > max_one * words_per_second:
                findings.append({
                    "severity": "critical",
                    "stage": "script",
                    "field": f"sections[{idx}].narration",
                    "message": f"{sid} 对白 {chars} 字超过单镜上限（{max_one:.0f}s ≈ {max_one * words_per_second:.0f} 字），生成期必然超镜",
                    "proposed_fix": f"在 script 阶段把 {sid} 拆成两镜（每镜 ≤ {max_one * words_per_second:.0f} 字）",
                })
        if video_loop == "ark":
            if max_one and duration > max_one:
                findings.append({
                    "severity": "suggestion",
                    "stage": "script",
                    "field": f"sections[{idx}].duration_seconds",
                    "message": f"{sid} 时长 {duration:.0f}s 超过 Seedance 单次约 {max_one:.0f}s",
                    "proposed_fix": f"拆成不超过 {max_one:.0f}s 的两段，不要把多场拼进一次调用",
                })
    return findings


def check_filmability(scene_plan: dict[str, Any]) -> list[dict[str, str]]:
    """扫描场景/动作描述中的心理动词：心理不可拍，须改写为可见动作。"""
    findings: list[dict[str, str]] = []
    scenes = scene_plan.get("scenes") or []
    for idx, scene in enumerate(scenes):
        sid = scene.get("id") or f"scene_{idx}"
        text = " ".join(
            str(scene.get(field) or "")
            for field in ("description",)
        )
        # 镜头级动作描述也检查（assets 阶段之前可用 scene_plan 粗查）
        hits = [w for w in _PSYCH_WORDS if w in text]
        if hits:
            findings.append({
                "severity": "warning",
                "stage": "scene_plan",
                "field": f"scenes[{idx}].description",
                "message": f"{sid} 描述含心理状态词（{'/'.join(hits[:4])}）：心理不可拍",
                "proposed_fix": (
                    f"改写为可见物理动作（例：'她感到害怕' → '她后退两步，手扶墙，"
                    f"指节发白'），情绪交给 shot_language 与对白"
                ),
            })
    return findings


def check_character_refs(
    script: dict[str, Any],
    scene_plan: dict[str, Any],
) -> list[dict[str, str]]:
    """人物引用一致性：character_ids / character_registry 指到 script.characters[].id，
    外观锚点逐字复制（禁止在分镜阶段改写人物）。"""
    findings: list[dict[str, str]] = []
    chars = script.get("characters") or []
    char_ids = {c.get("id") for c in chars if c.get("id")}

    registry = scene_plan.get("character_registry") or []
    reg_ids = {r.get("id") for r in registry if r.get("id")}

    # 1) registry 里的角色必须存在于 script.characters
    for rid in sorted(reg_ids - char_ids):
        findings.append({
            "severity": "critical",
            "stage": "scene_plan",
            "field": "character_registry",
            "message": f"角色 {rid} 在 character_registry 但不在 script.characters 中",
            "proposed_fix": f"在 script.characters 补人物卡 {rid}，或从 registry 删除",
        })

    # 2) 场景出镜角色必须存在于 script.characters
    for idx, scene in enumerate(scene_plan.get("scenes") or []):
        cids = set(scene.get("character_ids") or [])
        for cid in sorted(cids - char_ids):
            findings.append({
                "severity": "critical",
                "stage": "scene_plan",
                "field": f"scenes[{idx}].character_ids",
                "message": f"场景 {scene.get('id', idx)} 引用未知角色 {cid}",
                "proposed_fix": f"修正 character_ids（可选项：{sorted(char_ids)}）",
            })

    # 3) 外观锚点逐字复制校验（appearance / outfit_anchor）
    char_by_id = {c.get("id"): c for c in chars}
    for reg in registry:
        rid = reg.get("id")
        char = char_by_id.get(rid)
        if not char:
            continue
        for key, reg_key in (("appearance", "appearance"), ("outfit", "outfit_anchor")):
            src = str(char.get(key) or "").strip()
            dst = str(reg.get(reg_key) or "").strip()
            if src and dst and src != dst:
                findings.append({
                    "severity": "warning",
                    "stage": "scene_plan",
                    "field": f"character_registry[{rid}].{reg_key}",
                    "message": f"{rid} 的 {reg_key} 与人物卡 {key} 不一致（分镜阶段改写人物）",
                    "proposed_fix": f"逐字复制人物卡：{reg_key} = '{src}'",
                })

    return findings


def check_subject_forms(
    scene_plan: dict[str, Any],
    script: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """每镜 subjects[].form_id 必须是该角色 forms[].id；同镜同角色多形态给 warning。

    forms[] 优先读 character_registry（分镜镜像），回落 script/bible.characters。
    """
    findings: list[dict[str, str]] = []
    sources = (
        scene_plan.get("character_registry") or [],
        (script or {}).get("characters") or [],
    )

    def _forms_of(cid: str) -> list[str]:
        for source in sources:
            for char in source:
                if not isinstance(char, dict) or str(char.get("id") or "") != cid:
                    continue
                ids = [
                    str(form.get("id")).strip()
                    for form in char.get("forms") or []
                    if isinstance(form, dict) and str(form.get("id") or "").strip()
                ]
                if ids:
                    return ids
        return []

    for si, scene in enumerate(scene_plan.get("scenes") or []):
        if not isinstance(scene, dict):
            continue
        for ji, shot in enumerate(scene.get("shots") or []):
            if not isinstance(shot, dict):
                continue
            vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
            label = str(shot.get("shot_id") or f"{scene.get('id') or si}_{ji + 1}")
            # scene_plan 的 subjects 在 visual_details 下；bible 镜在 shot 顶层。
            subjects = vd.get("subjects") or shot.get("subjects") or []
            declared: dict[str, list[str]] = {}
            for sub in subjects:
                if not isinstance(sub, dict):
                    continue
                cid = str(sub.get("id") or "").strip()
                fid = str(sub.get("form_id") or "").strip()
                if not cid or not fid:
                    continue
                valid = _forms_of(cid)
                if fid not in valid:
                    findings.append({
                        "severity": "critical",
                        "stage": "scene_plan",
                        "field": f"scenes[{si}].shots[{ji}].subjects[].form_id",
                        "message": f"{label} 的角色 {cid} 声明未知形态 {fid}",
                        "proposed_fix": f"改用已声明形态：{valid or f'{cid} 无 forms[]'}",
                    })
                declared.setdefault(cid, [])
                if fid not in declared[cid]:
                    declared[cid].append(fid)
            for cid, fids in declared.items():
                if len(fids) > 1:
                    findings.append({
                        "severity": "warning",
                        "stage": "scene_plan",
                        "field": f"scenes[{si}].shots[{ji}].visual_details.subjects",
                        "message": (
                            f"{label} 同镜 {cid} 声明 {len(fids)} 个形态"
                            f"（{', '.join(fids)}），会多占参考图名额"
                        ),
                        "proposed_fix": "同镜只保留一个形态，或拆镜",
                    })
    return findings


def check_completeness(
    script: dict[str, Any],
    script_style: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """剧本要素完整性。无 script_style 时全是 warning；P1 起按 require_* 升 critical。"""
    findings: list[dict[str, str]] = []
    style = script_style if isinstance(script_style, dict) else {}

    def severity(flag: str) -> str:
        return "critical" if style.get(flag) else "warning"

    env_text = flatten_environment(script.get("environment"))
    if not env_text:
        findings.append({
            "severity": severity("require_environment"),
            "stage": "script",
            "field": "environment",
            "message": "缺少 environment（地点/空间/光线/色调/时代/氛围）",
            "proposed_fix": "填写 script.environment 对象或一句环境描述",
        })

    structure = script.get("structure") or {}
    if not isinstance(structure, dict) or not str(structure.get("hook") or "").strip():
        findings.append({
            "severity": severity("require_structure"),
            "stage": "script",
            "field": "structure.hook",
            "message": "缺少 structure.hook 节拍",
            "proposed_fix": "补四拍地图 hook/escalation/reveal/landing（短片够用）",
        })

    chars = script.get("characters") or []
    narrative = bool(chars) or bool(structure)
    if not chars:
        if style.get("require_characters"):
            findings.append({
                "severity": "critical",
                "stage": "script",
                "field": "characters",
                "message": "该风格要求人物卡",
                "proposed_fix": "在 script.characters[] 建立人物卡",
            })
        elif narrative:
            findings.append({
                "severity": "warning",
                "stage": "script",
                "field": "characters",
                "message": "有节拍地图但无人物卡",
                "proposed_fix": "叙事片在 script.characters[] 建立人物卡",
            })

    char_ids = {c.get("id") for c in chars if isinstance(c, dict) and c.get("id")}
    speaker_sev = severity("require_speakers")
    for idx, sec in enumerate(script.get("sections") or []):
        sid = sec.get("id") or f"section_{idx}"
        lines = sec.get("lines") or []
        narration = str(sec.get("narration") or "").strip()
        if narration and not lines:
            findings.append({
                "severity": speaker_sev,
                "stage": "script",
                "field": f"sections[{idx}].lines",
                "message": f"{sid} 有 narration 但无 lines[]（对白无法指到说话人）",
                "proposed_fix": "按句拆成 lines[{speaker_id, text}]；旁白 speaker_id=narrator",
            })
            continue
        for li, line in enumerate(lines):
            if not isinstance(line, dict):
                continue
            speaker = str(line.get("speaker_id") or "").strip()
            text = str(line.get("text") or "").strip()
            if text and not speaker:
                findings.append({
                    "severity": speaker_sev,
                    "stage": "script",
                    "field": f"sections[{idx}].lines[{li}].speaker_id",
                    "message": f"{sid} 对白无 speaker_id",
                    "proposed_fix": "填写 characters[].id 或 narrator",
                })
            elif speaker and char_ids and speaker not in char_ids and speaker != "narrator":
                findings.append({
                    "severity": "warning",
                    "stage": "script",
                    "field": f"sections[{idx}].lines[{li}].speaker_id",
                    "message": f"{sid} 说话人 {speaker} 不在 characters[]",
                    "proposed_fix": f"改成已知角色 id 或补人物卡（可选项：{sorted(char_ids)}）",
                })
    return findings


def _iter_shots(scene_plan: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """收集 scene_plan 嵌套 shots，字段路径供 findings 使用。"""
    collected: list[tuple[str, dict[str, Any]]] = []
    for si, scene in enumerate(scene_plan.get("scenes") or []):
        shots = scene.get("shots") or []
        sid = scene.get("id") or f"scene_{si}"
        if not shots:
            continue
        for ji, shot in enumerate(shots):
            if isinstance(shot, dict):
                collected.append((f"scenes[{si}].shots[{ji}]", shot | {"_scene_id": sid}))
    return collected


def check_presence_and_continuity(
    scene_plan: dict[str, Any],
) -> list[dict[str, str]]:
    """B2.5：逐镜在场清单（人物/道具/场景方位）与承接表门禁。

    规则：
    - 每镜必须有 presence.location.id；
    - 每镜必须有 characters[]，或是空镜并给 empty_reason；
    - props[].holder（若有）必须是本镜在场人物；
    - 承接：上镜在场且未标退场、本镜却缺失的人物/道具 → warning（模型漏项）。
    """
    from lib.shot_presence import build_ledger

    findings: list[dict[str, str]] = []
    ordered: list[dict[str, Any]] = []
    scenes_by_id: dict[str, dict[str, Any]] = {}
    for scene in scene_plan.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        sid = str(scene.get("id") or "").strip()
        scenes_by_id[sid] = scene
        for shot in scene.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            shot = dict(shot)
            shot.setdefault("scene_id", sid)
            ordered.append(shot)
    if not ordered:
        return findings
    registry = {
        str(row.get("id")): row
        for row in (scene_plan.get("character_registry") or [])
        if isinstance(row, dict) and row.get("id")
    }
    ledger = build_ledger(ordered, scenes_by_id=scenes_by_id, registry=registry)
    for shot in ordered:
        shot_id = str(shot.get("shot_id") or "")
        row = ledger["shots"].get(shot_id) or {}
        presence = row.get("presence") or {}
        if not (presence.get("location") or {}).get("id"):
            findings.append({
                "severity": "warning",
                "stage": "scene_plan",
                "field": f"{shot_id}.presence.location",
                "message": f"{shot_id} 在场清单缺场景（location.id）",
                "proposed_fix": "补 bible 该镜 presence.location.id（对应 locations[].id）",
            })
        characters = presence.get("characters") or []
        if not characters and not presence.get("empty_reason"):
            findings.append({
                "severity": "warning",
                "stage": "scene_plan",
                "field": f"{shot_id}.presence",
                "message": f"{shot_id} 既无人物也未说明是空镜",
                "proposed_fix": "补 presence.characters[]，或写 empty_reason",
            })
        on_stage = {str(c.get("id")) for c in characters}
        for prop in presence.get("props") or []:
            holder = str(prop.get("holder") or "").strip()
            if holder and holder not in on_stage:
                findings.append({
                    "severity": "warning",
                    "stage": "scene_plan",
                    "field": f"{shot_id}.presence.props",
                    "message": (
                        f"{shot_id} 道具 {prop.get('id')} 的持有者 {holder} "
                        "不在本镜人物清单里"
                    ),
                    "proposed_fix": "把持有者加进 characters[]，或改 props[].holder",
                })
        for gap in (row.get("continuity") or {}).get("missing") or []:
            findings.append({
                "severity": "warning",
                "stage": "scene_plan",
                "field": f"{shot_id}.continuity",
                "message": (
                    f"{shot_id} 承接缺失：{gap.get('kind')} {gap.get('id')}"
                    f"（{gap.get('why')}）"
                ),
                "proposed_fix": "本镜补写该实体，或在上镜标 exits:true",
            })
    return findings


def check_shot_completeness(
    scene_plan: dict[str, Any],
    video_loop: str = "none",
) -> list[dict[str, str]]:
    """镜头骨架完整性。P0 默认 warning/suggestion，不挡 completed。"""
    findings: list[dict[str, str]] = []
    shots = _iter_shots(scene_plan)
    if not shots:
        scenes = scene_plan.get("scenes") or []
        if scenes:
            findings.append({
                "severity": "warning",
                "stage": "scene_plan",
                "field": "scenes[].shots",
                "message": "scene_plan 无嵌套镜头骨架（scenes[].shots[] 为空）",
                "proposed_fix": "跑 script_to_scene_plan 生成骨架，或手写每场 shots[]",
            })
        return findings

    for path, shot in shots:
        vd = shot.get("visual_details") or {}
        if not isinstance(vd, dict):
            vd = {}
        env = flatten_environment(vd.get("environment"))
        shot_label = shot.get("shot_id") or shot.get("_scene_id") or path
        if not env:
            findings.append({
                "severity": "warning",
                "stage": "scene_plan",
                "field": f"{path}.visual_details.environment",
                "message": f"{shot_label} 缺少可拍环境",
                "proposed_fix": "从 script.environment 拍扁填入 visual_details.environment",
            })
        subjects = vd.get("subjects") or []
        has_action = False
        for sub in subjects:
            if not isinstance(sub, dict):
                continue
            action = sub.get("action")
            if isinstance(action, dict) and str(action.get("verb") or "").strip():
                has_action = True
                break
        if not has_action:
            findings.append({
                "severity": "warning",
                "stage": "scene_plan",
                "field": f"{path}.visual_details.subjects",
                "message": f"{shot_label} 缺少可拍动作（subjects[].action.verb）",
                "proposed_fix": "写成可见物理动作，避免心理动词",
            })
        duration = float(shot.get("duration_seconds") or 0)
        loop = normalize_video_loop(video_loop)
        policy = policy_for_loop(loop)
        values = tuple(float(v) for v in (policy.get("values") or [])) if policy.get("kind") == "enum" else ()
        max_one = policy_max(policy)
        if loop == "agnes" and max_one and duration > max_one:
            findings.append({
                "severity": "suggestion",
                "stage": "scene_plan",
                "field": f"{path}.duration_seconds",
                "message": f"{shot_label} 时长 {duration:.0f}s 超过 Agnes 约 {max_one:.0f}s",
                "proposed_fix": f"拆段或压到 {max_one:.0f}s 以内",
            })
        elif loop == "jimeng" and duration > 0 and duration not in values and max_one and duration % max_one != 0:
            findings.append({
                "severity": "suggestion",
                "stage": "scene_plan",
                "field": f"{path}.duration_seconds",
                "message": f"{shot_label} 时长 {duration:.0f}s 不在 5/10s 网格",
                "proposed_fix": "调整为 5 或 10 或其整数倍",
            })
    return findings


def _scene_duration(scene: dict[str, Any]) -> float:
    try:
        dur = float(scene.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        dur = 0.0
    if dur > 0:
        return dur
    try:
        start = float(scene.get("start_seconds") or 0)
        end = float(scene.get("end_seconds") or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(end - start, 0.0)


def check_shot_durations(
    scene_plan: dict[str, Any],
    video_loop: str = "none",
) -> list[dict[str, str]]:
    """逐镜时长断言：sum(shots) == 场景时长；有政策网格时每个值必须落格。

    复用 ``_policy_grid`` / ``AGNES_GRID``（此前是死代码）。不满足出 finding，
    把问题从"生成期才炸"前移到校验期。
    """
    findings: list[dict[str, str]] = []
    loop = normalize_video_loop(video_loop)
    grid = _policy_grid(policy_for_loop(loop))
    step = (grid[1] - grid[0]) if len(grid) > 1 else 1
    for sidx, scene in enumerate(scene_plan.get("scenes") or []):
        if not isinstance(scene, dict):
            continue
        sid = scene.get("id") or f"scene_{sidx}"
        shots = [s for s in (scene.get("shots") or []) if isinstance(s, dict)]
        if not shots:
            continue
        durs: list[tuple[str, float]] = []
        for j, shot in enumerate(shots):
            label = str(shot.get("shot_id") or f"{sid}_{j + 1:02d}")
            try:
                value = float(shot.get("duration_seconds") or 0)
            except (TypeError, ValueError):
                value = 0.0
            durs.append((label, value))
        total = round(sum(value for _, value in durs), 3)
        scene_dur = round(_scene_duration(scene), 3)
        if scene_dur > 0 and abs(total - scene_dur) > 0.05:
            findings.append({
                "severity": "warning",
                "stage": "scene_plan",
                "field": f"scenes[{sidx}].shots[].duration_seconds",
                "message": f"{sid} 各镜时长之和 {total:g}s ≠ 场景时长 {scene_dur:g}s",
                "proposed_fix": "按场景时长重算各镜（script_to_scene_plan 的权重分配应自动满足）",
            })
        if not grid:
            continue
        lo, hi = grid[0], grid[-1]
        for label, value in durs:
            if value <= 0:
                continue
            if int(value) != value or int(value) not in grid:
                findings.append({
                    "severity": "warning",
                    "stage": "scene_plan",
                    "field": f"scenes[{sidx}].shots[].duration_seconds",
                    "message": (
                        f"{label} 时长 {value:g}s 不在 {loop} 政策网格 "
                        f"{lo}–{hi}s（step {step}）内"
                    ),
                    "proposed_fix": f"调整为 {lo}–{hi}s 的整秒值",
                })
    return findings


def _shot_size_of(shot: dict[str, Any], scene: dict[str, Any]) -> str:
    sl = shot.get("shot_language") if isinstance(shot.get("shot_language"), dict) else {}
    size = str(sl.get("shot_size") or "").strip()
    if size:
        return size
    parent = scene.get("shot_language") if isinstance(scene.get("shot_language"), dict) else {}
    return str(parent.get("shot_size") or "").strip()


def _parse_subject_blocking(sub: dict[str, Any], shot_pos: str) -> dict[str, str] | None:
    """只认角色自己的站位。与镜级 blocking 相同的 position 视为 compile 盖章，跳过。"""
    own = normalize_blocking(sub.get("blocking"))
    if own:
        return own
    pos = str(sub.get("position") or "").strip()
    if not pos or pos == shot_pos:
        return None
    parts = pos.replace("，", "/").replace(",", "/").split("/")
    if len(parts) != 2:
        return None
    return normalize_blocking({"x": parts[0].strip(), "z": parts[1].strip()})


def check_composition(scene_plan: dict[str, Any]) -> list[dict[str, str]]:
    """景别×景深错配；遮挡只看角色自带 blocking。永不 critical。"""
    findings: list[dict[str, str]] = []
    for si, scene in enumerate(scene_plan.get("scenes") or []):
        if not isinstance(scene, dict):
            continue
        shots = [s for s in (scene.get("shots") or []) if isinstance(s, dict)]
        if not shots and isinstance(scene.get("blocking"), dict):
            shots = [scene]
        for ji, shot in enumerate(shots):
            label = str(shot.get("shot_id") or scene.get("id") or f"scenes[{si}].shots[{ji}]")
            path = f"scenes[{si}].shots[{ji}]" if shot is not scene else f"scenes[{si}]"
            size = _shot_size_of(shot, scene)
            blocking = normalize_blocking(shot.get("blocking") or scene.get("blocking"))
            depth = str((blocking or {}).get("z") or "")
            if size in CLOSE_SIZES and depth == "far":
                findings.append({
                    "severity": "warning",
                    "stage": "scene_plan",
                    "field": f"{path}.blocking",
                    "message": f"{label} 特写/近景但站位 far，有出画风险",
                    "proposed_fix": "改 shot_size 为 wide，或把 blocking.z 改为 near/mid",
                })
            if size in WIDE_SIZES and depth == "near":
                findings.append({
                    "severity": "suggestion",
                    "stage": "scene_plan",
                    "field": f"{path}.blocking",
                    "message": f"{label} 大远景但站位 near，主体可能顶满画面",
                    "proposed_fix": "空镜用 far/mid，或把景别收成 medium",
                })
            shot_pos = blocking_position(blocking)
            subjects = [
                s for s in ((shot.get("visual_details") or {}).get("subjects") or shot.get("subjects") or [])
                if isinstance(s, dict)
            ]
            explicit: list[tuple[str, dict[str, str]]] = []
            for sub in subjects:
                own = _parse_subject_blocking(sub, shot_pos)
                if own:
                    explicit.append((str(sub.get("id") or ""), own))
            for i, (lid, left) in enumerate(explicit):
                for rid, right in explicit[i + 1:]:
                    if left.get("x") == right.get("x") and left.get("z") == right.get("z"):
                        findings.append({
                            "severity": "warning",
                            "stage": "scene_plan",
                            "field": f"{path}.subjects",
                            "message": (
                                f"{label} 角色 {lid or i} 与 {rid or i + 1} "
                                f"站位重叠 {left.get('x')}/{left.get('z')}，有遮挡风险"
                            ),
                            "proposed_fix": "给其中一人换 x 或 z（left/center/right × near/mid/far）",
                        })
    return findings


def check_beat_coverage(
    script: dict[str, Any],
    scene_plan: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """structure 里写了的拍，分镜要有对应 narrative_role。永不 critical。"""
    findings: list[dict[str, str]] = []
    structure = script.get("structure") if isinstance(script.get("structure"), dict) else {}
    needed = [beat for beat in CANONICAL_BEATS if str(structure.get(beat) or "").strip()]
    if not needed:
        return findings
    scenes = [s for s in (scene_plan or {}).get("scenes") or [] if isinstance(s, dict)]
    if not scenes:
        scenes = [s for s in (script.get("sections") or []) if isinstance(s, dict)]
    present = {canonical_role(sc.get("narrative_role")) for sc in scenes}
    present.discard("")
    missing = [beat for beat in needed if beat not in present]
    if not missing:
        return findings
    sev = "suggestion" if len(scenes) <= 1 else "warning"
    findings.append({
        "severity": sev,
        "stage": "scene_plan" if scene_plan else "script",
        "field": "scenes[].narrative_role",
        "message": f"四拍 {', '.join(needed)} 中缺少 {', '.join(missing)}",
        "proposed_fix": (
            "给对应幕写 narrative_role（hook/escalation/reveal/landing，"
            "或 establish_context 等别名）；单幕短片可忽略"
        ),
    })
    return findings


def check_agnes_audio_prompts(
    shot_prompts: dict[str, Any],
    *,
    playbook: bool = False,
) -> list[dict[str, str]]:
    """agnes_prompt 镜头：video_prompt 应含【台词】/【声音】。默认 warning。"""
    findings: list[dict[str, str]] = []
    sev = "critical" if playbook else "warning"
    for i, shot in enumerate((shot_prompts or {}).get("shots") or []):
        if not isinstance(shot, dict):
            continue
        if str(shot.get("audio_source") or "") != "agnes_prompt":
            continue
        prompt = str(shot.get("video_prompt") or "")
        label = shot.get("shot_id") or f"shot_{i}"
        if "【台词】" not in prompt:
            findings.append({
                "severity": sev,
                "stage": "shot_prompts",
                "field": f"shots[{i}].video_prompt",
                "message": f"{label} audio_source=agnes_prompt 缺少【台词】",
                "proposed_fix": "agnes_audio=true 重建 video_prompt，写入对白全文",
            })
        if "【声音】" not in prompt:
            findings.append({
                "severity": "warning",
                "stage": "shot_prompts",
                "field": f"shots[{i}].video_prompt",
                "message": f"{label} audio_source=agnes_prompt 缺少【声音】",
                "proposed_fix": "补环境声/动作声小节，不要用 TTS 替代",
            })
    return findings


_APPEARANCE_STOP = (
    "东亚", "青年", "男性", "女性", "少年", "女孩", "男孩", "男子", "女子",
    "年轻人", "中年", "老人", "面容", "日常",
)
_EMPTY_ACTION_WORDS = ("打架", "打斗", "战斗", "很慌", "很紧张", "冲突", "搏斗", "开打")
_CONTACT_HINTS = ("领", "墙", "手", "肩", "衣", "门", "桌", "拳", "腕", "颈", "臂", "背")
_EMPTY_ENV_PHRASES = ("一个房间", "室内", "房间", "某处", "一个地方", "场景")


def _norm_face(text: str) -> str:
    return re.sub(r"[\s，。、,.;；：:]+", "", text or "")


def _appearance_tokens(text: str) -> set[str]:
    cleaned = text or ""
    for stop in _APPEARANCE_STOP:
        cleaned = cleaned.replace(stop, "")
    tokens: set[str] = set()
    for word in re.findall(r"[a-zA-Z][a-zA-Z0-9\-_]*", cleaned.lower()):
        if len(word) >= 2:
            tokens.add(word)
    for run in re.findall(r"[\u4e00-\u9fff]+", cleaned):
        if len(run) == 1:
            tokens.add(run)
        else:
            tokens.add(run)
            tokens.update(run[i:i + 2] for i in range(len(run) - 1))
    return {t for t in tokens if t not in _APPEARANCE_STOP}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def is_spoken_mode(
    bible: dict[str, Any] | None = None,
    format_card: dict[str, Any] | None = None,
    script_style: dict[str, Any] | None = None,
) -> bool:
    """口播跳过同质/站位/缺 appearance。"""
    style = script_style if isinstance(script_style, dict) else {}
    card = format_card if isinstance(format_card, dict) else {}
    doc = bible if isinstance(bible, dict) else {}
    chars = [c for c in (doc.get("characters") or []) if isinstance(c, dict)]
    if style.get("require_characters") is False and not chars:
        return True
    playbook = str(doc.get("playbook") or card.get("playbook") or "").strip()
    chosen = card.get("chosen") if isinstance(card.get("chosen"), dict) else {}
    playbook = playbook or str(chosen.get("playbook") or "")
    if playbook == "spoken_explain":
        return True
    medium = str(doc.get("medium") or card.get("medium") or chosen.get("medium") or "")
    return medium in ("spoken", "口播")


def _action_blob(action: dict[str, Any]) -> str:
    return "".join(str(action.get(k) or "") for k in ("verb", "contact", "body_part", "manner"))


def action_lacks_contact(action: dict[str, Any] | None) -> bool:
    """空动作：空词表动词且无接触点；「抓住衣领推向墙」因含接触暗示而通过。"""
    if not isinstance(action, dict):
        return True
    verb = str(action.get("verb") or "").strip()
    if not verb:
        return True
    has_fields = bool(str(action.get("contact") or "").strip() and str(action.get("body_part") or "").strip())
    has_hint = any(h in _action_blob(action) for h in _CONTACT_HINTS)
    empty = any(w in verb for w in _EMPTY_ACTION_WORDS)
    if empty and not has_fields and not has_hint:
        return True
    return False


def _check_character_forms(
    char: dict[str, Any],
    idx: int,
    *,
    video_loop: str = "",
) -> list[dict[str, str]]:
    """角色 forms[] 校验：id 非空/唯一、default 至多一个、数量上限、appearance 可回落。"""
    findings: list[dict[str, str]] = []
    forms = char.get("forms")
    if not isinstance(forms, list) or not forms:
        return findings
    cid = str(char.get("id") or f"char_{idx}")
    char_appearance = str(char.get("appearance") or "").strip()
    seen: set[str] = set()
    explicit = 0
    defaults = 0
    for fi, form in enumerate(forms):
        if not isinstance(form, dict):
            findings.append({
                "severity": "critical",
                "stage": "bible",
                "field": f"characters[{idx}].forms[{fi}]",
                "message": f"{cid} 的 forms[{fi}] 不是对象",
                "proposed_fix": "删掉或写成 {id, appearance, ...}",
            })
            continue
        fid = str(form.get("id") or "").strip()
        if not fid:
            findings.append({
                "severity": "critical",
                "stage": "bible",
                "field": f"characters[{idx}].forms[{fi}].id",
                "message": f"{cid} 的 forms[{fi}] 缺少非空 id",
                "proposed_fix": "补形态 id（如 human / ghost）",
            })
            continue
        explicit += 1
        if fid in seen:
            findings.append({
                "severity": "critical",
                "stage": "bible",
                "field": f"characters[{idx}].forms[{fi}].id",
                "message": f"{cid} 的形态 id {fid} 重复",
                "proposed_fix": "角色内形态 id 必须唯一",
            })
        seen.add(fid)
        if form.get("default") is True:
            defaults += 1
        if not char_appearance and not str(form.get("appearance") or "").strip():
            findings.append({
                "severity": "critical",
                "stage": "bible",
                "field": f"characters[{idx}].forms[{fi}].appearance",
                "message": f"{cid}.{fid} 既无形态 appearance 也无角色 appearance 可回落",
                "proposed_fix": "补形态 appearance，或补 characters[].appearance",
            })
    if explicit > _MAX_FORMS:
        findings.append({
            "severity": "critical",
            "stage": "bible",
            "field": f"characters[{idx}].forms",
            "message": f"{cid} 形态数 {explicit} 超过上限 {_MAX_FORMS}",
            "proposed_fix": "合并/删减形态，或分集处理",
        })
    if defaults > 1:
        findings.append({
            "severity": "critical",
            "stage": "bible",
            "field": f"characters[{idx}].forms",
            "message": f"{cid} 有 {defaults} 个 default=true 形态",
            "proposed_fix": "每角色至多一个 default=true",
        })
    if explicit and str(video_loop or "").strip().lower() == "kling":
        findings.append({
            "severity": "warning",
            "stage": "bible",
            "field": f"characters[{idx}].forms",
            "message": f"{cid} 声明了 forms，但可灵环忽略 forms（按单形态出 look_sheet）",
            "proposed_fix": "换 agnes 环，或去掉 forms",
        })
    return findings


def check_bible(
    bible: dict[str, Any],
    *,
    format_card: dict[str, Any] | None = None,
    playbook: dict[str, Any] | None = None,
    script_style: dict[str, Any] | None = None,
    video_loop: str = "",
) -> list[dict[str, str]]:
    """W1 圣经门禁。只由 purpose=bible 调用，不进 purpose=all。"""
    findings: list[dict[str, str]] = []
    if not isinstance(bible, dict):
        return [{
            "severity": "critical",
            "stage": "bible",
            "field": "series_bible",
            "message": "bible 不是对象",
            "proposed_fix": "写入 series_bible.json",
        }]
    style = script_style
    pb = playbook if isinstance(playbook, dict) else None
    if pb is None:
        name = str(playbook or "").strip() if isinstance(playbook, str) else ""
        name = name or str(bible.get("playbook") or "").strip()
        if name:
            from montage.playbooks import get_playbook

            pb = get_playbook(name)
    if style is None and isinstance(pb, dict):
        style = pb.get("script_style") if isinstance(pb.get("script_style"), dict) else None
    spoken = is_spoken_mode(bible, format_card, style if isinstance(style, dict) else None)
    chars = [c for c in (bible.get("characters") or []) if isinstance(c, dict)]
    default_face = ""
    if isinstance(pb, dict):
        default_face = str(((pb.get("asset_generation") or {}).get("character_appearance_default")) or "")

    if not spoken:
        for idx, char in enumerate(chars):
            cid = str(char.get("id") or f"char_{idx}")
            appearance = str(char.get("appearance") or "").strip()
            raw_forms = char.get("forms") if isinstance(char.get("forms"), list) else []
            form_appearances = [
                str(f.get("appearance") or "").strip()
                for f in raw_forms
                if isinstance(f, dict) and str(f.get("id") or "").strip()
            ]
            # 角色可把外观完全下放到形态；只有「角色空且形态也补不上」才 critical。
            covered_by_forms = bool(form_appearances) and all(form_appearances)
            if not appearance and not covered_by_forms:
                findings.append({
                    "severity": "critical",
                    "stage": "bible",
                    "field": f"characters[{idx}].appearance",
                    "message": f"{cid} 缺少独特 appearance",
                    "proposed_fix": "写可见外貌锚点（发色/疤/眼镜），禁止套 playbook 默认脸",
                })
            elif appearance and default_face and _norm_face(appearance) == _norm_face(default_face):
                findings.append({
                    "severity": "critical",
                    "stage": "bible",
                    "field": f"characters[{idx}].appearance",
                    "message": f"{cid} 外貌等于 playbook 默认脸",
                    "proposed_fix": "改成该角色独有的锚点，写入 contrast_notes",
                })
            findings.extend(_check_character_forms(char, idx, video_loop=video_loop))
        if len(chars) >= 2:
            styles = [str(c.get("speech_style") or "").strip() for c in chars]
            if any(not s for s in styles) or len(set(styles)) < len(styles):
                findings.append({
                    "severity": "critical",
                    "stage": "bible",
                    "field": "characters[].speech_style",
                    "message": "两名及以上角色 speech_style 相同或为空",
                    "proposed_fix": "每人写不同口吻（短句/慢条斯理/口头禅），不要做台词 NLP",
                })
            for i, left in enumerate(chars):
                tok_i = _appearance_tokens(str(left.get("appearance") or ""))
                for j, right in enumerate(chars[i + 1:], start=i + 1):
                    tok_j = _appearance_tokens(str(right.get("appearance") or ""))
                    if _jaccard(tok_i, tok_j) >= 0.5:
                        findings.append({
                            "severity": "critical",
                            "stage": "bible",
                            "field": f"characters[{i}].appearance",
                            "message": (
                                f"{left.get('id') or i} 与 {right.get('id') or j} "
                                "外貌 distinctive token 重叠过高"
                            ),
                            "proposed_fix": "拉开发色/疤痕/配饰，并写 contrast_notes",
                        })

    scenes = [s for s in (bible.get("scenes") or []) if isinstance(s, dict)]
    env_texts: list[str] = []
    for si, scene in enumerate(scenes):
        sid = str(scene.get("id") or f"sc{si + 1:02d}")
        env_texts.append(flatten_environment(scene.get("environment")))
        shots = [sh for sh in (scene.get("shots") or []) if isinstance(sh, dict)]
        if spoken:
            continue
        if not shots:
            findings.append({
                "severity": "critical",
                "stage": "bible",
                "field": f"scenes[{si}].shots",
                "message": f"{sid} 缺少可拍镜头 shots[]",
                "proposed_fix": "每场至少一镜：blocking + subjects[].action（动词+接触点）",
            })
            continue
        for ji, shot in enumerate(shots):
            label = str(shot.get("shot_id") or f"{sid}_{ji + 1:02d}")
            blocking = shot.get("blocking") or scene.get("blocking")
            if not isinstance(blocking, dict) or not (
                str(blocking.get("x") or blocking.get("lr") or "").strip()
                or str(blocking.get("z") or blocking.get("depth") or "").strip()
            ):
                findings.append({
                    "severity": "critical",
                    "stage": "bible",
                    "field": f"scenes[{si}].shots[{ji}].blocking",
                    "message": f"{label} 缺少站位 blocking",
                    "proposed_fix": "填写 {x: left|center|right, z: near|mid|far}",
                })
            subjects = [s for s in (shot.get("subjects") or []) if isinstance(s, dict)]
            if not subjects:
                # B2.5：空镜是合法镜头——只要 presence 明确写了 empty_reason
                # （结尾空镜、环境镜都属此类）。没有理由的空 subjects 仍是 critical。
                presence = shot.get("presence") if isinstance(shot.get("presence"), dict) else {}
                if str(presence.get("empty_reason") or "").strip():
                    findings.append({
                        "severity": "suggestion",
                        "stage": "bible",
                        "field": f"scenes[{si}].shots[{ji}].subjects",
                        "message": f"{label} 空镜（已写 empty_reason），无 subjects",
                    })
                else:
                    findings.append({
                        "severity": "critical",
                        "stage": "bible",
                        "field": f"scenes[{si}].shots[{ji}].subjects",
                        "message": f"{label} 缺少 subjects[]",
                        "proposed_fix": "列出谁在画面里，动作写成可见动词+接触点；空镜请写 presence.empty_reason",
                    })
                continue
            for sub in subjects:
                act = sub.get("action") if isinstance(sub.get("action"), dict) else None
                if action_lacks_contact(act):
                    findings.append({
                        "severity": "critical",
                        "stage": "bible",
                        "field": f"scenes[{si}].shots[{ji}].subjects",
                        "message": f"{label} 动作不可拍（空词或无接触点）",
                        "proposed_fix": "「两人打架」改为「抓住衣领推向墙」一类可见接触",
                    })
                    break
    for i in range(1, len(scenes)):
        prev_env = env_texts[i - 1]
        cur_env = env_texts[i]
        if not prev_env or not cur_env or prev_env == cur_env:
            continue
        later = scenes[i]
        hard = str(later.get("cut") or "").strip().lower() == "hard"
        if not hard:
            for sh in later.get("shots") or []:
                if isinstance(sh, dict) and str(sh.get("cut") or "").strip().lower() == "hard":
                    hard = True
                    break
        if not hard:
            sid = str(later.get("id") or f"sc{i + 1:02d}")
            findings.append({
                "severity": "warning",
                "stage": "bible",
                "field": f"scenes[{i}].cut",
                "message": f"{sid} 与上一场环境描述不同，但未标 cut=hard；生成层仍会桥接",
                "proposed_fix": "跳切则写 cut=hard 或不同 location_id；同空间连续戏可忽略本警告",
            })
    usable_env = [t for t in env_texts if t]
    if len(usable_env) >= 2 and len(set(usable_env)) == 1:
        sample = usable_env[0]
        if len(sample) <= 6 or sample in _EMPTY_ENV_PHRASES:
            findings.append({
                "severity": "critical",
                "stage": "bible",
                "field": "scenes[].environment",
                "message": f"多场环境是同一句空话（{sample}）",
                "proposed_fix": "每场写不同地点/光线/陈设，不要复制「一个房间」",
            })
    findings.extend(_bible_hero_findings(scenes))
    # 每镜形态声明必须指向 characters[].forms[].id。
    findings.extend(check_subject_forms(bible, bible))
    return findings


def _bible_hero_findings(scenes: list[dict[str, Any]]) -> list[dict[str, str]]:
    """hero 时长占比前移预警：produce 的 pending hero 30% 门禁（over_hero）。

    圣经期只拿得到 bible，没有 manifest，故按「全部 hero 都还没生成」算最坏值，
    与 produce 早期的 ratio 一致。镜头缺 ``duration_seconds`` 时按所在场时长均分，
    场景时长也没有则不计，避免用假分母误报。
    """
    hero_seconds = 0.0
    total_seconds = 0.0
    hero_labels: list[str] = []
    for scene in scenes:
        shots = [s for s in (scene.get("shots") or []) if isinstance(s, dict)]
        if not shots:
            continue
        try:
            scene_dur = max(0.0, float(scene.get("duration_seconds") or 0))
        except (TypeError, ValueError):
            scene_dur = 0.0
        share = scene_dur / len(shots) if scene_dur > 0 else 0.0
        for shot in shots:
            dur = shot_duration(shot) or share
            total_seconds += dur
            if normalize_class(shot.get("shot_budget_class")) == "hero":
                hero_seconds += dur
                hero_labels.append(str(shot.get("shot_id") or scene.get("id") or "?"))
    if total_seconds <= 0 or hero_seconds / total_seconds <= HERO_CAP:
        return []
    ratio = hero_seconds / total_seconds
    listed = "、".join(hero_labels[:6]) or "（未命名）"
    return [{
        "severity": "warning",
        "stage": "bible",
        "field": "scenes[].shots[].shot_budget_class",
        "message": (
            f"hero 时长占比 {ratio:.0%}"
            f"（{hero_seconds:.0f}/{total_seconds:.0f}s；{listed}）"
            f"超过 {HERO_CAP:.0%}；produce 会在 pending hero 阶段停 over_hero"
        ),
        "proposed_fix": (
            "标更少 hero、produce --trim-hero 降为 talk，"
            "或 produce --all-video 全视频跳过门禁"
        ),
    }]


def validate_bible(
    bible: dict[str, Any],
    *,
    format_card: dict[str, Any] | None = None,
    playbook: dict[str, Any] | None = None,
    script_style: dict[str, Any] | None = None,
    video_loop: str = "",
) -> dict[str, Any]:
    findings = check_bible(
        bible,
        format_card=format_card,
        playbook=playbook,
        script_style=script_style,
        video_loop=video_loop,
    )
    criticals = [f for f in findings if f["severity"] == "critical"]
    return {
        "pass": not criticals,
        "findings": findings,
        "counts": {
            "critical": len(criticals),
            "warning": len([f for f in findings if f["severity"] == "warning"]),
            "suggestion": len([f for f in findings if f["severity"] == "suggestion"]),
            "total": len(findings),
        },
    }


def validate_script(
    script: dict[str, Any],
    scene_plan: dict[str, Any] | None = None,
    *,
    video_loop: str = "none",
    words_per_second: float = _DEFAULT_WPS,
    purpose: str = "all",
    script_style: dict[str, Any] | None = None,
    shot_prompts: dict[str, Any] | None = None,
    bible: dict[str, Any] | None = None,
    format_card: dict[str, Any] | None = None,
    playbook: dict[str, Any] | None = None,
    project_dir: Any = None,
) -> dict[str, Any]:
    """综合校验；返回 {pass, findings, counts}。"""
    if purpose == "bible":
        doc = bible if isinstance(bible, dict) else script
        return validate_bible(
            doc if isinstance(doc, dict) else {},
            format_card=format_card,
            playbook=playbook,
            script_style=script_style,
            video_loop=resolve_video_loop(video_loop, project_dir),
        )
    findings: list[dict[str, str]] = []
    video_loop = resolve_video_loop(video_loop, project_dir)
    if purpose in ("all", "dialogue_budget"):
        findings.extend(check_dialogue_budget(script, video_loop=video_loop, words_per_second=words_per_second))
    if purpose in ("all", "filmability") and scene_plan:
        findings.extend(check_filmability(scene_plan))
    if purpose in ("all", "character_refs") and scene_plan:
        findings.extend(check_character_refs(script, scene_plan))
        findings.extend(check_subject_forms(scene_plan, script))
    if purpose in ("all", "completeness"):
        findings.extend(check_completeness(script, script_style=script_style))
    if purpose in ("all", "shot_completeness") and scene_plan:
        findings.extend(check_shot_completeness(scene_plan, video_loop=video_loop))
        findings.extend(check_shot_durations(scene_plan, video_loop=video_loop))
    if purpose in ("all", "shot_completeness", "presence") and scene_plan:
        findings.extend(check_presence_and_continuity(scene_plan))
    if purpose in ("all", "composition") and scene_plan:
        findings.extend(check_composition(scene_plan))
    if purpose in ("all", "beat_coverage"):
        findings.extend(check_beat_coverage(script, scene_plan))
    if purpose in ("all", "agnes_audio") or (purpose == "all" and shot_prompts):
        if shot_prompts:
            findings.extend(check_agnes_audio_prompts(shot_prompts, playbook=bool(script_style)))

    criticals = [f for f in findings if f["severity"] == "critical"]
    return {
        "pass": not criticals,
        "findings": findings,
        "counts": {
            "critical": len(criticals),
            "warning": len([f for f in findings if f["severity"] == "warning"]),
            "suggestion": len([f for f in findings if f["severity"] == "suggestion"]),
            "total": len(findings),
        },
    }


class ScriptValidator(BaseTool):
    """剧本/分镜质量门禁：对白预算 / 可拍性 / 人物引用 / 圣经。"""

    name = "script_validator"
    version = "0.4.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "properties": {
            "purpose": {
                "type": "string",
                "enum": [
                    "all", "dialogue_budget", "filmability", "character_refs",
                    "completeness", "shot_completeness", "composition",
                    "beat_coverage", "bible",
                ],
                "default": "all",
            },
            "script": {"type": "object", "description": "SCRIPT_SCHEMA 形状（sections/characters/structure）"},
            "bible": {"type": "object", "description": "series_bible；purpose=bible 时必填"},
            "format_card": {"type": "object"},
            "scene_plan": {"type": "object", "description": "SCENE_PLAN_SCHEMA 形状（可选，filmability/character_refs/composition 需要）"},
            "video_loop": {
                "type": "string",
                "enum": ["none", "jimeng", "agnes", "volcengine"],
                "default": "none",
                "description": "供应商闭环：jimeng/volcengine 时启用 5/10s 时长网格校验",
            },
            "words_per_second": {"type": "number", "default": 5.0},
            "playbook": {"type": "string", "description": "可选；用于读取 script_style 分档"},
            "script_style": {"type": "object"},
            "shot_prompts": {"type": "object"},
            "project_dir": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        purpose = str(inputs.get("purpose") or "all")
        style = inputs.get("script_style")
        if not isinstance(style, dict) or not style:
            name = str(inputs.get("playbook") or "").strip()
            if name:
                from montage.playbooks import get_playbook

                pb = get_playbook(name)
                style = (pb or {}).get("script_style")
        bible = inputs.get("bible") if isinstance(inputs.get("bible"), dict) else None
        script = inputs.get("script") if isinstance(inputs.get("script"), dict) else None
        if purpose == "bible":
            if not isinstance(bible, dict) and not isinstance(script, dict):
                return ToolResult(success=False, error="'bible' 必填（purpose=bible）")
            result = validate_script(
                script or {},
                purpose="bible",
                bible=bible,
                format_card=inputs.get("format_card") if isinstance(inputs.get("format_card"), dict) else None,
                playbook=inputs.get("playbook"),
                script_style=style if isinstance(style, dict) else None,
            )
            return ToolResult(success=True, data=result, meta={"purpose": "bible"})
        if not isinstance(script, dict):
            return ToolResult(success=False, error="'script' 必填（SCRIPT_SCHEMA 形状）")
        result = validate_script(
            script,
            inputs.get("scene_plan"),
            video_loop=inputs.get("video_loop", "none"),
            words_per_second=float(inputs.get("words_per_second", _DEFAULT_WPS)),
            purpose=purpose,
            script_style=style if isinstance(style, dict) else None,
            shot_prompts=inputs.get("shot_prompts") if isinstance(inputs.get("shot_prompts"), dict) else None,
            project_dir=inputs.get("project_dir"),
        )
        return ToolResult(
            success=True,
            data=result,
            meta={"purpose": purpose},
        )
