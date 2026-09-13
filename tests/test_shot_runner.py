"""shot_runner：dry_run 估算、超预算停、mock 执行写产物；不打真实 API。"""

import json
from pathlib import Path

from montage.engine.artifacts import ArtifactStore
from montage.pipelines import CINEMATIC, CLIP_FACTORY, DOCUMENTARY
from montage.providers.jimeng import JimengImage, JimengVideo
from montage.registry import ToolRegistry
from montage.schemas import get_schema
from montage.toolbase import ToolResult
from montage.tools.script_to_scene_plan import convert_script_to_scene_plan
from montage.tools.shot_runner import (
    ShotRunner,
    _clears_bridge,
    collect_prop_ids,
    collect_shots,
    lift_shot_prompts,
    resolve_shot_refs,
)


def _fixture_script():
    path = Path(__file__).parent / "fixtures" / "script_complete.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _plan():
    return convert_script_to_scene_plan(_fixture_script())["scene_plan"]


def _pass_quality(path, *, expected_duration=None):
    return {"ok": True, "issues": []}


def _fake_tail(src, dst):
    Path(dst).write_bytes(b"tail")
    return dst


def _fake_image(inputs):
    out = Path(inputs["output_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"img")
    return ToolResult(success=True, data={"output": str(out), "url": "https://example.test/img.png"}, cost_usd=0.04)


def _fake_video(inputs):
    out = Path(inputs["output_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"vid")
    return ToolResult(success=True, data={"output": str(out), "url": "https://example.test/vid.mp4"}, cost_usd=0.2)


def test_tool_discovered():
    reg = ToolRegistry()
    reg.discover()
    assert reg.get("shot_runner") is not None


def test_collect_and_lift_from_nested_shots():
    plan = _plan()
    shots = collect_shots(plan)
    assert shots
    assert all(s.get("scene_id") for s in shots)
    lifted = lift_shot_prompts(shots)
    errors = ArtifactStore.validate(lifted, get_schema("shot_prompts"))
    assert errors == []


def test_dry_run_default_and_estimate(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()
    tool = ShotRunner(
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
    )
    result = tool.execute({"scene_plan": _plan(), "project_dir": str(proj)})
    assert result.success
    data = result.data
    assert data["dry_run"] is True
    assert data["estimated_usd"] > 0
    kinds = [j["kind"] for j in data["jobs"]]
    assert "portrait" in kinds
    assert "first_frame" in kinds
    assert "video" in kinds
    assert "prop" in kinds
    # dry_run 记账一条总估算，不写媒体
    assert not list((proj / "assets").glob("**/*")) if (proj / "assets").exists() else True
    cost = (proj / "cost.jsonl").read_text(encoding="utf-8")
    assert "shot_runner/dry_run" in cost


def test_dry_run_over_budget_does_not_call_execute(tmp_path):
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"concept": "x", "budget_ceiling_usd": 0.01}, ensure_ascii=False),
        encoding="utf-8",
    )
    called = {"n": 0}

    def boom(inputs):
        called["n"] += 1
        raise AssertionError("dry_run 不应打 API")

    tool = ShotRunner(
        image_execute=boom,
        video_execute=boom,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
    )
    result = tool.execute({"scene_plan": _plan(), "project_dir": str(proj), "dry_run": True})
    assert result.success
    assert result.data["over_budget"] is True
    assert called["n"] == 0


def test_execute_blocked_when_over_ceiling(tmp_path):
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"concept": "x", "budget_ceiling_usd": 0.01}, ensure_ascii=False),
        encoding="utf-8",
    )
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
    )
    result = tool.execute({"scene_plan": _plan(), "project_dir": str(proj), "dry_run": False})
    assert not result.success
    assert result.data["blocked"] is True
    assert not (proj / "assets" / "videos").exists() or not list((proj / "assets" / "videos").glob("*"))


def test_execute_writes_manifest_and_shot_prompts(tmp_path):
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: JimengImage().estimate_cost(i),
        video_estimate=lambda i: JimengVideo().estimate_cost(i),
        quality_check=_pass_quality,
        extract_last_frame=_fake_tail,
    )
    result = tool.execute({"scene_plan": _plan(), "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    data = result.data
    assert data["retryable_ids"] == []
    store = ArtifactStore(proj)
    prompts = store.read("shot_prompts")
    manifest = store.read("asset_manifest")
    assert prompts and prompts["shots"]
    assert all(s.get("scene_id") for s in prompts["shots"])
    assert ArtifactStore.validate(prompts, get_schema("shot_prompts")) == []
    assert ArtifactStore.validate(manifest, get_schema("asset_manifest")) == []
    portraits = [r for r in manifest["reference_assets"] if r["kind"] == "portrait"]
    assert portraits
    assert (proj / "assets" / "images" / f"portrait_{portraits[0]['character_id']}.png").exists()
    cost = (proj / "cost.jsonl").read_text(encoding="utf-8")
    assert "/" in cost  # subject 字段 = scene_id/shot_id
    video_jobs = [j for j in data["jobs"] if j["kind"] == "video"]
    assert video_jobs
    assert any(video_jobs[0]["subject"] in line for line in cost.splitlines())


def test_one_shot_failure_continues(tmp_path):
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    plan = {
        "character_registry": [{"id": "a", "appearance": "黑发", "outfit_anchor": "风衣"}],
        "scenes": [{
            "id": "sc01",
            "character_ids": ["a"],
            "shots": [
                {
                    "shot_id": "sc01_01",
                    "shot_kind": "video",
                    "duration_seconds": 5,
                    "visual_details": {
                        "environment": "雨夜",
                        "subjects": [{"id": "a", "appearance_anchor": "黑发", "action": {"verb": "走"}}],
                    },
                },
                {
                    "shot_id": "sc01_02",
                    "shot_kind": "video",
                    "duration_seconds": 5,
                    "visual_details": {
                        "environment": "雨夜",
                        "subjects": [{"id": "a", "appearance_anchor": "黑发", "action": {"verb": "停"}}],
                    },
                },
            ],
        }],
    }
    failed = {"n": 0}

    def video_once(inputs):
        failed["n"] += 1
        if "sc01_01" in inputs["output_path"]:
            return ToolResult(success=False, error="boom")
        return _fake_video(inputs)

    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=video_once,
        quality_check=_pass_quality,
        extract_last_frame=lambda src, dst: None,
    )
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert result.success
    assert "sc01_01" in result.data["retryable_ids"]
    assert "sc01_02" not in result.data["retryable_ids"]
    videos = [i for i in result.data["asset_manifest"]["items"] if i["kind"] == "video"]
    assert any(i["shot_id"] == "sc01_02" for i in videos)


def test_no_ceiling_does_not_block(tmp_path):
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    tool = ShotRunner(
        image_estimate=lambda i: 99.0,
        video_estimate=lambda i: 99.0,
        quality_check=_pass_quality,
    )
    result = tool.execute({"scene_plan": _plan(), "project_dir": str(proj), "dry_run": True})
    assert result.success
    assert result.data["over_budget"] is False
    assert result.data["budget_ceiling_usd"] is None


def test_pipelines_list_shot_runner_not_clip_factory():
    cine = next(s["tools"] for s in CINEMATIC["stages"] if s["name"] == "assets")
    doc = next(s["tools"] for s in DOCUMENTARY["stages"] if s["name"] == "assets")
    clip = next(s["tools"] for s in CLIP_FACTORY["stages"] if s["name"] == "assets")
    assert "shot_runner" in cine
    assert "shot_runner" in doc
    assert "shot_runner" not in clip
    scene_clip = next(s["tools"] for s in CLIP_FACTORY["stages"] if s["name"] == "scene_plan")
    assert "shot_runner" not in scene_clip


def test_collect_prop_ids_unique_cap():
    shots = [{
        "visual_details": {
            "objects": [
                {"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}, {"id": "a"},
            ],
        },
    }]
    ids = collect_prop_ids(shots)
    assert ids == ["a", "b", "c"]


def test_dry_run_includes_prop_from_fixture(tmp_path):
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    store = ArtifactStore(proj)
    store.write("script", _fixture_script())
    store.write("scene_plan", _plan())
    tool = ShotRunner(image_estimate=lambda i: 0.04, video_estimate=lambda i: 0.2)
    result = tool.execute({"project_dir": str(proj), "dry_run": True})
    assert result.success
    props = [j for j in result.data["jobs"] if j["kind"] == "prop"]
    assert len(props) == 1
    assert props[0]["prop_id"] == "lighter"


def test_execute_writes_prop_reference(tmp_path):
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    store = ArtifactStore(proj)
    store.write("script", _fixture_script())
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.01,
        video_estimate=lambda i: 0.01,
        quality_check=_pass_quality,
    )
    result = tool.execute({"scene_plan": _plan(), "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    manifest = ArtifactStore(proj).read("asset_manifest")
    assert ArtifactStore.validate(manifest, get_schema("asset_manifest")) == []
    props = [r for r in manifest["reference_assets"] if r["kind"] == "prop"]
    assert len(props) == 1
    assert props[0]["prop_id"] == "lighter"
    refs = resolve_shot_refs(_plan()["scenes"][0]["shots"][0], manifest, _fixture_script(), _plan())
    assert any(r.get("kind") == "prop" for r in refs)


def _agnes_proj(tmp_path):
    proj = tmp_path / "agnes"
    (proj / "artifacts").mkdir(parents=True)
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"concept": "rain", "video_loop": "agnes"}, ensure_ascii=False),
        encoding="utf-8",
    )
    return proj


def _two_shots():
    return {
        "scenes": [{
            "id": "sc01",
            "shots": [
                {
                    "shot_id": "sc01_01",
                    "shot_kind": "video",
                    "duration_seconds": 5,
                    "visual_details": {
                        "environment": "雨夜",
                        "subjects": [{"id": "a", "appearance_anchor": "黑发", "action": {"verb": "走"}}],
                    },
                },
                {
                    "shot_id": "sc01_02",
                    "shot_kind": "video",
                    "duration_seconds": 5,
                    "visual_details": {
                        "environment": "雨夜",
                        "subjects": [{"id": "a", "appearance_anchor": "黑发", "action": {"verb": "停"}}],
                    },
                },
            ],
        }],
    }


def test_agnes_duration_chunks():
    from montage.tools.shot_runner import agnes_duration_chunks

    assert agnes_duration_chunks(5, v25=False) == [5]
    assert agnes_duration_chunks(25, v25=False) == [18, 7]
    assert agnes_duration_chunks(19, v25=False) == [18]
    assert agnes_duration_chunks(16, v25=True) == [12, 4]
    assert agnes_duration_chunks(13, v25=True) == [12]


def test_agnes_phase_a_stills_before_videos(tmp_path):
    proj = _agnes_proj(tmp_path)
    order: list[str] = []

    def img(inputs):
        order.append("image")
        return _fake_image(inputs)

    def vid(inputs):
        order.append("video")
        return _fake_video(inputs)

    tool = ShotRunner(
        image_execute=img,
        video_execute=vid,
        image_estimate=lambda i: 0.01,
        video_estimate=lambda i: 0.01,
        quality_check=_pass_quality,
        extract_last_frame=lambda src, dst: None,
    )
    result = tool.execute({
        "scene_plan": _two_shots(),
        "project_dir": str(proj),
        "dry_run": False,
    })
    assert result.success, result.error
    assert "video" in order
    first_video = order.index("video")
    assert all(kind == "image" for kind in order[:first_video])
    videos = [i for i in result.data["asset_manifest"]["items"] if i["kind"] == "video"]
    assert len(videos) == 2
    assert all(str(i.get("url") or "").startswith("http") for i in videos)
    firsts = [i for i in result.data["asset_manifest"]["items"] if str(i.get("id") or "").endswith("_first")]
    assert all(str(i.get("url") or "").startswith("http") for i in firsts)
    prompts = result.data["shot_prompts"]["shots"]
    assert all(s.get("audio_source") == "agnes_prompt" for s in prompts)


def test_agnes_refine_when_duration_short(tmp_path):
    proj = _agnes_proj(tmp_path)
    calls: list[dict] = []

    def vid(inputs):
        calls.append(dict(inputs))
        return _fake_video(inputs)

    def short_quality(path, *, expected_duration=None):
        if len(calls) == 1:
            return {"ok": True, "issues": [], "duration_seconds": 3}
        return {"ok": True, "issues": [], "duration_seconds": 5}

    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=vid,
        image_estimate=lambda i: 0.01,
        video_estimate=lambda i: 0.01,
        quality_check=short_quality,
        extract_last_frame=lambda src, dst: None,
    )
    plan = {
        "scenes": [{
            "id": "sc01",
            "shots": [{
                "shot_id": "sc01_01",
                "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {"environment": "雨夜"},
            }],
        }],
    }
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    assert len(calls) == 1
    assert any("补时长" in str(f.get("message") or "") for f in (result.data or {}).get("findings") or [])


