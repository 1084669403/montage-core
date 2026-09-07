"""decisions — 决策日志（append-only，审计追溯）。

记录生产过程中的关键决策（供应商选择、渲染运行时、风格方向等）。
规则：(category, subject) 二元组为键，同一键的新条目覆盖旧条目展示（标 revised），
旧条目不删除——日志是历史，不是草稿本。

本文件为全新原创代码。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class Decision:
    category: str
    subject: str
    choice: str
    options_considered: list[str] = field(default_factory=list)
    rejected_because: str = ""
    ts: str = ""
    revised: bool = False

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "subject": self.subject,
            "choice": self.choice,
            "options_considered": self.options_considered,
            "rejected_because": self.rejected_because,
            "ts": self.ts,
            "revised": self.revised,
        }


class DecisionLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        category: str,
        subject: str,
        choice: str,
        options_considered: list[str] | None = None,
        rejected_because: str = "",
    ) -> None:
        prev = self.current().get((category, subject))
        decision = Decision(
            category=category,
            subject=subject,
            choice=choice,
            options_considered=options_considered or [],
            rejected_because=rejected_because,
            ts=datetime.now(timezone.utc).isoformat(),
            revised=prev is not None,
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(decision.to_dict(), ensure_ascii=False) + "\n")

    def entries(self) -> list[Decision]:
        if not self.path.exists():
            return []
        out: list[Decision] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(Decision(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def current(self) -> dict[tuple[str, str], Decision]:
        """最新一条为准：(category, subject) -> Decision。"""
        result: dict[tuple[str, str], Decision] = {}
        for d in self.entries():
            result[(d.category, d.subject)] = d
        return result
