"""compose_planner：compose_plan 编译为 edit_decisions；不进 produces。"""

import json
from pathlib import Path

from montage.engine.artifacts import ArtifactStore
from montage.pipelines import CINEMATIC, CLIP_FACTORY, DOCUMENTARY
from montage.playbooks import get_playbook
from montage.registry import ToolRegistry
from montage.schemas import get_schema
from montage.style_packs import get_style_pack
from montage.tools.compose_planner import (
    ComposePlanner,
    build_compose_plan,
    compile_compose_plan,
    resolve_lut_file,
)
from montage.tools.script_to_scene_plan import convert_script_to_scene_plan


def _fixture():
    path = Path(__file__).parent / "fixtures" / "script_complete.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _plan():
    return convert_script_to_scene_plan(_fixture(), get_playbook("cyberpunk_neon"))["scene_plan"]


def test_tool_discovered():
    reg = ToolRegistry()
    reg.discover()
    assert reg.get("compose_planner") is not None


def test_schema_optional_not_required():
    schema = get_schema("compose_plan")
    assert schema is not None
    assert "required" not in schema or schema.get("required") in (None, [])


def test_build_and_compile_valid(tmp_path):
    plan = _plan()
    shot_id = plan["scenes"][0]["shots"][0]["shot_id"]
    clip = tmp_path / f"{shot_id}.mp4"
    clip.write_bytes(b"vid")
    manifest = {
        "items": [
            {"id": f"{shot_id}_video", "kind": "video", "path": str(clip), "shot_id": shot_id, "scene_id": "sc01"},
        ],
        "reference_assets": [],
    }
    built = build_compose_plan(
        plan,
        asset_manifest=manifest,
        playbook=get_playbook("cyberpunk_neon"),
        style_pack=get_style_pack("cyber"),
        edit_style="cinematic",
    )
    compose_plan = built["compose_plan"]
    assert ArtifactStore.validate(compose_plan, get_schema("compose_plan")) == []
    assert compose_plan["render_runtime"] == "ffmpeg"
    assert compose_plan["shots"]
    assert compose_plan["shots"][0]["render_kind"] == "ai_clip"
    assert compose_plan["lut"] == "luts/dark-moody"
    assert compose_plan["shots"][0]["clip_path"] == str(clip)
    assert compose_plan["shots"][0]["subtitle_cues"]
    decisions = compile_compose_plan(compose_plan)
    assert ArtifactStore.validate(decisions, get_schema("edit_decisions")) == []
    assert decisions["render_runtime"] == "ffmpeg"
    assert decisions["cuts"][0]["clip_path"] == str(clip)
    assert decisions["cuts"][0]["shot_id"]
    assert decisions["cuts"][0]["transition"] == compose_plan["shots"][0]["transition"]
    assert decisions["cuts"][0]["transition_in"] == decisions["cuts"][0]["transition"]


def test_cut_only_forces_cut():
    plan = {
        "scenes": [{
            "id": "sc01",
            "description": "x",
            "narrative_role": "hook",
            "start_seconds": 0,
            "end_seconds": 10,
            "hero_moment": True,
            "shots": [
                {"shot_id": "a", "shot_kind": "video", "duration_seconds": 5},
                {"shot_id": "b", "shot_kind": "video", "duration_seconds": 5, "hero_moment": True},
            ],
        }],
    }
    cine = build_compose_plan(plan, edit_style="cinematic")["compose_plan"]
    doc = build_compose_plan(plan, edit_style="documentary", transition_policy="cut_only")["compose_plan"]
    assert cine["shots"][0]["transition"] == "cut"
    assert cine["shots"][1]["transition"] in ("cut", "zoom_punch", "fade_black", "crossfade")
    assert cine["allow_non_cut"] is True
    assert all(s["transition"] == "cut" for s in doc["shots"])
    assert doc["allow_non_cut"] is False