def test_agnes_dry_run_chunks_and_pacing_note(tmp_path):
    proj = _agnes_proj(tmp_path)
    tool = ShotRunner(
        image_estimate=lambda i: 0.01,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
    )
    plan = {
        "scenes": [{
            "id": "sc01",
            "shots": [{
                "shot_id": "sc01_01",
                "shot_kind": "video",
                "duration_seconds": 25,
                "visual_details": {"environment": "雨夜"},
            }],
        }],
    }
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": True})
    assert result.success
    video_jobs = [j for j in result.data["jobs"] if j["kind"] == "video"]
    assert [j["seconds"] for j in video_jobs] == [12, 12]
    assert "1 RPM" in (result.data.get("pacing_note") or "")


def test_record_ledger_false_skips_cost_jsonl(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()
    tool = ShotRunner(
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
    )
    result = tool.execute({
        "scene_plan": _plan(),
        "project_dir": str(proj),
        "dry_run": True,
        "record_ledger": False,
    })
    assert result.success
    cost = proj / "cost.jsonl"
    assert not cost.is_file() or "shot_runner/dry_run" not in cost.read_text(encoding="utf-8")


def test_skip_existing_video_excludes_dry_run_jobs(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    plan = _plan()
    first = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert first.success, first.error
    second = tool.execute({
        "scene_plan": plan,
        "project_dir": str(proj),
        "dry_run": True,
        "record_ledger": False,
    })
    assert second.success
    assert not [j for j in second.data["jobs"] if j["kind"] == "video"]
    assert second.data.get("skipped_ids")


def test_retry_ids_overrides_skip(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    plan = _plan()
    first = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert first.success, first.error
    shot_id = collect_shots(plan)[0]["shot_id"]
    again = tool.execute({
        "scene_plan": plan,
        "project_dir": str(proj),
        "dry_run": True,
        "record_ledger": False,
        "retry_ids": [shot_id],
    })
    assert any(j["kind"] == "video" and j.get("shot_id") == shot_id for j in again.data["jobs"])


def test_portrait_fail_blocks_i2v(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()

    def fail_portrait(inputs):
        if "portrait_" in str(inputs.get("output_path") or ""):
            return ToolResult(success=False, error="portrait boom")
        return _fake_image(inputs)

    videos = []

    def track_video(inputs):
        videos.append(inputs)
        return _fake_video(inputs)

    plan = {
        "character_registry": [
            {"id": "li", "name": "李", "appearance": "黑发左眉一道疤"},
        ],
        "scenes": [{
            "id": "sc01",
            "character_ids": ["li"],
            "shots": [{
                "shot_id": "s1",
                "scene_id": "sc01",
                "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {
                    "environment": "雨夜巷口",
                    "subjects": [{"id": "li"}],
                },
            }],
        }],
    }
    tool = ShotRunner(
        image_execute=fail_portrait,
        video_execute=track_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert not result.success
    assert "I2V" in (result.error or "") or "定妆" in (result.error or "")
    assert videos == []
    assert not any(
        i.get("kind") == "video"
        for i in (result.data or {}).get("asset_manifest", {}).get("items") or []
    )


def test_spoken_skips_portrait_gate(tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()
    plan = {
        "playbook": "spoken_explain",
        "scenes": [{
            "id": "sc01",
            "shots": [{
                "shot_id": "s1",
                "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {"environment": "讲台"},
            }],
        }],
    }
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    kinds = [j["kind"] for j in result.data.get("jobs") or []]
    assert "portrait" not in kinds


def test_agnes_character_missing_url_hard_fails(tmp_path):
    proj = _agnes_proj(tmp_path)

    def still_no_url(inputs):
        out = Path(inputs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"img")
        return ToolResult(success=True, data={"output": str(out)}, cost_usd=0.04)

    videos = []

    def track_video(inputs):
        videos.append(inputs)
        return _fake_video(inputs)

    plan = {
        "character_registry": [
            {"id": "li", "name": "李", "appearance": "黑发左眉一道疤"},
        ],
        "scenes": [{
            "id": "sc01",
            "character_ids": ["li"],
            "shots": [{
                "shot_id": "s1",
                "scene_id": "sc01",
                "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {
                    "environment": "雨夜",
                    "subjects": [{"id": "li"}],
                },
            }],
        }],
    }
    tool = ShotRunner(
        image_execute=still_no_url,
        video_execute=track_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert not result.success
    assert "URL" in (result.error or "") or "文生" in (result.error or "")
    assert videos == []
    assert all("降级文生视频" not in str(f.get("message") or "") or f.get("severity") == "critical"
               for f in (result.data or {}).get("findings") or [])


def test_agnes_v25_sends_images_not_extra_body(tmp_path):
    proj = _agnes_proj(tmp_path)
    videos: list[dict] = []

    def track(inputs):
        videos.append(dict(inputs))
        return _fake_video(inputs)

    plan = {
        "character_registry": [
            {"id": "li", "name": "李", "appearance": "黑发左眉一道疤"},
        ],
        "scenes": [{
            "id": "sc01",
            "character_ids": ["li"],
            "shots": [{
                "shot_id": "s1",
                "scene_id": "sc01",
                "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {
                    "environment": "雨夜",
                    "subjects": [{"id": "li"}],
                },
            }],
        }],
    }
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=track,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    assert videos
    payload = videos[0]
    assert payload.get("mode") == "reference"
    assert payload.get("images")
    assert all(str(u).startswith("http") for u in payload["images"])
    assert "extra_body" not in payload
    assert "first_frame" not in payload
    assert "videos" not in payload


def test_agnes_flash_ignores_user_video_url(tmp_path):
    proj = _agnes_proj(tmp_path)
    videos: list[dict] = []

    def track(inputs):
        videos.append(dict(inputs))
        return _fake_video(inputs)

    plan = {
        "scenes": [{
            "id": "sc01",
            "shots": [{
                "shot_id": "s1",
                "shot_kind": "video",
                "duration_seconds": 5,
                "video_url": "https://cdn.example/prev.mp4",
                "visual_details": {"environment": "雨夜"},
            }],
        }],
    }
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=track,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    assert videos
    assert "videos" not in videos[0]
    assert any("videos[]" in str(f.get("message") or "") for f in (result.data or {}).get("findings") or [])


def test_agnes_flash_images_ranks_and_caps():
    from montage.tools._shot_refs import _agnes_flash_images

    refs = [
        {"url": "https://x/prop.png", "kind": "prop"},
        {"url": "https://x/scene.png", "kind": "scene_ref"},
        {"url": "https://x/port.png", "kind": "portrait"},
        {"url": "https://x/turn.png", "kind": "turnaround"},
        {"url": "https://x/p2.png", "kind": "prop"},
        {"url": "https://x/p3.png", "kind": "prop"},
        {"url": "https://x/extra.png", "kind": "style_anchor"},
    ]
    # 定妆最前、四视图垫底；上限 5 张后 prop p3/style/turnaround 被截。
    assert _agnes_flash_images(refs) == [
        "https://x/port.png",
        "https://x/scene.png",
        "https://x/prop.png",
        "https://x/p2.png",
        "https://x/p3.png",
    ]


def test_agnes_flash_images_downgrades_turnaround_and_reports_drops():
    from montage.tools._shot_refs import _agnes_flash_image_plan, agnes_ref_findings

    refs = [
        {"url": "https://x/turn.png", "kind": "turnaround", "character_id": "c1"},
        {"url": "https://x/port.png", "kind": "portrait", "character_id": "c1"},
        {"url": "https://x/scene.png", "kind": "scene_ref", "location_id": "alley"},
        {"url": "/local/prop.png", "kind": "prop", "prop_id": "p1"},
    ]
    plan = _agnes_flash_image_plan(refs)
    assert plan["urls"] == [
        "https://x/port.png",
        "https://x/scene.png",
        "https://x/turn.png",
    ]
    assert [e["index"] for e in plan["entries"]] == [1, 2, 3]
    # 本地 prop 被 URL 过滤丢弃 → 出 finding
    findings = agnes_ref_findings(plan, "sc01/sh01")
    assert len(findings) == 1
    assert "道具参考图" in findings[0]["message"]
    assert findings[0]["field"] == "sc01/sh01"


def test_agnes_flash_images_identity_keeps_turnaround():
    """多角色镜不再硬丢四视图；identity 标记的条目统一 rank 0 防截断。"""
    from montage.tools._shot_refs import _agnes_flash_image_plan

    refs = [
        {"url": "https://x/t1.png", "kind": "turnaround", "character_id": "c1", "identity": True},
        {"url": "https://x/p1.png", "kind": "portrait", "character_id": "c1", "identity": True},
        {"url": "https://x/p2.png", "kind": "portrait", "character_id": "c2", "identity": True},
        {"url": "https://x/s1.png", "kind": "scene_ref", "location_id": "plaza"},
    ]
    plan = _agnes_flash_image_plan(refs)
    # identity 项（含 turnaround）先于 scene_ref，不被截断
    assert plan["urls"] == [
        "https://x/t1.png", "https://x/p1.png", "https://x/p2.png", "https://x/s1.png",
    ]
    assert plan["dropped"] == []
    # 身份图即便当选也不进 scene_ref/prop 的丢弃 finding
    from montage.tools._shot_refs import agnes_ref_findings

    assert agnes_ref_findings(plan, "sc01/sh02") == []


def test_agnes_flash_images_identity_beats_scene_ref_on_overflow():
    from montage.tools._shot_refs import _agnes_flash_image_plan

    refs = [
        {"url": "https://x/t.png", "kind": "turnaround", "character_id": "c1", "identity": True},
        {"url": "https://x/s1.png", "kind": "scene_ref", "location_id": "a"},
        {"url": "https://x/s2.png", "kind": "scene_ref", "location_id": "b"},
        {"url": "https://x/p1.png", "kind": "prop", "prop_id": "x"},
        {"url": "https://x/s3.png", "kind": "scene_ref", "location_id": "c"},
    ]
    plan = _agnes_flash_image_plan(refs, max_images=3)
    # 四视图当选也不被 scene_ref/prop 挤掉（rank 0）
    assert plan["urls"][0] == "https://x/t.png"
    assert len(plan["urls"]) == 3
    assert {d["kind"] for d in plan["dropped"]} == {"scene_ref", "prop"}


def test_agnes_flash_chunked_shot_reuses_images_no_videos(tmp_path, monkeypatch):
    proj = _agnes_proj(tmp_path)
    videos: list[dict] = []
    concat_calls: list[tuple[list[str], str]] = []

    def track(inputs):
        videos.append(dict(inputs))
        return _fake_video(inputs)

    def fake_concat(srcs, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"concat")
        concat_calls.append(([str(p) for p in srcs], str(dest)))

    monkeypatch.setattr("montage.compose.ffmpeg_engine.concat_videos", fake_concat)

    plan = {
        "character_registry": [
            {"id": "li", "name": "李", "appearance": "黑发左眉一道疤"},
        ],
        "scenes": [{
            "id": "sc01",
            "character_ids": ["li"],
            "shots": [{
                "shot_id": "s1",
                "scene_id": "sc01",
                "shot_kind": "video",
                "duration_seconds": 16,
                "visual_details": {
                    "environment": "雨夜",
                    "subjects": [{"id": "li"}],
                },
            }],
        }],
    }
    tool = ShotRunner(
        image_execute=_uniq_image,
        video_execute=track,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    assert len(videos) == 2
    assert videos[0].get("seconds") == 12
    assert videos[1].get("seconds") == 4
    assert videos[0].get("mode") == "reference"
    assert videos[1].get("mode") == "reference"
    assert videos[0].get("images")
    assert videos[0]["images"] == videos[1]["images"]
    assert "videos" not in videos[0]
    assert "videos" not in videos[1]
    assert concat_calls
    assert len(concat_calls[0][0]) == 2
    assert concat_calls[0][1].endswith("s1.mp4")


def _write_jimeng_loop(proj: Path) -> None:
    (proj / "artifacts").mkdir(parents=True, exist_ok=True)
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"concept": "t", "video_loop": "volcengine"}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_kling_loop(proj: Path) -> None:
    (proj / "artifacts").mkdir(parents=True, exist_ok=True)
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"concept": "t", "video_loop": "kling"}, ensure_ascii=False),
        encoding="utf-8",
    )


def _uniq_image(inputs):
    out = Path(inputs["output_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"img")
    return ToolResult(
        success=True,
        data={"output": str(out), "url": f"https://example.test/{out.name}"},
        cost_usd=0.04,
    )


def _two_scene_plan(*, shot_b=None, loc_a="", loc_b=""):
    a = {
        "shot_id": "a",
        "shot_kind": "video",
        "duration_seconds": 5,
        "visual_details": {
            "environment": "雨夜巷口",
            "subjects": [{"id": "x", "action": {"verb": "走"}}],
        },
    }
    b = {
        "shot_id": "b",
        "shot_kind": "video",
        "duration_seconds": 5,
        "visual_details": {
            "environment": "雨夜巷口，霓虹更亮",
            "subjects": [{"id": "x", "action": {"verb": "停"}}],
        },
    }
    if loc_a:
        a["location_id"] = loc_a
    if loc_b:
        b["location_id"] = loc_b
    if shot_b:
        b.update(shot_b)
    return {
        "character_registry": [{"id": "x", "appearance": "黑发", "outfit_anchor": "风衣"}],
        "scenes": [
            {"id": "sc01", "shots": [a]},
            {"id": "sc02", "shots": [b]},
        ],
    }


def test_clears_bridge_only_hard_or_location():
    assert _clears_bridge({"cut": "hard"}, "") is True
    assert _clears_bridge({}, "") is False
    assert _clears_bridge({"location_id": "alley"}, "alley") is False
    assert _clears_bridge({"location_id": "roof"}, "alley") is True
    assert _clears_bridge({"location_id": "roof"}, "") is False


def test_cross_scene_bridge_uses_prev_tail_as_first(tmp_path):
    proj = tmp_path / "p"
    _write_jimeng_loop(proj)
    seen = []

    def spy_video(inputs):
        seen.append(dict(inputs))
        return _fake_video(inputs)

    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=spy_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=_fake_tail,
    )
    result = tool.execute({"scene_plan": _two_scene_plan(), "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    by_stem = {Path(p.get("output_path") or "").stem: p for p in seen}
    second = by_stem["b"]
    assert "last_frame_path" not in second and "last_frame_url" not in second
    first_ref = str(second.get("image_path") or second.get("first_frame_path") or "")
    assert first_ref.endswith("a_tail.png")


def test_hard_cut_uses_own_first(tmp_path):
    proj = tmp_path / "p"
    _write_jimeng_loop(proj)
    seen = []

    def spy_video(inputs):
        seen.append(dict(inputs))
        return _fake_video(inputs)

    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=spy_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=_fake_tail,
    )
    plan = _two_scene_plan(shot_b={"cut": "hard"})
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    second = {Path(p.get("output_path") or "").stem: p for p in seen}["b"]
    first_ref = str(second.get("image_path") or second.get("first_frame_path") or "")
    assert first_ref.endswith("b_first.png")
    assert "tail" not in first_ref


def test_location_change_clears_bridge(tmp_path):
    proj = tmp_path / "p"
    _write_jimeng_loop(proj)
    seen = []

    def spy_video(inputs):
        seen.append(dict(inputs))
        return _fake_video(inputs)

    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=spy_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=_fake_tail,
    )
    plan = _two_scene_plan(loc_a="alley", loc_b="roof")
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    second = {Path(p.get("output_path") or "").stem: p for p in seen}["b"]
    first_ref = str(second.get("image_path") or second.get("first_frame_path") or "")
    assert first_ref.endswith("b_first.png")


def test_no_last_frame_caps_still_chains_first(tmp_path, monkeypatch):
    from montage.providers.capabilities import video_caps as real_caps
    import montage.tools.shot_runner as shot_runner_mod

    def fake_caps(*, tool="", provider=""):
        caps = real_caps(tool=tool, provider=provider)
        caps["last_frame"] = False
        caps["last_url_fields"] = ()
        caps["last_path_fields"] = ()
        return caps

    monkeypatch.setattr(shot_runner_mod, "video_caps", fake_caps)
    proj = tmp_path / "p"
    _write_jimeng_loop(proj)
    seen = []

    def spy_video(inputs):
        seen.append(dict(inputs))
        return _fake_video(inputs)

    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=spy_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=_fake_tail,
    )
    result = tool.execute({"scene_plan": _two_scene_plan(), "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    second = {Path(p.get("output_path") or "").stem: p for p in seen}["b"]
    assert "last_frame_path" not in second
    first_ref = str(second.get("image_path") or second.get("first_frame_path") or "")
    assert first_ref.endswith("a_tail.png")


def _cast_bible():
    return {
        "playbook": "cyberpunk_neon",
        "characters": [
            {
                "id": "a",
                "name": "阿甲",
                "appearance": "黑发齐耳，左眉一道旧疤",
                "outfit": "深灰风衣",
            },
            {
                "id": "b",
                "name": "阿乙",
                "appearance": "银白短发，右耳三枚耳环",
                "outfit": "红色皮夹克",
                "skip_turnaround": True,
            },
        ],
        "locations": [{
            "id": "alley",
            "name": "沿海旧巷",
            "appearance": "雨夜旧巷，霓虹积水，无人",
        }],
        "props": [{"id": "lighter", "appearance": "铜壳打火机"}],
    }


def test_cast_dry_run_without_scene_plan(tmp_path):
    from montage.tools.shot_runner import ShotRunner, collect_cast_jobs

    bible = _cast_bible()
    jobs = collect_cast_jobs(bible)
    kinds = [j["kind"] for j in jobs]
    assert kinds.count("portrait") == 2
    assert kinds.count("turnaround") == 1
    assert "scene_ref" in kinds
    assert "prop" in kinds
    assert not any(j.get("character_id") == "b" and j["kind"] == "turnaround" for j in jobs)

    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    tool = ShotRunner(
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
    )
    result = tool.execute({
        "stage": "cast",
        "bible": bible,
        "project_dir": str(proj),
        "dry_run": True,
    })
    assert result.success, result.error
    data = result.data
    assert data["stage"] == "cast"
    assert data["dry_run"] is True
    out_kinds = [j["kind"] for j in data["jobs"]]
    assert "portrait" in out_kinds
    assert "turnaround" in out_kinds
    assert "first_frame" not in out_kinds
    assert "video" not in out_kinds
    assert not (proj / "assets" / "images").exists() or not list((proj / "assets" / "images").glob("*"))


def test_kling_cast_jobs_ignore_skip_turnaround_and_filter_plan():
    from montage.tools.shot_runner import collect_cast_jobs

    bible = _cast_bible()
    jobs = collect_cast_jobs(bible, video_loop="kling")
    kinds = [j["kind"] for j in jobs]
    assert kinds.count("portrait") == 2
    assert "turnaround" not in kinds
    plan = {
        "scenes": [{
            "id": "sc01",
            "location_id": "alley",
            "character_ids": ["a"],
            "shots": [{
                "shot_id": "sh01",
                "scene_id": "sc01",
                "location_id": "alley",
                "visual_details": {"subjects": [{"id": "a"}]},
                "duration_seconds": 5,
            }],
        }],
    }
    filtered = collect_cast_jobs(bible, scene_plan=plan, video_loop="kling")
    cids = {j.get("character_id") for j in filtered if j["kind"] == "portrait"}
    assert cids == {"a"}
    assert any(j.get("location_id") == "alley" for j in filtered)

    plan["scenes"][0]["shots"].append({
        "shot_id": "sh02",
        "scene_id": "sc01",
        "location_id": "alley",
        "visual_details": {"subjects": [{"id": "a"}]},
        "duration_seconds": 5,
    })
    plan["scenes"].append({
        "id": "sc02",
        "location_id": "roof",
        "character_ids": ["b"],
        "shots": [{
            "shot_id": "sh03",
            "scene_id": "sc02",
            "location_id": "roof",
            "visual_details": {"subjects": [{"id": "b"}]},
            "duration_seconds": 5,
        }],
    })
    bible["locations"].append({"id": "roof", "appearance": "天台"})
    sample = collect_cast_jobs(
        bible, scene_plan=plan, video_loop="kling", sample_shot_ids=["sh01"],
    )
    sample_cids = {j.get("character_id") for j in sample if j["kind"] == "portrait"}
    sample_locs = {j.get("location_id") for j in sample if j["kind"] == "scene_ref"}
    assert sample_cids == {"a"}
    assert sample_locs == {"alley"}
    windowed = collect_cast_jobs(
        bible, scene_plan=plan, video_loop="kling", sample_shot_ids=["sh01", "sh02"],
    )
    assert {j.get("character_id") for j in windowed if j["kind"] == "portrait"} == {"a"}
    assert not any(j.get("location_id") == "roof" for j in windowed)


def test_cast_writes_portrait_and_turnaround_without_scene_plan(tmp_path):
    from montage.schemas import get_schema
    from montage.tools.shot_runner import ShotRunner

    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
    )
    result = tool.execute({
        "stage": "cast",
        "bible": _cast_bible(),
        "project_dir": str(proj),
        "dry_run": False,
    })
    assert result.success, result.error
    assert not (proj / "artifacts" / "scene_plan.json").is_file()
    manifest = result.data["asset_manifest"]
    kinds = {r["kind"] for r in manifest["reference_assets"]}
    assert "portrait" in kinds
    assert "turnaround" in kinds
    assert "scene_ref" in kinds
    assert "prop" in kinds
    assert (proj / "assets" / "images" / "portrait_a.png").is_file()
    assert (proj / "assets" / "images" / "turnaround_a.png").is_file()
    assert not (proj / "assets" / "images" / "turnaround_b.png").is_file()
    assert (proj / "assets" / "images" / "scene_alley.png").is_file()
    turn = [r for r in manifest["reference_assets"] if r["kind"] == "turnaround"][0]
    assert str(turn.get("url") or "").startswith("http")
    errors = ArtifactStore.validate(manifest, get_schema("asset_manifest"))
    assert errors == []


def test_cast_skips_spoken_explain(tmp_path):
    from montage.tools.shot_runner import ShotRunner

    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    called = {"n": 0}

    def boom(inputs):
        called["n"] += 1
        raise AssertionError("口播不应出定妆")

    tool = ShotRunner(
        image_execute=boom,
        video_execute=boom,
        image_estimate=lambda i: 0.04,
        quality_check=_pass_quality,
    )
    bible = _cast_bible()
    bible["playbook"] = "spoken_explain"
    bible["medium"] = "spoken"
    result = tool.execute({
        "stage": "cast",
        "bible": bible,
        "project_dir": str(proj),
        "dry_run": False,
    })
    assert result.success, result.error
    assert result.data.get("skipped_cast") is True
    assert called["n"] == 0
    assert result.data["jobs"] == []


def test_frames_stage_dry_run_has_no_video_jobs(tmp_path):
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    video_calls = []

    def spy_video(inputs):
        video_calls.append(dict(inputs))
        return _fake_video(inputs)

    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=spy_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
    )
    dry = tool.execute({
        "stage": "frames",
        "scene_plan": _plan(),
        "project_dir": str(proj),
        "dry_run": True,
    })
    assert dry.success, dry.error
    assert dry.data["stage"] == "frames"
    kinds = [j["kind"] for j in dry.data["jobs"]]
    assert "first_frame" in kinds
    assert "video" not in kinds
    ran = tool.execute({
        "stage": "frames",
        "scene_plan": _plan(),
        "project_dir": str(proj),
        "dry_run": False,
    })
    assert ran.success, ran.error
    assert video_calls == []
    firsts = list((proj / "assets" / "images").glob("*_first.png"))
    assert firsts
    assert not (proj / "assets" / "videos").exists() or not list((proj / "assets" / "videos").glob("*.mp4"))


def test_shot_dialogue_reads_dialogue_text_and_speaker():
    from montage.tools.shot_runner import _shot_dialogue

    shot = {
        "audio_prompt": {
            "dialogue": [{"dialogue_text": "站住", "role": "女子"}],
        },
    }
    assert _shot_dialogue(shot) == "站住"
    shot2 = {
        "audio_prompt": {
            "dialogue": [{"text": "站住", "speaker_id": "女子"}],
        },
    }
    assert _shot_dialogue(shot2) == "站住"


def test_kling_dual_bridge_packs_next_design_first(tmp_path, monkeypatch):
    monkeypatch.delenv("KLING_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    proj = tmp_path / "p"
    _write_kling_loop(proj)
    seen = []

    def spy_video(inputs):
        seen.append(dict(inputs))
        return _fake_video(inputs)

    tool = ShotRunner(
        image_execute=_uniq_image,
        video_execute=spy_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=_fake_tail,
        vlm_review=lambda _p, _c: {"ok": True, "skipped": True, "issues": []},
    )
    result = tool.execute({
        "scene_plan": _two_scene_plan(),
        "project_dir": str(proj),
        "dry_run": False,
    })
    assert result.success, result.error
    by_stem = {Path(p.get("output_path") or "").stem: p for p in seen}
    first = by_stem["a"]
    second = by_stem["b"]
    assert str(first.get("last_frame_url") or "").endswith("b_first.png")
    first_ref = str(second.get("image_url") or second.get("first_frame_url") or "")
    assert first_ref.endswith("a_tail.png")
    assert not second.get("last_frame_url")


def test_kling_location_change_omits_last_frame(tmp_path, monkeypatch):
    monkeypatch.delenv("KLING_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    proj = tmp_path / "p"
    _write_kling_loop(proj)
    seen = []

    def spy_video(inputs):
        seen.append(dict(inputs))
        return _fake_video(inputs)

    tool = ShotRunner(
        image_execute=_uniq_image,
        video_execute=spy_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=_fake_tail,
        vlm_review=lambda _p, _c: {"ok": True, "skipped": True, "issues": []},
    )
    plan = _two_scene_plan(loc_a="alley", loc_b="roof")
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    by_stem = {Path(p.get("output_path") or "").stem: p for p in seen}
    assert not by_stem["a"].get("last_frame_url")
    first_ref = str(by_stem["b"].get("image_url") or by_stem["b"].get("first_frame_url") or "")
    assert first_ref.endswith("b_first.png")


def test_kling_sample_window_skips_next_video(tmp_path, monkeypatch):
    monkeypatch.delenv("KLING_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    proj = tmp_path / "p"
    _write_kling_loop(proj)
    seen = []

    def spy_video(inputs):
        seen.append(dict(inputs))
        return _fake_video(inputs)

    tool = ShotRunner(
        image_execute=_uniq_image,
        video_execute=spy_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=_fake_tail,
        vlm_review=lambda _p, _c: {"ok": True, "skipped": True, "issues": []},
    )
    result = tool.execute({
        "scene_plan": _two_scene_plan(),
        "project_dir": str(proj),
        "dry_run": False,
        "retry_ids": ["a", "b"],
        "video_ids": ["a"],
    })
    assert result.success, result.error
    assert [Path(p.get("output_path") or "").stem for p in seen] == ["a"]
    assert str(seen[0].get("last_frame_url") or "").endswith("b_first.png")
    stills = [i for i in result.data["asset_manifest"]["items"] if i.get("kind") == "image"]
    assert {i.get("shot_id") for i in stills} >= {"a", "b"}


def test_kling_generate_look_sheet_retries_new_image(tmp_path, monkeypatch):
    monkeypatch.delenv("KLING_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    proj = tmp_path / "p"
    _write_kling_loop(proj)
    sheets: list[str] = []
    crops = {"n": 0}

    def track_image(inputs):
        path = str(inputs.get("output_path") or "")
        if Path(path).name.startswith("look_sheet"):
            sheets.append(path)
        return _uniq_image(inputs)

    def fake_crop(sheet_path, img_dir, cid):
        crops["n"] += 1
        dest = Path(img_dir)
        dest.mkdir(parents=True, exist_ok=True)
        portrait = dest / f"portrait_{cid}.png"
        portrait.write_bytes(b"p" * 3000)
        if crops["n"] == 1:
            return {"ok": False, "cells": {}, "notes": ["qc fail"]}
        return {
            "ok": True,
            "cells": {
                "portrait": str(portrait),
                "side": str(portrait),
                "back": str(portrait),
                "tq": str(portrait),
            },
            "notes": [],
        }

    monkeypatch.setattr(
        "montage.providers._kling_looksheet.crop_look_sheet", fake_crop,
    )
    tool = ShotRunner(
        image_execute=track_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
        vlm_review=lambda _p, _c: {"ok": True, "skipped": True, "issues": []},
    )
    plan = {
        "character_registry": [{"id": "x", "appearance": "黑发", "outfit_anchor": "风衣"}],
        "scenes": [{
            "id": "sc01",
            "character_ids": ["x"],
            "shots": [{
                "shot_id": "sh01",
                "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {
                    "environment": "雨夜",
                    "subjects": [{"id": "x", "action": {"verb": "走"}}],
                },
            }],
        }],
    }
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    assert len(sheets) == 2
    assert crops["n"] == 2


def test_kling_generate_prop_sheet_retries_and_writes_prop_ref(tmp_path, monkeypatch):
    monkeypatch.delenv("KLING_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    proj = tmp_path / "p"
    _write_kling_loop(proj)
    sheets: list[str] = []
    crop_n = {"cup": 0}
    voices: list[dict] = []

    def track_image(inputs):
        path = str(inputs.get("output_path") or "")
        if Path(path).name.startswith("look_sheet_prop"):
            sheets.append(path)
            prompt = str(inputs.get("prompt") or "")
            assert "四分之三" in prompt
            assert "无人无手" in prompt
        return _uniq_image(inputs)

    def fake_crop(sheet_path, img_dir, cid):
        dest = Path(img_dir)
        dest.mkdir(parents=True, exist_ok=True)
        portrait = dest / f"portrait_{cid}.png"
        portrait.write_bytes(b"p" * 3000)
        cells = {
            "portrait": str(portrait),
            "side": str(portrait),
            "back": str(portrait),
            "tq": str(portrait),
        }
        if cid == "cup":
            crop_n["cup"] += 1
            if crop_n["cup"] == 1:
                return {"ok": False, "cells": {}, "notes": ["qc fail"]}
        return {"ok": True, "cells": cells, "notes": []}

    def fake_voice(**kwargs):
        voices.append(kwargs)
        return {"ok": False, "skipped": True}

    monkeypatch.setattr(
        "montage.providers._kling_looksheet.crop_look_sheet", fake_crop,
    )
    monkeypatch.setattr("montage.providers.kling.kling_create_voice", fake_voice)
    tool = ShotRunner(
        image_execute=track_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
        vlm_review=lambda _p, _c: {"ok": True, "skipped": True, "issues": []},
    )
    plan = {
        "character_registry": [{"id": "x", "appearance": "黑发", "outfit_anchor": "风衣"}],
        "scenes": [{
            "id": "sc01",
            "character_ids": ["x"],
            "shots": [{
                "shot_id": "sh01",
                "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {
                    "environment": "雨夜",
                    "subjects": [{"id": "x", "action": {"verb": "走"}}],
                    "objects": [{"id": "cup", "name": "杯"}],
                },
            }],
        }],
    }
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    assert len(sheets) == 2
    assert crop_n["cup"] == 2
    assert voices == []
    manifest = ArtifactStore(proj).read("asset_manifest")
    props = [r for r in manifest["reference_assets"] if r.get("kind") == "prop"]
    assert len(props) == 1
    assert props[0]["prop_id"] == "cup"
    assert "front" in (props[0].get("views") or [])
    assert not any(
        r.get("kind") == "turnaround" and r.get("prop_id") == "cup"
        for r in manifest["reference_assets"]
    )


# ---- A1: _profile_aspect 取值链 ----

def test_profile_aspect_chain(monkeypatch):
    from montage.providers.agnes import AGNES_IMAGE_RATIOS, AGNES_VIDEO_RATIOS

    tool = ShotRunner()
    monkeypatch.delenv("AGNES_RATIO", raising=False)
    assert tool._profile_aspect({}, env_key="AGNES_RATIO", allowed=AGNES_IMAGE_RATIOS) == "16:9"
    assert tool._profile_aspect(
        {"output_profile": "douyin_vertical"}, env_key="AGNES_RATIO", allowed=AGNES_IMAGE_RATIOS,
    ) == "9:16"
    assert tool._profile_aspect(
        {"output_profile": "wechat_vertical"}, env_key="AGNES_RATIO", allowed=AGNES_IMAGE_RATIOS,
    ) == "9:16"
    assert tool._profile_aspect(
        {"output_profile": "cinematic_21_9"}, env_key="AGNES_RATIO",
        allowed=AGNES_VIDEO_RATIOS, allow_21_9=True,
    ) == "21:9"
    assert tool._profile_aspect(
        {"output_profile": "cinematic_21_9"}, env_key="AGNES_RATIO", allowed=AGNES_VIDEO_RATIOS,
    ) == "16:9"
    monkeypatch.setenv("AGNES_RATIO", "9:16")
    assert tool._profile_aspect(
        {"output_profile": "youtube_landscape"}, env_key="AGNES_RATIO", allowed=AGNES_IMAGE_RATIOS,
    ) == "9:16"
    # 非法/不适用于该媒介的值落回档案推导
    monkeypatch.setenv("AGNES_RATIO", "2:3")
    assert tool._profile_aspect(
        {"output_profile": "youtube_landscape"}, env_key="AGNES_RATIO", allowed=AGNES_VIDEO_RATIOS,
    ) == "16:9"
    assert tool._profile_aspect(
        {"output_profile": "youtube_landscape"}, env_key="AGNES_RATIO", allowed=AGNES_IMAGE_RATIOS,
    ) == "2:3"


def test_seedream_aspect_keeps_unvalidated_env(monkeypatch):
    tool = ShotRunner()
    monkeypatch.setenv("SEEDREAM_ASPECT", "not-a-ratio")
    assert tool._seedream_aspect({}) == "not-a-ratio"
    monkeypatch.delenv("SEEDREAM_ASPECT", raising=False)
    assert tool._seedream_aspect({"output_profile": "douyin_vertical"}) == "9:16"


# ---- V2/A1: 首帧 ratio 只对纯文生 ----

def _agnes_vertical_proj(tmp_path):
    proj = _agnes_proj(tmp_path)
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps(
            {"concept": "rain", "video_loop": "agnes", "output_profile": "douyin_vertical"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return proj


def test_first_frame_ratio_set_for_pure_t2i(tmp_path, monkeypatch):
    monkeypatch.setenv("MONTAGE_SKIP_PACING", "1")
    proj = _agnes_vertical_proj(tmp_path)
    images: list[dict] = []

    def cap(inputs):
        images.append(dict(inputs))
        return _fake_image(inputs)

    plan = {"scenes": [{"id": "sc01", "shots": [{
        "shot_id": "s1", "shot_kind": "video", "duration_seconds": 5,
        "visual_details": {"environment": "雨夜"},
    }]}]}
    tool = ShotRunner(
        image_execute=cap, video_execute=_fake_video,
        image_estimate=lambda i: 0.04, video_estimate=lambda i: 0.2,
        quality_check=_pass_quality, extract_last_frame=lambda *_a, **_k: None,
    )
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    first = next(p for p in images if str(p.get("output_path", "")).endswith("_first.png"))
    assert first.get("ratio") == "9:16"


def test_first_frame_ratio_skipped_for_img2img(tmp_path, monkeypatch):
    monkeypatch.setenv("MONTAGE_SKIP_PACING", "1")
    proj = _agnes_vertical_proj(tmp_path)
    images: list[dict] = []

    def cap(inputs):
        images.append(dict(inputs))
        return _fake_image(inputs)

    plan = {
        "character_registry": [{"id": "li", "name": "李", "appearance": "黑发"}],
        "scenes": [{
            "id": "sc01", "character_ids": ["li"],
            "shots": [{
                "shot_id": "s1", "scene_id": "sc01", "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {"environment": "雨夜", "subjects": [{"id": "li"}]},
            }],
        }],
    }
    tool = ShotRunner(
        image_execute=cap, video_execute=_fake_video,
        image_estimate=lambda i: 0.04, video_estimate=lambda i: 0.2,
        quality_check=_pass_quality, extract_last_frame=lambda *_a, **_k: None,
    )
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": False})
    assert result.success, result.error
    first = next(p for p in images if str(p.get("output_path", "")).endswith("_first.png"))
    assert first.get("operation") == "image_reference"
    assert "ratio" not in first


# ---- R1: pacing 只在缓存未命中时执行 ----

def test_pacing_skipped_on_cache_hit(tmp_path):
    from montage.tools.generation_cache import GenerationCache

    tool = ShotRunner()
    cache = GenerationCache(tmp_path / "c")
    pace_calls: list[int] = []
    gen_calls: list[int] = []

    def gen(payload):
        gen_calls.append(1)
        Path(payload["output_path"]).write_bytes(b"data")
        return ToolResult(success=True, data={"output": payload["output_path"]})

    out1 = tmp_path / "a.bin"
    r1 = tool._cached_or_generate(
        cache=cache, params={"a": 1}, output_path=str(out1), generate=gen,
        payload={"output_path": str(out1)}, pace=lambda: pace_calls.append(1),
    )
    assert r1.success and not (r1.meta or {}).get("cache_hit")
    out2 = tmp_path / "b.bin"
    r2 = tool._cached_or_generate(
        cache=cache, params={"a": 1}, output_path=str(out2), generate=gen,
        payload={"output_path": str(out2)}, pace=lambda: pace_calls.append(1),
    )
    assert (r2.meta or {}).get("cache_hit")
    assert len(pace_calls) == 1
    assert len(gen_calls) == 1


# ---- P1: 配额计数时机 ----

def _quota_env(tmp_path, monkeypatch):
    usage = tmp_path / "agnes_usage.json"
    monkeypatch.setenv("MONTAGE_AGNES_USAGE_PATH", str(usage))
    monkeypatch.setenv("AGNES_ACCESS_TYPE", "tokenplan")
    monkeypatch.setenv("MONTAGE_SKIP_PACING", "1")
    return usage


def test_quota_counts_fresh_not_cache_hit(tmp_path, monkeypatch):
    from montage.providers import agnes_usage
    from montage.tools.generation_cache import GenerationCache

    _quota_env(tmp_path, monkeypatch)
    tool = ShotRunner(image_execute=_fake_image, quality_check=_pass_quality)
    tool._agnes_image_active = True
    cache = GenerationCache(tmp_path / "c")
    out1 = tmp_path / "i1.png"
    r1 = tool._generate_with_retry(
        kind="image", payload={"output_path": str(out1), "prompt": "x", "size": "2K"},
        output_path=str(out1), cache=cache, cache_params={"prompt": "x"}, expected_duration=None,
    )
    assert r1.success
    assert agnes_usage.read_usage("tokenplan")["images"] == 1
    out2 = tmp_path / "i2.png"
    r2 = tool._generate_with_retry(
        kind="image", payload={"output_path": str(out2), "prompt": "x", "size": "2K"},
        output_path=str(out2), cache=cache, cache_params={"prompt": "x"}, expected_duration=None,
    )
    assert (r2.meta or {}).get("cache_hit")
    assert agnes_usage.read_usage("tokenplan")["images"] == 1


def test_quota_counts_before_quality_gate(tmp_path, monkeypatch):
    from montage.providers import agnes_usage
    from montage.tools.generation_cache import GenerationCache

    _quota_env(tmp_path, monkeypatch)

    def always_critical(path, *, expected_duration=None):
        return {"ok": False, "issues": [{"severity": "critical", "message": "bad"}]}

    tool = ShotRunner(image_execute=_fake_image, quality_check=always_critical)
    tool._agnes_image_active = True
    cache = GenerationCache(tmp_path / "c")
    out = tmp_path / "i.png"
    result = tool._generate_with_retry(
        kind="image", payload={"output_path": str(out), "prompt": "x", "size": "2K"},
        output_path=str(out), cache=cache, cache_params={"prompt": "x"}, expected_duration=None,
    )
    assert not result.success
    # 每次重试都真实消耗配额（质量门失败也算），MAX_ATTEMPTS=3
    assert agnes_usage.read_usage("tokenplan")["images"] == 3


# ---- R1: dry_run 排片提示含档位与图片/视频 ----

def test_dry_run_pacing_note_reports_tier(tmp_path, monkeypatch):
    proj = _agnes_proj(tmp_path)
    monkeypatch.setenv("AGNES_ACCESS_TYPE", "tokenplan")
    tool = ShotRunner(
        image_estimate=lambda i: 0.01, video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
    )
    plan = {"scenes": [{"id": "sc01", "shots": [{
        "shot_id": "s1", "shot_kind": "video", "duration_seconds": 5,
        "visual_details": {"environment": "雨夜"},
    }]}]}
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": True})
    assert result.success
    note = result.data.get("pacing_note") or ""
    assert "tokenplan" in note
    assert "5 RPM" in note


def test_dry_run_quota_warning_field_is_independent(tmp_path, monkeypatch):
    from montage.providers import agnes_usage

    proj = _agnes_proj(tmp_path)
    monkeypatch.setenv("AGNES_ACCESS_TYPE", "tokenplan")
    monkeypatch.setenv("MONTAGE_AGNES_USAGE_PATH", str(tmp_path / "u.json"))
    agnes_usage.add_images("tokenplan", 4000)
    tool = ShotRunner(
        image_estimate=lambda i: 0.01, video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
    )
    plan = {"scenes": [{"id": "sc01", "shots": [{
        "shot_id": "s1", "shot_kind": "video", "duration_seconds": 5,
        "visual_details": {"environment": "雨夜"},
    }]}]}
    result = tool.execute({"scene_plan": plan, "project_dir": str(proj), "dry_run": True})
    assert result.success
    quota_rows = [
        f for f in (result.data.get("findings") or []) if f.get("field") == "agnes_quota"
    ]
    assert quota_rows
    assert all(f.get("severity") == "warning" for f in quota_rows)


def test_overlay_plan_rework_duration_follows_scene_plan():
    """改 bible/scene_plan 时长后重抽，shot_prompts 快照不得让旧时长继续生效。"""
    from montage.tools._shot_route import overlay_plan_rework

    shots = [{"shot_id": "sc01_01", "duration_seconds": 6.0, "shot_kind": "video"}]
    scene_plan = {"scenes": [{"id": "sc01", "shots": [
        {"shot_id": "sc01_01", "duration_seconds": 9.0},
    ]}]}
    overlay_plan_rework(shots, scene_plan)
    assert shots[0]["duration_seconds"] == 9.0


def test_overlay_plan_rework_keeps_snapshot_when_scene_plan_lacks_duration():
    from montage.tools._shot_route import overlay_plan_rework

    shots = [{"shot_id": "sc01_01", "duration_seconds": 6.0, "shot_kind": "video"}]
    scene_plan = {"scenes": [{"id": "sc01", "shots": [
        {"shot_id": "sc01_01", "rework_mode": "feature"},
    ]}]}
    overlay_plan_rework(shots, scene_plan)
    assert shots[0]["duration_seconds"] == 6.0
    assert shots[0]["rework_mode"] == "feature"


def test_upsert_item_moves_to_end_and_dedupes():
    from montage.tools._shot_refs import _upsert_item

    items = [
        {"id": "a", "v": 1},
        {"id": "b", "v": 1},
        {"id": "a", "v": 2},
    ]
    _upsert_item(items, {"id": "a", "v": 3})
    # 同 id 全部旧行删除，新行落到末尾；无 id 行不受影响。
    assert [it["id"] for it in items] == ["b", "a"]
    assert items[-1]["v"] == 3


def test_upsert_item_last_wins_after_retry():
    from montage.tools._shot_refs import _upsert_item

    items: list[dict] = []
    _upsert_item(items, {"id": "sc01_01_video", "path": "first.mp4", "url": "https://x/1.mp4"})
    _upsert_item(items, {"id": "sc01_01_video", "path": "second.mp4", "url": "https://x/2.mp4"})
    # 消费方按末尾取最新（compose_planner videos[-1] / first_frame_item reversed）。
    assert len(items) == 1
    assert items[-1]["path"] == "second.mp4"
    assert items[-1]["url"] == "https://x/2.mp4"


def test_upsert_item_appends_rows_without_id():
    from montage.tools._shot_refs import _upsert_item

    items: list[dict] = [{"id": "a", "v": 1}]
    _upsert_item(items, {"path": "no-id.mp4"})
    assert [it.get("id") for it in items] == ["a", None]


def test_dedupe_items_by_id_keeps_last_and_order():
    from montage.tools._shot_refs import dedupe_items_by_id

    items = [
        {"id": "a", "v": 1},
        {"id": "b", "v": 1},
        {"id": "a", "v": 2},
        {"kind": "loose"},
        {"id": "b", "v": 3},
    ]
    out = dedupe_items_by_id(items)
    # 顺序按首次出现，值取最后一次。
    assert [it.get("id") for it in out] == ["a", "b", None]
    assert out[0]["v"] == 2
    assert out[1]["v"] == 3
    assert out[2]["kind"] == "loose"


def test_dedupe_items_by_id_empty_and_clean():
    from montage.tools._shot_refs import dedupe_items_by_id

    assert dedupe_items_by_id([]) == []
    clean = [{"id": "x", "v": 1}, {"id": "y", "v": 2}]
    assert dedupe_items_by_id(clean) == clean


def test_resolve_cast_ref_kind_priority_and_kling():
    from montage.engine.policy import resolve_cast_ref_kind

    assert resolve_cast_ref_kind("turnaround") == "turnaround"
    assert resolve_cast_ref_kind("bogus") == "portrait"
    assert resolve_cast_ref_kind(None) == "portrait"
    # character 覆盖 packet
    assert resolve_cast_ref_kind("portrait", {"cast_ref_kind": "turnaround"}) == "turnaround"
    # form 覆盖 character / packet
    assert resolve_cast_ref_kind(
        "portrait",
        {"cast_ref_kind": "portrait"},
        {"cast_ref_kind": "turnaround"},
    ) == "turnaround"
    # 可灵强制 turnaround（无视声明）
    assert resolve_cast_ref_kind(
        "portrait",
        {"cast_ref_kind": "portrait"},
        {"cast_ref_kind": "portrait"},
        video_loop="kling",
    ) == "turnaround"


def test_form_naming_helpers_roundtrip():
    from montage.tools._shot_refs import form_ref_id, form_subject, parse_form_subject

    assert form_ref_id("portrait", "g", "") == "portrait_g"
    assert form_ref_id("portrait", "g", "ghost") == "portrait_g_ghost"
    assert form_subject("turnaround", "g", "") == "turnaround/g"
    assert form_subject("turnaround", "g", "ghost") == "turnaround/g:ghost"
    assert parse_form_subject("portrait/g") == ("g", "")
    assert parse_form_subject("portrait/g:ghost") == ("g", "ghost")
    assert parse_form_subject("ghost") == ("", "")
    assert parse_form_subject("portrait/") == ("", "")


def test_blend_character_form_overrides_and_falls_back():
    from montage.tools._shot_refs import blend_character_form

    char = {"id": "g", "name": "画皮", "appearance": "基础人形", "outfit_anchor": "素衣"}
    merged = blend_character_form(char, {
        "id": "ghost", "name": "鬼形", "appearance": "青面獠牙", "outfit_anchor": "破红嫁衣",
    })
    assert merged["form_id"] == "ghost"
    assert merged["name"] == "鬼形"
    assert merged["appearance"] == "青面獠牙"
    assert merged["outfit_anchor"] == "破红嫁衣"
    # 形态缺字段时回落角色
    partial = blend_character_form(char, {"id": "human"})
    assert partial["appearance"] == "基础人形"
    assert partial["outfit_anchor"] == "素衣"
    assert partial["name"] == "画皮"
    # 原字典不被就地改写
    assert char.get("form_id") is None


def test_collect_cast_jobs_forms_expand_and_skip_fallback():
    from montage.tools.shot_runner import collect_cast_jobs

    bible = {"characters": [{
        "id": "g",
        "appearance": "人皮",
        "outfit": "素衣",
        "skip_turnaround": True,
        "forms": [
            {"id": "human", "appearance": "清秀书生", "skip_turnaround": False},
            {"id": "ghost", "appearance": "青面獠牙"},
        ],
    }], "locations": [], "props": []}
    jobs = collect_cast_jobs(bible)
    assert [(j["kind"], j["subject"]) for j in jobs] == [
        ("portrait", "portrait/g:human"),
        ("turnaround", "turnaround/g:human"),
        ("portrait", "portrait/g:ghost"),
    ]
    assert [j["form_id"] for j in jobs] == ["human", "human", "ghost"]
    # 无 form 级 skip 时回落 char.skip_turnaround=True → ghost 不出四视图
    assert not any(j["kind"] == "turnaround" and j["form_id"] == "ghost" for j in jobs)


def test_collect_cast_jobs_kling_ignores_forms():
    from montage.tools.shot_runner import collect_cast_jobs

    bible = {"characters": [{
        "id": "g", "appearance": "x", "forms": [{"id": "human"}, {"id": "ghost"}],
    }]}
    jobs = collect_cast_jobs(bible, video_loop="kling")
    assert [(j["kind"], j["subject"], j.get("form_id")) for j in jobs] == [
        ("portrait", "portrait/g", ""),
    ]


def test_cast_still_prompt_merges_form(tmp_path, monkeypatch):
    from montage.tools.shot_runner import ShotRunner
    from montage.tools.visual_prompt_builder import VisualPromptBuilder

    captured: dict = {}

    def fake_execute(self, inputs):
        captured.update(inputs)
        return ToolResult(success=True, data={"first_frame_prompt": "P"})

    monkeypatch.setattr(VisualPromptBuilder, "execute", fake_execute)
    tool = ShotRunner(image_estimate=lambda i: 0.0, video_estimate=lambda i: 0.0)
    bible = {"characters": [{
        "id": "g", "name": "画皮", "appearance": "基础人形", "outfit": "素衣",
    }]}
    job = {
        "kind": "portrait", "character_id": "g", "form_id": "ghost",
        "form": {"id": "ghost", "name": "鬼形", "appearance": "青面獠牙", "outfit_anchor": "破红嫁衣"},
    }
    prompt, finding = tool._cast_still_prompt(
        job, bible=bible, project_dir=str(tmp_path), kling_loop=False,
    )
    assert finding is None and prompt == "P"
    merged = captured["character"]
    assert merged["name"] == "鬼形"
    assert merged["appearance"] == "青面獠牙"
    assert merged["outfit_anchor"] == "破红嫁衣"


def test_cast_writes_form_named_files(tmp_path):
    from montage.schemas import get_schema
    from montage.tools.shot_runner import ShotRunner, collect_cast_jobs

    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    bible = {"characters": [{
        "id": "g", "name": "画皮", "appearance": "基础人形", "outfit": "素衣",
        "forms": [
            {"id": "human", "name": "人皮形", "appearance": "清秀书生"},
            {"id": "ghost", "name": "鬼形", "appearance": "青面獠牙"},
        ],
    }], "locations": [], "props": []}
    assert len([j for j in collect_cast_jobs(bible) if j["kind"] == "portrait"]) == 2
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
    )
    result = tool.execute({
        "stage": "cast", "bible": bible, "project_dir": str(proj), "dry_run": False,
    })
    assert result.success, result.error
    imgs = proj / "assets" / "images"
    assert (imgs / "portrait_g_human.png").is_file()
    assert (imgs / "turnaround_g_human.png").is_file()
    assert (imgs / "portrait_g_ghost.png").is_file()
    # 隐式单形态文件名不得出现（否则两形态互相覆盖）
    assert not (imgs / "portrait_g.png").exists()
    refs = {r["id"]: r for r in result.data["asset_manifest"]["reference_assets"]}
    assert refs["portrait_g_human"]["form_id"] == "human"
    assert refs["portrait_g_ghost"]["form_id"] == "ghost"
    assert refs["turnaround_g_human"]["form_id"] == "human"
    errors = ArtifactStore.validate(result.data["asset_manifest"], get_schema("asset_manifest"))
    assert errors == []


def test_retry_covers_form_token():
    from montage.tools._shot_refs import _retry_covers

    assert _retry_covers({"portrait/g:ghost"}, portrait_id="g", form_id="ghost")
    assert _retry_covers({"turnaround/g:human"}, turnaround_id="g", form_id="human")
    assert not _retry_covers({"portrait/g:ghost"}, portrait_id="g", form_id="human")
    # 无 form 的整角色重抽命中全部形态
    assert _retry_covers({"portrait/g"}, portrait_id="g", form_id="human")
    assert _retry_covers({"g"}, portrait_id="g", form_id="human")


def test_portrait_index_cid_keyed_still_works_and_form_index_separates():
    from montage.tools._shot_refs import _portrait_index, _portrait_refs_by_form

    manifest = {"reference_assets": [
        {"id": "portrait_g_human", "kind": "portrait", "character_id": "g", "form_id": "human", "path": "h.png"},
        {"id": "portrait_g_ghost", "kind": "portrait", "character_id": "g", "form_id": "ghost", "path": "x.png"},
    ]}
    # 旧 cid-keyed 索引仍可用（last-wins），不因多形态炸掉
    assert _portrait_index(manifest).get("g", {}).get("id") == "portrait_g_ghost"
    by_form = _portrait_refs_by_form(manifest)
    assert by_form[("g", "human")]["id"] == "portrait_g_human"
    assert by_form[("g", "ghost")]["id"] == "portrait_g_ghost"


def test_cast_missing_form_aware():
    from montage.tools._shot_refs import cast_missing

    bible = {"characters": [{
        "id": "g", "appearance": "x",
        "forms": [{"id": "human"}, {"id": "ghost"}],
    }]}
    manifest = {"items": [], "reference_assets": [
        {"id": "portrait_g_human", "kind": "portrait", "character_id": "g", "form_id": "human", "url": "https://x/h.png"},
        {"id": "turnaround_g_human", "kind": "turnaround", "character_id": "g", "form_id": "human", "url": "https://x/ht.png"},
    ]}
    missing = cast_missing(bible, manifest, "", require_url=True)
    kinds = {(j["kind"], j.get("form_id")) for j in missing}
    assert kinds == {("portrait", "ghost"), ("turnaround", "ghost")}


def test_known_retry_ids_keeps_form_tokens(tmp_path):
    from montage.engine._produce_common import match_retry_ids

    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    store = ArtifactStore(proj)
    store.write("series_bible", {
        "characters": [{"id": "g", "appearance": "x", "forms": [{"id": "ghost"}]}],
    })
    known = match_retry_ids(proj, ["portrait/g:ghost", "portrait/g", "portrait/g:nope"])
    assert "portrait/g:ghost" in known
    assert "portrait/g" in known
    assert "portrait/g:nope" not in known


def test_copy_sibling_form_refs(tmp_path):
    from test_episodes import _seed_series
    from montage.engine.episodes import copy_sibling_still_refs, materialize_episodes

    series = _seed_series(tmp_path)
    materialize_episodes(series)
    ep01 = series / "episodes" / "ep01"
    ep02 = series / "episodes" / "ep02"
    src = ep01 / "assets" / "images" / "portrait_g_ghost.png"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"ghost-bytes")
    man = ArtifactStore(ep01).read("asset_manifest") or {"items": [], "reference_assets": []}
    man.setdefault("items", [])
    man.setdefault("reference_assets", [])
    man["reference_assets"].append({
        "id": "portrait_g_ghost", "kind": "portrait", "character_id": "g",
        "form_id": "ghost", "path": "assets/images/portrait_g_ghost.png",
    })
    man["items"].append({
        "id": "portrait_g_ghost", "kind": "image",
        "path": "assets/images/portrait_g_ghost.png",
    })
    ArtifactStore(ep01).write("asset_manifest", man)
    copied = copy_sibling_still_refs(
        ep02,
        portrait_forms=[("g", "ghost")],
        retry_ids=set(),
        manifest={"items": [], "reference_assets": []},
    )
    dest = ep02 / "assets" / "images" / "portrait_g_ghost.png"
    assert dest.is_file()
    assert dest.read_bytes() == b"ghost-bytes"
    ref = [r for r in copied["reference_assets"] if r["kind"] == "portrait"][0]
    assert ref["id"] == "portrait_g_ghost"
    assert ref["form_id"] == "ghost"


def _forms_plan() -> dict:
    return {
        "character_registry": [
            {"id": "g", "forms": [{"id": "human", "default": True}, {"id": "ghost"}]},
            {"id": "h", "forms": [{"id": "a"}, {"id": "b"}]},
        ],
        "scenes": [{"id": "sc01", "character_ids": ["g", "h"]}],
    }


def test_shot_character_forms_declared_and_default():
    from montage.tools._shot_refs import _shot_character_forms

    shot = {
        "scene_id": "sc01",
        "visual_details": {"subjects": [{"id": "g", "form_id": "ghost"}, {"id": "h"}]},
    }
    assert _shot_character_forms(shot, _forms_plan()) == [
        ("g", "ghost", True),
        ("h", "a", True),  # 未声明 → 首个形态（无 default）
    ]
    # 声明形态但角色无 forms[]（旧数据）→ has_forms=False, form_id=""
    legacy = {"character_registry": [{"id": "g"}], "scenes": [{"id": "sc01", "character_ids": ["g"]}]}
    shot2 = {"scene_id": "sc01", "visual_details": {"subjects": [{"id": "g", "form_id": "ghost"}]}}
    assert _shot_character_forms(shot2, legacy) == [("g", "ghost", False)]


def test_resolve_shot_refs_prefers_declared_form():
    from montage.tools._shot_refs import resolve_shot_refs

    manifest = {"reference_assets": [
        {"id": "portrait_g_human", "kind": "portrait", "character_id": "g", "form_id": "human", "path": "h.png"},
        {"id": "portrait_g_ghost", "kind": "portrait", "character_id": "g", "form_id": "ghost", "path": "x.png"},
        {"id": "turnaround_g_ghost", "kind": "turnaround", "character_id": "g", "form_id": "ghost", "path": "xt.png"},
    ]}
    plan = _forms_plan()
    shot = {"scene_id": "sc01", "visual_details": {"subjects": [{"id": "g", "form_id": "ghost"}]}}
    ids = [r["id"] for r in resolve_shot_refs(shot, manifest, None, plan)]
    assert ids == ["portrait_g_ghost"]  # 不是 last-wins 的 human
    shot["_include_turnaround_refs"] = True
    ids2 = [r["id"] for r in resolve_shot_refs(shot, manifest, None, plan)]
    assert ids2 == ["turnaround_g_ghost", "portrait_g_ghost"]
    # 未声明 → 默认形态 human
    shot3 = {"scene_id": "sc01", "visual_details": {"subjects": [{"id": "g"}]}}
    assert [r["id"] for r in resolve_shot_refs(shot3, manifest, None, plan)] == ["portrait_g_human"]


def test_resolve_shot_refs_kling_implicit_form_fallback_and_legacy():
    from montage.tools._shot_refs import resolve_shot_refs

    # 可灵环：registry 有 forms，但参考落在隐式 form_id="" → 回落命中
    manifest = {"reference_assets": [
        {"id": "portrait_g", "kind": "portrait", "character_id": "g", "path": "g.png"},
    ]}
    plan = _forms_plan()
    shot = {"scene_id": "sc01", "visual_details": {"subjects": [{"id": "g"}]}}
    assert [r["id"] for r in resolve_shot_refs(shot, manifest, None, plan)] == ["portrait_g"]
    # 旧数据（registry 无 forms）→ cid-keyed
    legacy = {"character_registry": [{"id": "g"}], "scenes": [{"id": "sc01", "character_ids": ["g"]}]}
    assert [r["id"] for r in resolve_shot_refs(shot, manifest, None, legacy)] == ["portrait_g"]


def test_resolve_shot_refs_declared_form_missing_no_wrong_fallback():
    from montage.tools._shot_refs import resolve_shot_refs

    # 声明 ghost 但只有 human 落盘：不得静默回落到 human
    manifest = {"reference_assets": [
        {"id": "portrait_g_human", "kind": "portrait", "character_id": "g", "form_id": "human", "path": "h.png"},
    ]}
    shot = {"scene_id": "sc01", "visual_details": {"subjects": [{"id": "g", "form_id": "ghost"}]}}
    assert resolve_shot_refs(shot, manifest, None, _forms_plan()) == []


def _identity_plan() -> dict:
    return {
        "character_registry": [
            {
                "id": "g",
                "name": "画皮鬼",
                "forms": [
                    {"id": "human", "name": "人皮形", "default": True},
                    {"id": "ghost", "name": "鬼形"},
                ],
            },
        ],
        "scenes": [{"id": "sc01", "character_ids": ["g"]}],
    }


def _identity_refs() -> list:
    return [
        {"id": "p_g_human", "kind": "portrait", "character_id": "g", "form_id": "human",
         "name": "画皮鬼", "url": "https://x/ph.png"},
        {"id": "t_g_human", "kind": "turnaround", "character_id": "g", "form_id": "human",
         "name": "画皮鬼", "url": "https://x/th.png"},
        {"id": "p_g_ghost", "kind": "portrait", "character_id": "g", "form_id": "ghost",
         "name": "画皮鬼", "url": "https://x/pg.png"},
        {"id": "t_g_ghost", "kind": "turnaround", "character_id": "g", "form_id": "ghost",
         "name": "画皮鬼", "url": "https://x/tg.png"},
        {"id": "s1", "kind": "scene_ref", "location_id": "plaza", "url": "https://x/s.png"},
    ]


def test_identity_http_refs_sends_one_per_declared_form():
    from montage.tools._shot_refs import _identity_http_refs

    plan = _identity_plan()
    shot = {"scene_id": "sc01", "location_id": "plaza",
            "visual_details": {"subjects": [{"id": "g", "form_id": "ghost"}]}}
    got = _identity_http_refs(shot, _identity_refs(), None, plan)
    identity = [r for r in got if r.get("identity")]
    # 只发声明形态的一张（默认 portrait）；同角色另一形态与四视图都不进候选
    assert [(r["kind"], r["form_id"]) for r in identity] == [("portrait", "ghost")]
    assert all(r["kind"] != "turnaround" for r in got)
    assert identity[0]["name"] == "画皮鬼·鬼形"
    # 场景参考仍照常带上，不占身份名额
    assert any(r["kind"] == "scene_ref" for r in got)


def test_identity_http_refs_turnaround_opt_in_and_default_form():
    from montage.tools._shot_refs import _identity_http_refs

    plan = _identity_plan()
    undeclared = {"scene_id": "sc01", "visual_details": {"subjects": [{"id": "g"}]}}
    # 未声明 → 默认形态 human，只发一张
    got = _identity_http_refs(undeclared, _identity_refs(), None, plan)
    assert [(r["kind"], r["form_id"]) for r in got if r.get("identity")] == [("portrait", "human")]
    # cast_ref_kind=turnaround 显式开启 → 发四视图
    got_t = _identity_http_refs(
        undeclared, _identity_refs(), None, plan, cast_ref_kind="turnaround",
    )
    assert [(r["kind"], r["form_id"]) for r in got_t if r.get("identity")] == [("turnaround", "human")]
    # 可灵强制 turnout（无视 packet 的 portrait）
    got_k = _identity_http_refs(
        undeclared, _identity_refs(), None, plan,
        cast_ref_kind="portrait", video_loop="kling",
    )
    assert [(r["kind"], r["form_id"]) for r in got_k if r.get("identity")] == [("turnaround", "human")]


def test_identity_http_refs_kling_prefers_element_then_falls_back_to_url():
    from montage.tools._shot_refs import _identity_http_refs

    plan = _identity_plan()
    shot = {"scene_id": "sc01", "visual_details": {"subjects": [{"id": "g"}]}}
    base = _identity_refs()
    # 可灵工牌：四视图只有 element_id，没有 URL，也要发（否则 Omni 丢主体）
    with_element = [
        {**r, "element_id": "el_g"} if r["kind"] == "turnaround" else r for r in base
    ]
    got = _identity_http_refs(shot, with_element, None, plan, video_loop="kling")
    ident = [r for r in got if r.get("identity")]
    assert len(ident) == 1
    assert ident[0]["kind"] == "turnaround"
    assert ident[0]["element_id"] == "el_g"
    # 四视图既无 URL 又无 element（拼板降级）→ 回落定妆 URL，不整镜丢身份
    no_turn = [r for r in base if r["kind"] != "turnaround"]
    got2 = _identity_http_refs(shot, no_turn, None, plan, video_loop="kling")
    ident2 = [r for r in got2 if r.get("identity")]
    assert [(r["kind"], r["url"]) for r in ident2] == [("portrait", "https://x/ph.png")]


def test_plan_reference_segments_joint_slice_and_bridge():
    from montage.tools._shot_refs import plan_reference_segments

    refs = [
        {"kind": "portrait", "character_id": "g", "url": "https://x/p.png", "identity": True},
        {"kind": "scene_ref", "location_id": "plaza", "url": "https://x/s.png"},
        *[
            {"kind": "prop", "prop_id": f"q{i}", "url": f"https://x/q{i}.png"}
            for i in range(6)
        ],
    ]
    plan = plan_reference_segments(refs, max_images=5, wanted_seconds=12, max_segments=4)
    assert plan["mode"] == "segment"
    segs = plan["segments"]
    # 6 道具 / 每段 2 个 → 3 段；12s 均分 4s 一段，总时长不变
    assert len(segs) == 3
    assert [s["seconds"] for s in segs] == [4.0, 4.0, 4.0]
    assert sum(s["seconds"] for s in segs) == 12.0
    assert [s["bridge"] for s in segs] == [False, True, True]
    for s in segs:
        kinds = [r["kind"] for r in s["refs"]]
        # 身份 + 场景每段必带
        assert kinds.count("portrait") == 1
        assert kinds.count("scene_ref") == 1
        # 段内不超名额（第 2 段起还要留 1 个桥接位）
        limit = 5 if not s["bridge"] else 4
        assert len(s["refs"]) <= limit
    all_props = [r["prop_id"] for s in segs for r in s["refs"] if r["kind"] == "prop"]
    assert sorted(all_props) == sorted(f"q{i}" for i in range(6))


def test_plan_reference_segments_duration_only_overflow_single():
    from montage.tools._shot_refs import plan_reference_segments

    refs = [
        {"kind": "portrait", "character_id": "g", "url": "https://x/p.png", "identity": True},
    ]
    # 未溢出 → 不触发续拍
    plan = plan_reference_segments(refs, max_images=5, wanted_seconds=12)
    assert plan["mode"] == "single"


def test_plan_reference_segments_infeasible_falls_back_single():
    from montage.tools._shot_refs import plan_reference_segments

    # 身份 3 + 场景 3 已超每段名额：分段也救不了 → single + reason
    blocked = [
        *[
            {"kind": "portrait", "character_id": f"c{i}", "url": f"https://x/p{i}.png", "identity": True}
            for i in range(3)
        ],
        *[
            {"kind": "scene_ref", "location_id": f"l{i}", "url": f"https://x/s{i}.png"}
            for i in range(3)
        ],
    ]
    plan = plan_reference_segments(blocked, max_images=5, wanted_seconds=12)
    assert plan["mode"] == "single"
    assert "不可行" in plan["reason"]

    # 时长不足以切成所需段数（10s 切 3 段，每段 floor 4s）→ single
    short = [
        {"kind": "portrait", "character_id": "g", "url": "https://x/p.png", "identity": True},
        {"kind": "scene_ref", "location_id": "l", "url": "https://x/s.png"},
        *[
            {"kind": "prop", "prop_id": f"q{i}", "url": f"https://x/q{i}.png"}
            for i in range(6)
        ],
    ]
    plan2 = plan_reference_segments(short, max_images=5, wanted_seconds=10, max_segments=4)
    assert plan2["mode"] == "single"
    assert "不足" in plan2["reason"]

    # 超段数封顶 → single
    huge = [
        {"kind": "portrait", "character_id": "g", "url": "https://x/p.png", "identity": True},
        *[
            {"kind": "prop", "prop_id": f"q{i}", "url": f"https://x/q{i}.png"}
            for i in range(20)
        ],
    ]
    plan3 = plan_reference_segments(huge, max_images=5, wanted_seconds=12, max_segments=4)
    assert plan3["mode"] == "single"
    assert "上限" in plan3["reason"]


def test_split_segment_seconds_bounds_and_sum():
    from montage.tools._shot_refs import split_segment_seconds

    assert split_segment_seconds(12, 3, min_seconds=4, max_seconds=12) == [4.0, 4.0, 4.0]
    assert split_segment_seconds(10, 2, min_seconds=4, max_seconds=12) == [5.0, 5.0]
    # 不可行：5s 切 2 段（每段 ≥4s）
    assert split_segment_seconds(5, 2, min_seconds=4, max_seconds=12) is None
    # 单段直通
    assert split_segment_seconds(5, 1, min_seconds=4, max_seconds=12) == [5.0]


def test_ref_overflow_segments_end_to_end(tmp_path, monkeypatch):
    """溢出镜按 joint 切片跑多段，段 2 以桥接首帧作 images[0]。"""
    monkeypatch.delenv("AGNES_VIDEO_MODEL", raising=False)
    proj = tmp_path / "seg"
    (proj / "artifacts").mkdir(parents=True)
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"video_loop": "agnes"}, ensure_ascii=False),
        encoding="utf-8",
    )
    plan = {
        "character_registry": [
            {"id": "a", "appearance": "黑发", "outfit": "蓝衣"},
            {"id": "b", "appearance": "白发", "outfit": "红衣"},
        ],
        "locations": [{"id": "plaza", "sensory": "雨夜广场", "appearance": "雨夜广场"}],
        "scenes": [{
            "id": "sc01", "location_id": "plaza", "character_ids": ["a", "b"],
            "shots": [{
                "shot_id": "sc01_01", "scene_id": "sc01", "shot_kind": "video",
                "duration_seconds": 12, "location_id": "plaza", "character_ids": ["a", "b"],
                "visual_details": {
                    "environment": "雨夜广场",
                    "subjects": [
                        {"id": "a", "appearance_anchor": "黑发", "action": {"verb": "走"}},
                        {"id": "b", "appearance_anchor": "白发", "action": {"verb": "站"}},
                    ],
                    "objects": [{"id": f"p{i}"} for i in range(1, 4)],
                },
            }],
        }],
    }
    manifest = {
        "items": [],
        "reference_assets": [
            {"id": "portrait_a", "kind": "portrait", "character_id": "a",
             "path": "assets/images/portrait_a.png", "url": "https://x/a.png"},
            {"id": "portrait_b", "kind": "portrait", "character_id": "b",
             "path": "assets/images/portrait_b.png", "url": "https://x/b.png"},
            {"id": "scene_plaza", "kind": "scene_ref", "location_id": "plaza",
             "path": "assets/images/scene_plaza.png", "url": "https://x/plaza.png"},
            *[
                {"id": f"prop_p{i}", "kind": "prop", "prop_id": f"p{i}",
                 "path": f"assets/images/p{i}.png", "url": f"https://x/p{i}.png"}
                for i in range(1, 4)
            ],
        ],
    }
    ArtifactStore(proj).write("asset_manifest", manifest)
    calls: list[dict] = []

    def track_video(inputs):
        calls.append(dict(inputs))
        return _fake_video(inputs)

    def named_image(inputs):
        out = Path(inputs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"img")
        return ToolResult(
            success=True,
            data={"output": str(out), "url": f"https://example.test/{out.stem}.png"},
            cost_usd=0.04,
        )

    tool = ShotRunner(
        image_execute=named_image,
        video_execute=track_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=_fake_tail,
        vlm_review=lambda _p, _c: {"ok": True, "skipped": True, "issues": []},
    )
    result = tool.execute({
        "scene_plan": plan, "project_dir": str(proj), "dry_run": False,
        "script": {"characters": [
            {"id": "a", "appearance": "黑发", "outfit": "蓝衣"},
            {"id": "b", "appearance": "白发", "outfit": "红衣"},
        ]},
    })
    assert result.success, result.error
    # 2 身份 + 1 场景 = 3，每段仅余 1 个道具位 → 3 段
    assert len(calls) == 3
    for call in calls:
        assert len(list(call.get("images") or [])) <= 5
    # 段 2+ 的 <Picture 1> 是续接首帧（桥接图 URL），不再是本镜首帧
    for i, call in enumerate(calls[1:], start=2):
        imgs = list(call.get("images") or [])
        assert imgs and imgs[0] == f"https://example.test/sc01_01_bridge{i}.png"
    # 段 1 不带桥接：首图是身份/场景/道具之一
    imgs1 = list(calls[0].get("images") or [])
    assert imgs1 and "bridge" not in imgs1[0]


def test_ref_overflow_dry_run_estimates_segments_and_bridge(tmp_path, monkeypatch):
    """dry_run 按镜内实际段数估算，并把续接首帧单列成 job。"""
    monkeypatch.delenv("AGNES_VIDEO_MODEL", raising=False)
    proj = tmp_path / "segest"
    (proj / "artifacts").mkdir(parents=True)
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"video_loop": "agnes"}, ensure_ascii=False),
        encoding="utf-8",
    )
    plan = {
        "character_registry": [
            {"id": "a", "appearance": "黑发", "outfit": "蓝衣"},
            {"id": "b", "appearance": "白发", "outfit": "红衣"},
        ],
        "locations": [{"id": "plaza", "sensory": "雨夜广场"}],
        "scenes": [{
            "id": "sc01", "location_id": "plaza", "character_ids": ["a", "b"],
            "shots": [{
                "shot_id": "sc01_01", "scene_id": "sc01", "shot_kind": "video",
                "duration_seconds": 12, "location_id": "plaza", "character_ids": ["a", "b"],
                "visual_details": {
                    "environment": "雨夜广场",
                    "subjects": [{"id": "a"}, {"id": "b"}],
                    "objects": [{"id": f"p{i}"} for i in range(1, 4)],
                },
            }],
        }],
    }
    manifest = {
        "items": [],
        "reference_assets": [
            {"id": "portrait_a", "kind": "portrait", "character_id": "a",
             "path": "assets/images/portrait_a.png", "url": "https://x/a.png"},
            {"id": "portrait_b", "kind": "portrait", "character_id": "b",
             "path": "assets/images/portrait_b.png", "url": "https://x/b.png"},
            {"id": "scene_plaza", "kind": "scene_ref", "location_id": "plaza",
             "path": "assets/images/scene_plaza.png", "url": "https://x/plaza.png"},
            *[
                {"id": f"prop_p{i}", "kind": "prop", "prop_id": f"p{i}",
                 "path": f"assets/images/p{i}.png", "url": f"https://x/p{i}.png"}
                for i in range(1, 4)
            ],
        ],
    }
    ArtifactStore(proj).write("asset_manifest", manifest)
    tool = ShotRunner(image_estimate=lambda i: 0.04, video_estimate=lambda i: 0.2)
    result = tool.execute({
        "scene_plan": plan, "project_dir": str(proj), "dry_run": True,
        "script": {"characters": [
            {"id": "a", "appearance": "黑发", "outfit": "蓝衣"},
            {"id": "b", "appearance": "白发", "outfit": "红衣"},
        ]},
    })
    assert result.success, result.error
    videos = [j for j in result.data["jobs"] if j["kind"] == "video"]
    assert len(videos) == 3
    assert [v["seconds"] for v in videos] == [4.0, 4.0, 4.0]
    bridges = [j for j in result.data["jobs"] if j["kind"] == "bridge_frame"]
    assert len(bridges) == 2
    assert any(
        "分段" in f["message"] or "续拍" in f["message"]
        for f in result.data["findings"]
    )


