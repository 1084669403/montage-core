"""budget — 预算记账：估算 → 预留 → 结算（append-only，可审计）。

- ``estimate``：追加一条 estimate 行（type="estimate"）。
- ``settle``：**追加**一条 settlement 行（type="settlement"，关联 estimate_id），
  不反写原 estimate 行——日志是历史，不可改写历史（审计语义）。
- ``totals``：对账——``estimated_raw_usd`` 为 estimate 行原额，
  ``settled_usd`` 为已结算实际额（旧格式 settle 行与新 settlement 行都计），
  ``estimated_outstanding_usd`` = 原额 - 已结算。
- ``budget_ceiling_usd``：预算封顶；``over_budget()`` / ``estimate_checked()``
  供调用方在触发 API 调用前决策（默认继续 + 告警，不硬锁调用）。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class CostEntry:
    id: str
    category: str  # capability 或业务类别（image_generation / tts / compose ...）
    subject: str  # 描述性主题（如 "sc01 首帧图"）
    item: str  # 工具名
    estimate_usd: float
    actual_usd: float | None = None
    status: str = "estimated"  # estimated / settled
    ts: str = ""
    type: str = "estimate"  # estimate / settlement
    estimate_id: str = ""  # settlement 行关联的 estimate 行 id

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "category": self.category,
            "subject": self.subject,
            "item": self.item,
            "estimate_usd": self.estimate_usd,
            "actual_usd": self.actual_usd,
            "status": self.status,
            "ts": self.ts,
            "type": self.type,
            "estimate_id": self.estimate_id,
        }


class BudgetLedger:
    def __init__(
        self,
        path: str | Path,
        budget_ceiling_usd: float | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.budget_ceiling_usd = budget_ceiling_usd

    # -- 写 ---------------------------------------------------------------

    def estimate(self, category: str, subject: str, item: str, usd: float) -> str:
        entry = CostEntry(
            id=uuid.uuid4().hex[:12],
            category=category,
            subject=subject,
            item=item,
            estimate_usd=round(float(usd), 6),
            ts=datetime.now(timezone.utc).isoformat(),
            type="estimate",
        )
        self._append(entry)
        return entry.id

    def estimate_checked(self, category: str, subject: str, item: str, usd: float) -> tuple[str, bool]:
        """估算并返回 (entry_id, 是否未超预算)。超预算时仍记账（历史不可丢），
        由调用方决定是否继续（默认继续 + 告警，避免硬锁 API 调用）。"""
        eid = self.estimate(category, subject, item, usd)
        return eid, not self.over_budget()

    def settle(self, entry_id: str, actual_usd: float) -> None:
        """追加一条 settlement 行关联 estimate_id（**不反写原 estimate 行**）。

        保留 status="settled" 与 category="settle" 以兼容既有读侧
        （totals 的 settled_usd 聚合条件不变）。
        """
        entry = CostEntry(
            id=uuid.uuid4().hex[:12],
            category="settle",
            subject="",
            item="",
            estimate_usd=0.0,
            actual_usd=round(float(actual_usd), 6),
            status="settled",
            ts=datetime.now(timezone.utc).isoformat(),
            type="settlement",
            estimate_id=entry_id,
        )
        self._append(entry)

    def _append(self, entry: CostEntry) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

    # -- 读 ---------------------------------------------------------------

    def entries(self) -> list[CostEntry]:
        """返回全部行（estimate + settlement，append-only 全量）。"""
        if not self.path.exists():
            return []
        out: list[CostEntry] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(CostEntry(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def estimates(self) -> list[CostEntry]:
        return [e for e in self.entries() if e.type != "settlement"]

    def settlements(self) -> list[CostEntry]:
        return [e for e in self.entries() if e.type == "settlement"]

    def over_budget(self) -> bool:
        """已估算总额是否超过预算封顶（未设封顶恒为 False）。"""
        if self.budget_ceiling_usd is None:
            return False
        return self.totals()["estimated_raw_usd"] > self.budget_ceiling_usd

    def totals(self) -> dict:
        entries = self.entries()
        estimates = [e for e in entries if e.type != "settlement"]
        # settled_usd：status=="settled" 聚合（旧格式 settle 行 + 新 settlement 行都计）
        settled = sum(e.actual_usd or 0.0 for e in entries if e.status == "settled")
        estimated_raw = sum(e.estimate_usd for e in estimates)
        by_category: dict[str, float] = {}
        for e in estimates:
            by_category[e.category] = by_category.get(e.category, 0.0) + e.estimate_usd
        return {
            "entries": len(entries),  # 全部行（estimate + settlement）
            "estimates": len(estimates),
            "settlements": len(entries) - len(estimates),
            "estimated_usd": round(estimated_raw, 6),
            "estimated_raw_usd": round(estimated_raw, 6),
            "estimated_outstanding_usd": round(max(estimated_raw - settled, 0.0), 6),
            "settled_usd": round(settled, 6),
            "by_category": {k: round(v, 6) for k, v in sorted(by_category.items())},
        }
