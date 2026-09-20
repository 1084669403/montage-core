"""review_logger — 角色评审日志（V35/V30/V47/V49；无 LLM 纯计算）。

三日志分工（V35）：
- ``decisions.jsonl``  = 剪辑决策（auto_edit 在用）
- ``cost.jsonl``       = 成本审计（BudgetLedger）
- ``review_log.jsonl`` = 角色评审（本工具，append-only）

operation:
- record  ：追加一行评审记录到 artifacts/review_log.jsonl（不走 ArtifactStore——
            它只写 .json 且整文件覆盖；本日志必须 append-only）。
- summary ：按 subject 汇总轮次/最终 decision/未解决 findings + 振荡检测（V22 简化版）
            + role 级跨点位 revise 轮合计（V49）。
- metrics ：（P0-4）算 DIRECT 客观量规（m1/m2/m5/m6 + 身份漂移，删 m3/m4）→ 写
            ``artifacts/edit_metrics.json``，并按 ``record``（缺省 true）直接落一行
            review_log：role=edit_director / subject=edit_plan，每条 finding 带
            ``metric``/``value``/``threshold``，decision 由量规自动判定。这是
            「assemble 前人审」的机械依据（见 ``montage/engine/edit_metrics.py``）。

关键纪律：
- V30 subject 枚举表：表外值 record 记 warning 不拒绝（append-only 容错）；
  summary 聚合时表外值单列一节提示协议违规。
- V30 round：summary 按（行序=timestamp 序）自动计算轮次，不信任手填 round。
- V47 phase：``first_pass``=子 Agent 关卡（剧本 SA 终审/点位二首审，不占 4 轮额度）；
  ``revise``=返修轮（缺省）。轮次统计与振荡检测只数 revise 行。
- V22 振荡：同 (role, subject, field) 连续 2 个 revise 轮即 osc；regression 以
  message 相似度为辅助信号；域冲突（不同角色同字段）不算振荡，标记后走导演仲裁。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from montage.engine.artifacts import ArtifactStore
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

REVIEW_LOG_NAME = "review_log.jsonl"

# V30 subject 值域枚举表（匹配键的地基；Agent 措辞漂移会让同键判定静默失效）
REVIEW_LOG_SUBJECTS: list[str] = [
    "setup",
    "outline",
    "design",
    "cast",
    "checkpoint_1",
    "final_prompt",
    "frames",
    "clips",
    "retry",
    "draft_selection",
    "edit_plan",
    "finding_status",
]

REVIEW_LOG_ROLES: list[str] = [
    "screenwriter",
    "director",
    "art_director",
    "action_director",
    "vfx_director",
    "edit_director",
    "user",
]

PHASE_FIRST_PASS = "first_pass"
PHASE_REVISE = "revise"
PHASE_STATUS_UPDATE = "status_update"
PHASE_VALUES = (PHASE_FIRST_PASS, PHASE_REVISE, PHASE_STATUS_UPDATE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / "artifacts" / REVIEW_LOG_NAME


def record_review(project_dir: str | Path, row: dict[str, Any]) -> Path:
    """追加一行到 review_log.jsonl（append-only；显式 utf-8）。供工具与测试复用。"""
    path = _log_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    return path


def load_reviews(project_dir: str | Path) -> list[dict[str, Any]]:
    """读全部评审行；损坏行跳过（append-only 日志容错）。"""
    path = _log_path(project_dir)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        text = raw.strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def _auto_rounds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 (role, subject) 分组、timestamp 序自动编号 revise 轮（V30+V47）。

    first_pass 行不占轮次（V36/V47 定案：关卡与返修分离）；行上补 ``auto_round``
    与 ``phase_eff``（first_pass 行缺省 phase 也按 revise 记，向后兼容旧行）。
    """
    out: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], int] = {}
    for row in rows:
        row = dict(row)
        phase = str(row.get("phase") or PHASE_REVISE).strip() or PHASE_REVISE
        if phase not in PHASE_VALUES:
            phase = PHASE_REVISE
        row["phase_eff"] = phase
        key = (str(row.get("role") or ""), str(row.get("subject") or ""))
        if phase == PHASE_REVISE:
            groups[key] = groups.get(key, 0) + 1
            row["auto_round"] = groups[key]
        else:
            row["auto_round"] = 0
        out.append(row)
    return out