def test_missing_identity_refs_form_aware():
    from montage.tools._shot_refs import missing_identity_refs

    plan = _identity_plan()
    # 只有 human 的 portrait/turnaround 落盘
    refs = [
        {"id": "p_g_human", "kind": "portrait", "character_id": "g", "form_id": "human",
         "url": "https://x/ph.png"},
        {"id": "t_g_human", "kind": "turnaround", "character_id": "g", "form_id": "human",
         "url": "https://x/th.png"},
    ]
    # 声明 human 且要求 URL → 已就绪，不误拦
    human = {"scene_id": "sc01", "visual_details": {"subjects": [{"id": "g", "form_id": "human"}]}}
    assert missing_identity_refs(human, refs, plan, require_url=True) == []
    # 声明 ghost 但只有 human → 缺重（不是 cid 泛检通过）
    ghost = {"scene_id": "sc01", "visual_details": {"subjects": [{"id": "g", "form_id": "ghost"}]}}
    assert missing_identity_refs(ghost, refs, plan, require_url=True) == ["g:ghost"]
    # 要求 URL 但只有本地 path → 也算缺
    local = [{"id": "p_g_human", "kind": "portrait", "character_id": "g",
              "form_id": "human", "path": "p.png"}]
    undeclared = {"scene_id": "sc01", "visual_details": {"subjects": [{"id": "g"}]}}
    assert missing_identity_refs(undeclared, local, plan, require_url=True) == ["g:human"]


