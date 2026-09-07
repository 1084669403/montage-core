"""W4 finish 步：显式 LUT / 字幕旁路；默认 skip。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from montage.engine.artifacts import ArtifactStore
from montage.engine.finish import flatten_cues, inspect_finish, lut_cube_path, script_title
from montage.toolbase import ToolResult
from montage.tools.subtitle_builder import SubtitleBuilder
from test_produce import FakeTool, _bag, _run, _seed_project


def test_inspect_default_w0_fixture_skips(tmp_path):
    proj = _seed_project(tmp_path)
    jobs = inspect_finish(proj)
    assert jobs["lut_path"] == ""
    assert jobs["profile"] == ""
    assert jobs["title"] == ""
    assert jobs["cues"] == []
    assert jobs["need_ffmpeg"] is False
    assert jobs["need_srt"] is False


def test_bad_lut_id_is_not_a_cube(tmp_path):
    proj = _seed_project(tmp_path)
    ArtifactStore(proj).write("compose_plan", {"lut": "luts/does-not-exist", "shots": []})
    assert lut_cube_path(proj) is None
    jobs = inspect_finish(proj)
    assert jobs["lut_path"] == ""
    assert jobs["need_ffmpeg"] is False


def test_known_lut_resolves(tmp_path):
    proj = _seed_project(tmp_path)
    ArtifactStore(proj).write("compose_plan", {"lut": "luts/teal-orange", "shots": []})
    cube = lut_cube_path(proj)
    assert cube is not None and cube.suffix == ".cube"


def test_flatten_cues_does_not_reoffset(tmp_path):
    proj = _seed_project(tmp_path)
    ArtifactStore(proj).write("compose_plan", {
        "shots": [
            {"shot_id": "sh01", "subtitle_cues": [
                {"text": "一", "start_seconds": 5, "end_seconds": 7},
            ]},
            {"shot_id": "sh02", "subtitle_cues": [
                {"text": "二", "start_seconds": 10, "end_seconds": 12},
            ]},
        ],
    })
    cues = flatten_cues(proj)
    assert [c["start_seconds"] for c in cues] == [5, 10]


def test_script_title_not_project_title(tmp_path):
    proj = _seed_project(tmp_path)
    assert script_title(proj) == ""
    ArtifactStore(proj).write("script", {"title": "雨夜", "sections": []})
    assert script_title(proj) == "雨夜"


def test_produce_writes_srt_without_extra_ffmpeg(tmp_path):

    proj = _seed_project(tmp_path)
    tools, _order, _bgm = _bag(proj)
    inner = tools["compose_planner"]._handler

    def compose(inputs):
        result = inner(inputs)
        if not inputs.get("realize"):
            store = ArtifactStore(proj)
            plan = store.read("compose_plan") or {}
            shots = list(plan.get("shots") or [{"shot_id": "sh01"}])
            shots[0] = dict(shots[0])
            shots[0]["subtitle_cues"] = [
                {"text": "你好", "start_seconds": 0, "end_seconds": 2},
            ]
            plan["shots"] = shots
            store.write("compose_plan", plan)
        return result

    tools["compose_planner"] = FakeTool("compose_planner", compose)
    tools["subtitle_builder"] = SubtitleBuilder()
    result = _run(proj, tools)
    assert result["success"]
    assert result["progress"]["steps"]["finish"]["status"] == "ok"
    srt = (proj / "renders" / "final.srt").read_text(encoding="utf-8")
    assert "你好" in srt
    assert len(tools["ffmpeg_compose"].calls) == 1


def _bag_finish_ops(proj):
    tools, order, bgm = _bag(proj)
    inner = tools["ffmpeg_compose"]._handler

    def compose(inputs):
        op = str(inputs.get("operation") or "assemble")
        if op == "assemble":
            return inner(inputs)
        if op == "apply_profile":
            assert "project_dir" not in inputs
            assert inputs.get("profile")
        out = Path(inputs.get("output_path") or "out.mp4")
        out.parent.mkdir(parents=True, exist_ok=True)
        if op == "concat":
            clips = [Path(p) for p in (inputs.get("clips") or [])]
            src = next((c for c in clips if c.is_file()), None)
            out.write_bytes(src.read_bytes() if src is not None else b"concat")
        else:
            src = Path(inputs.get("input_path") or "")
            out.write_bytes(src.read_bytes() if src.is_file() else b"ff")
        return ToolResult(success=True, data={"output": str(out), "operation": op})

    tools["ffmpeg_compose"] = FakeTool("ffmpeg_compose", compose)
    return tools, order, bgm


def test_proposal_profile_triggers_inspect(tmp_path):
    proj = _seed_project(tmp_path)
    ArtifactStore(proj).write("proposal_packet", {"output_profile": "douyin_vertical"})
    jobs = inspect_finish(proj)
    assert jobs["profile"] == "douyin_vertical"
    assert jobs["need_ffmpeg"] is True


def test_unknown_profile_is_not_a_trigger(tmp_path):
    proj = _seed_project(tmp_path)
    ArtifactStore(proj).write("proposal_packet", {"output_profile": "nope"})
    jobs = inspect_finish(proj)
    assert jobs["profile"] == ""
    assert jobs["need_ffmpeg"] is False


def test_cli_profile_overrides_proposal(tmp_path):
    proj = _seed_project(tmp_path)
    ArtifactStore(proj).write("proposal_packet", {"output_profile": "youtube_landscape"})
    jobs = inspect_finish(proj, cli_profile="cinematic_21_9")
    assert jobs["profile"] == "cinematic_21_9"


def test_punct_only_title_skips_card(tmp_path):
    proj = _seed_project(tmp_path)
    ArtifactStore(proj).write("script", {"title": ":\n'"})
    jobs = inspect_finish(proj)
    assert jobs["title"] == ":\n'"
    assert jobs["drawtext"] == ""
    assert jobs["need_ffmpeg"] is False


def test_lower_third_adds_character_name(tmp_path):
    proj = _seed_project(tmp_path)
    ArtifactStore(proj).write("script", {
        "title": "雨夜",
        "characters": [{"name": "林"}],
    })
    jobs = inspect_finish(proj)
    assert jobs["drawtext"]
    assert jobs["lower_third"] == "雨夜 · 林"


def test_produce_title_card_and_lower_third(tmp_path):
    proj = _seed_project(tmp_path)
    ArtifactStore(proj).write("script", {
        "title": "雨夜",
        "characters": [{"name": "林"}],
        "sections": [],
    })
    tools, _order, _bgm = _bag_finish_ops(proj)
    result = _run(proj, tools)
    assert result["success"]
    ops = [c.get("operation") for c in tools["ffmpeg_compose"].calls]
    assert ops == ["assemble", "title_card", "concat", "lower_third"]
    title_call = tools["ffmpeg_compose"].calls[1]
    assert title_call["title"] == "雨夜"
    assert "project_dir" not in title_call
    lower = tools["ffmpeg_compose"].calls[3]
    assert lower["title"] == "雨夜 · 林"
    assert lower["start_seconds"] == 2.0
    assert result["progress"]["steps"]["finish"]["status"] == "ok"
    assert result["progress"]["steps"]["finish"]["title_dur"] == 2.0
    assert tools["release_pack"].calls[0].get("title_dur") == 2.0
    leftover = [p.name for p in (proj / "renders").glob("*.mp4")]
    assert leftover == ["final.mp4"]


def test_produce_burn_subs_opt_in(tmp_path):
    proj = _seed_project(tmp_path)
    tools, _order, _bgm = _bag_finish_ops(proj)
    inner = tools["compose_planner"]._handler

    def compose(inputs):
        result = inner(inputs)
        if not inputs.get("realize"):
            store = ArtifactStore(proj)
            plan = store.read("compose_plan") or {}
            shots = list(plan.get("shots") or [{"shot_id": "sh01"}])
            shots[0] = dict(shots[0])
            shots[0]["subtitle_cues"] = [
                {"text": "你好", "start_seconds": 0, "end_seconds": 2},
            ]
            plan["shots"] = shots
            store.write("compose_plan", plan)
        return result

    tools["compose_planner"] = FakeTool("compose_planner", compose)
    tools["subtitle_builder"] = SubtitleBuilder()
    result = _run(proj, tools, burn_subs=True)
    assert result["success"]
    ops = [c.get("operation") for c in tools["ffmpeg_compose"].calls]
    assert ops == ["assemble", "burn_subtitles"]
    assert "你好" in (proj / "renders" / "final.srt").read_text(encoding="utf-8")


def test_produce_explicit_profile_without_project_dir(tmp_path):
    proj = _seed_project(tmp_path)
    ArtifactStore(proj).write("proposal_packet", {"output_profile": "douyin_vertical"})
    tools, _order, _bgm = _bag_finish_ops(proj)
    result = _run(proj, tools)
    assert result["success"]
    ops = [c.get("operation") for c in tools["ffmpeg_compose"].calls]
    assert ops == ["assemble", "apply_profile"]
    profile_call = tools["ffmpeg_compose"].calls[1]
    assert profile_call["profile"] == "douyin_vertical"
    assert "project_dir" not in profile_call
    assert result["progress"]["steps"]["finish"]["title_dur"] == 0.0


def test_produce_cli_profile_not_pipeline_default(tmp_path):
    proj = _seed_project(tmp_path)
    tools, _order, _bgm = _bag_finish_ops(proj)
    result = _run(proj, tools, profile="youtube_landscape")
    assert result["success"]
    ops = [c.get("operation") for c in tools["ffmpeg_compose"].calls]
    assert ops == ["assemble", "apply_profile"]
    assert tools["ffmpeg_compose"].calls[1]["profile"] == "youtube_landscape"


def test_project_title_alone_does_not_make_title_card(tmp_path):
    proj = _seed_project(tmp_path)
    tools, _order, _bgm = _bag(proj)
    result = _run(proj, tools)
    assert result["success"]
    assert len(tools["ffmpeg_compose"].calls) == 1
    assert tools["ffmpeg_compose"].calls[0]["operation"] == "assemble"
