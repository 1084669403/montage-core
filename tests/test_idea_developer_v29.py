"""V29 — idea_developer compile 的 project_dir 直读回退。

inputs 仅 {"operation":"compile"} + project_dir 时,从 <dir>/artifacts/series_bible.json
直读 bible(复用 format_card 同款回退惯例);无 bible 时报错。
validate 分支不受影响(其语义就是校验显式传入的稿)。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_bible import _ok_bible

from montage.engine.project import init_project
from montage.tools.idea_developer import IdeaDeveloper


def _write_bible_file(proj: Path, bible: dict) -> None:
    art = proj / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    (art / "series_bible.json").write_text(
        json.dumps(bible, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def test_compile_without_bible_reads_from_project_dir(tmp_path):
    proj = init_project(tmp_path, "v29a", "直读回退", "cinematic")
    bible = _ok_bible()
    bible["logline"] = "直读回退专属标记"
    _write_bible_file(proj, bible)

    result = IdeaDeveloper().execute({
        "operation": "compile",
        "project_dir": str(proj),
        # 极简 inputs：不内联 bible
    })
    assert result.success, result.error
    assert result.data["bible_source"] == "project_dir"
    assert result.data["bible"]["logline"] == "直读回退专属标记"
    assert (proj / "artifacts" / "script.json").is_file()
    assert (proj / "artifacts" / "scene_plan.json").is_file()


def test_compile_explicit_bible_wins_over_project_dir(tmp_path):
    proj = init_project(tmp_path, "v29b", "显式优先", "cinematic")
    _write_bible_file(proj, _ok_bible())

    explicit = _ok_bible()
    explicit["logline"] = "显式传入优先"
    result = IdeaDeveloper().execute({
        "operation": "compile",
        "project_dir": str(proj),
        "bible": explicit,
    })
    assert result.success, result.error
    assert result.data["bible_source"] == "inputs"
    assert result.data["bible"]["logline"] == "显式传入优先"


def test_compile_without_bible_and_without_project_file_fails(tmp_path):
    proj = init_project(tmp_path, "v29c", "无圣经", "cinematic")
    result = IdeaDeveloper().execute({
        "operation": "compile",
        "project_dir": str(proj),
    })
    assert not result.success
    assert "bible" in (result.error or "")


def test_validate_still_requires_explicit_bible(tmp_path):
    proj = init_project(tmp_path, "v29d", "validate 不改", "cinematic")
    _write_bible_file(proj, _ok_bible())
    result = IdeaDeveloper().execute({
        "operation": "validate",
        "project_dir": str(proj),
    })
    # validate 语义=校验显式传入的稿,不直读回退
    assert not result.success
    assert "bible" in (result.error or "")


def test_compile_direct_read_recompiles_after_edit(tmp_path):
    """模拟 V23 重编译条款：改项目内 bible 后,极简 inputs 重编译,改动进 scene_plan。"""
    proj = init_project(tmp_path, "v29e", "重编译链路", "cinematic")
    bible = _ok_bible()
    _write_bible_file(proj, bible)

    first = IdeaDeveloper().execute({"operation": "compile", "project_dir": str(proj)})
    assert first.success, first.error

    edited = _ok_bible()
    edited["scenes"][0]["environment"]["lighting"] = "冷白顶光直射"
    _write_bible_file(proj, edited)

    second = IdeaDeveloper().execute({"operation": "compile", "project_dir": str(proj)})
    assert second.success, second.error
    assert second.data["bible_source"] == "project_dir"
    plan_text = (proj / "artifacts" / "scene_plan.json").read_text(encoding="utf-8")
    assert "冷白顶光直射" in plan_text