# --- image_bindings（图 ↔ 场景文字 冻结绑定） ---

def _binding_plan():
    return {
        "character_registry": [{"id": "a", "appearance": "黑发", "outfit": "蓝衣"}],
        "scenes": [{
            "id": "sc01", "location_id": "plaza", "character_ids": ["a"],
            "shots": [{
                "shot_id": "sc01_01", "scene_id": "sc01", "location_id": "plaza",
                "location_sensory": "雨夜广场", "character_ids": ["a"],
                "visual_details": {
                    "environment": "雨夜广场",
                    "subjects": [{"id": "a"}],
                    "objects": [{"id": "p1"}],
                },
            }],
        }],
    }


def _binding_manifest():
    return {
        "items": [],
        "reference_assets": [
            {"id": "portrait_a", "kind": "portrait", "character_id": "a",
             "path": "assets/images/portrait_a.png", "url": "https://x/a.png"},
            {"id": "scene_plaza", "kind": "scene_ref", "location_id": "plaza",
             "path": "assets/images/scene_plaza.png", "url": "https://x/plaza.png"},
        ],
    }


def test_build_image_bindings_picture_index_from_sent_plan(tmp_path):
    from montage.tools._shot_refs import build_image_bindings

    proj = tmp_path / "b"
    (proj / "artifacts").mkdir(parents=True)
    plan = _binding_plan()
    shot = plan["scenes"][0]["shots"][0]
    shot["_agnes_ref_plan"] = {"entries": [
        {"index": 1, "url": "https://x/a.png", "kind": "portrait",
         "identity": True, "id": "portrait_a", "character_id": "a"},
        {"index": 2, "url": "https://x/plaza.png", "kind": "scene_ref",
         "id": "scene_plaza", "location_id": "plaza"},
    ]}
    out = build_image_bindings(
        [shot], _binding_manifest(), plan, {"characters": [{"id": "a"}]},
        project_dir=str(proj),
    )
    row = out["shots"]["sc01_01"]
    # 序号与实发 plan 同源，不是重算
    assert [r["picture_index"] for r in row["refs"]] == [1, 2]
    assert row["refs"][0]["role"] == "identity"
    assert row["refs"][0]["source"] == "sent_plan"
    # path 由 manifest 反查补齐（plan 只有 url）
    assert row["refs"][0]["path"] == "assets/images/portrait_a.png"
    assert row["location_sensory"] == "雨夜广场"
    assert row["environment"] == "雨夜广场"
    assert row["location_id"] == "plaza"
    assert row["character_forms"] == [{"character_id": "a", "form_id": ""}]
    assert "p1" in row["prop_ids"]
    assert row["first_frame"] is None
    assert out["cast"]["portrait_a"]["character_id"] == "a"
    assert ArtifactStore.validate(out, get_schema("image_bindings")) == []


