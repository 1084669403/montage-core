"""export_bundle — 项目导出包（100% 自创实现，纯标准库 zipfile）。

把项目交付物打包成一个 zip：成片（renders/）、规范产物（artifacts/）、
字幕（如有）、成本账本、决策日志、checkpoint、素材清单（assets/ 文件列表 +
许可证说明），并生成 manifest.json（清单/版本/时间戳）供归档与分发。

用法：``export_bundle`` 工具（capability=export），输入 project_dir + 输出目录。
"""

from __future__ import annotations

import hashlib
import json
import re
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
# 中间产物：assemble 把整片中间文件写成 renders/*.joined.mp4，而 cleanup_temps 排在
# export **之后**，不显式排除就会把一份整片长度的中间视频打进交付包（本片约 40MB）。
_EXCLUDE_SUFFIXES = (".joined.mp4", ".concat.txt")
# 本工具写出的包名固定为 <project_id>_<UTC 时间戳>.zip；只清理这种命名，
# 不碰用户在 exports/ 里手放的其它 zip。
_BUNDLE_STAMP_RE = re.compile(r"_\d{8}T\d{6}\.zip$")


def _prune_exports(output_dir: Path, project_id: str, keep: int) -> list[str]:
    """保留最新 ``keep`` 个本工具导出的包，删除更旧的；keep<=0 一律不删（默认安全）。"""
    if keep <= 0:
        return []
    candidates = [
        p for p in output_dir.glob(f"{project_id}_*.zip")
        if _BUNDLE_STAMP_RE.search(p.name)
    ]
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    removed: list[str] = []
    for stale in candidates[keep:]:
        try:
            stale.unlink()
        except OSError:
            continue
        removed.append(str(stale))
    return removed


def _excluded(rel: Path) -> bool:
    """``rel`` 是相对项目根的路径。

    隐藏目录/文件一律不入包：``.cache`` 是下载去重缓存，内容与 ``assets/images``
    逐字节重复（画皮项目里占包体 1/3），``.env`` 是密钥。
    """
    if rel.name.startswith(".env") or any(part.startswith(".") for part in rel.parts):
        return True
    if rel.name.endswith(_EXCLUDE_SUFFIXES):
        return True
    return any(part in _EXCLUDE_PARTS for part in rel.parts)


def sha256_file(path: str | Path, *, chunk: int = 1 << 20) -> str:
    """文件 sha256（分块读，避免把整片读进内存）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def verify_bundle(
    bundle: str | Path,
    project_dir: str | Path,
) -> dict[str, Any]:
    """核对交付包里的成片是否**仍是**磁盘上那一版。

    动机（实测事故）：produce 出包之后又单独重编码了带字幕的 ``final.mp4``，
    包里的成片就变成了旧版，而机器记录仍指向那个包——对外表现为"成片没有
    字幕"。这里按 manifest 里记的 sha256 逐条比对，任何一处不一致都报出来。

    返回 ``{checked, ok, mismatches[], missing[], bundle}``；manifest 缺失或
    没记指纹时 ``checked=False``（老包按"没测到"处理，不误报）。
    """
    bundle_path = Path(bundle)
    project = Path(project_dir)
    report: dict[str, Any] = {
        "bundle": str(bundle_path), "checked": False, "ok": True,
        "mismatches": [], "missing": [],
    }
    if not bundle_path.is_file():
        report["ok"] = False
        report["missing"].append(str(bundle_path))
        return report
    try:
        with zipfile.ZipFile(bundle_path) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            fingerprints = manifest.get("render_fingerprints") or []
            for row in fingerprints:
                rel = str(row.get("path") or "")
                want = str(row.get("sha256") or "")
                if not rel or not want:
                    continue
                report["checked"] = True
                try:
                    packed = sha256_file_from_zip(zf, rel)
                except KeyError:
                    report["ok"] = False
                    report["missing"].append(rel)
                    continue
                if packed != want:
                    report["ok"] = False
                    report["mismatches"].append({
                        "path": rel, "in_bundle": packed, "manifest": want,
                    })
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError, KeyError):
        report["ok"] = False
        report["missing"].append("manifest.json")
        return report
    # 与磁盘现状比对：包内成片与当前 renders/ 不一致 = 包已过期
    for row in fingerprints:
        rel = str(row.get("path") or "")
        if not rel:
            continue
        local = project / rel
        if not local.is_file():
            report["missing"].append(rel)
            report["ok"] = False
            continue
        current = sha256_file(local)
        if current != str(row.get("sha256") or ""):
            report["ok"] = False
            report["mismatches"].append({
                "path": rel, "on_disk": current, "manifest": str(row.get("sha256") or ""),
            })
    return report


def sha256_file_from_zip(zf: zipfile.ZipFile, name: str) -> str:
    """zip 内条目的 sha256。"""
    digest = hashlib.sha256()
    with zf.open(name) as handle:
        while True:
            block = handle.read(1 << 20)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def build_bundle(
    project_dir: str | Path,
    output_dir: str | Path,
    *,
    include_media: bool = True,
    keep: int = 0,
) -> dict[str, Any]:
    """打包项目为 zip；返回 {output, manifest, entries, ...}。

    ``keep`` > 0 时保留最新 ``keep`` 个导出包、删除更旧的；默认 0 = 只增不删。
    """
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
    render_fingerprints: list[dict[str, Any]] = []
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        for sub, pattern in _EXPORT_GLOBS:
            base = project_dir / sub
            if not base.exists():
                continue
            for f in sorted(base.glob(pattern)):
                if f.is_dir():
                    continue
                rel = f.relative_to(project_dir).as_posix()
                if _excluded(Path(rel)):
                    continue
                zf.write(f, rel)
                entries.append(rel)
                if sub == "renders" and f.suffix in (".mp4", ".webm"):
                    media_files.append(rel)
                    # 成片指纹：出包后若有人重编码 renders/，verify_bundle 能立刻
                    # 发现"登记的包不是最新成片"（实测字幕版就是这么丢的）。
                    render_fingerprints.append({
                        "path": rel,
                        "size_bytes": f.stat().st_size,
                        "sha256": sha256_file(f),
                    })

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
                if f.is_file() and not _excluded(f.relative_to(project_dir)):
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
            "render_fingerprints": render_fingerprints,
            "attributions": attributions,
            "note": "署名见 CREDITS.txt",
        }
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    # 保留策略：默认不删；显式 keep>0 才清理旧包（produce --prune-exports N）。
    pruned = _prune_exports(output_dir, project_id, int(keep or 0))
    return {
        "output": str(bundle),
        "manifest": manifest,
        "entries": len(entries),
        "size_bytes": bundle.stat().st_size,
        "pruned": pruned,
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
            "keep_exports": {
                "type": "integer",
                "default": 0,
                "description": "保留最新 N 个导出包，删除更旧的；默认 0 = 只增不删（安全）",
            },
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
            data = build_bundle(
                project_dir, output_dir,
                keep=int(inputs.get("keep_exports") or 0),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"导出失败: {exc}")
        return ToolResult(success=True, data=data)
