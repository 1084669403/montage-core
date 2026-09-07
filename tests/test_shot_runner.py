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
    assert _agnes_flash_images(refs) == [
        "https://x/turn.png",
        "https://x/port.png",
        "https://x/scene.png",
        "https://x/prop.png",
        "https://x/p2.png",
    ]


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