def test_build_image_bindings_recomputed_has_no_index():
    from montage.tools._shot_refs import build_image_bindings

    plan = _binding_plan()
    shot = plan["scenes"][0]["shots"][0]
    out = build_image_bindings([shot], _binding_manifest(), plan, None)
    refs = out["shots"]["sc01_01"]["refs"]
    assert refs and all(r["picture_index"] is None for r in refs)
    assert all(r["source"] == "recomputed" for r in refs)
    assert ArtifactStore.validate(out, get_schema("image_bindings")) == []


def test_build_image_bindings_segments_shape():
    from montage.tools._shot_refs import build_image_bindings

    plan = _binding_plan()
    shot = plan["scenes"][0]["shots"][0]
    shot["_agnes_segment_plan"] = {"mode": "segment", "segments": [
        {"seconds": 6.0, "bridge": False, "_plan": {"entries": [
            {"index": 1, "url": "https://x/a.png", "kind": "portrait",
             "identity": True, "id": "portrait_a"},
            {"index": 2, "url": "https://x/plaza.png", "kind": "scene_ref",
             "id": "scene_plaza"},
        ]}},
        {"seconds": 6.0, "bridge": True, "_plan": {"entries": [
            {"index": 1, "url": "https://x/bridge2.png", "kind": "first_frame"},
            {"index": 2, "url": "https://x/a.png", "kind": "portrait",
             "identity": True, "id": "portrait_a"},
        ]}},
    ]}
    out = build_image_bindings([shot], _binding_manifest(), plan, None)
    segs = out["shots"]["sc01_01"]["segments"]
    assert [s["index"] for s in segs] == [1, 2]
    assert segs[1]["bridge"] is True
    # segments[].refs 是对象数组，picture_index 是「该段」下标
    assert segs[1]["refs"][0]["role"] == "first_frame"
    assert segs[1]["refs"][0]["picture_index"] == 1
    assert ArtifactStore.validate(out, get_schema("image_bindings")) == []


