"""project — 项目工作区初始化。

每个生产项目在 <root>/projects/<project_id>/ 下：
  project.json          项目元数据（看板读它）
  REVIEW.md             给人看的成片/进度入口
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
    return project_dir
