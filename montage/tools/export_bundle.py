"""export_bundle — 项目导出包（100% 自创实现，纯标准库 zipfile）。

把项目交付物打包成一个 zip：成片（renders/）、规范产物（artifacts/）、
字幕（如有）、成本账本、决策日志、checkpoint、素材清单（assets/ 文件列表 +
许可证说明），并生成 manifest.json（清单/版本/时间戳）供归档与分发。

用法：``export_bundle`` 工具（capability=export），输入 project_dir + 输出目录。
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_EXPORT_GLOBS = (
    ("renders", "*.mp4"),
    ("renders", "*.webm"),
    ("renders", "*.wav"),
    ("renders", "*.mp3"),
    ("renders", "*.jpg"),
    ("renders", "*.png"),
    ("renders", "*.srt"),
    ("renders", "*.ass"),
    ("artifacts", "*.json"),
    ("assets", "**/*"),
)

_EXCLUDE_PARTS = ("__pycache__", "tmp_autoedit", "auto_edit", "history")


def _excluded(path: Path) -> bool:
    if path.name.startswith(".env"):
        return True
    return any(part in _EXCLUDE_PARTS for part in path.parts)


def build_bundle(project_dir: str | Path, output_dir: str | Path, *, include_media: bool = True) -> dict[str, Any]:
    """打包项目为 zip；返回 {output, manifest, entries}。"""
    project_dir = Path(project_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    project_id = (project_dir / "project.json").exists() and json.loads(
        (project_dir / "project.json").read_text(encoding="utf-8")
    ).get("project_id") or project_dir.name
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    bundle = output_dir / f"{project_id}_{stamp}.zip"

    entries: list[dict[str, Any]] = []
    media_files: list[dict[str, Any]] = []
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        for sub, pattern in _EXPORT_GLOBS:
            base = project_dir / sub
            if not base.exists():
                continue
            for f in sorted(base.glob(pattern)):
                if f.is_dir() or _excluded(f):
                    continue
                rel = f.relative_to(project_dir).as_posix()
                zf.write(f, rel)
                entries.append(rel)
                if sub == "renders" and f.suffix in (".mp4", ".webm"):
                    media_files.append(rel)

        # 元数据文件（账本/决策/checkpoint）
        for name in ("cost.jsonl", "decisions.jsonl", "project.json"):
            p = project_dir / name
            if p.exists():
                zf.write(p, name)
                entries.append(name)
        for cp in sorted(project_dir.glob("checkpoint_*.json")):
            zf.write(cp, cp.name)
            entries.append(cp.name)

        # 素材清单（assets 下真实文件，含许可证说明占位）
        assets_dir = project_dir / "assets"
        if assets_dir.exists():
            for f in sorted(assets_dir.rglob("*")):
                if f.is_file() and not _excluded(f):
                    rel = f.relative_to(project_dir).as_posix()
                    media_files.append(rel)

        soundtrack_path = project_dir / "artifacts" / "soundtrack.json"
        attributions: list[str] = []
        if soundtrack_path.is_file():
            try:
                packed = json.loads(soundtrack_path.read_text(encoding="utf-8"))
                attributions = [
                    str(a) for a in (packed.get("attributions") or []) if str(a).strip()
                ]
            except (OSError, json.JSONDecodeError):
                attributions = []
        if attributions:
            credits_body = "\n".join(attributions) + "\n"
        else:
            credits_body = "本片配乐为 CC0 或未使用需署名材料。\n"
        zf.writestr("CREDITS.txt", credits_body)
        entries.append("CREDITS.txt")

        manifest = {
            "project_id": project_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "entries": entries,
            "media_files": media_files,
            "attributions": attributions,
            "note": "署名见 CREDITS.txt",
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    return {
        "output": str(bundle),
        "manifest": manifest,
        "entries": len(entries),
        "size_bytes": bundle.stat().st_size,
    }


class ExportBundle(BaseTool):
    """项目导出包：成片 + 产物 + 账本 + 素材清单 → zip。"""

    name = "export_bundle"
    version = "0.1.0"
    capability = "export"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["project_dir"],
        "properties": {
            "project_dir": {"type": "string", "description": "项目目录（含 renders/artifacts/assets）"},
            "output_dir": {"type": "string", "description": "导出目录（默认项目同级 exports/）"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        project_dir = Path(inputs.get("project_dir") or "")
        if not (project_dir / "project.json").exists():
            return ToolResult(success=False, error=f"项目目录无效（缺 project.json）: {project_dir}")
        output_dir = Path(inputs.get("output_dir") or project_dir.parent / "exports")
        try:
            data = build_bundle(project_dir, output_dir)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"导出失败: {exc}")
        return ToolResult(success=True, data=data)
