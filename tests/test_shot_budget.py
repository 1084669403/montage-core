"""W3 波次 1：shot_budget 分层与 30% pending hero。"""

from __future__ import annotations

from montage.engine.artifacts import ArtifactStore
from montage.engine.bible import compile_bible
from montage.engine.project import init_project
from montage.engine.shot_budget import (
    apply_kind_from_class,
    enforce_hero_cap,
    fill_shot_budget_class,
    pending_hero_ratio,
    trim_pending_hero,
)

from test_produce import _run, _seed_project
from test_produce_gen import _gen_bag, _seed_gen


def _plan(*classes, duration=5.0):
    shots = []
    for i, klass in enumerate(classes, start=1):
        row = {
            "shot_id": f"sh{i:02d}",
            "shot_kind": "video",
            "duration_seconds": duration,
            "visual_details": {
                "environment": "雨夜街道",
                "subjects": [{"id": "a", "action": {"verb": "停"}}],
            },
        }
        if klass:
            row["shot_budget_class"] = klass
        shots.append(row)
    return {"scenes": [{"id": "sc01", "shots": shots}]}


def test_fill_defaults_talk_never_hero():
    plan = _plan("", "HERO", "nope", "establishing")
    fill_shot_budget_class(plan)
    classes = [s["shot_budget_class"] for s in plan["scenes"][0]["shots"]]
    assert classes == ["talk", "hero", "talk", "establishing"]


def test_compile_fills_talk_does_not_change_kind():
    bible = {
        "title": "雨",
        "playbook": "spoken_explain",
        "scenes": [{
            "id": "sc01",
            "narration": "讲解开始。下一句。",
            "duration_seconds": 10,
            "shots": [{"shot_id": "sc01_01"}],
        }],
    }
    out = compile_bible(bible, duration_policy={"kind": "none"})
    shot = (out["scene_plan"].get("scenes") or [{}])[0].get("shots") or []
    assert shot
    assert shot[0].get("shot_budget_class") == "talk"
    assert shot[0].get("shot_kind") == "video"


def test_kind_mapping_and_ready_video_kept():
    plan = _plan("talk", "hero", "establishing")
    apply_kind_from_class(plan, ready_video_ids={"sh01"})
    kinds = [s["shot_kind"] for s in plan["scenes"][0]["shots"]]
    assert kinds == ["video", "video", "image"]


def test_ratio_pending_ignores_existing_video(tmp_path):
    proj = init_project(tmp_path, "r", "r")
    plan = _plan("hero", "hero", "talk", "talk")
    vid = proj / "assets" / "videos" / "a.mp4"
    vid.parent.mkdir(parents=True, exist_ok=True)
    vid.write_bytes(b"vid")
    manifest = {"items": [{
        "id": "sh01_video", "kind": "video", "shot_id": "sh01", "path": str(vid),
    }]}
    ratio = pending_hero_ratio(plan, manifest, str(proj))
    assert abs(ratio - 0.25) < 1e-9
    cap = enforce_hero_cap(plan, manifest, str(proj))
    assert cap["ok"] is True


def test_two_pending_heroes_over_cap():
    plan = _plan("hero", "hero", "talk", "talk")
    cap = enforce_hero_cap(plan, {}, "")
    assert cap["ok"] is False
    assert cap["ratio"] == 0.5


def test_exact_thirty_passes():
    plan = _plan("hero", "talk", "talk", "talk", "talk", "talk", "talk", "talk", "talk", "talk")
    assert abs(pending_hero_ratio(plan) - 0.10) < 1e-9
    plan2 = {"scenes": [{"id": "sc01", "shots": [
        {"shot_id": "h", "shot_budget_class": "hero", "duration_seconds": 3},
        {"shot_id": "a", "shot_budget_class": "talk", "duration_seconds": 7},
    ]}]}
    cap = enforce_hero_cap(plan2)
    assert abs(cap["ratio"] - 0.30) < 1e-9
    assert cap["ok"] is True


def test_trim_drops_later_heroes():
    plan = _plan("hero", "hero", "talk", "talk")
    trim_pending_hero(plan)
    classes = [s["shot_budget_class"] for s in plan["scenes"][0]["shots"]]
    assert classes[0] == "hero"
    assert classes[1] == "talk"


def test_produce_over_hero_skips_generate(tmp_path):
    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _plan("hero", "hero", "talk", "talk"))
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, sample_hero=False)
    assert not result["success"]
    assert result["progress"]["status"] == "over_hero"
    assert "shot_dry_run" not in order


def test_produce_trim_hero_then_gen(tmp_path):
    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _plan("hero", "hero", "talk", "talk"))
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, sample_hero=False, trim_hero=True)
    assert result["success"], result["error"]
    plan = ArtifactStore(proj).read("scene_plan")
    classes = [s["shot_budget_class"] for s in plan["scenes"][0]["shots"]]
    assert classes.count("hero") == 1
    assert "shot_generate" in order


def test_unlabeled_gen_is_image_not_video(tmp_path):
    proj = _seed_gen(tmp_path)
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, sample_hero=False)
    assert result["success"], result["error"]
    assert "shot_generate" in order
    store = ArtifactStore(proj)
    plan = store.read("scene_plan")
    assert plan["scenes"][0]["shots"][0]["shot_kind"] == "image"
    items = (store.read("asset_manifest") or {}).get("items") or []
    assert any(i.get("kind") == "image" and i.get("shot_id") == "sh01" for i in items)
    assert not any(i.get("kind") == "video" and i.get("shot_id") == "sh01" for i in items)


def test_all_video_emits_video_for_talk(tmp_path):
    proj = _seed_gen(tmp_path)
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, sample_hero=False, all_video=True)
    assert result["success"], result["error"]
    items = (ArtifactStore(proj).read("asset_manifest") or {}).get("items") or []
    assert any(i.get("kind") == "video" and i.get("shot_id") == "sh01" for i in items)


def test_w0_complete_ignores_all_video(tmp_path):
    proj = _seed_project(tmp_path)
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, all_video=True)
    assert result["success"]
    assert "shot_dry_run" not in order
    assert order[0] == "soundtrack"
    notes = result["progress"].get("findings") or []
    assert any("成片已齐" in str(n.get("message") or "") for n in notes if isinstance(n, dict))


def test_next_hero_tail_helper():
    from montage.tools.shot_runner import _next_hero_tail

    timeline = [
        {"shot_id": "sh01", "shot_budget_class": "hero"},
        {"shot_id": "sh02", "cut": "bridge"},
    ]
    stills = {"sh02": {"path": "n.png", "url": "http://n"}}
    assert _next_hero_tail(timeline[0], timeline, stills) == ("n.png", "http://n")
    timeline[1]["cut"] = "hard"
    assert _next_hero_tail(timeline[0], timeline, stills) == ("", "")
    timeline[1]["cut"] = "bridge"
    timeline[0]["location_id"] = "alley"
    timeline[1]["location_id"] = "roof"
    assert _next_hero_tail(timeline[0], timeline, stills) == ("", "")
    assert _next_hero_tail(timeline[0], timeline[:1], stills) == ("", "")
