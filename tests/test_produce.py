"""W0 produce 薄编排器：mock 工具，不打 HTTP / 真 ffmpeg。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from montage.engine.artifacts import ArtifactStore
from montage.engine.produce import run_produce
from montage.engine.project import init_project
from montage.registry import ToolRegistry
from montage.toolbase import BaseTool, ToolResult, ToolRuntime


class FakeTool(BaseTool):
    name = "fake"
    capability = "analysis"
    provider = "test"
    runtime = ToolRuntime.LOCAL
    input_schema = {}

    def __init__(self, name: str, handler):
        self.name = name
        self.calls: list[dict] = []
        self._handler = handler

    def execute(self, inputs):
        self.calls.append(dict(inputs))
        return self._handler(inputs)


def _scene_plan(shot_id="sh01"):
    return {
        "scenes": [{
            "id": "sc01",
            "start_seconds": 0,
            "end_seconds": 5,
            "shots": [{
                "shot_id": shot_id,
                "scene_id": "sc01",
                "duration_seconds": 5,
                "shot_kind": "video",
            }],
        }],
    }


def _seed_project(root: Path, *, rel_clip=True, png=False, pipeline="cinematic"):
    proj = init_project(root, "film", "演示", pipeline)
    store = ArtifactStore(proj)
    store.write("scene_plan", _scene_plan())
    clip_dir = proj / "assets" / "videos"
    clip_dir.mkdir(parents=True, exist_ok=True)
    clip = clip_dir / ("a.png" if png else "a.mp4")
    clip.write_bytes(b"\x89PNG" if png else b"vid")
    path = "assets/videos/" + clip.name if rel_clip else str(clip)
    store.write("asset_manifest", {
        "items": [{
            "id": "sh01_clip",
            "kind": "image" if png else "video",
            "path": path,
            "shot_id": "sh01",
            "scene_id": "sc01",
        }],
        "reference_assets": [],
    })
    return proj


def _bag(proj: Path, *, events=None, fail_at=None, overlay_fail=False):
    store = ArtifactStore(proj)
    order: list[str] = []
    bgm = proj / "assets" / "music" / "theme.wav"
    bgm.parent.mkdir(parents=True, exist_ok=True)
    bgm.write_bytes(b"RIFF")
    default_events = events
    if default_events is None:
        default_events = [{
            "kind": "bgm",
            "asset_id": "bgm/theme",
            "path": str(bgm),
            "available": True,
        }]

    def soundtrack(_inputs):
        order.append("soundtrack")
        if fail_at == "soundtrack":
            return ToolResult(success=False, error="soundtrack boom")
        data = {"events": list(default_events), "attributions": [], "findings": []}
        store.write("soundtrack", data)
        return ToolResult(success=True, data=data)

    def compose(inputs):
        if inputs.get("realize"):
            order.append("realize")
            if fail_at == "realize":
                return ToolResult(success=False, error="realize boom")
            decisions = store.read("edit_decisions") or {"cuts": []}
            cuts = []
            dest = proj / "assets" / "kenburns" / "sh01_kb.mp4"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"vid")
            for cut in decisions.get("cuts") or []:
                row = dict(cut)
                row["clip_path"] = str(dest)
                cuts.append(row)
            decisions["cuts"] = cuts
            store.write("edit_decisions", decisions)
            return ToolResult(success=True, data={"edit_decisions": decisions})
        order.append("compose_plan")
        if fail_at == "compose_plan":
            return ToolResult(success=False, error="compose boom")
        assert inputs.get("overwrite") is True
        clip = (store.read("asset_manifest") or {}).get("items", [{}])[0].get("path")
        plan = {"shots": [{"shot_id": "sh01", "clip_path": clip, "effects": []}]}
        decisions = {
            "render_runtime": "ffmpeg",
            "cuts": [{"shot_id": "sh01", "clip_path": clip, "transition": "cut"}],
        }
        store.write("compose_plan", plan)
        store.write("edit_decisions", decisions)
        return ToolResult(success=True, data={"compose_plan": plan, "edit_decisions": decisions})

    def place(_inputs):
        order.append("place_audio")
        if fail_at == "place_audio":
            return ToolResult(success=False, error="place boom")
        findings = []
        if overlay_fail:
            findings.append({
                "severity": "warning",
                "field": "sh01",
                "message": "叠音失败: ffmpeg missing",
            })
        data = {
            "music_path": str(bgm),
            "music_segments": [],
            "mix_source_audio": False,
            "findings": findings,
            "assemble_hints": {
                "music_path": str(bgm),
                "music_segments": [],
                "mix_source_audio": False,
            },
        }
        return ToolResult(success=True, data=data)

    def assemble(inputs):
        op = str(inputs.get("operation") or "assemble")
        if op != "assemble":
            raise AssertionError(f"finish 全 skip 时不应调用 ffmpeg_compose operation={op}")
        order.append("assemble")
        if fail_at == "assemble":
            return ToolResult(success=False, error="assemble boom")
        out = Path(inputs["output_path"])
        out.parent.mkdir(parents=True, exist_ok=True)
        joined = out.with_suffix(".joined.mp4")
        joined.write_bytes(b"joined")
        joined.with_suffix(".concat.txt").write_text("file x\n", encoding="utf-8")
        out.write_bytes(b"final")
        return ToolResult(success=True, data={"output": str(out)})

    def export(inputs):
        order.append("export")
        if fail_at == "export":
            return ToolResult(success=False, error="export boom")
        dest = Path(inputs["output_dir"])
        dest.mkdir(parents=True, exist_ok=True)
        zpath = dest / "film.zip"
        zpath.write_bytes(b"zip")
        return ToolResult(success=True, data={"output": str(zpath)})

    def release(inputs):
        order.append("release")
        root = Path(inputs["project_dir"])
        ArtifactStore(root).write("publish_log", {"status": "exported"})
        ArtifactStore(root).write("release_pack", {"cover": "", "blurbs": {}})
        log_path = str(root / "artifacts" / "publish_log.json")
        return ToolResult(success=True, data={"publish_log_path": log_path, "cover": "", "blurbs": {}})

    tools = {
        "soundtrack_planner": FakeTool("soundtrack_planner", soundtrack),
        "compose_planner": FakeTool("compose_planner", compose),
        "place_audio": FakeTool("place_audio", place),
        "ffmpeg_compose": FakeTool("ffmpeg_compose", assemble),
        "export_bundle": FakeTool("export_bundle", export),
        "release_pack": FakeTool("release_pack", release),
    }
    return tools, order, bgm


def _run(proj, tools, **kwargs):
    return run_produce(
        proj,
        tools=tools,
        run_tool_fn=lambda tool, inputs: tool.execute(inputs),
        **kwargs,
    )


def test_order_soundtrack_before_compose(tmp_path):
    proj = _seed_project(tmp_path)
    tools, order, bgm = _bag(proj)
    result = _run(proj, tools)
    assert result["success"]
    assert result["progress"]["next"]["argv"] == []
    assert order[:3] == ["soundtrack", "compose_plan", "place_audio"]
    assert "assemble" in order and "export" in order
    assert tools["ffmpeg_compose"].calls[0]["music_path"] == str(bgm)
    assert tools["compose_planner"].calls[0].get("overwrite") is True
    assert not (proj / "scratch").exists()
    assert not list(proj.glob("renders/*.joined.mp4"))
    assert (proj / "renders" / "final.mp4").is_file()
    assert (proj / "exports" / "film.zip").is_file()
    assert "成片" in (proj / "REVIEW.md").read_text(encoding="utf-8")


def test_season_concat_ignored_on_flat(tmp_path):
    proj = _seed_project(tmp_path)
    tools, order, _bgm = _bag(proj)
    result = _run(proj, tools, season_concat=True)
    assert result["success"], result.get("error")
    assert result["progress"]["status"] == "ok"
    assert (proj / "renders" / "final.mp4").is_file()
    assert not (proj / "renders" / "season.mp4").is_file()
    assert order[0] == "soundtrack"


def test_finish_skip_release_writes_log(tmp_path):
    from montage.engine.produce import STEP_IDS

    proj = _seed_project(tmp_path)
    tools, order, _bgm = _bag(proj)
    result = _run(proj, tools)
    assert result["success"]
    assert STEP_IDS[-3:] == ("finish", "release", "export")
    steps = result["progress"]["steps"]
    assert steps["finish"]["status"] == "skip"
    assert steps["release"]["status"] == "ok"
    assert "release" in order
    assert len(tools["ffmpeg_compose"].calls) == 1
    assert tools["ffmpeg_compose"].calls[0]["operation"] == "assemble"
    assert (proj / "artifacts" / "publish_log.json").is_file()


def test_clip_factory_with_clips_runs_soundtrack_and_zip(tmp_path):
    proj = _seed_project(tmp_path, pipeline="clip_factory")
    tools, order, _bgm = _bag(proj)
    result = _run(proj, tools)
    assert result["success"], result["error"]
    assert "soundtrack" in order
    assert "assemble" in order and "export" in order
    assert "shot_dry_run" not in order
    assert (proj / "exports" / "film.zip").is_file()


def test_missing_clip_does_not_call_soundtrack(tmp_path):
    proj = _seed_project(tmp_path)
    (proj / "assets" / "videos" / "a.mp4").unlink()
    tools, order, _ = _bag(proj)
    result = _run(proj, tools)
    assert not result["success"]
    assert order == []
    err = result["error"] or ""
    assert "shot_runner" in err or "clip" in err.lower() or "不存在" in err


def test_missing_scene_plan(tmp_path):
    proj = _seed_project(tmp_path)
    (proj / "artifacts" / "scene_plan.json").unlink()
    tools, order, _ = _bag(proj)
    result = _run(proj, tools)
    assert not result["success"]
    assert order == []


def test_first_step_fail_stops(tmp_path):
    proj = _seed_project(tmp_path)
    tools, order, _ = _bag(proj, fail_at="soundtrack")
    result = _run(proj, tools)
    assert not result["success"]
    assert order == ["soundtrack"]
    assert "compose_plan" not in order


def test_resume_skips_ok_steps(tmp_path):
    proj = _seed_project(tmp_path)
    tools, order, _ = _bag(proj)
    assert _run(proj, tools)["success"]
    tools2, order2, _ = _bag(proj)
    result = _run(proj, tools2, resume=True)
    assert result["success"]
    assert order2 == []


def test_rerun_without_resume_overwrites(tmp_path):
    proj = _seed_project(tmp_path)
    tools, _, _ = _bag(proj)
    assert _run(proj, tools)["success"]
    tools2, order2, _ = _bag(proj)
    result = _run(proj, tools2, resume=False)
    assert result["success"]
    assert order2[0] == "soundtrack"
    assert tools2["compose_planner"].calls[0].get("overwrite") is True


def test_lock_other_pid_refused(tmp_path):
    proj = _seed_project(tmp_path)
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        lock = proj / "artifacts" / "produce.lock"
        lock.write_text(json.dumps({"pid": holder.pid}), encoding="utf-8")
        tools, order, _ = _bag(proj)
        result = _run(proj, tools)
        assert not result["success"]
        assert order == []
        assert "pid" in (result["error"] or "")
    finally:
        holder.kill()
        holder.wait()


def test_keep_scratch_keeps_intermediates(tmp_path):
    proj = _seed_project(tmp_path)
    tools, _, _ = _bag(proj)
    result = _run(proj, tools, keep_scratch=True)
    assert result["success"]
    assert (proj / "scratch").is_dir()
    names = {p.name for p in (proj / "scratch").iterdir()}
    assert any("joined" in n or "concat" in n for n in names)
    assert not list(proj.glob("renders/*.joined.mp4"))


def test_overlay_fail_promoted(tmp_path):
    proj = _seed_project(tmp_path)
    tools, order, _ = _bag(proj, overlay_fail=True)
    result = _run(proj, tools)
    assert not result["success"]
    assert "叠音失败" in (result["error"] or "")
    assert "assemble" not in order


def test_bgm_only_still_calls_place_audio(tmp_path):
    proj = _seed_project(tmp_path)
    tools, order, bgm = _bag(proj)
    result = _run(proj, tools)
    assert result["success"]
    assert "place_audio" in order
    assert tools["ffmpeg_compose"].calls[0].get("music_path") == str(bgm)


def test_relative_clip_path(tmp_path):
    proj = _seed_project(tmp_path, rel_clip=True)
    tools, _, _ = _bag(proj)
    assert _run(proj, tools)["success"]


def test_still_image_realize(tmp_path):
    proj = _seed_project(tmp_path, png=True)
    tools, order, _ = _bag(proj)
    result = _run(proj, tools)
    assert result["success"]
    assert "realize" in order
    path = tools["ffmpeg_compose"].calls[0]["edit_decisions_path"]
    decisions = json.loads(Path(path).read_text(encoding="utf-8"))
    assert decisions["cuts"][0]["clip_path"].endswith(".mp4")


def test_empty_events_skips_place_audio(tmp_path):
    proj = _seed_project(tmp_path)
    tools, order, _ = _bag(proj, events=[])
    result = _run(proj, tools)
    assert result["success"]
    assert "place_audio" not in order
    assert "assemble" in order


def test_export_dir_inside_project(tmp_path):
    proj = _seed_project(tmp_path)
    tools, _, _ = _bag(proj)
    _run(proj, tools)
    assert Path(tools["export_bundle"].calls[0]["output_dir"]) == proj / "exports"


def test_produce_not_in_registry():
    reg = ToolRegistry()
    reg.discover()
    assert reg.get("produce") is None


def test_cli_produce_missing_project(tmp_path):
    from montage.cli import main

    code = main(["produce", str(tmp_path / "nope")])
    assert code == 2


def test_stale_lock_overwritten(tmp_path):
    proj = _seed_project(tmp_path)
    (proj / "artifacts" / "produce.lock").write_text(
        json.dumps({"pid": 999999}), encoding="utf-8",
    )
    tools, _, _ = _bag(proj)
    assert _run(proj, tools)["success"]
    assert not (proj / "artifacts" / "produce.lock").exists()


def test_corrupt_progress_fails(tmp_path):
    proj = _seed_project(tmp_path)
    (proj / "artifacts").mkdir(parents=True, exist_ok=True)
    (proj / "artifacts" / "produce_progress.json").write_text("{not-json", encoding="utf-8")
    tools, order, _ = _bag(proj)
    result = _run(proj, tools)
    assert not result["success"]
    assert "损坏" in (result["error"] or "")
    assert order == []