def test_measured_duration_overrides_timeline():
    """asset_manifest 的实测时长盖过计划时长，字幕 cue 跟着整体后移。

    对应真实事故：Agnes 请求 6s 实回 6.59s，compose_plan 仍按 6s 算 cue，
    第 4 条字幕起累计早约 1.8s。
    """
    def shot(sid, text):
        return {
            "shot_id": sid,
            "shot_kind": "video",
            "duration_seconds": 6,
            "audio_prompt": {"dialogue": [{"text": text, "speaker_id": "narrator"}]},
        }

    plan = {
        "scenes": [
            {
                "id": "sc01",
                "description": "x",
                "start_seconds": 0,
                "end_seconds": 12,
                "shots": [shot("a", "第一句"), shot("b", "第二句")],
            },
            {
                "id": "sc02",
                "description": "y",
                "start_seconds": 12,
                "end_seconds": 18,
                "shots": [shot("c", "第三句")],
            },
        ],
    }
    manifest = {"items": [
        {"id": "a_video", "kind": "video", "path": "/nonexistent/a.mp4",
         "shot_id": "a", "duration_seconds": 6.59},
        # b 既没记实测、文件也不存在 → probe 得 0，退回计划 6.0
        {"id": "b_video", "kind": "video", "path": "/nonexistent/b.mp4", "shot_id": "b"},
        {"id": "c_video", "kind": "video", "path": "/nonexistent/c.mp4",
         "shot_id": "c", "duration_seconds": 7.5},
    ]}
    built = build_compose_plan(plan, asset_manifest=manifest)
    shots = built["compose_plan"]["shots"]
    assert shots[0]["duration_seconds"] == 6.59
    assert shots[1]["duration_seconds"] == 6.0
    assert shots[0]["subtitle_cues"][0]["start_seconds"] == 0.0
    assert shots[0]["subtitle_cues"][0]["end_seconds"] == 6.59
    assert abs(shots[1]["subtitle_cues"][0]["start_seconds"] - 6.59) < 1e-6
    # 场起点也要按实测顺延：sc01 实际 6.59+6.0=12.59，sc02 不能还从计划的 12.0 开始
    assert abs(shots[2]["subtitle_cues"][0]["start_seconds"] - 12.59) < 1e-6
    assert any("实测时长" in f["message"] for f in built["findings"])


def test_graphic_kind_degrades_with_finding():
    plan = {
        "scenes": [{
            "id": "sc01",
            "description": "片头",
            "start_seconds": 0,
            "end_seconds": 5,
            "shots": [{
                "shot_id": "title",
                "shot_kind": "image",
                "duration_seconds": 5,
                "render_kind": "title_card",
            }],
        }],
    }
    built = build_compose_plan(plan)
    shot = built["compose_plan"]["shots"][0]
    assert shot["render_kind"] == "ai_clip"
    assert shot["effects"] and shot["effects"][0]["operation"] == "ken_burns"
    assert any("图形镜" in f["message"] for f in built["findings"])


def test_tool_writes_edit_decisions_not_required_plan(tmp_path):
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    store = ArtifactStore(proj)
    store.write("scene_plan", _plan())
    tool = ComposePlanner()
    result = tool.execute({"project_dir": str(proj)})
    assert result.success, result.error
    assert store.read("edit_decisions")
    assert store.read("compose_plan")  # 可选写入，但不进 produces
    assert ArtifactStore.validate(store.read("edit_decisions"), get_schema("edit_decisions")) == []


def test_refuse_overwrite(tmp_path):
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    store = ArtifactStore(proj)
    store.write("scene_plan", _plan())
    store.write("edit_decisions", {"cuts": [{"clip_path": "kept.mp4"}]})
    tool = ComposePlanner()
    redone = tool.execute({"project_dir": str(proj)})
    assert not redone.success
    assert redone.data.get("refused")
    forced = tool.execute({"project_dir": str(proj), "overwrite": True})
    assert forced.success


def test_pipelines_no_compose_plan_in_produces():
    for pipe in (CINEMATIC, DOCUMENTARY, CLIP_FACTORY):
        compose = next(s for s in pipe["stages"] if s["name"] == "compose")
        assert "edit_decisions" in compose["produces"]
        assert "compose_plan" not in compose["produces"]
        assert "compose_planner" in compose["tools"]


def test_realize_rewrites_cuts_without_overwrite(tmp_path):
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    img = tmp_path / "still.png"
    img.write_bytes(b"png")
    store = ArtifactStore(proj)
    plan = {
        "version": "1",
        "render_runtime": "ffmpeg",
        "lut": "luts/dark-moody",
        "shots": [{
            "shot_id": "sc01_01",
            "clip_path": str(img),
            "duration_seconds": 4,
            "effects": [{"operation": "ken_burns", "zoom": "in"}],
            "transition": "cut",
        }],
    }
    store.write("compose_plan", plan)
    store.write("edit_decisions", {
        "cuts": [{"shot_id": "sc01_01", "clip_path": str(img), "transition": "cut"}],
        "render_runtime": "ffmpeg",
    })
    calls: list = []

    def fake_kb(src, dest, dur, **kw):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"vid")
        calls.append((str(src), str(dest), dur, kw.get("zoom")))
        return dest

    tool = ComposePlanner(ken_burns_fn=fake_kb)
    result = tool.execute({"project_dir": str(proj), "realize": True})
    assert result.success, result.error
    assert calls
    new_path = store.read("edit_decisions")["cuts"][0]["clip_path"]
    assert new_path != str(img)
    assert Path(new_path).exists()
    assert result.data["realized"] == ["sc01_01"]
    lut = resolve_lut_file("luts/dark-moody")
    assert lut.endswith(".cube")
    assert Path(lut).exists()


