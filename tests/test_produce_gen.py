"""W2 produce GEN：缺 clip 时 dry_run→generate；有 clip 不跑 shot_runner。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from montage.engine.artifacts import ArtifactStore
from montage.engine.produce import (
    GEN_STEP_IDS,
    STEP_IDS,
    generation_needed,
    pick_sample_shot_id,
)
from montage.engine.stages import CheckpointStore, StageStatus
from montage.engine.project import init_project
from montage.toolbase import ToolResult
from montage.tools.idea_developer import IdeaDeveloper
from montage.tools.shot_runner import ShotRunner

from test_produce import FakeTool, _bag, _run, _seed_project
from test_shot_runner import _fake_image, _fake_video, _pass_quality


def _scene_with_char():
    return {
        "character_registry": [
            {"id": "li", "name": "李", "appearance": "黑发左眉一道疤"},
        ],
        "scenes": [{
            "id": "sc01",
            "start_seconds": 0,
            "end_seconds": 5,
            "character_ids": ["li"],
            "shots": [{
                "shot_id": "sh01",
                "scene_id": "sc01",
                "duration_seconds": 5,
                "shot_kind": "video",
                "visual_details": {
                    "environment": "雨夜巷口",
                    "subjects": [{"id": "li"}],
                },
            }],
        }],
    }


def _seed_gen(root: Path, *, pipeline="cinematic"):
    proj = init_project(root, "gen", "生成", pipeline)
    ArtifactStore(proj).write("scene_plan", _scene_with_char())
    return proj


def _gen_bag(proj: Path):
    tools, order, bgm = _bag(proj)
    payloads: list[dict] = []
    video_outputs: list[str] = []

    def spy_video(inputs):
        video_outputs.append(str(inputs.get("output_path") or ""))
        return _fake_video(inputs)

    runner = ShotRunner(
        image_execute=_fake_image,
        video_execute=spy_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )

    class Counted(ShotRunner):
        def execute(self, inputs):
            if inputs.get("stage") == "cast":
                order.append("shot_cast")
                return runner.execute(inputs)
            payloads.append(dict(inputs))
            order.append("shot_dry_run" if inputs.get("dry_run") else "shot_generate")
            if inputs.get("dry_run"):
                assert inputs.get("record_ledger") is False
            assert "max_shots" not in inputs
            return runner.execute(inputs)

    counted = Counted(
        image_execute=_fake_image,
        video_execute=spy_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    counted.payloads = payloads
    counted.video_outputs = video_outputs
    tools["shot_runner"] = counted
    voice_calls: list[dict] = []

    def voice(inputs):
        order.append("voice")
        voice_calls.append(dict(inputs))
        return ToolResult(success=True, data={"assignments": []})

    tools["voice_director"] = FakeTool("voice_director", voice)
    return tools, order, bgm, voice_calls


def test_step_ids_not_mixed():
    assert "shot_dry_run" not in STEP_IDS
    assert GEN_STEP_IDS == ("shot_dry_run", "shot_generate", "shot_bind", "voice")
    assert STEP_IDS[-3:] == ("finish", "release", "export")


def test_w0_with_clips_skips_gen(tmp_path):
    proj = _seed_project(tmp_path)
    tools, order, _bgm, _voice = _gen_bag(proj)
    result = _run(proj, tools, sample_hero=False)
    assert result["success"]
    assert order[0] == "soundtrack"
    assert "shot_dry_run" not in order


def test_idea_still_skips_shot_runner(tmp_path):
    proj = init_project(tmp_path, "idea1", "想法", "cinematic")
    tools, order, _bgm = _bag(proj)
    tools["idea_developer"] = IdeaDeveloper()
    result = _run(proj, tools, idea="讲量子计算")
    assert "shot_dry_run" not in order
    assert "soundtrack" not in order
    assert result["progress"].get("status") in ("need_bible", "fail") or result["progress"].get("mode") == "idea"


def test_missing_scene_plan_gen_path(tmp_path):
    proj = init_project(tmp_path, "bare", "空", "cinematic")
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, sample_hero=False)
    assert not result["success"]
    assert order == []
    assert "scene_plan" in (result["error"] or "")


def test_gen_then_soundtrack(tmp_path):
    proj = _seed_gen(tmp_path)
    tools, order, _bgm, voice_calls = _gen_bag(proj)
    result = _run(proj, tools, sample_hero=False)
    assert result["success"], result["error"]
    assert order[:2] == ["shot_dry_run", "shot_generate"]
    assert "soundtrack" in order
    assert "voice" not in order
    assert voice_calls == []
    manifest = ArtifactStore(proj).read("asset_manifest")
    assert any(i.get("kind") == "image" and i.get("shot_id") == "sh01" for i in manifest["items"])
    assert not any(i.get("kind") == "video" and i.get("shot_id") == "sh01" for i in manifest["items"])
    cost = (proj / "cost.jsonl")
    if cost.is_file():
        assert "shot_runner/dry_run" not in cost.read_text(encoding="utf-8")


def test_shot_bind_backfills_and_preserves_index(tmp_path):
    """shot_bind 回填绑定并保留 inline 真序号；幂等，schema 校验通过。"""
    proj = _seed_gen(tmp_path)
    store = ArtifactStore(proj)
    store.write("asset_manifest", {
        "items": [],
        "reference_assets": [{
            "id": "portrait_li", "kind": "portrait", "character_id": "li",
            "path": "assets/images/p.png", "url": "https://example.test/p.png",
        }],
    })
    store.write("image_bindings", {"version": 1, "shots": {"sh01": {"refs": [
        {"id": "portrait_li", "kind": "portrait", "picture_index": 1, "source": "sent_plan"},
    ]}}, "cast": {}})
    from montage.engine.produce import _run_shot_bind
    from montage.schemas import get_schema

    progress = {"steps": {}}
    _run_shot_bind(proj, progress)
    bindings = store.read("image_bindings")
    refs = bindings["shots"]["sh01"]["refs"]
    assert [r["id"] for r in refs] == ["portrait_li"]
    # 回填写 recomputed，但真序号按 id 平移回来（不被洗掉）
    assert refs[0]["picture_index"] == 1
    assert bindings["cast"]["portrait_li"]["kind"] == "portrait"
    assert progress["steps"]["shot_bind"]["status"] == "ok"
    # 幂等：再跑一次结果一致
    _run_shot_bind(proj, {"steps": {}})
    again = store.read("image_bindings")
    assert again["shots"]["sh01"]["refs"][0]["picture_index"] == 1
    assert ArtifactStore.validate(again, get_schema("image_bindings")) == []


def test_partial_clips_only_fill_missing(tmp_path):
    proj = _seed_gen(tmp_path)
    store = ArtifactStore(proj)
    plan = _scene_with_char()
    plan["scenes"][0]["shots"].append({
        "shot_id": "sh02",
        "scene_id": "sc01",
        "duration_seconds": 5,
        "shot_kind": "video",
        "visual_details": {
            "environment": "雨夜巷口",
            "subjects": [{"id": "li"}],
        },
    })
    plan["scenes"][0]["end_seconds"] = 10
    store.write("scene_plan", plan)
    vid = proj / "assets" / "videos" / "sh01.mp4"
    vid.parent.mkdir(parents=True, exist_ok=True)
    from conftest import write_tiny_video

    write_tiny_video(vid)
    store.write("asset_manifest", {
        "items": [{
            "id": "sh01_video",
            "kind": "video",
            "path": str(vid),
            "shot_id": "sh01",
            "scene_id": "sc01",
        }],
        "reference_assets": [{
            "id": "portrait_li",
            "kind": "portrait",
            "character_id": "li",
            "path": str(vid),
            "url": "https://example.test/p.png",
        }],
    })
    assert generation_needed(proj)
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, sample_hero=False)
    assert result["success"], result["error"]
    assert order[:2] == ["shot_dry_run", "shot_generate"]
    items = (store.read("asset_manifest") or {}).get("items") or []
    assert any(i.get("shot_id") == "sh02" and i.get("kind") == "image" for i in items)
    assert not any(i.get("shot_id") == "sh02" and i.get("kind") == "video" for i in items)
    assert any(i.get("shot_id") == "sh01" and i.get("kind") == "video" for i in items)


def test_over_budget_skips_generate(tmp_path):
    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("proposal_packet", {
        "concept": "rain",
        "video_loop": "none",
        "budget_ceiling_usd": 0.001,
    })
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, sample_hero=False)
    assert not result["success"]
    assert result["progress"]["status"] == "over_budget"
    assert "shot_generate" not in order
    assert "soundtrack" not in order


def test_no_tts_skips_voice(tmp_path):
    proj = _seed_gen(tmp_path)
    tools, order, _bgm, voice_calls = _gen_bag(proj)
    assert _run(proj, tools, sample_hero=False, tts=False)["success"]
    assert voice_calls == []


def test_clip_factory_does_not_gen(tmp_path):
    proj = _seed_gen(tmp_path, pipeline="clip_factory")
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, sample_hero=False)
    assert not result["success"]
    assert "shot_dry_run" not in order
    err = result["error"] or ""
    assert "clip" in err.lower() or "manifest" in err or "不存在" in err


def test_idea_progress_not_resumed_as_generate(tmp_path):
    from montage.engine.bible import write_bible
    from test_bible import _ok_bible

    proj = _seed_gen(tmp_path)
    write_bible(proj, _ok_bible())
    (proj / "artifacts" / "produce_progress.json").write_text(
        json.dumps({
            "version": "1",
            "status": "await_bible",
            "mode": "idea",
            "steps": {"cascade": {"status": "ok"}},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    tools, order, _bgm, _ = _gen_bag(proj)
    tools["idea_developer"] = IdeaDeveloper()
    result = _run(proj, tools, resume=True)
    assert result["success"], result["error"]
    assert order[:3] == ["shot_cast", "shot_dry_run", "shot_generate"]
    assert result["progress"].get("mode") == "generate"
    assert result["progress"]["status"] == "await_sample"
    assert (proj / "artifacts" / "script.json").is_file()
    refs = (ArtifactStore(proj).read("asset_manifest") or {}).get("reference_assets") or []
    assert any(str(r.get("url") or "").startswith("http") for r in refs)


def test_await_bible_without_bible_fails(tmp_path):
    proj = _seed_gen(tmp_path)
    (proj / "artifacts" / "produce_progress.json").write_text(
        json.dumps({
            "version": "1",
            "status": "await_bible",
            "mode": "idea",
            "steps": {"cascade": {"status": "ok"}},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, sample_hero=False)
    assert not result["success"]
    assert "series_bible" in (result["error"] or "")
    assert "shot_generate" not in order


def _two_shot_plan(*, hero_second=True):
    plan = _scene_with_char()
    plan["scenes"][0]["shots"][0]["shot_budget_class"] = "talk" if hero_second else "hero"
    plan["scenes"][0]["shots"][0]["duration_seconds"] = 7 if hero_second else 3
    plan["scenes"][0]["shots"].append({
        "shot_id": "sh02",
        "scene_id": "sc01",
        "duration_seconds": 3 if hero_second else 7,
        "shot_kind": "video",
        "shot_budget_class": "hero" if hero_second else "talk",
        "visual_details": {
            "environment": "雨夜巷口",
            "subjects": [{"id": "li"}],
        },
    })
    plan["scenes"][0]["end_seconds"] = 10
    return plan


def test_pick_sample_prefers_first_hero(tmp_path):
    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _two_shot_plan(hero_second=True))
    assert pick_sample_shot_id(proj) == "sh02"


def test_default_sample_stops_before_w0(tmp_path):
    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _two_shot_plan(hero_second=True))
    tools, order, _bgm, voice_calls = _gen_bag(proj)
    result = _run(proj, tools, tts=True)
    assert result["success"], result["error"]
    assert result["progress"]["status"] == "await_sample"
    argv = result["progress"]["next"]["argv"]
    assert argv[:4] == [sys.executable, "-m", "montage", "produce"]
    assert "--resume" in argv
    assert result["progress"].get("sample_id") == "sh02"
    assert "soundtrack" not in order
    assert "voice" not in order
    assert voice_calls == []
    gens = [p for p in tools["shot_runner"].payloads if not p.get("dry_run")]
    assert len(gens) == 1
    assert gens[0].get("retry_ids") == ["sh02"]
    assert (result["progress"].get("steps") or {}).get("shot_generate", {}).get("status") != "ok"
    store = CheckpointStore(proj)
    for stage in ("proposal", "script", "scene_plan", "assets", "publish"):
        cp = store.read(stage)
        assert cp is None or cp.status != StageStatus.AWAITING_HUMAN.value


def test_resume_after_sample_force_regen_sample(tmp_path):
    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _two_shot_plan(hero_second=True))
    tools, order, _bgm, voice_calls = _gen_bag(proj)
    first = _run(proj, tools)
    assert first["progress"]["status"] == "await_sample"
    sample_videos = list(tools["shot_runner"].video_outputs)
    assert len(sample_videos) == 1
    tools2, order2, _bgm2, voice2 = _gen_bag(proj)
    second = _run(proj, tools2, resume=True)
    assert second["success"], second["error"]
    assert second["progress"]["status"] == "ok"
    assert "shot_dry_run" not in order2
    gens = [p for p in tools2["shot_runner"].payloads if not p.get("dry_run")]
    assert gens and "retry_ids" not in gens[0]
    assert gens[0].get("force_ids") == ["sh02"]
    assert "soundtrack" in order2
    assert voice2 == []
    assert len(tools2["shot_runner"].video_outputs) >= 1
    manifest = ArtifactStore(proj).read("asset_manifest") or {}
    vids = {i.get("shot_id") for i in manifest.get("items") or [] if i.get("kind") == "video"}
    assert "sh02" in vids
    assert "sh01" not in vids
    assert any(i.get("shot_id") == "sh01" and i.get("kind") == "image" for i in manifest.get("items") or [])
    script_cp = CheckpointStore(proj).read("script")
    assert script_cp is None or script_cp.status != StageStatus.COMPLETED.value
    assets_cp = CheckpointStore(proj).read("assets")
    if assets_cp and assets_cp.status == StageStatus.COMPLETED.value:
        assert assets_cp.human_approved is False
        assert assets_cp.approved_by == "produce"


def test_review_none_stops_at_final_prompt(tmp_path):
    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _two_shot_plan(hero_second=True))
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, review="none")
    assert result["success"], result.get("error")
    assert result["progress"]["status"] == "await_final_prompt"
    assert result["progress"].get("status") != "await_sample"
    assert (proj / "artifacts" / "shot_prompts.json").is_file()
    assert "soundtrack" not in order

    tools2, order2, _bgm2, _ = _gen_bag(proj)
    second = _run(proj, tools2, resume=True, review="none")
    assert second["success"], second.get("error")
    assert second["progress"]["status"] == "ok"
    assert "soundtrack" in order2
    gens = [
        p for p in tools2["shot_runner"].payloads
        if not p.get("dry_run") and p.get("stage") != "cast" and p.get("stage") != "prompt_preview"
    ]
    assert gens and "retry_ids" not in gens[0]


def test_w0_clips_do_not_write_gated_completed(tmp_path):
    proj = _seed_project(tmp_path)
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools)
    assert result["success"]
    assert "shot_dry_run" not in order
    store = CheckpointStore(proj)
    for stage in ("proposal", "script", "scene_plan", "assets", "publish"):
        cp = store.read(stage)
        assert cp is None or cp.status != StageStatus.COMPLETED.value


def test_gen_completes_publish_w0_does_not(tmp_path):
    w0 = _seed_project(tmp_path / "w0")
    tools, order, _bgm, _ = _gen_bag(w0)
    result = _run(w0, tools)
    assert result["success"]
    assert "shot_dry_run" not in order
    w0_pub = CheckpointStore(w0).read("publish")
    assert w0_pub is None or w0_pub.status != StageStatus.COMPLETED.value
    w0_log = ArtifactStore(w0).read("publish_log")
    assert w0_log and w0_log.get("status") == "exported"

    gen = _seed_gen(tmp_path / "gen")
    tools2, order2, _bgm2, _ = _gen_bag(gen)
    result2 = _run(gen, tools2, sample_hero=False)
    assert result2["success"], result2["error"]
    assert "shot_generate" in order2
    log = ArtifactStore(gen).read("publish_log")
    assert log and log.get("status") == "exported"
    pub = CheckpointStore(gen).read("publish")
    assert pub is not None
    assert pub.status == StageStatus.COMPLETED.value
    assert pub.human_approved is False
    assert pub.approved_by == "produce"


def test_produce_does_not_complete_script(tmp_path):
    proj = _seed_gen(tmp_path)
    tools, _order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, sample_hero=False)
    assert result["success"], result["error"]
    store = CheckpointStore(proj)
    for stage in ("proposal", "script", "scene_plan"):
        cp = store.read(stage)
        assert cp is None or cp.status != StageStatus.COMPLETED.value
    assets = store.read("assets")
    if assets and assets.status == StageStatus.COMPLETED.value:
        assert assets.human_approved is False
        assert assets.approved_by == "produce"


def test_retryable_ids_fail_full_generate(tmp_path):
    from montage.toolbase import ToolResult
    from test_produce import FakeTool, _bag
    from test_shot_runner import _fake_image, _pass_quality

    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _two_shot_plan(hero_second=False))
    tools, _order, _bgm = _bag(proj)
    tools["shot_runner"] = ShotRunner(
        image_execute=_fake_image,
        video_execute=lambda i: ToolResult(success=False, error="boom"),
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    tools["voice_director"] = FakeTool("voice_director", lambda i: ToolResult(success=True, data={"assignments": []}))
    result = _run(proj, tools, sample_hero=False)
    assert not result["success"]
    assert result["progress"]["status"] == "fail"
    assert "sh01" in (result["progress"].get("retryable_ids") or [])
    argv = result["progress"]["next"]["argv"]
    assert "--retry" in argv
    assert "sh01" in argv[argv.index("--retry") + 1]
    assert (result["progress"].get("steps") or {}).get("shot_generate", {}).get("status") == "fail"


def test_sample_retryable_does_not_await_sample(tmp_path):
    from montage.toolbase import ToolResult
    from test_produce import FakeTool, _bag
    from test_shot_runner import _fake_image, _pass_quality

    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _two_shot_plan(hero_second=False))
    tools, _order, _bgm = _bag(proj)
    tools["shot_runner"] = ShotRunner(
        image_execute=_fake_image,
        video_execute=lambda i: ToolResult(success=False, error="boom"),
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    tools["voice_director"] = FakeTool("voice_director", lambda i: ToolResult(success=True, data={"assignments": []}))
    result = _run(proj, tools)
    assert not result["success"]
    assert result["progress"]["status"] == "fail"
    assert result["progress"].get("status") != "await_sample"


def test_await_prompt_stops_without_resume(tmp_path):
    from test_produce import FakeTool, _bag
    from test_shot_runner import _fake_image, _fake_video, _pass_quality

    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _two_shot_plan(hero_second=True))
    tools, _order, _bgm = _bag(proj)
    inner = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )

    class Over(ShotRunner):
        def execute(self, inputs):
            if inputs.get("dry_run") or inputs.get("stage") == "cast":
                return inner.execute(inputs)
            return ToolResult(
                success=True,
                data={
                    "prompt_too_long": True,
                    "prompt_over_ids": ["sh02"],
                    "retryable_ids": [],
                    "findings": [],
                },
            )

    tools["shot_runner"] = Over(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    tools["voice_director"] = FakeTool(
        "voice_director", lambda i: ToolResult(success=True, data={"assignments": []}),
    )
    result = _run(proj, tools, sample_hero=False)
    assert result["success"], result.get("error")
    assert result["code"] == 0
    assert result["progress"]["status"] == "await_prompt"
    assert result["progress"]["next"]["argv"] == []
    assert "3000" in (result["progress"]["next"].get("note") or "")


def test_await_prompt_resume_sets_fallback(tmp_path):
    from test_produce import FakeTool, _bag
    from test_shot_runner import _fake_image, _fake_video, _pass_quality

    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _two_shot_plan(hero_second=True))
    (proj / "artifacts" / "produce_progress.json").write_text(
        json.dumps({
            "version": "1",
            "status": "await_prompt",
            "mode": "generate",
            "steps": {"shot_dry_run": {"status": "ok"}},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    tools, _order, _bgm = _bag(proj)
    inner = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    payloads: list[dict] = []

    class Spy(ShotRunner):
        def execute(self, inputs):
            payloads.append(dict(inputs))
            if inputs.get("dry_run") or inputs.get("stage") == "cast":
                return inner.execute(inputs)
            return ToolResult(
                success=True,
                data={"prompt_too_long": True, "prompt_over_ids": ["sh02"], "retryable_ids": []},
            )

    tools["shot_runner"] = Spy(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    tools["voice_director"] = FakeTool(
        "voice_director", lambda i: ToolResult(success=True, data={"assignments": []}),
    )
    result = _run(proj, tools, resume=True, sample_hero=False)
    gens = [p for p in payloads if not p.get("dry_run") and p.get("stage") != "cast"]
    assert gens and gens[0].get("prompt_fallback") is True
    assert result["progress"]["status"] == "await_prompt"


def test_await_prompt_headless_fails(tmp_path):
    proj = _seed_gen(tmp_path)
    ArtifactStore(proj).write("scene_plan", _two_shot_plan(hero_second=True))
    (proj / "artifacts" / "produce_progress.json").write_text(
        json.dumps({
            "version": "1",
            "status": "await_prompt",
            "mode": "generate",
            "steps": {},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, resume=True, headless=True)
    assert not result["success"]
    assert result["progress"]["status"] == "fail"
    assert "HEADLESS" in (result["error"] or "")
    assert "shot_generate" not in order


def _kling_sample_bible():
    return {
        "playbook": "cyberpunk_neon",
        "characters": [
            {"id": "a", "name": "阿甲", "appearance": "黑发齐耳", "outfit": "风衣"},
            {"id": "b", "name": "阿乙", "appearance": "银白短发", "outfit": "夹克"},
        ],
        "locations": [
            {"id": "alley", "appearance": "雨夜巷"},
            {"id": "roof", "appearance": "天台"},
        ],
    }


def _kling_three_shot_plan():
    return {
        "character_registry": [
            {"id": "a", "appearance": "黑发齐耳", "outfit_anchor": "风衣"},
            {"id": "b", "appearance": "银白短发", "outfit_anchor": "夹克"},
        ],
        "scenes": [{
            "id": "sc01",
            "start_seconds": 0,
            "end_seconds": 15,
            "character_ids": ["a", "b"],
            "location_id": "alley",
            "shots": [
                {
                    "shot_id": "sh01",
                    "scene_id": "sc01",
                    "duration_seconds": 3,
                    "shot_kind": "video",
                    "shot_budget_class": "hero",
                    "location_id": "alley",
                    "visual_details": {
                        "environment": "雨夜巷口",
                        "subjects": [{"id": "a"}],
                    },
                },
                {
                    "shot_id": "sh02",
                    "scene_id": "sc01",
                    "duration_seconds": 5,
                    "shot_kind": "video",
                    "shot_budget_class": "talk",
                    "location_id": "alley",
                    "visual_details": {
                        "environment": "雨夜巷口",
                        "subjects": [{"id": "a"}],
                    },
                },
                {
                    "shot_id": "sh03",
                    "scene_id": "sc01",
                    "duration_seconds": 5,
                    "shot_kind": "video",
                    "shot_budget_class": "talk",
                    "location_id": "roof",
                    "visual_details": {
                        "environment": "天台",
                        "subjects": [{"id": "b"}],
                    },
                },
            ],
        }],
    }


def test_kling_sample_cast_partial_and_phase_a_window(tmp_path, monkeypatch):
    monkeypatch.delenv("KLING_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    proj = _seed_gen(tmp_path)
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"concept": "雨", "video_loop": "kling"}, ensure_ascii=False),
        encoding="utf-8",
    )
    ArtifactStore(proj).write("scene_plan", _kling_three_shot_plan())
    ArtifactStore(proj).write("series_bible", _kling_sample_bible())
    tools, order, _bgm, _ = _gen_bag(proj)
    counted = tools["shot_runner"]
    orig = counted.execute
    casts: list[dict] = []

    def wrap(inputs):
        if inputs.get("stage") == "cast":
            casts.append(dict(inputs))
        return orig(inputs)

    counted.execute = wrap
    first = _run(proj, tools)
    assert first["success"], first["error"]
    assert first["progress"]["status"] == "await_sample"
    assert first["progress"].get("sample_id") == "sh01"
    cast_step = (first["progress"].get("steps") or {}).get("shot_cast") or {}
    assert cast_step.get("status") == "partial"
    pending = set(cast_step.get("pending_subjects") or [])
    assert "portrait/b" in pending
    assert "scene_ref/roof" in pending
    assert casts and casts[0].get("sample_shot_ids") == ["sh01"]
    gens = [p for p in counted.payloads if not p.get("dry_run")]
    assert gens
    assert gens[0].get("retry_ids") == ["sh01", "sh02"]
    assert gens[0].get("video_ids") == ["sh01"]
    assert len(counted.video_outputs) == 1
    assert "sh01" in counted.video_outputs[0]

    tools2, _order2, _bgm2, _ = _gen_bag(proj)
    counted2 = tools2["shot_runner"]
    orig2 = counted2.execute
    casts2: list[dict] = []

    def wrap2(inputs):
        if inputs.get("stage") == "cast":
            casts2.append(dict(inputs))
        return orig2(inputs)

    counted2.execute = wrap2
    second = _run(proj, tools2, resume=True)
    assert second["success"], second["error"]
    assert second["progress"]["status"] == "ok"
    resume_cast = (second["progress"].get("steps") or {}).get("shot_cast") or {}
    assert resume_cast.get("status") == "ok"
    assert casts2 and "sample_shot_ids" not in casts2[0]
    gens2 = [p for p in counted2.payloads if not p.get("dry_run")]
    assert gens2 and "retry_ids" not in gens2[0]
    assert gens2[0].get("force_ids") == ["sh01"]


def test_voice_step_assembles_narration_and_assemble_consumes_it(tmp_path):
    """narration_path repair: voice step assembles narration track; assemble payload carries it."""
    proj = _seed_gen(tmp_path)
    # _seed_gen 场景无对白（voice 会被 skip）——写进镜头对白触发 voice 步骤
    store0 = ArtifactStore(proj)
    plan = store0.read("scene_plan")
    plan["scenes"][0]["shots"][0]["audio_prompt"] = {
        "dialogue": [{"role": "李", "text": "雨还在下。"}],
    }
    store0.write("scene_plan", plan)
    tools, order, _bgm, voice_calls = _gen_bag(proj)
    calls: list[dict] = []

    # voice_director 返回两个 TTS 段（模拟真实 synthesize 后产物）
    def voice_with_sections(inputs):
        order.append("voice")
        voice_calls.append(dict(inputs))
        return ToolResult(success=True, data={
            "assignments": [],
            "narration_sections": [
                {"id": "sh01", "narration_audio": "assets/audio/line_00.mp3"},
                {"id": "sh02", "narration_audio": "assets/audio/line_01.mp3"},
            ],
        })

    tools["voice_director"] = FakeTool("voice_director", voice_with_sections)

    # ffmpeg_compose 记录 operation + narration_path
    assemble_payloads: list[dict] = []

    class FF(FakeTool):
        def execute(self, inputs):
            calls.append(dict(inputs))
            op = str(inputs.get("operation") or "assemble")
            if op == "assemble_narration":
                out = Path(inputs["output_path"])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(b"narr")
                return ToolResult(success=True, data={"output": str(out)})
            assemble_payloads.append(dict(inputs))
            return super().execute(inputs)

    tools["ffmpeg_compose"] = FF("ffmpeg_compose", tools["ffmpeg_compose"]._handler)

    result = _run(proj, tools, sample_hero=False, tts=True)
    assert result["success"], result["error"]
    ops = [c.get("operation") for c in calls]
    assert "assemble_narration" in ops
    ncall = next(c for c in calls if c.get("operation") == "assemble_narration")
    assert len(ncall["sections"]) == 2
    # assemble payload carries narration_path only when the file exists
    assert assemble_payloads, "assemble must be called"
    narration_sent = assemble_payloads[0].get("narration_path") or ""
    # fake narration file path written by assemble_narration branch
    expected = str(proj / "assets" / "audio" / "narration.mp3")
    assert narration_sent == expected
    # voice step records narration_path for resume
    vrow = (result["progress"].get("steps") or {}).get("voice") or {}
    assert vrow.get("narration_path") == expected


def test_voice_skip_means_no_narration_path(tmp_path):
    """no tts -> voice skip -> assemble payload must NOT carry narration_path."""
    proj = _seed_gen(tmp_path)
    tools, _order, _bgm, _vc = _gen_bag(proj)
    assemble_payloads: list[dict] = []

    orig_execute = tools["ffmpeg_compose"].execute

    class FF(FakeTool):
        def execute(self, inputs):
            if str(inputs.get("operation") or "") == "assemble":
                assemble_payloads.append(dict(inputs))
            return orig_execute(inputs)

    tools["ffmpeg_compose"] = FF("ffmpeg_compose", tools["ffmpeg_compose"]._handler)
    result = _run(proj, tools, sample_hero=False, tts=False)
    assert result["success"], result["error"]
    assert assemble_payloads
    assert not assemble_payloads[0].get("narration_path")
