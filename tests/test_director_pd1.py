"""P-D1：定妆提前于 compile；await_cast 有失败不能进分镜。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_director_pd0 import _cast_tools, _director_bible, _idea_tools
from test_produce import _run

from montage.engine.bible import write_bible
from montage.engine.project import init_project
from montage.toolbase import ToolResult
from montage.tools.shot_runner import ShotRunner


def test_spoken_director_skips_cast(tmp_path):
    proj = init_project(tmp_path, "d1-spoken", "口播", "cinematic")
    write_bible(proj, _director_bible(playbook="spoken_explain", medium="spoken"))
    tools, order, _bgm = _cast_tools(proj)
    _run(proj, tools, idea="讲量子计算", review="director")
    _run(proj, tools, resume=True)
    _run(proj, tools, resume=True)
    shots = _run(proj, tools, resume=True)
    assert shots["progress"]["status"] == "await_shots", shots["progress"].get("findings")
    assert (proj / "artifacts" / "script.json").is_file()
    assert not (proj / "assets" / "images").exists() or not any(
        (proj / "assets" / "images").glob("portrait_*.png")
    )
    assert "soundtrack" not in order


def test_cast_failure_blocks_compile(tmp_path):
    proj = init_project(tmp_path, "d1-fail", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    tools, order, _bgm = _idea_tools(proj)

    def boom(inputs):
        return ToolResult(success=False, error="no key")

    tools["shot_runner"] = ShotRunner(
        image_execute=boom,
        video_execute=boom,
        image_estimate=lambda i: 0.04,
        quality_check=lambda *_a, **_k: {"ok": True, "issues": []},
    )
    _run(proj, tools, idea="雨夜巷口对峙", review="director")
    _run(proj, tools, resume=True)
    _run(proj, tools, resume=True)
    cast = _run(proj, tools, resume=True)
    assert cast["success"]
    assert cast["progress"]["status"] == "await_cast"
    stuck = _run(proj, tools, resume=True)
    assert stuck["progress"]["status"] == "await_cast"
    assert not (proj / "artifacts" / "script.json").is_file()
    review = (proj / "artifacts" / "REVIEW.md").read_text(encoding="utf-8")
    assert "失败" in review or "await_cast" in review
    assert "soundtrack" not in order


def test_cli_prints_await_cast(tmp_path):
    from contextlib import redirect_stdout
    from io import StringIO

    from montage.cli import main

    proj = init_project(tmp_path, "d1-cli", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    tools, _order, _bgm = _cast_tools(proj)
    _run(proj, tools, idea="雨夜巷口对峙", review="director")
    _run(proj, tools, resume=True)
    _run(proj, tools, resume=True)
    cast = _run(proj, tools, resume=True)
    assert cast["progress"]["status"] == "await_cast"
    card = json.loads((proj / "artifacts" / "review_card.json").read_text(encoding="utf-8"))
    assert card["status"] == "await_cast"
    assert card["step"] == "cast"
    buf = StringIO()
    with redirect_stdout(buf):
        code = main(["produce", str(proj)])
    assert code == 0
    assert "await_cast" in buf.getvalue()
    assert "produce: ok" not in buf.getvalue()


def test_kling_director_compile_before_cast(tmp_path):
    proj = init_project(tmp_path, "d1-kling", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"concept": "雨", "video_loop": "kling"}, ensure_ascii=False),
        encoding="utf-8",
    )
    tools, order, _bgm = _idea_tools(proj)
    _run(proj, tools, idea="雨夜巷口对峙", review="director")
    _run(proj, tools, resume=True)
    _run(proj, tools, resume=True)
    shots = _run(proj, tools, resume=True)
    assert shots["progress"]["status"] == "await_shots", shots["progress"].get("findings")
    assert (proj / "artifacts" / "script.json").is_file()
    assert "soundtrack" not in order
    assert not (proj / "assets" / "images").exists() or not any(
        (proj / "assets" / "images").glob("portrait_*.png")
    )
