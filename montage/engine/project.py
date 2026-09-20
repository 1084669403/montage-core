"""project — 项目工作区初始化。

每个生产项目在 <root>/projects/<project_id>/ 下：
  project.json          项目元数据（看板读它）
  REVIEW.md             给人看的成片/进度入口
  PROGRESS_TRACKER.md   项目随行手册（init 时从 docs/PROJECT_TEMPLATE.md 复制，V39）
  STATUS.md             跨会话手账骨架（唯一手写可覆盖文件，V39）
  artifacts/            各阶段规范产物
  assets/{images,videos,audio,music,placed,kenburns}/  素材
  scratch/              produce 临时文件
  renders/              成片
  history/              checkpoint 历史归档
  checkpoint_<stage>.json  阶段状态

本文件为全新原创代码。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "docs"


def _copy_tracker_files(project_dir: Path) -> None:
    """V39：把随行手册复制入项目（缺才写，存在即跳过——绝不覆盖既有内容）。

    PROGRESS_TRACKER.md 一项目一份不重生成；STATUS.md 是唯一手写可覆盖文件，
    复制只提供空白骨架。模板缺失时静默跳过（向后兼容，旧库升级无痛）。
    写入显式 utf-8（Windows 缺省 GBK 会弄乱中文/mermaid，V50）。
    """
    template = _TEMPLATE_DIR / "PROJECT_TEMPLATE.md"
    tracker = project_dir / "PROGRESS_TRACKER.md"
    if not tracker.exists() and template.is_file():
        tracker.write_text(
            template.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    status = project_dir / "STATUS.md"
    if not status.exists():
        status.write_text(
            "# STATUS 手账\n\n"
            "<!-- 人写：本片关键决策/用户拍板记录/已知坑。唯一手写可覆盖文件。 -->\n"
            "<!-- 每停点 ≤5 行，只记跨会话不看就会重蹈覆辙的事。 -->\n",
            encoding="utf-8",
        )


def init_project(
    root: str | Path,
    project_id: str,
    title: str,
    pipeline_type: str = "cinematic",
) -> Path:
    """初始化项目工作区并写 project.json，返回项目目录。"""
    project_dir = Path(root) / "projects" / project_id
    for sub in ("artifacts", "renders", "history", "scratch"):
        (project_dir / sub).mkdir(parents=True, exist_ok=True)
    for sub in ("images", "video", "videos", "audio", "music", "placed", "kenburns"):
        (project_dir / "assets" / sub).mkdir(parents=True, exist_ok=True)

    meta = {
        "version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_id": project_id,
        "title": title,
        "pipeline_type": pipeline_type,
    }
    (project_dir / "project.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (project_dir / "REVIEW.md").write_text(
        "# REVIEW\n\n"
        "本目录是一部片子的项目文件夹。成片跑 `python -m montage produce .` "
        "（前提：磁盘上已有 clip）。\n",
        encoding="utf-8",
    )
    _copy_tracker_files(project_dir)
    return project_dir
