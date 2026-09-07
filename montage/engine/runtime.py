"""runtime — Agent 调工具的唯一入口（校验 / 锁定 / 预算）。

``BaseTool.execute`` 仍可直接调用（单测逃生舱）。生产路径走 ``run_tool``：
注入 ``project_dir``、``validate_inputs``、图/视频供应商锁定、预算硬停。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from montage.engine.budget import BudgetLedger
from montage.engine.policy import (
    enforce_concrete_provider_lock,
    infer_project_dir,
    load_loop_policy,
    lock_applies,
)
from montage.toolbase import BaseTool, ToolResult, validate_inputs


def run_tool(tool: BaseTool, inputs: dict[str, Any] | None = None) -> ToolResult:
    """执行工具：注入项目目录 → 输入校验 → 图/视频锁定 → 预算硬停 → execute。"""
    payload = dict(inputs or {})
    proj = payload.get("project_dir")
    if not proj:
        inferred = infer_project_dir(
            payload.get("output_path"),
            payload.get("edit_decisions_path"),
            payload.get("input_path"),
            payload.get("image_path"),
            payload.get("audio_path"),
        )
        if inferred is not None:
            proj = str(inferred)
    if proj:
        payload["project_dir"] = str(proj)

    errors = validate_inputs(tool, payload)
    if errors:
        return ToolResult(success=False, error="输入校验失败: " + "; ".join(errors[:8]))

    policy = load_loop_policy(proj) if proj else {}
    lock_err = enforce_concrete_provider_lock(tool, payload, policy)
    if lock_err:
        return ToolResult(success=False, error=lock_err)

    if lock_applies(tool) and policy.get("allowed_providers"):
        payload.setdefault("allowed_providers", policy["allowed_providers"])
        if policy.get("lock_preferred_provider"):
            payload.setdefault("lock_preferred_provider", True)
        if policy.get("video_loop"):
            payload.setdefault("video_loop", policy["video_loop"])

    ceiling = policy.get("budget_ceiling_usd")
    ledger: BudgetLedger | None = None
    estimate_id = ""
    if proj and ceiling is not None:
        cost = float(tool.estimate_cost(payload) or 0.0)
        ledger = BudgetLedger(Path(proj) / "cost.jsonl", budget_ceiling_usd=float(ceiling))
        outstanding = ledger.totals()["estimated_raw_usd"]
        over = outstanding + cost > float(ceiling)
        if over and os.environ.get("MONTAGE_BUDGET_SOFT") != "1":
            return ToolResult(
                success=False,
                error=(
                    f"预算硬停：估算 ${outstanding:.4f} + ${cost:.4f} 超过封顶 "
                    f"${float(ceiling):.4f}（USD 为近似值；设 MONTAGE_BUDGET_SOFT=1 可继续）"
                ),
                meta={"budget_ceiling_usd": ceiling, "estimated_raw_usd": outstanding},
            )
        if cost > 0:
            estimate_id = ledger.estimate(
                tool.capability or "tool",
                getattr(tool, "name", "tool"),
                getattr(tool, "name", "tool"),
                cost,
            )

    result = tool.execute(payload)
    if result.success and ledger is not None and estimate_id:
        actual = result.cost_usd if result.cost_usd else float(tool.estimate_cost(payload) or 0.0)
        ledger.settle(estimate_id, actual)
    return result