def _finding_keys(row: dict[str, Any]) -> set[str]:
    return {
        str(f.get("field") or "").strip()
        for f in (row.get("findings") or [])
        if isinstance(f, dict) and str(f.get("field") or "").strip()
    }


def _finding_status_row(
    *,
    timestamp: str,
    role: str,
    finding_id: str,
    status: str,
    evidence: list[str],
    note: str,
) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "finding_id": finding_id,
        "status": status,
    }
    if evidence:
        finding["evidence"] = evidence
    if note:
        finding["message"] = note
    return {
        "timestamp": timestamp,
        "role": role,
        "subject": "finding_status",
        "phase": PHASE_STATUS_UPDATE,
        "decision": "STATUS_UPDATE",
        "findings": [finding],
    }


def _messages_for(row: dict[str, Any], field: str) -> list[str]:
    return [
        str(f.get("message") or "")
        for f in (row.get("findings") or [])
        if isinstance(f, dict) and str(f.get("field") or "") == field
    ]


def detect_oscillation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """V22 简化版：同 (role, subject, field) 连续 2 个 revise 轮即 osc。

    regression：早轮出现过、上一轮消失、本轮复现（message 相似度仅作辅助证据）。
    domain_conflict：同一 (subject, field) 出现不同 role → 标记，走导演仲裁不算振荡。
    """
    rows = _auto_rounds(rows)
    # 按 (role, subject) 分组取 revise 轮序列
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("phase_eff") != PHASE_REVISE:
            continue
        by_key.setdefault((str(row.get("role") or ""), str(row.get("subject") or "")), []).append(row)

    oscillations: list[dict[str, Any]] = []
    regressions: list[dict[str, Any]] = []
    seen_fields: dict[tuple[str, str, str], dict[str, list[str]]] = {}
    for (role, subject), seq in by_key.items():
        prev_fields: set[str] = set()
        prev2_fields: set[str] = set()
        prev_msgs: dict[str, list[str]] = {}
        for row in seq:
            cur = _finding_keys(row)
            rnd = int(row.get("auto_round") or 0)
            for field in cur:
                seen_fields.setdefault((subject, field, ""), {})
                seen_fields[(subject, field, "")].setdefault(role, []).append(
                    f"r{rnd}"
                )
                if field in prev_fields:
                    oscillations.append({
                        "role": role,
                        "subject": subject,
                        "field": field,
                        "round": rnd,
                        "kind": "osc",
                        "messages": _messages_for(row, field)[:2],
                    })
                elif field in prev2_fields:
                    # 上一轮消失、本轮复现
                    regressions.append({
                        "role": role,
                        "subject": subject,
                        "field": field,
                        "round": rnd,
                        "kind": "regression",
                        "prev_messages": (prev_msgs.get(field) or [])[:2],
                        "messages": _messages_for(row, field)[:2],
                    })
            prev2_fields = prev_fields
            prev_msgs = {f: _messages_for(row, f) for f in prev_fields}
            prev_fields = cur

    domain_conflicts: list[dict[str, Any]] = []
    for (subject, field, _), roles in seen_fields.items():
        if len(roles) > 1:
            domain_conflicts.append({
                "subject": subject,
                "field": field,
                "roles": sorted(roles),
                "note": "不同角色同字段→不算振荡，走导演仲裁（约束力条款）",
            })
    return {
        "oscillations": oscillations,
        "regressions": regressions,
        "domain_conflicts": domain_conflicts,
    }


def _project_fps(store: ArtifactStore) -> float:
    """成片帧率：按 output_profile 取；未知按 30（与 profiles.py 默认一致）。"""
    packet = store.read("proposal_packet") or {}
    name = str(packet.get("output_profile") or "").strip()
    if not name:
        return 30.0
    try:
        from montage.compose.profiles import get_profile

        profile = get_profile(name)
    except Exception:  # noqa: BLE001
        return 30.0
    return float(getattr(profile, "fps", 30) or 30)


def _timeline_shots(store: ArtifactStore) -> list[dict[str, Any]]:
    """成片时间轴镜序（终剪顺序）：scene_plan + 实测时长优先。"""
    from montage.tools.compose_planner import _measured_durations, _scene_plan_with_measured
    from montage.tools.voice_director import shots_with_timeline

    scene_plan = store.read("scene_plan")
    manifest = store.read("asset_manifest")
    measured = _measured_durations(manifest)
    timed = shots_with_timeline(_scene_plan_with_measured(scene_plan, measured))
    if timed:
        return timed
    prompts = store.read("shot_prompts") or {}
    return [s for s in (prompts.get("shots") or []) if isinstance(s, dict)]


