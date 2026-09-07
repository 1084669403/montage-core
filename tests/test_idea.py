"""W1 --idea 收编：不跑 W0 合成链。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_bible import _ok_bible
from test_produce import _bag, _run, _seed_project

from montage.engine.bible import write_bible
from montage.engine.produce import IDEA_STEP_IDS, STEP_IDS, run_produce
from montage.engine.project import init_project
from montage.registry import ToolRegistry
from montage.tools.idea_developer import IdeaDeveloper


def test_step_ids_not_mixed():
    assert "cascade" not in STEP_IDS
    assert IDEA_STEP_IDS == ("cascade", "validate_bible", "compile")


def test_idea_developer_discovered():
    reg = ToolRegistry()
    reg.discover()
    assert reg.get("idea_developer") is IdeaDeveloper
    assert reg.get("produce") is None


def test_idea_without_bible_does_not_touch_soundtrack(tmp_path):
    proj = init_project(tmp_path, "idea1", "想法", "cinematic")
    tools, order, _bgm = _bag(proj)
    tools["idea_developer"] = IdeaDeveloper()
    result = _run(proj, tools, idea="讲量子计算")
    assert result["progress"]["status"] == "need_bible"
    assert "soundtrack" not in order
    assert (proj / "artifacts" / "format_card.json").is_file()
    assert result["progress"]["next"]["argv"] == []
    card = (proj / "artifacts" / "format_card.json").read_text(encoding="utf-8")
    assert "spoken" in card


def test_idea_compiles_valid_bible_and_stops(tmp_path):
    proj = init_project(tmp_path, "idea2", "圣经", "cinematic")
    write_bible(proj, _ok_bible())
    tools, order, _bgm = _bag(proj)
    tools["idea_developer"] = IdeaDeveloper()
    result = _run(proj, tools, idea="雨夜巷口对峙")
    assert result["success"]
    assert result["progress"]["status"] == "await_bible"
    assert "soundtrack" not in order
    assert not (proj / "artifacts" / "script.json").is_file()
    assert not (proj / "artifacts" / "scene_plan.json").is_file()
    argv = result["progress"]["next"]["argv"]
    assert argv[:4] == [sys.executable, "-m", "montage", "produce"]
    assert "--idea" not in argv
    assert "--review" not in argv
    note = str(result["progress"]["next"].get("note") or "")
    assert "尚未编译" in note


def test_second_produce_compiles_then_samples(tmp_path):
    from test_produce_gen import _gen_bag

    proj = init_project(tmp_path, "idea2b", "圣经", "cinematic")
    bible = _ok_bible()
    bible["locations"] = [{
        "id": "alley",
        "name": "沿海旧巷",
        "sensory": "近处积水反霓虹，中景铁门半掩，远处港口灯塔轮廓",
        "appearance": "雨夜旧巷无人",
    }]
    write_bible(proj, bible)
    tools, order, _bgm = _bag(proj)
    tools["idea_developer"] = IdeaDeveloper()
    first = _run(proj, tools, idea="雨夜巷口对峙")
    assert first["success"]
    assert first["progress"]["status"] == "await_bible"
    assert not (proj / "artifacts" / "script.json").is_file()

    tools2, order2, _bgm2, _ = _gen_bag(proj)
    tools2["idea_developer"] = IdeaDeveloper()
    second = _run(proj, tools2)
    assert second["success"], second.get("error")
    assert (proj / "artifacts" / "script.json").is_file()
    plan = json.loads((proj / "artifacts" / "scene_plan.json").read_text(encoding="utf-8"))
    shot = plan["scenes"][0]["shots"][0]
    assert "积水反霓虹" in str(shot.get("location_sensory") or "")
    assert second["progress"]["status"] == "await_sample"
    assert order2[:3] == ["shot_cast", "shot_dry_run", "shot_generate"]
    assert "soundtrack" not in order
    refs = json.loads((proj / "artifacts" / "asset_manifest.json").read_text(encoding="utf-8"))
    assert any(
        str(r.get("kind") or "") in ("portrait", "turnaround", "scene_ref")
        and str(r.get("url") or "").startswith("http")
        for r in (refs.get("reference_assets") or [])
    )


def test_season_concat_ignored_on_idea(tmp_path):
    proj = init_project(tmp_path, "idea-season", "圣经", "cinematic")
    write_bible(proj, _ok_bible())
    tools, order, _bgm = _bag(proj)
    tools["idea_developer"] = IdeaDeveloper()
    result = _run(proj, tools, idea="雨夜巷口对峙", season_concat=True)
    assert result["success"], result.get("error")
    assert result["progress"]["status"] == "await_bible"
    assert "soundtrack" not in order
    assert not (proj / "renders" / "season.mp4").is_file()
    fields = [str(f.get("field") or "") for f in (result["progress"].get("findings") or [])]
    assert "season_concat" in fields


def test_idea_review_none_compiled_next_has_review_none(tmp_path):
    proj = init_project(tmp_path, "idea-none", "圣经", "cinematic")
    write_bible(proj, _ok_bible())
    tools, order, _bgm = _bag(proj)
    tools["idea_developer"] = IdeaDeveloper()
    result = _run(proj, tools, idea="雨夜巷口对峙", review="none")
    assert result["success"]
    assert result["progress"]["status"] == "compiled"
    assert "soundtrack" not in order
    argv = result["progress"]["next"]["argv"]
    assert "--idea" not in argv
    assert argv[-2:] == ["--review", "none"]


def test_w0_produce_ignores_bible(tmp_path):
    proj = _seed_project(tmp_path)
    write_bible(proj, _ok_bible())
    tools, order, _bgm = _bag(proj)
    result = _run(proj, tools)
    assert result["success"]
    assert order[0] == "soundtrack"


def test_idea_developer_compile_accepts_null_episode_plan():
    from montage.toolbase import validate_inputs

    errs = validate_inputs(IdeaDeveloper(), {
        "operation": "compile",
        "bible": {"logline": "x"},
        "format_card": None,
        "episode_plan": None,
        "project_dir": ".",
    })
    assert errs == []


def test_idea_developer_compile_injects_hits():
    card = {"library_hit_ids": ["characters/scar-glasses"]}
    bible = _ok_bible()
    bible["library_hit_ids"] = ["scenes/city-cyberpunk"]
    result = IdeaDeveloper().execute({
        "operation": "compile",
        "bible": bible,
        "format_card": card,
        "idea": "雨夜",
    })
    assert result.success
    hits = result.data["bible"]["library_hit_ids"]
    assert "characters/scar-glasses" in hits
    assert "scenes/city-cyberpunk" in hits
    assert result.data["script"]["library_hit_ids"]
