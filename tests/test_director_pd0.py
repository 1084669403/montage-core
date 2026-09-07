"""P-D0：--review director 只切 idea 侧停点，不掉进 GEN。"""

from __future__ import annotations

import json
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_bible import _ok_bible
from test_produce import _bag, _run

from montage.cli import build_parser, main
from montage.engine.bible import write_bible
from montage.engine.project import init_project
from montage.tools.idea_developer import IdeaDeveloper


def _director_bible(**extra):
    bible = _ok_bible()
    bible["title"] = "雨夜巷口"
    bible["target_duration_seconds"] = 20
    bible["locations"] = [{
        "id": "alley",
        "name": "沿海旧巷",
        "sensory": "霓虹积水",
        "appearance": "雨夜旧巷，霓虹倒映积水，无人",
    }]
    bible.update(extra)
    return bible


def _idea_tools(proj: Path):
    tools, order, bgm = _bag(proj)
    tools["idea_developer"] = IdeaDeveloper()
    return tools, order, bgm


def test_review_default_is_director():
    ns = build_parser().parse_args(["produce", "proj"])
    assert ns.review == "director"
    ns = build_parser().parse_args(["produce", "proj", "--review", "bible"])
    assert ns.review == "bible"


def test_director_without_bible_still_need_bible(tmp_path):
    proj = init_project(tmp_path, "d0-empty", "想法", "cinematic")
    tools, order, _bgm = _idea_tools(proj)
    result = _run(proj, tools, idea="讲量子计算", review="director")
    assert result["progress"]["status"] == "need_bible"
    assert result["success"] is False
    assert "soundtrack" not in order
    assert not (proj / "artifacts" / "script.json").is_file()


def test_director_idea_stops_at_setup_without_compile(tmp_path):
    proj = init_project(tmp_path, "d0-setup", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    tools, order, _bgm = _idea_tools(proj)
    result = _run(proj, tools, idea="雨夜巷口对峙", review="director")
    assert result["success"], result.get("error")
    assert result["code"] == 0
    assert result["progress"]["status"] == "await_setup"
    assert "soundtrack" not in order
    assert not (proj / "artifacts" / "script.json").is_file()
    assert not (proj / "assets" / "videos").exists() or not any(
        (proj / "assets" / "videos").glob("*")
    )
    review = (proj / "artifacts" / "REVIEW.md").read_text(encoding="utf-8")
    assert "## 摘要" in review
    assert "## 全部可改" in review
    card = json.loads((proj / "artifacts" / "review_card.json").read_text(encoding="utf-8"))
    assert card["status"] == "await_setup"
    assert card["step"] == "setup"
    argv = result["progress"]["next"]["argv"]
    assert argv[:4] == [sys.executable, "-m", "montage", "produce"]
    assert "--resume" in argv
    assert "--idea" not in argv


def test_director_resume_without_flag_does_not_generate(tmp_path):
    proj = init_project(tmp_path, "d0-stay", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    tools, order, _bgm = _idea_tools(proj)
    _run(proj, tools, idea="雨夜巷口对峙", review="director")
    result = _run(proj, tools)
    assert result["success"]
    assert result["progress"]["status"] == "await_setup"
    assert "soundtrack" not in order
    assert not (proj / "artifacts" / "script.json").is_file()


def _cast_tools(proj: Path):
    from montage.tools.shot_runner import ShotRunner
    from test_shot_runner import _fake_image, _fake_video, _pass_quality

    tools, order, bgm = _idea_tools(proj)
    tools["shot_runner"] = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    return tools, order, bgm


def test_director_resume_walks_to_await_shots(tmp_path):
    proj = init_project(tmp_path, "d0-walk", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    tools, order, _bgm = _cast_tools(proj)
    first = _run(proj, tools, idea="雨夜巷口对峙", review="director")
    assert first["progress"]["status"] == "await_setup"
    outline = _run(proj, tools, resume=True)
    assert outline["progress"]["status"] == "await_outline", outline["progress"].get("findings")
    design = _run(proj, tools, resume=True)
    assert design["progress"]["status"] == "await_design"
    cast = _run(proj, tools, resume=True)
    assert cast["success"], cast.get("error")
    assert cast["progress"]["status"] == "await_cast"
    assert not (proj / "artifacts" / "script.json").is_file()
    assert (proj / "assets" / "images" / "portrait_a.png").is_file()
    assert (proj / "assets" / "images" / "turnaround_a.png").is_file()
    shots = _run(proj, tools, resume=True)
    assert shots["success"], shots.get("error")
    assert shots["progress"]["status"] == "await_shots"
    assert (proj / "artifacts" / "script.json").is_file()
    assert (proj / "artifacts" / "scene_plan.json").is_file()
    plan = json.loads((proj / "artifacts" / "scene_plan.json").read_text(encoding="utf-8"))
    assert plan["scenes"][0]["shots"][0].get("location_id") == "alley"
    assert "soundtrack" not in order
    assert not (proj / "renders" / "final.mp4").is_file()
    assert not (proj / "assets" / "videos").exists() or not any(
        (proj / "assets" / "videos").glob("*.mp4")
    )
    review = (proj / "artifacts" / "REVIEW.md").read_text(encoding="utf-8")
    assert "await_shots" in review
    argv = shots["progress"]["next"]["argv"]
    assert "--resume" in argv
    assert "--idea" not in argv


def test_director_setup_blocks_resume_without_duration(tmp_path):
    proj = init_project(tmp_path, "d0-block", "圣经", "cinematic")
    write_bible(proj, _ok_bible())
    tools, order, _bgm = _idea_tools(proj)
    first = _run(proj, tools, idea="雨夜巷口对峙", review="director")
    assert first["progress"]["status"] == "await_setup"
    stuck = _run(proj, tools, resume=True)
    assert stuck["success"]
    assert stuck["progress"]["status"] == "await_setup"
    assert "soundtrack" not in order
    fields = [str(f.get("field") or "") for f in (stuck["progress"].get("findings") or [])]
    assert any("target_duration_seconds" in f or "title" in f for f in fields)


def test_director_ignores_idea_after_setup(tmp_path):
    proj = init_project(tmp_path, "d0-ignore", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    tools, order, _bgm = _idea_tools(proj)
    _run(proj, tools, idea="雨夜巷口对峙", review="director")
    again = _run(proj, tools, idea="另一句想法会重置吗", review="director")
    assert again["progress"]["status"] == "await_setup"
    fields = [str(f.get("field") or "") for f in (again["progress"].get("findings") or [])]
    assert "idea" in fields
    assert not (proj / "artifacts" / "script.json").is_file()


def test_bible_review_unchanged(tmp_path):
    proj = init_project(tmp_path, "d0-bible", "圣经", "cinematic")
    write_bible(proj, _ok_bible())
    tools, order, _bgm = _idea_tools(proj)
    result = _run(proj, tools, idea="雨夜巷口对峙")
    assert result["progress"]["status"] == "await_bible"
    assert not (proj / "artifacts" / "script.json").is_file()
    assert "soundtrack" not in order


def test_cli_prints_await_setup(tmp_path):
    proj = init_project(tmp_path, "d0-cli", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    buf = StringIO()
    with redirect_stdout(buf):
        code = main(["produce", str(proj), "--idea", "雨夜巷口对峙", "--review", "director"])
    assert code == 0
    out = buf.getvalue()
    assert "await_setup" in out
    assert "produce: ok" not in out
