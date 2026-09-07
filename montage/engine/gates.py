"""gates — 阶段完成产物门禁（独立于 CheckpointStore）。

让 ``pipelines.py`` 的 ``produces`` 声明从"文档"变成"强制"：阶段写
``completed`` checkpoint 时，校验该阶段应产出的产物**存在且通过 schema**。

设计约束（与 CheckpointStore 保持极简、互不耦合）：
- 人审/机器收口由 ``CheckpointStore.write`` 的 human_approved / approved_by 把关；
- 产物完整性由本层把关：只在调用方写 ``completed`` 那一刻触发；
- 只拦 ``COMPLETED``：``in_progress`` / ``awaiting_human`` 永不触发校验；
- 对全部阶段一致生效（非门禁阶段如 research/compose 也有 produces）；
- 不回溯历史：``next_stage()`` / ``progress()`` 仍以 checkpoint 状态为准；
- ``strict=False``（环境变量 ``MONTAGE_RELAX_GATES=1`` 或显式传参）→ 仅告警不阻塞。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from montage.engine.artifacts import ArtifactStore
from montage.pipelines import get_pipeline
from montage.registry import ToolRegistry
from montage.schemas import get_schema


class GateError(RuntimeError):
    """产物门禁未通过（缺产物或 schema 校验失败）。"""


def _relaxed() -> bool:
    return os.environ.get("MONTAGE_RELAX_GATES") == "1"


def _pipeline_for(project_dir: str | Path) -> dict[str, Any] | None:
    """从 project.json 的 pipeline_type 读取管线配置；缺失时返回 None。"""
    meta_path = Path(project_dir) / "project.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return get_pipeline(str(meta.get("pipeline_type") or "cinematic"))


def _stage_config(pipeline: dict[str, Any] | None, stage: str) -> dict[str, Any] | None:
    if not pipeline:
        return None
    for cfg in pipeline.get("stages") or []:
        if cfg.get("name") == stage:
            return cfg
    return None


def _produces_for(pipeline: dict[str, Any] | None, stage: str) -> list[str]:
    cfg = _stage_config(pipeline, stage)
    if not cfg:
        return []
    return list(cfg.get("produces") or [])


def validate_completion(
    project_dir: str | Path,
    stage: str,
    pipeline: dict[str, Any] | None = None,
    *,
    strict: bool | None = None,
) -> dict[str, Any]:
    """校验阶段完成产物：存在 + schema 通过。

    Args:
        project_dir: 项目目录（artifacts/ 所在处）。
        stage: 阶段名（research/proposal/...）。
        pipeline: 可选管线配置；缺省时从 project.json 读取。
        strict: 是否阻塞（缺省取环境变量 MONTAGE_RELAX_GATES）。

    Returns:
        {"ok", "missing", "invalid", "warnings"}。strict 且不通过时抛 GateError。
    """
    if pipeline is None:
        pipeline = _pipeline_for(project_dir)
    produces = _produces_for(pipeline, stage)
    store = ArtifactStore(project_dir)
    missing: list[str] = []
    invalid: list[str] = []
    for name in produces:
        if not store.exists(name):
            missing.append(name)
            continue
        schema = get_schema(name)
        if schema is None:
            continue
        data = store.read(name) or {}
        errors = store.validate(data, schema)
        if errors:
            invalid.append(f"{name}: {'; '.join(errors[:5])}")

    invalid.extend(_completeness_blockers(store, stage))

    ok = not missing and not invalid
    if not ok:
        if strict is None:
            strict = not _relaxed()
        if strict:
            parts = [f"阶段 {stage} 产物门禁未通过"]
            if missing:
                parts.append(f"缺失产物: {missing}")
            if invalid:
                parts.append(f"schema 校验失败: {invalid}")
            parts.append("（先产出对应 artifact，或设 MONTAGE_RELAX_GATES=1 降级为告警）")
            raise GateError("；".join(parts))
        return {
            "ok": True,
            "missing": missing,
            "invalid": invalid,
            "warnings": [
                f"产物门禁放宽（MONTAGE_RELAX_GATES=1）：{stage} 缺失 {missing} / 无效 {invalid}"
            ],
        }
    return {"ok": True, "missing": missing, "invalid": invalid, "warnings": []}


def _completeness_blockers(store: ArtifactStore, stage: str) -> list[str]:
    """仅当 proposal.playbook 存在且 script_style.require_* 为真时，完整性 critical 才挡门。"""
    if stage != "script":
        return []
    packet = store.read("proposal_packet") or {}
    name = str(packet.get("playbook") or "").strip()
    if not name:
        return []
    from montage.playbooks import get_playbook
    from montage.tools.script_validator import check_completeness

    playbook = get_playbook(name)
    if playbook is None:
        return []
    style = playbook.get("script_style") or {}
    script = store.read("script")
    if not isinstance(script, dict):
        return []
    blockers: list[str] = []
    for finding in check_completeness(script, script_style=style if isinstance(style, dict) else None):
        if finding.get("severity") == "critical":
            blockers.append(f"completeness: {finding.get('message')}")
    return blockers


def stage_capabilities(pipeline: dict[str, Any] | None, stage: str) -> set[str]:
    """把阶段声明的工具名解析为能力族（capability）集合。

    白名单以能力族为单位（与 toolbase.BaseTool.capability 对齐），
    不落到具体供应商工具名——供应商路由由选型器负责。
    """
    cfg = _stage_config(pipeline, stage)
    if not cfg:
        return set()
    reg = ToolRegistry()
    reg.discover()
    caps: set[str] = set()
    for tool_name in cfg.get("tools") or []:
        cls = reg.get(tool_name)
        if cls is not None:
            caps.add(cls.capability)
    return caps


def check_tool_allowlist(
    stage: str,
    capabilities_used: list[str],
    pipeline: dict[str, Any] | None = None,
    *,
    project_dir: str | Path | None = None,
) -> list[str]:
    """校验使用的能力族是否在该阶段白名单内；返回越权告警（非阻塞）。

    Args:
        stage: 阶段名。
        capabilities_used: 本次实际使用的能力族列表（如 ["analysis", "tts"]）。
        pipeline: 可选管线配置；缺省时若给 project_dir 则从 project.json 读取。
        project_dir: 可选项目目录（pipeline 缺省时用于读管线配置）。

    Returns:
        告警消息列表（空 = 全部在允许范围内）。
    """
    if pipeline is None and project_dir is not None:
        pipeline = _pipeline_for(project_dir)
    if _stage_config(pipeline, stage) is None:
        return []  # 未知阶段：无配置约束，不校验
    allowed = stage_capabilities(pipeline, stage)
    violations = [c for c in capabilities_used if c not in allowed]
    if not violations:
        return []
    allow_txt = ", ".join(sorted(allowed)) if allowed else "（该阶段未声明任何工具）"
    return [
        f"阶段 {stage} 工具白名单不允许能力族 {c}（允许: {allow_txt}）"
        for c in violations
    ]
