"""stages — 管线阶段状态机与 checkpoint 存储。

阶段顺序固定为：research → proposal → script → scene_plan → assets → compose → publish。
每个阶段一个 checkpoint JSON 文件（checkpoint_<stage>.json），状态机维护：
pending / in_progress / awaiting_human / completed / failed。

门禁规则：配置为 gated 的阶段在无 human_approved 且 approved_by 不是 human/produce 时不可写 completed。
旧 checkpoint 只有 human_approved=true 仍视为通过。produce 不得写假 human_approved。
被覆盖的历史 checkpoint 自动归档到 history/，阶段重跑不丢历史。

本文件为全新原创代码。
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


STAGE_ORDER: tuple[str, ...] = (
    "research",
    "proposal",
    "script",
    "scene_plan",
    "assets",
    "compose",
    "publish",
)


class StageStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_HUMAN = "awaiting_human"
    COMPLETED = "completed"
    FAILED = "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Checkpoint:
    stage: str
    status: str = StageStatus.PENDING.value
    human_approved: bool = False
    approved_by: str = ""  # human | produce | 空
    artifact: str | None = None  # 规范产物文件名（如 script.json）
    note: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "status": self.status,
            "human_approved": self.human_approved,
            "approved_by": self.approved_by,
            "artifact": self.artifact,
            "note": self.note,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Checkpoint":
        approved = bool(data.get("human_approved", False))
        by = str(data.get("approved_by") or "").strip()
        if approved and not by:
            by = "human"
        return cls(
            stage=data.get("stage", ""),
            status=data.get("status", StageStatus.PENDING.value),
            human_approved=approved,
            approved_by=by,
            artifact=data.get("artifact"),
            note=data.get("note", ""),
            updated_at=data.get("updated_at", ""),
        )


def _gate_allows(human_approved: bool, approved_by: str) -> bool:
    if human_approved:
        return True
    return str(approved_by or "").strip() in {"human", "produce"}


class CheckpointStore:
    """读写 checkpoint 并维护历史归档。"""

    def __init__(
        self,
        project_dir: str | Path,
        gated_stages: set[str] | None = None,
    ) -> None:
        self.project_dir = Path(project_dir)
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.gated = gated_stages or {"proposal", "script", "scene_plan", "assets", "publish"}

    # -- 读写 -------------------------------------------------------------

    def _path(self, stage: str) -> Path:
        return self.project_dir / f"checkpoint_{stage}.json"

    def read(self, stage: str) -> Checkpoint | None:
        path = self._path(stage)
        if not path.exists():
            return None
        try:
            return Checkpoint.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError):
            return None

    def write(
        self,
        stage: str,
        status: str,
        human_approved: bool = False,
        artifact: str | None = None,
        note: str = "",
        approved_by: str = "",
    ) -> Checkpoint:
        if stage not in STAGE_ORDER:
            raise ValueError(f"未知阶段: {stage}，可选 {STAGE_ORDER}")
        if status not in {s.value for s in StageStatus}:
            raise ValueError(f"非法状态: {status}")

        by = str(approved_by or "").strip()
        if human_approved and not by:
            by = "human"
        if status == StageStatus.COMPLETED.value and stage in self.gated and not _gate_allows(human_approved, by):
            raise ValueError(
                f"GATE VIOLATION: 阶段 {stage} 需要 human_approved 或 approved_by=human|produce 才能标记 completed"
            )

        prev = self.read(stage)
        if prev is not None and prev.status in (
            StageStatus.COMPLETED.value,
            StageStatus.AWAITING_HUMAN.value,
        ):
            self._archive(stage, prev)

        cp = Checkpoint(
            stage=stage,
            status=status,
            human_approved=human_approved,
            approved_by=by,
            artifact=artifact,
            note=note,
            updated_at=_now(),
        )
        self._path(stage).write_text(
            json.dumps(cp.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return cp

    def _archive(self, stage: str, prev: Checkpoint) -> None:
        history = self.project_dir / "history"
        history.mkdir(parents=True, exist_ok=True)
        stamp = prev.updated_at.replace(":", "").replace("+", "_").replace("-", "")
        target = history / f"checkpoint_{stage}_{stamp}.json"
        if not target.exists():
            target.write_text(
                json.dumps(prev.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    # -- 状态查询 ---------------------------------------------------------

    def next_stage(self) -> str | None:
        """返回第一个未 completed 的阶段；全部完成返回 None。"""
        for stage in STAGE_ORDER:
            cp = self.read(stage)
            if cp is None or cp.status != StageStatus.COMPLETED.value:
                return stage
        return None

    def progress(self) -> dict[str, str]:
        """阶段 -> 状态，供看板/CLI 展示。"""
        return {
            stage: (self.read(stage).status if self.read(stage) else StageStatus.PENDING.value)
            for stage in STAGE_ORDER
        }
