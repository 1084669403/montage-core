"""W5 波次 2：MONTAGE_HEADLESS 进门拒绝。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from montage.engine.artifacts import ArtifactStore
from montage.engine.bible import write_bible
from montage.engine.project import init_project
from montage.tools.idea_developer import IdeaDeveloper
from test_bible import _ok_bible
from test_episodes import _run as _run_series
from test_episodes import _seed_series, _series_bag
from test_produce import _bag, _run, _seed_project
from test_produce_gen import _gen_bag, _seed_gen, _two_shot_plan


def test_headless_blocks_sample_before_generate(tmp_path):
    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _two_shot_plan(hero_second=True))
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, headless=True)
    assert result["success"] is False
    assert result["code"] == 2
    assert result["progress"]["status"] == "fail"
    assert "MONTAGE_HEADLESS" in (result["error"] or "")
    assert result["progress"]["next"]["argv"][-2:] == ["--review", "none"]
    assert tools["shot_runner"].payloads == []
    assert "soundtrack" not in order


def test_headless_false_ignores_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MONTAGE_HEADLESS", "1")
    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _two_shot_plan(hero_second=True))
    tools, _order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, headless=False)
    assert result["success"]
    assert result["progress"]["status"] == "await_sample"


def test_headless_allows_idea_bible(tmp_path):
    proj = init_project(tmp_path, "h-idea", "圣经", "cinematic")
    write_bible(proj, _ok_bible())
    tools, order, _bgm = _bag(proj)
    tools["idea_developer"] = IdeaDeveloper()
    result = _run(proj, tools, idea="雨夜巷口对峙", headless=True)
    assert result["success"]
    assert result["progress"]["status"] == "await_bible"
    assert "soundtrack" not in order


def test_headless_allows_w0(tmp_path):
    proj = _seed_project(tmp_path)
    tools, order, _bgm = _bag(proj)
    result = _run(proj, tools, headless=True)
    assert result["success"]
    assert result["progress"]["status"] == "ok"
    assert order[0] == "soundtrack"


def test_headless_blocks_unconfirmed_retry(tmp_path):
    proj = _seed_project(tmp_path)
    tools, _order, _bgm, _ = _gen_bag(proj)
    first = _run(proj, tools, sample_hero=False)
    assert first["success"]
    tools2, order2, _bgm2, _ = _gen_bag(proj)
    second = _run(proj, tools2, retry_ids=["sh01"], retry_confirmed=False, headless=True)
    assert second["success"] is False
    assert second["progress"]["status"] == "fail"
    assert "--resume" in second["progress"]["next"]["argv"]
    assert "--review" not in second["progress"]["next"]["argv"]
    assert "shot_generate" not in order2
    assert tools2["shot_runner"].payloads == []


def test_headless_blocks_final_prompt_even_with_review_none(tmp_path):
    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _two_shot_plan(hero_second=True))
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, review="none", headless=True)
    assert result["success"] is False
    assert result["code"] == 2
    assert result["progress"]["status"] == "fail"
    assert "MONTAGE_HEADLESS" in (result["error"] or "")
    assert "await_final_prompt" in (result["error"] or "") or "HEADLESS" in (result["error"] or "")
    assert "soundtrack" not in order


def test_headless_blocks_series_before_call_ep(tmp_path):
    series = _seed_series(tmp_path)
    tools, _order, payloads = _series_bag()
    result = _run_series(series, tools, headless=True)
    assert result["success"] is False
    assert result["progress"]["status"] == "fail"
    assert payloads == []


def test_headless_series_resume_sample_allowed(tmp_path):
    series = _seed_series(tmp_path)
    tools, _order, _p = _series_bag()
    first = _run_series(series, tools)
    assert first["progress"]["status"] == "await_sample"
    tools2, _o2, payloads2 = _series_bag()
    second = _run_series(series, tools2, resume=True, headless=True)
    assert second["success"], second.get("error")
    assert second["progress"]["status"] == "await_episode"
    assert payloads2