def test_merge_image_bindings_replaces_entry_not_deep_merge():
    from montage.tools._shot_refs import merge_image_bindings

    base = {
        "version": 1,
        "shots": {
            "s1": {"refs": [{"id": "a"}], "segments": [{"index": 1}, {"index": 2}, {"index": 3}]},
            "s2": {"refs": []},
        },
        "cast": {"r1": {"url": "u1"}},
    }
    incoming = {
        "version": 1,
        "shots": {"s1": {"refs": [{"id": "b"}], "segments": [{"index": 1}]}},
        "cast": {"r2": {"url": "u2"}},
    }
    out = merge_image_bindings(base, incoming)
    # 同一 shot_id 整条替换：旧段不残留
    assert len(out["shots"]["s1"]["segments"]) == 1
    assert out["shots"]["s1"]["refs"][0]["id"] == "b"
    # 未提到的 key 保留（shot-only retry 不抹掉 cast/其他镜）
    assert "s2" in out["shots"]
    assert "r1" in out["cast"] and "r2" in out["cast"]


def test_reconcile_bindings_preserves_sent_index(tmp_path):
    from montage.tools._shot_refs import reconcile_image_bindings

    proj = tmp_path / "rc"
    (proj / "artifacts").mkdir(parents=True)
    existing = {
        "version": 1,
        "shots": {"sc01_01": {"refs": [
            {"id": "portrait_a", "kind": "portrait", "picture_index": 1, "source": "sent_plan"},
            {"id": "scene_plaza", "kind": "scene_ref", "picture_index": 2, "source": "sent_plan"},
        ]}},
        "cast": {},
    }
    plan = _binding_plan()
    shot = plan["scenes"][0]["shots"][0]
    # 无内存 plan（模拟另一次调用回填）
    merged, findings = reconcile_image_bindings(
        existing, shots=[shot], manifest=_binding_manifest(),
        scene_plan=plan, project_dir=str(proj),
    )
    refs = {r["id"]: r for r in merged["shots"]["sc01_01"]["refs"]}
    # 回填写 recomputed，但真序号按 id 平移回来，不被洗掉
    assert refs["portrait_a"]["source"] == "recomputed"
    assert refs["portrait_a"]["picture_index"] == 1
    assert refs["scene_plaza"]["picture_index"] == 2
    # 集合一致 → 无漂移 finding
    assert findings == []