def compute_project_metrics(project_dir: str | Path) -> dict[str, Any]:
    """从项目产物算 DIRECT 客观量规（P0-4）；纯读盘，零 LLM。

    P0-5：同时读 ``tmp_autoedit/beat_map.json``——切点若被能量波接管，m5/m6 打
    ``circular``（自证，不作质量证据），m6 用实测 bar 能量而非音频编排代理。
    """
    from montage.engine.edit_metrics import compute_edit_metrics
    from montage.engine.identity import load_memory as load_identity_memory
    from montage.tools.auto_edit import beat_map_contour, load_beat_map
    from montage.engine.transition_contract import build_transition_contract_projection

    store = ArtifactStore(project_dir)
    scene_plan = store.read("scene_plan") or {}
    compose_plan = store.read("compose_plan") or {}
    shots = _timeline_shots(store)
    prompts = {
        str(s.get("shot_id") or ""): str(s.get("video_prompt") or "")
        for s in ((store.read("shot_prompts") or {}).get("shots") or [])
        if isinstance(s, dict)
    }
    registry = {
        str(c.get("id") or ""): c
        for c in (scene_plan.get("character_registry") or [])
        if isinstance(c, dict) and c.get("id")
    }
    # P0-5：auto_edit 的能量波产物。有它 → m5/m6 变自证（circular），且 m6 用实测能量。
    beat_map = load_beat_map(project_dir)
    contour = beat_map_contour(beat_map) or None
    if beat_map and not beat_map.get("feasible"):
        beat_map = None      # DP 不可行 = 切点没被能量波接管，指标仍是真证据
    return compute_edit_metrics(
        shots=shots,
        scene_plan=scene_plan,
        transition_contract_projection=build_transition_contract_projection(
            compose_plan=compose_plan if isinstance(compose_plan, dict) else None,
            scene_plan=scene_plan if isinstance(scene_plan, dict) else None,
        ),
        registry=registry,
        prompts=prompts,
        image_bindings=store.read("image_bindings"),
        vlm=store.read("vlm_review"),
        soundtrack=store.read("soundtrack"),
        identity_memory=load_identity_memory(project_dir),
        energy_contour=contour,
        beat_map=beat_map,
        fps=_project_fps(store),
    )


def summarize_reviews(project_dir: str | Path, subject: str = "") -> dict[str, Any]:
    from montage.engine.review_findings import (
        build_review_findings,
        review_findings_summary,
    )

    rows = load_reviews(project_dir)
    if subject:
        rows = [r for r in rows if str(r.get("subject") or "") == subject]
    rows = _auto_rounds(rows)

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(
            (str(row.get("role") or ""), str(row.get("subject") or "")), []
        ).append(row)

    subjects: list[dict[str, Any]] = []
    off_enum: list[str] = []
    role_totals: dict[str, dict[str, Any]] = {}
    for (role, subj), seq in groups.items():
        if subj not in REVIEW_LOG_SUBJECTS:
            off_enum.append(subj)
        revise = [r for r in seq if r.get("phase_eff") == PHASE_REVISE]
        first_pass = [r for r in seq if r.get("phase_eff") == PHASE_FIRST_PASS]
        last = seq[-1] if seq else {}
        latest_revise = revise[-1] if revise else None
        unresolved: list[dict[str, Any]] = []
        if latest_revise is not None and str(latest_revise.get("decision") or "").upper() != "PASS":
            unresolved = [
                f for f in (latest_revise.get("findings") or []) if isinstance(f, dict)
            ]
        subjects.append({
            "role": role,
            "subject": subj,
            "revise_rounds": len(revise),
            "first_pass_count": len(first_pass),
            "final_decision": str(last.get("decision") or ""),
            "latest_revise_round": int(latest_revise.get("auto_round") or 0) if latest_revise else 0,
            "unresolved_findings": unresolved,
        })
        totals = role_totals.setdefault(
            role, {"revise_rounds_total": 0, "first_pass_total": 0, "subjects": {}}
        )
        totals["revise_rounds_total"] += len(revise)
        totals["first_pass_total"] += len(first_pass)
        totals["subjects"][subj] = len(revise)

    lifecycle = review_findings_summary(
        build_review_findings(rows),
        log_present=True,
    )
    return {
        "rows": len(rows),
        "subjects": subjects,
        "role_totals": [
            {
                "role": role,
                "revise_rounds_total": totals["revise_rounds_total"],
                "first_pass_total": totals["first_pass_total"],
                "by_subject": totals["subjects"],
                "note": "V49：点位一+点位二共用每角色 4 轮额度，按 role 聚合核对（first_pass 不计）",
            }
            for role, totals in sorted(role_totals.items())
        ],
        "subjects_off_enum": sorted(set(off_enum)) or None,
        "finding_lifecycle": lifecycle,
        **detect_oscillation(rows),
    }