def test_realize_ken_burns_uses_target_size_not_1080p(tmp_path):
    """target_size 优先：静图镜按成片画布出片，不默认 1920x1080。"""
    from montage.tools.compose_planner import realize_ken_burns

    img = tmp_path / "still.png"
    img.write_bytes(b"png")
    plan = {"shots": [{
        "shot_id": "a", "clip_path": str(img), "duration_seconds": 5,
        "effects": [{"operation": "ken_burns"}],
    }]}
    decisions = {"cuts": [{"shot_id": "a", "clip_path": str(img)}]}
    seen: dict = {}

    def fake_kb(src, dest, dur, **kw):
        seen.update(kw)
        Path(dest).write_bytes(b"v")
        return dest

    _, _, realized = realize_ken_burns(
        plan, decisions, out_dir=tmp_path / "kb", ken_burns_fn=fake_kb,
        target_size=(1080, 1920),
    )
    assert realized == ["a"]
    assert (seen.get("width"), seen.get("height")) == (1080, 1920)


def test_realize_uses_proposal_output_profile_size(tmp_path):
    """realize 从 proposal_packet.output_profile 推成片画布，而非硬编码横屏。"""
    proj = tmp_path / "p"
    (proj / "artifacts").mkdir(parents=True)
    img = tmp_path / "still.png"
    img.write_bytes(b"png")
    store = ArtifactStore(proj)
    store.write("proposal_packet", {"output_profile": "douyin_vertical"})
    store.write("compose_plan", {
        "version": "1",
        "render_runtime": "ffmpeg",
        "shots": [{
            "shot_id": "a", "clip_path": str(img), "duration_seconds": 4,
            "effects": [{"operation": "ken_burns"}], "transition": "cut",
        }],
    })
    store.write("edit_decisions", {
        "cuts": [{"shot_id": "a", "clip_path": str(img), "transition": "cut"}],
        "render_runtime": "ffmpeg",
    })
    seen: dict = {}

    def fake_kb(src, dest, dur, **kw):
        seen.update(kw)
        Path(dest).write_bytes(b"v")
        return dest

    result = ComposePlanner(ken_burns_fn=fake_kb).execute({
        "project_dir": str(proj), "realize": True,
    })
    assert result.success, result.error
    assert (seen.get("width"), seen.get("height")) == (1080, 1920)


def test_realize_skips_video_clips(tmp_path):
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"vid")
    plan = {
        "shots": [{
            "shot_id": "a",
            "clip_path": str(vid),
            "duration_seconds": 5,
            "effects": [{"operation": "ken_burns"}],
        }],
    }
    decisions = {"cuts": [{"shot_id": "a", "clip_path": str(vid)}]}
    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("视频镜不应 ken_burns")

    from montage.tools.compose_planner import realize_ken_burns

    out, _, realized = realize_ken_burns(
        plan, decisions, out_dir=tmp_path / "kb", ken_burns_fn=boom,
    )
    assert called["n"] == 0
    assert realized == []
    assert out["cuts"][0]["clip_path"] == str(vid)


def test_agnes_keep_audio_hints():
    plan = {
        "scenes": [{
            "id": "sc01",
            "description": "x",
            "start_seconds": 0,
            "end_seconds": 5,
            "shots": [{"shot_id": "a", "shot_kind": "video", "duration_seconds": 5}],
        }],
    }
    built = build_compose_plan(
        plan,
        shot_prompts={"shots": [{"shot_id": "a", "audio_source": "agnes_prompt"}]},
        keep_audio=True,
    )
    hints = built["compose_plan"]["assemble_hints"]
    assert hints["mix_source_audio"] is True
    assert hints["ducking"] is False


def test_kling_mixed_keep_audio_hints():
    plan = {
        "scenes": [{
            "id": "sc01",
            "description": "x",
            "start_seconds": 0,
            "end_seconds": 10,
            "shots": [
                {"shot_id": "a", "shot_kind": "video", "duration_seconds": 5},
                {"shot_id": "b", "shot_kind": "video", "duration_seconds": 5},
            ],
        }],
    }
    built = build_compose_plan(
        plan,
        shot_prompts={"shots": [
            {"shot_id": "a", "audio_source": "kling_prompt"},
            {"shot_id": "b"},
        ]},
        keep_audio=False,
        mix_source_audio=True,
        ducking=True,
    )
    hints = built["compose_plan"]["assemble_hints"]
    assert hints["mix_source_audio"] is True
    assert hints["ducking"] is True
