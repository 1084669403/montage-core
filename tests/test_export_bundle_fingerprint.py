"""交付包指纹：包里的成片必须仍是磁盘上那一版。

动机是实测事故——produce 出包后又单独重编码了带字幕的 final.mp4，包里的成片
变成旧版，而机器记录仍指向那个包，对外表现为"成片没有字幕"。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from montage.tools.export_bundle import build_bundle, sha256_file, verify_bundle


def _fake_project(root: Path) -> Path:
    (root / "renders").mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "renders" / "final.mp4").write_bytes(b"subtitle-less-master")
    (root / "renders" / "final.ass").write_text("dialogue\n", encoding="utf-8")
    (root / "artifacts" / "render_report.json").write_text("{}", encoding="utf-8")
    (root / "project.json").write_text(
        json.dumps({"project_id": "demo"}), encoding="utf-8"
    )
    return root


def test_bundle_records_render_fingerprint(tmp_path):
    project = _fake_project(tmp_path / "proj")
    result = build_bundle(project, tmp_path / "exports")
    with zipfile.ZipFile(result["output"]) as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    rows = {row["path"]: row for row in manifest["render_fingerprints"]}
    assert "renders/final.mp4" in rows
    assert rows["renders/final.mp4"]["sha256"] == sha256_file(
        project / "renders" / "final.mp4"
    )


def test_verify_bundle_passes_when_unchanged(tmp_path):
    project = _fake_project(tmp_path / "proj")
    result = build_bundle(project, tmp_path / "exports")
    report = verify_bundle(result["output"], project)
    assert report["checked"] is True
    assert report["ok"] is True
    assert report["mismatches"] == []


def test_verify_bundle_flags_stale_bundle_after_reencode(tmp_path):
    """出包后重编码成片（例如补烧字幕）→ 必须报"包已过期"。"""
    project = _fake_project(tmp_path / "proj")
    result = build_bundle(project, tmp_path / "exports")
    # 模拟"又单独重编码了一版带字幕的成片"
    (project / "renders" / "final.mp4").write_bytes(b"subtitle-burned-master")
    report = verify_bundle(result["output"], project)
    assert report["checked"] is True
    assert report["ok"] is False
    assert any(row["path"] == "renders/final.mp4" for row in report["mismatches"])


def test_verify_bundle_reports_missing_bundle(tmp_path):
    report = verify_bundle(tmp_path / "nope.zip", tmp_path)
    assert report["ok"] is False
    assert report["missing"]