class ReviewLogger(BaseTool):
    """角色评审日志：record（append-only 追加）+ summary（轮次/振荡/额度合计）+ metrics（P0-4 客观量规）。"""

    name = "review_logger"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["record", "summary", "metrics", "finding_status"],
                "default": "record",
            },
            "project_dir": {"type": "string"},
            "role": {
                "type": "string",
                "description": "screenwriter|director|art_director|action_director|edit_director|user",
            },
            "subject": {
                "type": "string",
                "description": "V30 枚举：setup/outline/design/cast/checkpoint_1/"
                "final_prompt/frames/clips/retry/draft_selection/edit_plan",
            },
            "phase": {
                "type": "string",
                "enum": list(PHASE_VALUES),
                "description": "V47：first_pass=子 Agent 关卡（不占额度）；缺省=revise",
            },
            "round": {
                "type": "integer",
                "description": "手填参考值；summary 按时间戳序自动计算（V30）",
            },
            "decision": {"type": "string", "description": "PASS|REVISE|其他角色决策"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "finding_id": {"type": "string"},
                        "severity": {"type": "string"},
                        "field": {"type": "string"},
                        "message": {"type": "string"},
                        "proposed_fix": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": [
                                "open", "in_progress", "fixed", "verified",
                                "waived", "invalid",
                            ],
                        },
                        "evidence": {"type": "array", "items": {"type": "string"}},
                        "metric": {
                            "type": "string",
                            "description": "P0-4 客观量规编号：m1/m2/m5/m6/drift（m3/m4 已删）",
                        },
                        "value": {"type": "number", "description": "实测值"},
                        "threshold": {"type": "number", "description": "阈值（判定依据）"},
                    },
                },
            },
            "dissent": {"type": "string"},
            "scores": {"type": "object"},
            "self_review_findings_fixed": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        op = str(inputs.get("operation") or "record")
        project_dir = str(inputs.get("project_dir") or "").strip()
        if not project_dir:
            return ToolResult(success=False, error="'project_dir' 必填")
        if op == "summary":
            return ToolResult(
                success=True,
                data=summarize_reviews(project_dir, str(inputs.get("subject") or "")),
                meta={"operation": "summary"},
            )
        if op == "finding_status":
            from montage.engine.review_findings import REVIEW_FINDING_STATUSES

            finding_id = str(inputs.get("finding_id") or "").strip()
            status = str(inputs.get("status") or "").strip().lower()
            evidence_raw = inputs.get("evidence")
            evidence = [
                str(item) for item in (evidence_raw if isinstance(evidence_raw, list) else [])
                if str(item)
            ]
            missing = [name for name, value in (
                ("finding_id", finding_id), ("status", status),
            ) if not value]
            if missing:
                return ToolResult(success=False, error=f"'{missing[0]}' 必须填写")
            if status not in REVIEW_FINDING_STATUSES:
                return ToolResult(
                    success=False,
                    error=f"status 必须是: {', '.join(sorted(REVIEW_FINDING_STATUSES))}",
                )
            role = str(inputs.get("role") or "user").strip() or "user"
            row = _finding_status_row(
                timestamp=_now(),
                role=role,
                finding_id=finding_id,
                status=status,
                evidence=evidence,
                note=str(inputs.get("note") or ""),
            )
            path = record_review(project_dir, row)
            return ToolResult(
                success=True,
                data={"row": row, "path": str(path)},
                meta={"operation": "finding_status"},
            )
        if op == "metrics":
            return self._metrics(project_dir, inputs)
        if op != "record":
            return ToolResult(success=False, error=f"未知 operation: {op}")

        role = str(inputs.get("role") or "").strip()
        subject = str(inputs.get("subject") or "").strip()
        decision = str(inputs.get("decision") or "").strip()
        missing = [name for name, val in (
            ("role", role), ("subject", subject), ("decision", decision),
        ) if not val]
        if missing:
            return ToolResult(success=False, error=f"'{missing[0]}' 必填")

        phase = str(inputs.get("phase") or PHASE_REVISE).strip() or PHASE_REVISE
        warnings: list[str] = []
        if phase not in PHASE_VALUES:
            warnings.append(f"phase 表外值 {phase!r}，按 revise 记录（V47）")
            phase = PHASE_REVISE
        if subject not in REVIEW_LOG_SUBJECTS:
            # V30：表外值 warning 不拒绝（append-only 容错）；summary 单列协议违规
            warnings.append(
                f"subject 表外值 {subject!r}（V30 枚举表：{', '.join(REVIEW_LOG_SUBJECTS)}）"
            )
        if role and REVIEW_LOG_ROLES and role not in REVIEW_LOG_ROLES:
            warnings.append(f"role 表外值 {role!r}（约定角色：{', '.join(REVIEW_LOG_ROLES)}）")

        row: dict[str, Any] = {
            "timestamp": _now(),
            "role": role,
            "subject": subject,
            "phase": phase,
            "decision": decision,
        }
        if inputs.get("round") is not None:
            row["round"] = inputs.get("round")
        findings = inputs.get("findings")
        if isinstance(findings, list):
            row["findings"] = [f for f in findings if isinstance(f, dict)]
        if inputs.get("dissent"):
            row["dissent"] = str(inputs.get("dissent"))
        scores = inputs.get("scores")
        if isinstance(scores, dict):
            row["scores"] = scores
        fixed = inputs.get("self_review_findings_fixed")
        if isinstance(fixed, list):
            row["self_review_findings_fixed"] = [str(x) for x in fixed]

        path = record_review(project_dir, row)
        return ToolResult(
            success=True,
            data={"row": row, "path": str(path), "warnings": warnings or None},
            meta={"operation": "record"},
        )

    def _metrics(self, project_dir: str, inputs: dict[str, Any]) -> ToolResult:
        """metrics：算 DIRECT 客观量规，可选直接落一行 review_log（P0-4）。

        「assemble 前人审」的机械依据：每条 finding 都带 metric/value/threshold，
        剪辑导演的 4 轮 REVISE 不再靠主观感受（V21 每轮必须 record）。
        ``record=false`` 只算不落盘（停点卡预读/CI 断言用）。
        """
        from montage.engine.edit_metrics import metric_findings, metric_summary, overall_decision
        from montage.schemas import get_schema

        try:
            report = compute_project_metrics(project_dir)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"量规计算失败: {exc}")
        ArtifactStore(project_dir).write("edit_metrics", report, schema=get_schema("edit_metrics"))
        data: dict[str, Any] = {"report": report, "summary": metric_summary(report)}
        if not inputs.get("record", True):
            return ToolResult(success=True, data=data, meta={"operation": "metrics"})

        phase = str(inputs.get("phase") or PHASE_REVISE).strip() or PHASE_REVISE
        if phase not in PHASE_VALUES:
            phase = PHASE_REVISE
        role = str(inputs.get("role") or "edit_director").strip() or "edit_director"
        subject = str(inputs.get("subject") or "edit_plan").strip() or "edit_plan"
        row: dict[str, Any] = {
            "timestamp": _now(),
            "role": role,
            "subject": subject,
            "phase": phase,
            "decision": str(inputs.get("decision") or overall_decision(report)).strip()
            or overall_decision(report),
            "findings": metric_findings(report),
            "scores": {
                metric: row_data.get("value")
                for metric, row_data in (report.get("metrics") or {}).items()
                if isinstance(row_data, dict)
            },
            "metrics_summary": metric_summary(report),
        }
        if inputs.get("dissent"):
            row["dissent"] = str(inputs.get("dissent"))
        recorded = record_review(project_dir, row)
        data["row"] = row
        data["path"] = str(recorded)
        return ToolResult(success=True, data=data, meta={"operation": "metrics"})
