"""进化方案 P3：消费四视图 / 场记 / 千问 VLM。零真实调用。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.shot_prompt_builder import build_shot_prompt_pair
from montage.engine.continuity import (
    format_continuity_note,
    load_or_seed_continuity,
    update_continuity,
)
from montage.engine.episodes import copy_sibling_still_refs, materialize_episodes
from montage.engine.produce import STEP_IDS
from montage.pipelines import CINEMATIC, DOCUMENTARY
from montage.providers.capabilities import apply_seedance_content, video_surface
from montage.providers.kling import _video_payload
from montage.providers.prompt_adapter import adapt_visual_prompt
from montage.schemas import get_schema
from montage.toolbase import ToolResult
from montage.tools.shot_runner import ShotRunner, lift_shot_prompts
from montage.tools.vlm_reviewer import VLM_KINDS, parse_vlm_response, review_media
from test_episodes import _seed_series
from test_shot_runner import _fake_video, _pass_quality

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "agnes_v20_prompt_golden.json"


def _uniq_image(inputs):
    out = Path(inputs["output_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"img")
    return ToolResult(
        success=True,
        data={"output": str(out), "url": f"https://example.test/{out.stem}.png"},
        cost_usd=0.04,
    )


def _hero_plan(*, appearance: str = "黑发短寸，圆框眼镜"):
    return {
        "character_registry": [
            {"id": "a", "appearance": appearance, "outfit": "蓝外套", "outfit_anchor": "蓝外套"},
        ],
        "scenes": [{
            "id": "sc01",
            "location_id": "alley",
            "character_ids": ["a"],
            "shots": [{
                "shot_id": "sh01",
                "scene_id": "sc01",
                "shot_kind": "video",
                "shot_budget_class": "hero",
                "duration_seconds": 5,
                "character_ids": ["a"],
                "visual_details": {
                    "environment": "雨夜巷口",
                    "subjects": [{
                        "id": "a",
                        "appearance_anchor": appearance,
                        "action": {"verb": "站住"},
                    }],
                },
            }],
        }],
    }


def test_sidecar_schemas_not_in_produces():
    assert get_schema("continuity") is not None
    assert get_schema("vlm_review") is not None
    assets = next(s for s in CINEMATIC["stages"] if s["name"] == "assets")
    assert "vlm_reviewer" in assets["tools"]
    assert "vlm_reviewer" not in assets["produces"]
    assert "continuity" not in assets["produces"]
    doc_assets = next(s for s in DOCUMENTARY["stages"] if s["name"] == "assets")
    assert "vlm_reviewer" in doc_assets["tools"]
    assert "vlm_reviewer" not in doc_assets["produces"]
    publish = next(s for s in CINEMATIC["stages"] if s["name"] == "publish")
    assert "vlm_review" not in publish["produces"]
    assert STEP_IDS[-3:] == ("finish", "release", "export")
    assert "vlm_reviewer" not in STEP_IDS


def test_vlm_kinds_match_c6():
    assert VLM_KINDS == (
        "人物不一致", "道具丢失", "场景错位", "崩坏", "构图",
    )


def test_parse_vlm_black_hair_vs_blonde():
    raw = {
        "choices": [{
            "message": {
                "content": json.dumps({
                    "ok": False,
                    "score": 0.1,
                    "issues": [{
                        "severity": "critical",
                        "kind": "人物不一致",
                        "message": "定妆黑发，成片为金发",
                        "proposed_fix": "重抽该镜",
                    }],
                }, ensure_ascii=False),
            },
        }],
    }
    parsed = parse_vlm_response(raw)
    assert parsed["ok"] is False
    assert parsed["issues"][0]["kind"] == "人物不一致"
    assert parsed["issues"][0]["severity"] == "critical"
    assert "金发" in parsed["issues"][0]["message"]


def test_parse_vlm_unknown_kind_not_critical():
    parsed = parse_vlm_response('{"ok":false,"issues":[{"severity":"critical","kind":"时间码","message":"2.0s"}]}')
    assert parsed["issues"][0]["severity"] == "warning"
    assert parsed["issues"][0]["kind"] == "构图"
    assert "时间码" in parsed["issues"][0]["message"]


def test_parse_vlm_non_json_does_not_block():
    parsed = parse_vlm_response("不是 JSON")
    assert parsed["ok"] is True
    assert parsed["issues"][0]["severity"] == "warning"


def test_vlm_skipped_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    img = tmp_path / "f.png"
    img.write_bytes(b"img")
    report = review_media(media_path=str(img), expected={"appearance": "黑发"}, mode="first_frame")
    assert report["skipped"] is True
    assert report["ok"] is False


def test_seedance_frames_still_drop_identity_refs():
    payload: dict = {"ratio": "9:16"}
    notes = apply_seedance_content(
        payload,
        text="雨夜",
        first_url="http://x/f.png",
        last_url="http://x/t.png",
        refs=[{"url": "http://x/portrait.png"}],
        caps=video_surface("seedance_25"),
    )
    assert any("互斥" in n for n in notes)
    roles = [item.get("role") for item in payload["content"] if item.get("type") != "text"]
    assert roles == ["first_frame", "last_frame"]
    assert "reference_image" not in roles


def test_omni_payload_includes_subject_refs(monkeypatch):
    monkeypatch.setenv("KLING_OMNI_MODEL", "kling-v3-omni")
    path, payload = _video_payload("kling_omni_30", {
        "prompt": "雨夜",
        "seconds": 5,
        "image_url": "http://x/f.png",
        "refs": [{"url": "http://x/face.png", "type": "subject"}],
    }, "雨夜")
    types = [item["type"] for item in payload["contents"]]
    assert path == "/omni-video/kling-3.0-omni"
    assert types == ["prompt", "first_frame", "refer_image"]
    assert payload["contents"][1].get("id") is None
    assert payload["contents"][2]["id"] == "image_1"


def test_continuity_note_seedance_not_agnes():
    shot = {
        "shot_id": "sh02",
        "scene_id": "sc01",
        "character_ids": ["a"],
        "location_id": "alley",
        "visual_details": {"objects": [{"id": "lighter"}]},
    }
    state = update_continuity(
        None, shot,
        registry={"a": {"outfit": "蓝外套"}},
    )
    note = format_continuity_note(state, shot)
    assert "蓝外套" in note
    assert "lighter" in note
    assert "@图片" not in note
    assert "<<<image" not in note
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    pair = build_shot_prompt_pair(golden["shot"], **golden["builder_kwargs"])
    agnes = adapt_visual_prompt("agnes_v20", pair, continuity_note=note)
    assert agnes["passthrough"] is True
    assert agnes["video_prompt"] == golden["video_prompt"]
    seedance = adapt_visual_prompt(
        "seedance_25",
        {"video_prompt": "他站住", "first_frame_prompt": "巷口"},
        continuity_note=note,
        duration_seconds=5,
    )
    assert seedance["video_prompt"].startswith("场记：")
    assert "蓝外套" in seedance["video_prompt"]
    assert "[0s-" in seedance["video_prompt"]


def test_seed_continuity_from_previous_episode(tmp_path):
    series = _seed_series(tmp_path)
    materialize_episodes(series)
    ep01 = series / "episodes" / "ep01"
    ep02 = series / "episodes" / "ep02"
    (ep01 / "artifacts" / "continuity.json").write_text(
        json.dumps({"last_shot_id": "sh09", "characters": [{"id": "a", "outfit": "蓝外套"}]}),
        encoding="utf-8",
    )
    seeded = load_or_seed_continuity(ep02)
    assert seeded["last_shot_id"] == "sh09"
    saved = json.loads((ep02 / "artifacts" / "continuity.json").read_text(encoding="utf-8"))
    assert saved["last_shot_id"] == "sh09"


def test_copy_sibling_turnaround_with_portrait(tmp_path):
    series = _seed_series(tmp_path)
    materialize_episodes(series)
    ep01 = series / "episodes" / "ep01"
    ep02 = series / "episodes" / "ep02"
    img = ep01 / "assets" / "images"
    img.mkdir(parents=True, exist_ok=True)
    (img / "portrait_a.png").write_bytes(b"p")
    (img / "turnaround_a.png").write_bytes(b"t")
    from montage.engine.artifacts import ArtifactStore

    ArtifactStore(ep01).write("asset_manifest", {
        "items": [
            {"id": "portrait_a", "kind": "image", "path": "assets/images/portrait_a.png"},
            {"id": "turnaround_a", "kind": "image", "path": "assets/images/turnaround_a.png"},
        ],
        "reference_assets": [
            {"id": "portrait_a", "kind": "portrait", "character_id": "a", "path": "assets/images/portrait_a.png"},
            {"id": "turnaround_a", "kind": "turnaround", "character_id": "a", "path": "assets/images/turnaround_a.png"},
        ],
    })
    copied = copy_sibling_still_refs(
        ep02,
        portrait_ids=["a"],
        retry_ids=set(),
        manifest={"items": [], "reference_assets": []},
    )
    kinds = {r["kind"] for r in copied["reference_assets"]}
    assert "portrait" in kinds
    assert "turnaround" in kinds
    assert (ep02 / "assets" / "images" / "turnaround_a.png").is_file()
    assert (ep02 / "assets" / "images" / "turnaround_a.png").read_bytes() == b"t"


def test_shot_runner_omni_gets_subject_refs(tmp_path, monkeypatch):
    monkeypatch.setenv("KLING_OMNI_MODEL", "kling-v3-omni")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"video_loop": "kling"}),
        encoding="utf-8",
    )
    calls: list[dict] = []

    def track_video(inputs):
        calls.append(dict(inputs))
        return _fake_video(inputs)

    tool = ShotRunner(
        image_execute=_uniq_image,
        video_execute=track_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
        vlm_review=lambda _p, _c: {"ok": True, "skipped": True, "issues": []},
    )
    plan = _hero_plan()
    plan["scenes"][0]["shots"][0]["api_id"] = "kling_omni_30"
    result = tool.execute({
        "scene_plan": plan,
        "project_dir": str(proj),
        "dry_run": False,
        "script": {"characters": [{"id": "a", "appearance": "黑发短寸"}]},
    })
    assert result.success, result.error
    assert calls
    video = calls[-1]
    refs = video.get("refs") or []
    assert any(r.get("type") == "subject" for r in refs)
    extra = json.loads((proj / "artifacts" / "continuity.json").read_text(encoding="utf-8"))
    assert extra.get("last_shot_id") == "sh01"


def test_shot_runner_agnes_keyframes_keep_prompt(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.setenv("AGNES_VIDEO_MODEL", "agnes-video-v2.0")
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()
    (proj / "artifacts" / "proposal_packet.json").write_text(
        json.dumps({"video_loop": "agnes"}),
        encoding="utf-8",
    )
    calls: list[dict] = []

    def track_video(inputs):
        calls.append(dict(inputs))
        return _fake_video(inputs)

    tool = ShotRunner(
        image_execute=_uniq_image,
        video_execute=track_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
        vlm_review=lambda _p, _c: {"ok": True, "skipped": True, "issues": []},
    )
    result = tool.execute({
        "scene_plan": _hero_plan(),
        "project_dir": str(proj),
        "dry_run": False,
        "script": {"characters": [{"id": "a", "appearance": "黑发短寸"}]},
    })
    assert result.success, result.error
    assert calls
    video = calls[-1]
    images = (video.get("extra_body") or {}).get("image") or []
    assert any("example.test" in str(u) for u in images)
    assert "场记：" not in str(video.get("prompt") or "")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    pair = build_shot_prompt_pair(golden["shot"], **golden["builder_kwargs"])
    adapted = adapt_visual_prompt("agnes_v20", pair, continuity_note="蓝外套")
    assert adapted["video_prompt"] == golden["video_prompt"]


def test_vlm_rejects_blonde_clip(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()

    def fake_vlm(_path, ctx):
        if (ctx or {}).get("mode") == "video_clip":
            return {
                "ok": False,
                "skipped": False,
                "issues": [{
                    "severity": "critical",
                    "kind": "人物不一致",
                    "message": "定妆黑发，成片为金发",
                    "proposed_fix": "重抽",
                }],
            }
        return {"ok": True, "skipped": False, "issues": []}

    tool = ShotRunner(
        image_execute=_uniq_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
        vlm_review=fake_vlm,
    )
    result = tool.execute({
        "scene_plan": _hero_plan(),
        "project_dir": str(proj),
        "dry_run": False,
        "script": {"characters": [{"id": "a", "appearance": "黑发短寸"}]},
    })
    data = result.data if isinstance(result.data, dict) else {}
    assert "sh01" in (data.get("retryable_ids") or [])
    items = (data.get("asset_manifest") or {}).get("items") or []
    assert not any(i.get("kind") == "video" and i.get("shot_id") == "sh01" for i in items)
    sidecar = json.loads((proj / "artifacts" / "vlm_review.json").read_text(encoding="utf-8"))
    assert sidecar["pass"] is False
    kinds = [i.get("kind") for row in sidecar.get("shots") or [] for i in row.get("issues") or []]
    assert "人物不一致" in kinds


def test_no_key_shot_runner_still_succeeds(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    proj = tmp_path / "p"
    proj.mkdir()
    (proj / "artifacts").mkdir()
    tool = ShotRunner(
        image_execute=_uniq_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    result = tool.execute({
        "scene_plan": _hero_plan(),
        "project_dir": str(proj),
        "dry_run": False,
        "script": {"characters": [{"id": "a", "appearance": "黑发短寸"}]},
    })
    assert result.success, result.error
    sidecar = json.loads((proj / "artifacts" / "vlm_review.json").read_text(encoding="utf-8"))
    assert sidecar["skipped"] is True
    assert sidecar["pass"] is False
    lifted = lift_shot_prompts(_hero_plan()["scenes"][0]["shots"])
    assert lifted["shots"][0]["shot_id"] == "sh01"


def test_cast_does_not_call_vlm(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    called: list[int] = []

    def track_vlm(_path, _ctx):
        called.append(1)
        return {"ok": True, "skipped": False, "issues": []}

    tool = ShotRunner(
        image_execute=_uniq_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        vlm_review=track_vlm,
    )
    result = tool.execute({
        "stage": "cast",
        "bible": {
            "playbook": "cyberpunk_neon",
            "characters": [{"id": "a", "name": "阿宁", "appearance": "黑发"}],
            "locations": [{"id": "alley", "appearance": "雨夜巷"}],
        },
        "project_dir": str(proj),
        "dry_run": False,
    })
    assert result.success, result.error
    assert called == []


def test_machine_complete_skips_assets_on_vlm_fail(tmp_path, monkeypatch):
    from montage.engine import produce as produce_mod
    from montage.engine import stages as stages_mod
    from montage.engine import gates as gates_mod
    from montage.engine.stages import StageStatus

    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    (proj / "artifacts" / "vlm_review.json").write_text(
        json.dumps({"pass": False, "skipped": False, "shots": []}),
        encoding="utf-8",
    )
    written: list[str] = []

    class _Store:
        def __init__(self, _root):
            pass

        def write(self, stage, status, **kwargs):
            written.append(stage)
            assert status == StageStatus.COMPLETED.value
            assert kwargs.get("approved_by") == "produce"

    monkeypatch.setattr(stages_mod, "CheckpointStore", _Store)
    monkeypatch.setattr(
        gates_mod, "validate_completion",
        lambda *_a, **_k: {"missing": [], "invalid": []},
    )
    produce_mod._machine_complete(proj, ran_gen=True)
    assert "assets" not in written
    assert "compose" in written
    assert "publish" in written


def test_machine_complete_relax_still_writes_assets(tmp_path, monkeypatch):
    from montage.engine import produce as produce_mod
    from montage.engine import stages as stages_mod
    from montage.engine import gates as gates_mod
    from montage.engine.stages import StageStatus

    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    (proj / "artifacts" / "vlm_review.json").write_text(
        json.dumps({"pass": False, "skipped": False, "shots": []}),
        encoding="utf-8",
    )
    written: list[str] = []

    class _Store:
        def __init__(self, _root):
            pass

        def write(self, stage, status, **kwargs):
            written.append(stage)
            assert status == StageStatus.COMPLETED.value

    monkeypatch.setenv("MONTAGE_RELAX_GATES", "1")
    monkeypatch.setattr(stages_mod, "CheckpointStore", _Store)
    monkeypatch.setattr(
        gates_mod, "validate_completion",
        lambda *_a, **_k: {"missing": [], "invalid": []},
    )
    produce_mod._machine_complete(proj, ran_gen=True)
    assert "assets" in written
