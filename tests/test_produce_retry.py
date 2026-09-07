"""W4 retry：强制 GEN、关样品停、await_retry code=0。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from montage.engine.produce import load_progress, match_retry_ids, run_produce
from montage.engine.project import init_project
from test_episodes import _run as _run_series
from test_episodes import _seed_series, _series_bag
from test_produce import _run, _seed_project
from test_produce_gen import _gen_bag, _seed_gen, _two_shot_plan
from montage.engine.artifacts import ArtifactStore


def test_match_retry_ids_shots(tmp_path):
    proj = _seed_project(tmp_path)
    assert match_retry_ids(proj, ["sh01"]) == ["sh01"]
    assert match_retry_ids(proj, ["nope"]) == []


def test_retry_preview_does_not_write_video(tmp_path):
    proj = _seed_project(tmp_path)
    tools, _order, _bgm, _ = _gen_bag(proj)
    first = _run(proj, tools, sample_hero=False)
    assert first["success"]
    assert first["progress"]["status"] == "ok"
    tools2, order2, _bgm2, _ = _gen_bag(proj)
    second = _run(proj, tools2, retry_ids=["sh01"], retry_confirmed=False)
    assert second["success"]
    assert second["code"] == 0
    assert second["progress"]["status"] == "await_retry"
    argv = second["progress"]["next"]["argv"]
    assert argv[:4] == [sys.executable, "-m", "montage", "produce"]
    assert "--resume" in argv
    assert "--yes" not in argv
    assert second["progress"]["retry_ids"] == ["sh01"]
    assert "shot_generate" not in order2
    assert "soundtrack" not in order2
    gens = [p for p in tools2["shot_runner"].payloads if not p.get("dry_run")]
    assert gens == []
    assert tools2["shot_runner"].video_outputs == []
    assert load_progress(proj)["steps"]["assemble"]["status"] == "ok"
    review = (proj / "artifacts" / "REVIEW.md").read_text(encoding="utf-8")
    assert "await_retry" in review
    card = json.loads((proj / "artifacts" / "review_card.json").read_text(encoding="utf-8"))
    assert card["status"] == "await_retry"
    assert "feature" not in json.dumps(card.get("choices") or {}, ensure_ascii=False)


def test_retry_resume_confirms_and_skips_sample(tmp_path):
    proj = _seed_project(tmp_path)
    tools, _order, _bgm, _ = _gen_bag(proj)
    _run(proj, tools, sample_hero=False)
    tools2, _o2, _b2, _ = _gen_bag(proj)
    preview = _run(proj, tools2, retry_ids=["sh01"], retry_confirmed=False)
    assert preview["progress"]["status"] == "await_retry"
    tools3, order3, _b3, _ = _gen_bag(proj)
    confirmed = _run(proj, tools3, resume=True, sample_hero=True)
    assert confirmed["success"], confirmed["error"]
    assert confirmed["progress"]["status"] != "await_sample"
    gens = [p for p in tools3["shot_runner"].payloads if not p.get("dry_run")]
    assert gens and gens[0].get("retry_ids") == ["sh01"]
    assert "shot_generate" in order3


def test_retry_confirmed_force_gen_when_clips_ready(tmp_path):
    proj = _seed_project(tmp_path)
    tools, _order, _bgm, _ = _gen_bag(proj)
    _run(proj, tools, sample_hero=False)
    tools2, order2, _b2, _ = _gen_bag(proj)
    result = _run(proj, tools2, retry_ids=["sh01"], retry_confirmed=True, sample_hero=True)
    assert result["success"], result["error"]
    assert result["progress"]["status"] != "await_sample"
    assert "shot_dry_run" in order2
    assert "shot_generate" in order2
    gens = [p for p in tools2["shot_runner"].payloads if not p.get("dry_run")]
    assert gens[0].get("retry_ids") == ["sh01"]


def test_retry_unknown_id_does_not_reset(tmp_path):
    proj = _seed_project(tmp_path)
    tools, _order, _bgm, _ = _gen_bag(proj)
    first = _run(proj, tools, sample_hero=False)
    assert first["progress"]["steps"]["assemble"]["status"] == "ok"
    tools2, order2, _b2, _ = _gen_bag(proj)
    bad = _run(proj, tools2, retry_ids=["nope"], retry_confirmed=True)
    assert not bad["success"]
    assert load_progress(proj)["status"] == "ok"
    assert load_progress(proj)["steps"]["assemble"]["status"] == "ok"
    assert "shot_generate" not in order2


def test_retry_clip_factory_fails(tmp_path):
    proj = _seed_gen(tmp_path, pipeline="clip_factory")
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, retry_ids=["sh01"], retry_confirmed=True)
    assert not result["success"]
    assert "切片厂" in (result["error"] or "")
    assert "shot_generate" not in order


def test_retry_series_root_fails(tmp_path):
    series = _seed_series(tmp_path)
    tools, _order, _ = _series_bag()
    result = _run_series(series, tools, retry_ids=["sh01"], retry_confirmed=True)
    assert not result["success"]
    assert "系列根" in (result["error"] or "")


def test_retry_during_await_sample_fails(tmp_path):
    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _two_shot_plan(hero_second=True))
    tools, _order, _bgm, _ = _gen_bag(proj)
    first = _run(proj, tools)
    assert first["progress"]["status"] == "await_sample"
    tools2, order2, _b2, _ = _gen_bag(proj)
    bad = _run(proj, tools2, retry_ids=["sh02"], retry_confirmed=True)
    assert not bad["success"]
    assert "await_sample" in (bad["error"] or "")
    assert load_progress(proj)["status"] == "await_sample"
    assert "shot_generate" not in order2


def test_retry_with_idea_fails(tmp_path):
    proj = init_project(tmp_path, "idea1", "想法", "cinematic")
    result = run_produce(proj, idea="讲量子计算", retry_ids=["sh01"], retry_confirmed=True)
    assert not result["success"]
    assert "互斥" in (result["error"] or "")


def test_cli_retry_and_yes():
    from montage.cli import build_parser

    ns = build_parser().parse_args(["produce", "proj", "--retry", "a,b", "--yes"])
    assert ns.retry == "a,b"
    assert ns.yes is True


def test_await_retry_kling_card_offers_feature(tmp_path):
    from montage.engine.director import apply_review_fields, write_director_review

    proj = _seed_project(tmp_path)
    ArtifactStore(proj).write("proposal_packet", {"concept": "雨", "video_loop": "kling"})
    card = write_director_review(proj, "await_retry", retry_ids=["sh01"])
    assert card["status"] == "await_retry"
    assert card["step"] == "retry"
    modes = [str(x.get("id")) for x in (card.get("choices") or {}).get("rework_mode") or []]
    assert modes == ["regenerate", "feature"]
    paths = [str(f.get("path")) for f in card.get("fields") or [] if isinstance(f, dict)]
    assert "scene_plan.shots[sh01].rework_mode" in paths
    assert "scene_plan.shots[sh01].revision_note" in paths
    assert not any(p.startswith("retry:") for p in paths)
    applied = apply_review_fields(proj, [
        {"path": "scene_plan.shots[sh01].rework_mode", "value": "feature"},
        {"path": "scene_plan.shots[sh01].revision_note", "value": "眼镜改圆框"},
    ], card=card)
    assert "scene_plan.shots[sh01].rework_mode" in applied["applied"]
    plan = ArtifactStore(proj).read("scene_plan")
    shot = plan["scenes"][0]["shots"][0]
    assert shot["rework_mode"] == "feature"
    assert shot["revision_note"] == "眼镜改圆框"