def test_reconcile_bindings_reports_set_drift(tmp_path):
    from montage.tools._shot_refs import reconcile_image_bindings

    proj = tmp_path / "rc2"
    (proj / "artifacts").mkdir(parents=True)
    existing = {
        "version": 1,
        "shots": {"sc01_01": {"refs": [
            {"id": "portrait_a", "kind": "portrait", "picture_index": 1, "source": "sent_plan"},
        ]}},
        "cast": {},
    }
    plan = _binding_plan()
    shot = plan["scenes"][0]["shots"][0]
    _merged, findings = reconcile_image_bindings(
        existing, shots=[shot], manifest=_binding_manifest(),
        scene_plan=plan, project_dir=str(proj),
    )
    assert findings and "绑定集合与当前参考不一致" in findings[0]["message"]


def test_execute_writes_image_bindings(tmp_path):
    """生成一次后 image_bindings 落盘，含 identity 序号与场景文字。"""
    proj = tmp_path / "bw"
    (proj / "artifacts").mkdir(parents=True)
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"video_loop": "agnes"}, ensure_ascii=False), encoding="utf-8",
    )
    plan = _binding_plan()
    ArtifactStore(proj).write("asset_manifest", _binding_manifest())
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=_fake_tail,
        vlm_review=lambda _p, _c: {"ok": True, "skipped": True, "issues": []},
    )
    result = tool.execute({
        "scene_plan": plan, "project_dir": str(proj), "dry_run": False,
        "script": {"characters": [{"id": "a", "appearance": "黑发", "outfit": "蓝衣"}]},
    })
    assert result.success, result.error
    bindings = ArtifactStore(proj).read("image_bindings")
    assert bindings and "sc01_01" in bindings["shots"]
    row = bindings["shots"]["sc01_01"]
    assert row["refs"] and row["refs"][0]["picture_index"] == 1
    assert row["refs"][0]["role"] == "identity"
    assert row["location_sensory"] == "雨夜广场"
    assert bindings["cast"]["portrait_a"]["kind"] == "portrait"
    assert ArtifactStore.validate(bindings, get_schema("image_bindings")) == []


def test_bindings_write_failure_is_non_fatal(tmp_path, monkeypatch):
    proj = tmp_path / "bf"
    (proj / "artifacts").mkdir(parents=True)
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"video_loop": "agnes"}, ensure_ascii=False), encoding="utf-8",
    )
    plan = _binding_plan()
    ArtifactStore(proj).write("asset_manifest", _binding_manifest())
    real_write = ArtifactStore.write

    def boom(self, name, data, schema=None):
        if name == "image_bindings":
            raise ValueError("boom")
        return real_write(self, name, data, schema=schema)

    monkeypatch.setattr(ArtifactStore, "write", boom)
    tool = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=_fake_tail,
        vlm_review=lambda _p, _c: {"ok": True, "skipped": True, "issues": []},
    )
    result = tool.execute({
        "scene_plan": plan, "project_dir": str(proj), "dry_run": False,
        "script": {"characters": [{"id": "a", "appearance": "黑发", "outfit": "蓝衣"}]},
    })
    assert result.success, result.error
    assert any(
        "绑定产物写入失败" in f["message"] for f in result.data["findings"]
    )

