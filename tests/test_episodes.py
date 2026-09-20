"""W3 波次 2：episodes.json 物化、系列调度、WebUI 只读子集。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from montage.engine.artifacts import ArtifactStore
from montage.engine.episodes import (
    copy_sibling_still_refs,
    is_series_root,
    load_episodes_index,
    materialize_episodes,
)
from montage.engine.produce import load_progress, run_produce
from montage.engine.project import init_project
from montage.schemas import get_schema
from montage.toolbase import ToolResult
from montage.tools.compose_planner import clip_path_for_shot, is_still_image
from montage.tools.shot_runner import ShotRunner
from montage.tools.voice_director import shots_with_timeline
from montage.webui.state import collect_projects, project_detail

from test_bible import _ok_chars, _ok_shot
from test_produce import FakeTool
from test_shot_runner import _fake_image, _fake_video, _pass_quality

_REPO = Path(__file__).resolve().parents[1]
_BGM_INDEX = _REPO / "assets" / "bgm" / "INDEX.md"


def _scene(sid: str, speaker: str, klass: str) -> dict:
    shot = _ok_shot(
        shot_id=f"{sid}_01",
        shot_budget_class=klass,
        subjects=[{
            "id": speaker,
            "action": {"verb": "抓住衣领推向墙", "body_part": "右手", "contact": "衣领"},
        }],
    )
    return {
        "id": sid,
        "environment": {
            "location": "沿海旧巷",
            "lighting": "霓虹积水",
            "atmosphere": "雨夜",
        },
        "duration_seconds": 5,
        "lines": [{"speaker_id": speaker, "text": f"{sid} 雨还在下。"}],
        "shots": [shot],
    }


def _three_by_four_bible() -> dict:
    """3 集 × 4 镜，每集显式 1 hero + 3 talk、等长。"""
    groups = [
        (["sc01", "sc02", "sc03", "sc04"], "a"),
        (["sc05", "sc06", "sc07", "sc08"], "a"),
        (["sc09", "sc10", "sc11", "sc12"], "b"),
    ]
    scenes = []
    for ids, speaker in groups:
        for i, sid in enumerate(ids):
            scenes.append(_scene(sid, speaker, "hero" if i == 0 else "talk"))
    return {
        "logline": "雨夜巷口三人轮番对峙",
        "medium": "film",
        "genres": ["thriller"],
        "playbook": "cyberpunk_neon",
        "title": "三集演示",
        "characters": _ok_chars(),
        "props": [
            {"id": "lighter", "appearance": "铜壳打火机"},
            {"id": "gun", "appearance": "短管左轮"},
            {"id": "photo", "appearance": "湿透的旧照片"},
        ],
        "scenes": scenes,
    }


def _episodes_index() -> dict:
    return {
        "episodes": [
            {
                "episode_id": "ep01",
                "scene_ids": ["sc01", "sc02", "sc03", "sc04"],
                "character_ids": ["a"],
            },
            {
                "episode_id": "ep02",
                "scene_ids": ["sc05", "sc06", "sc07", "sc08"],
                "character_ids": ["a", "b"],
            },
            {
                "episode_id": "ep03",
                "scene_ids": ["sc09", "sc10", "sc11", "sc12"],
                "character_ids": ["b"],
            },
        ]
    }


def _seed_series(root: Path):
    series = init_project(root, "show", "三集演示", "cinematic")
    store = ArtifactStore(series)
    store.write("series_bible", _three_by_four_bible())
    store.write("episodes", _episodes_index())
    return series


def _classes_of(plan: dict) -> list[str]:
    out = []
    for sc in plan.get("scenes") or []:
        for sh in sc.get("shots") or []:
            out.append(str(sh.get("shot_budget_class") or ""))
    return out


def _series_bag():
    order: list[str] = []
    payloads: list[dict] = []

    def soundtrack(inputs):
        order.append("soundtrack")
        proj = Path(inputs["project_dir"])
        bgm = proj / "assets" / "music" / "theme.wav"
        bgm.parent.mkdir(parents=True, exist_ok=True)
        bgm.write_bytes(b"RIFF")
        data = {
            "events": [{
                "kind": "bgm",
                "asset_id": "bgm/theme",
                "path": str(bgm),
                "available": True,
            }],
            "attributions": [],
            "findings": [],
        }
        ArtifactStore(proj).write("soundtrack", data)
        return ToolResult(success=True, data=data)

    def compose(inputs):
        proj = Path(inputs["project_dir"])
        store = ArtifactStore(proj)
        if inputs.get("realize"):
            order.append("realize")
            decisions = store.read("edit_decisions") or {"cuts": []}
            dest_dir = proj / "assets" / "kenburns"
            dest_dir.mkdir(parents=True, exist_ok=True)
            cuts = []
            for cut in decisions.get("cuts") or []:
                row = dict(cut)
                raw = str(row.get("clip_path") or "")
                if raw and is_still_image(raw):
                    dest = dest_dir / f"{row.get('shot_id') or 'shot'}_kb.mp4"
                    from conftest import write_tiny_video

                    write_tiny_video(dest)
                    row["clip_path"] = str(dest)
                cuts.append(row)
            decisions["cuts"] = cuts
            store.write("edit_decisions", decisions)
            return ToolResult(success=True, data={"edit_decisions": decisions})
        order.append("compose_plan")
        plan_doc = store.read("scene_plan") or {}
        manifest = store.read("asset_manifest") or {}
        cuts = []
        shots = []
        for shot in shots_with_timeline(plan_doc):
            sid = str(shot.get("shot_id") or "")
            scene_id = str(shot.get("scene_id") or "")
            raw = clip_path_for_shot(sid, scene_id, manifest)
            cuts.append({"shot_id": sid, "clip_path": raw, "transition": "cut"})
            shots.append({"shot_id": sid, "clip_path": raw, "effects": []})
        plan = {"shots": shots}
        decisions = {"render_runtime": "ffmpeg", "cuts": cuts}
        store.write("compose_plan", plan)
        store.write("edit_decisions", decisions)
        return ToolResult(success=True, data={"compose_plan": plan, "edit_decisions": decisions})

    def place(inputs):
        order.append("place_audio")
        proj = Path(inputs["project_dir"])
        bgm = proj / "assets" / "music" / "theme.wav"
        data = {
            "music_path": str(bgm),
            "music_segments": [],
            "mix_source_audio": False,
            "findings": [],
            "assemble_hints": {
                "music_path": str(bgm),
                "music_segments": [],
                "mix_source_audio": False,
            },
        }
        return ToolResult(success=True, data=data)

    def assemble(inputs):
        op = str(inputs.get("operation") or "assemble")
        if op == "assemble":
            order.append("assemble")
            out = Path(inputs["output_path"])
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"final")
            return ToolResult(success=True, data={"output": str(out)})
        if op == "apply_profile":
            assert "project_dir" not in inputs
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

    def export(inputs):
        order.append("export")
        dest = Path(inputs["output_dir"])
        dest.mkdir(parents=True, exist_ok=True)
        zpath = dest / "film.zip"
        zpath.write_bytes(b"zip")
        return ToolResult(success=True, data={"output": str(zpath)})

    def release(inputs):
        proj = Path(inputs["project_dir"])
        ArtifactStore(proj).write("publish_log", {"status": "exported"})
        ArtifactStore(proj).write("release_pack", {"cover": "", "blurbs": {}})
        return ToolResult(
            success=True,
            data={"publish_log_path": str(proj / "artifacts" / "publish_log.json")},
        )

    inner = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )

    class Counted(ShotRunner):
        def execute(self, inputs):
            payloads.append(dict(inputs))
            order.append("shot_dry_run" if inputs.get("dry_run") else "shot_generate")
            return inner.execute(inputs)

    counted = Counted(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    tools = {
        "soundtrack_planner": FakeTool("soundtrack_planner", soundtrack),
        "compose_planner": FakeTool("compose_planner", compose),
        "place_audio": FakeTool("place_audio", place),
        "ffmpeg_compose": FakeTool("ffmpeg_compose", assemble),
        "export_bundle": FakeTool("export_bundle", export),
        "release_pack": FakeTool("release_pack", release),
        "shot_runner": counted,
        "voice_director": FakeTool("voice_director", lambda _i: ToolResult(success=True, data={"assignments": []})),
    }
    return tools, order, payloads


def _run(series, tools, **kwargs):
    return run_produce(
        series,
        tools=tools,
        run_tool_fn=lambda tool, inputs: tool.execute(inputs),
        skip_export=True,
        **kwargs,
    )


def test_episodes_schema_registered():
    schema = get_schema("episodes")
    assert schema is not None
    assert "episodes" in schema["properties"]


def test_is_series_root_needs_two_and_no_parent(tmp_path):
    series = _seed_series(tmp_path)
    assert is_series_root(series)
    assert len(load_episodes_index(series)) == 3
    one = init_project(tmp_path, "one", "单", "cinematic")
    ArtifactStore(one).write("episodes", {"episodes": [{"episode_id": "ep01", "scene_ids": ["sc01"]}]})
    assert not is_series_root(one)


def test_materialize_three_by_four(tmp_path):
    series = _seed_series(tmp_path)
    out = materialize_episodes(series)
    assert len(out["runnable"]) == 3
    for eid in ("ep01", "ep02", "ep03"):
        ep = series / "episodes" / eid
        assert (ep / "project.json").is_file()
        assert (ep / "artifacts" / "scene_plan.json").is_file()
        meta = json.loads((ep / "project.json").read_text(encoding="utf-8"))
        assert meta["parent_id"] == "show"
        assert meta["episode_id"] == eid
        plan = ArtifactStore(ep).read("scene_plan")
        classes = _classes_of(plan)
        assert classes.count("hero") == 1
        assert classes.count("talk") == 3
        assert "hero" in classes and "establishing" not in classes
    script = ArtifactStore(series / "episodes" / "ep01").read("script")
    char_ids = [c.get("id") for c in (script.get("characters") or [])]
    assert char_ids == ["a"]
    script3 = ArtifactStore(series / "episodes" / "ep03").read("script")
    assert [c.get("id") for c in (script3.get("characters") or [])] == ["b"]
    assert not (series / "artifacts" / "series_bible.json").read_text(encoding="utf-8") == ""
    assert not (series / "episodes" / "ep01" / "artifacts" / "series_bible.json").is_file()
    assert not is_series_root(series / "episodes" / "ep01")


def test_empty_scene_ids_skips_compile(tmp_path):
    series = init_project(tmp_path, "empty", "空场", "cinematic")
    ArtifactStore(series).write("series_bible", _three_by_four_bible())
    ArtifactStore(series).write("episodes", {
        "episodes": [
            {"episode_id": "ep01", "scene_ids": ["sc01", "sc02", "sc03", "sc04"], "character_ids": ["a"]},
            {"episode_id": "ep02", "scene_ids": [], "character_ids": ["a"]},
        ]
    })
    out = materialize_episodes(series)
    assert any("scene_ids 为空" in str(f.get("message") or "") for f in out["findings"])
    assert (series / "episodes" / "ep02" / "project.json").is_file()
    assert not (series / "episodes" / "ep02" / "artifacts" / "scene_plan.json").is_file()
    plan1 = ArtifactStore(series / "episodes" / "ep01").read("scene_plan")
    ids = {sc.get("id") for sc in (plan1.get("scenes") or [])}
    assert ids <= {"sc01", "sc02", "sc03", "sc04"}
    assert "sc09" not in ids


def test_overlap_warns_but_materializes(tmp_path):
    series = init_project(tmp_path, "ov", "重叠", "cinematic")
    ArtifactStore(series).write("series_bible", _three_by_four_bible())
    ArtifactStore(series).write("episodes", {
        "episodes": [
            {"episode_id": "ep01", "scene_ids": ["sc01"], "character_ids": ["a"]},
            {"episode_id": "ep02", "scene_ids": ["sc01"], "character_ids": ["a"]},
        ]
    })
    out = materialize_episodes(series)
    assert any("重叠" in str(f.get("message") or "") for f in out["findings"])
    assert (series / "episodes" / "ep01" / "artifacts" / "scene_plan.json").is_file()
    assert (series / "episodes" / "ep02" / "artifacts" / "scene_plan.json").is_file()


def test_produce_sample_then_await_episode(tmp_path):
    series = _seed_series(tmp_path)
    tools, order, payloads = _series_bag()
    result = _run(series, tools)
    assert result["success"], result.get("error")
    assert result["progress"]["status"] == "await_sample"
    assert result["progress"]["episode_id"] == "ep01"
    assert result["progress"].get("mode") == "series"
    assert "shot_dry_run" not in (result["progress"].get("steps") or {})
    dirs = [str(p.get("project_dir") or "").replace("\\", "/") for p in payloads]
    assert dirs
    assert all("/episodes/" in d for d in dirs)
    assert not (series / "renders" / "final.mp4").is_file()

    tools2, _, _ = _series_bag()
    result2 = _run(series, tools2, resume=True)
    assert result2["success"], result2.get("error")
    assert result2["progress"]["status"] == "await_episode"
    assert result2["progress"]["episode_id"] == "ep01"
    ep01 = load_progress(series / "episodes" / "ep01")
    assert ep01.get("status") == "ok"
    assert (series / "episodes" / "ep01" / "renders" / "final.mp4").is_file()
    assert not (series / "renders" / "final.mp4").is_file()

    tools3, _, _ = _series_bag()
    result3 = _run(series, tools3, resume=True)
    assert result3["success"], result3.get("error")
    assert result3["progress"]["status"] == "await_sample"
    assert result3["progress"]["episode_id"] == "ep02"
    assert load_progress(series / "episodes" / "ep01").get("status") == "ok"


def test_review_none_runs_all_then_ok(tmp_path):
    series = _seed_series(tmp_path)
    tools, _, _ = _series_bag()
    result = _run(series, tools, review="none")
    assert result["success"], result.get("error")
    assert result["progress"]["status"] == "ok"
    for eid in ("ep01", "ep02", "ep03"):
        assert load_progress(series / "episodes" / eid).get("status") == "ok"
        assert (series / "episodes" / eid / "renders" / "final.mp4").is_file()
    assert not (series / "renders" / "final.mp4").is_file()
    names = [p.name for p in (series / "renders").glob("*.mp4")] if (series / "renders").exists() else []
    assert "ep01.mp4" not in names


def test_direct_episode_does_not_touch_siblings(tmp_path):
    series = _seed_series(tmp_path)
    materialize_episodes(series)
    ep01 = series / "episodes" / "ep01"
    ep02 = series / "episodes" / "ep02"
    ArtifactStore(ep01).dir.mkdir(parents=True, exist_ok=True)
    marker = ep01 / "artifacts" / "produce_progress.json"
    marker.write_text(json.dumps({"version": "1", "status": "await_sample", "steps": {}}), encoding="utf-8")
    tools, _, _ = _series_bag()
    result = _run(ep02, tools)
    assert result["progress"]["status"] == "await_sample"
    left = json.loads(marker.read_text(encoding="utf-8"))
    assert left["status"] == "await_sample"
    assert not (series / "artifacts" / "produce_progress.json").is_file()


def test_root_without_resume_does_not_wipe_children(tmp_path):
    series = _seed_series(tmp_path)
    tools, _, _ = _series_bag()
    _run(series, tools)
    ep02 = series / "episodes" / "ep02"
    (ep02 / "artifacts").mkdir(parents=True, exist_ok=True)
    keep = ep02 / "artifacts" / "produce_progress.json"
    keep.write_text(json.dumps({"version": "1", "status": "ok", "steps": {"soundtrack": {"status": "ok"}}}), encoding="utf-8")
    sentinel = ep02 / "renders" / "keep.mp4"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_bytes(b"keep")
    tools2, _, _ = _series_bag()
    _run(series, tools2, resume=False)
    assert json.loads(keep.read_text(encoding="utf-8"))["status"] == "ok"
    assert sentinel.is_file()


def test_idea_does_not_materialize(tmp_path):
    series = _seed_series(tmp_path)
    from montage.tools.idea_developer import IdeaDeveloper
    tools, _, _ = _series_bag()
    tools["idea_developer"] = IdeaDeveloper()
    result = run_produce(
        series,
        idea="讲量子计算",
        tools=tools,
        run_tool_fn=lambda tool, inputs: tool.execute(inputs),
    )
    assert result["progress"].get("mode") == "idea" or result["progress"].get("status") in ("await_bible", "compiled", "need_bible", "fail")
    assert not (series / "episodes").exists()


def test_collect_projects_lists_series_and_episodes(tmp_path):
    series = _seed_series(tmp_path)
    materialize_episodes(series)
    cards = collect_projects(tmp_path)
    kinds = {c["kind"] for c in cards}
    assert "series" in kinds
    episodes = [c for c in cards if c.get("kind") == "episode"]
    assert len(episodes) == 3
    ids = {(c["parent_id"], c["episode_id"]) for c in episodes}
    assert ("show", "ep01") in ids
    assert all(c.get("detail_path", "").endswith(f"/episodes/{c['episode_id']}") for c in episodes)
    assert all("::" in str(c.get("project_id") or "") for c in episodes)
    detail = project_detail(tmp_path, "show", episode_id="ep01")
    assert detail is not None
    assert detail["readonly"] is False
    assert detail["kind"] == "episode"
    assert detail["parent_id"] == "show"
    flat = init_project(tmp_path, "film", "单集", "cinematic")
    cards2 = collect_projects(tmp_path)
    film = next(c for c in cards2 if c.get("project_id") == "film")
    assert film["kind"] == "project"
    assert not (flat / "episodes").exists()


def test_cli_review_each_episode():
    from montage.cli import build_parser

    ns = build_parser().parse_args(["produce", "proj", "--review", "each_episode"])
    assert ns.review == "each_episode"


def test_cli_season_concat_flag():
    from montage.cli import build_parser

    ns = build_parser().parse_args(["produce", "proj", "--season-concat"])
    assert ns.season_concat is True
    ns2 = build_parser().parse_args(["produce", "proj"])
    assert ns2.season_concat is False


def test_season_concat_does_not_fail_await_sample(tmp_path):
    series = _seed_series(tmp_path)
    tools, _, _ = _series_bag()
    result = _run(series, tools, season_concat=True)
    assert result["success"], result.get("error")
    assert result["code"] == 0
    assert result["progress"]["status"] == "await_sample"
    assert not (series / "renders" / "season.mp4").is_file()
    assert not (series / "renders" / "final.mp4").is_file()


def test_review_none_without_flag_has_no_season(tmp_path):
    series = _seed_series(tmp_path)
    tools, _, _ = _series_bag()
    result = _run(series, tools, review="none")
    assert result["success"], result.get("error")
    assert result["progress"]["status"] == "ok"
    assert not (series / "renders" / "season.mp4").is_file()
    assert not (series / "renders" / "final.mp4").is_file()


def test_season_concat_after_all_ok(tmp_path):
    series = _seed_series(tmp_path)
    tools, _, _ = _series_bag()
    first = _run(series, tools, review="none")
    assert first["progress"]["status"] == "ok"
    assert not (series / "renders" / "season.mp4").is_file()
    tools2, _, _ = _series_bag()
    second = _run(series, tools2, resume=True, season_concat=True)
    assert second["success"], second.get("error")
    assert second["progress"]["status"] == "ok"
    assert (series / "renders" / "season.mp4").is_file()
    assert not (series / "renders" / "final.mp4").is_file()
    assert (series / "episodes" / "ep01" / "renders" / "final.mp4").is_file()
    concat_outs = [
        str(c.get("output_path") or "").replace("\\", "/")
        for c in tools2["ffmpeg_compose"].calls
        if c.get("operation") == "concat"
    ]
    assert any(p.endswith("/renders/season.mp4") or p.endswith("/scratch/season.mp4") for p in concat_outs)
    assert tools2["export_bundle"].calls == []


def test_season_concat_with_review_none(tmp_path):
    series = _seed_series(tmp_path)
    tools, _, _ = _series_bag()
    result = _run(series, tools, review="none", season_concat=True)
    assert result["success"], result.get("error")
    assert (series / "renders" / "season.mp4").is_file()
    assert not (series / "renders" / "final.mp4").is_file()
    assert not (series / "episodes" / "ep01" / "renders" / "season.mp4").is_file()


def test_season_concat_fails_if_final_missing(tmp_path):
    series = _seed_series(tmp_path)
    tools, _, _ = _series_bag()
    _run(series, tools, review="none")
    missing = series / "episodes" / "ep02" / "renders" / "final.mp4"
    missing.unlink()
    prior = series / "renders" / "season.mp4"
    prior.parent.mkdir(parents=True, exist_ok=True)
    prior.write_bytes(b"keep-me")
    tools2, _, _ = _series_bag()
    result = _run(series, tools2, resume=True, season_concat=True)
    assert result["success"] is False
    assert result["code"] == 2
    assert "season-concat" in (result["error"] or "")
    assert "ep02" in (result["error"] or "")
    assert prior.read_bytes() == b"keep-me"
    argv = result["progress"]["next"]["argv"]
    assert "--season-concat" in argv
    assert "--resume" in argv
    assert "--review" not in argv


def test_headless_season_concat_still_blocks_unfinished(tmp_path):
    series = _seed_series(tmp_path)
    tools, _order, payloads = _series_bag()
    result = _run(series, tools, headless=True, season_concat=True)
    assert result["success"] is False
    assert "MONTAGE_HEADLESS" in (result["error"] or "")
    assert payloads == []
    assert not (series / "renders" / "season.mp4").is_file()


def test_headless_season_concat_when_all_ok(tmp_path):
    series = _seed_series(tmp_path)
    tools, _, _ = _series_bag()
    _run(series, tools, review="none")
    tools2, _, _ = _series_bag()
    result = _run(series, tools2, resume=True, headless=True, season_concat=True)
    assert result["success"], result.get("error")
    assert (series / "renders" / "season.mp4").is_file()


def test_index_md_unchanged_after_mock_produce(tmp_path):
    before = _BGM_INDEX.read_text(encoding="utf-8") if _BGM_INDEX.is_file() else ""
    series = _seed_series(tmp_path)
    tools, _, _ = _series_bag()
    _run(series, tools, review="none")
    after = _BGM_INDEX.read_text(encoding="utf-8") if _BGM_INDEX.is_file() else ""
    assert after == before


def _put_still(
    ep_dir: Path,
    *,
    kind: str,
    asset_id: str,
    data: bytes | None = None,
    url: str = "",
) -> Path | None:
    dest = None
    ref: dict = {"id": f"{kind}_{asset_id}", "kind": kind}
    if kind == "portrait":
        ref["character_id"] = asset_id
    else:
        ref["prop_id"] = asset_id
    row: dict = {"id": ref["id"], "kind": "image"}
    if data is not None:
        dest = ep_dir / "assets" / "images" / f"{kind}_{asset_id}.png"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        rel = dest.relative_to(ep_dir).as_posix()
        ref["path"] = rel
        row["path"] = rel
    if url:
        ref["url"] = url
        row["url"] = url
    store = ArtifactStore(ep_dir)
    man = store.read("asset_manifest") or {"items": [], "reference_assets": []}
    man.setdefault("items", [])
    man.setdefault("reference_assets", [])
    man["reference_assets"] = [
        r for r in man["reference_assets"]
        if not (
            isinstance(r, dict)
            and r.get("kind") == kind
            and str(r.get("character_id") if kind == "portrait" else r.get("prop_id") or "") == asset_id
        )
    ]
    man["items"] = [i for i in man["items"] if not (isinstance(i, dict) and i.get("id") == ref["id"])]
    man["reference_assets"].append(ref)
    man["items"].append(row)
    store.write("asset_manifest", man)
    return dest


def test_copy_sibling_portrait_file_into_ep02(tmp_path):
    series = _seed_series(tmp_path)
    materialize_episodes(series)
    ep01 = series / "episodes" / "ep01"
    ep02 = series / "episodes" / "ep02"
    src = _put_still(ep01, kind="portrait", asset_id="a", data=b"orig-bytes")
    copied = copy_sibling_still_refs(
        ep02,
        portrait_ids=["a"],
        retry_ids=set(),
        manifest={"items": [], "reference_assets": []},
    )
    dest = ep02 / "assets" / "images" / "portrait_a.png"
    assert dest.is_file()
    assert not dest.is_symlink()
    assert dest.read_bytes() == b"orig-bytes"
    assert dest.resolve() != src.resolve()
    ref = copied["reference_assets"][0]
    assert ref["path"] == "assets/images/portrait_a.png"
    assert ".." not in ref["path"]
    assert str(ep01) not in ref["path"]
    src.write_bytes(b"changed")
    assert dest.read_bytes() == b"orig-bytes"
    saved = ArtifactStore(ep02).read("asset_manifest")
    assert saved["reference_assets"][0]["path"] == "assets/images/portrait_a.png"


def test_copy_sibling_url_only_no_download(tmp_path):
    series = _seed_series(tmp_path)
    materialize_episodes(series)
    ep01 = series / "episodes" / "ep01"
    ep02 = series / "episodes" / "ep02"
    _put_still(ep01, kind="portrait", asset_id="a", url="https://example.test/portrait_a.png")
    copied = copy_sibling_still_refs(
        ep02,
        portrait_ids=["a"],
        retry_ids=set(),
        manifest={"items": [], "reference_assets": []},
    )
    assert not (ep02 / "assets" / "images" / "portrait_a.png").exists()
    ref = copied["reference_assets"][0]
    assert ref.get("url") == "https://example.test/portrait_a.png"
    assert not ref.get("path")


def test_copy_sibling_skips_retry_and_flat(tmp_path):
    series = _seed_series(tmp_path)
    materialize_episodes(series)
    ep01 = series / "episodes" / "ep01"
    ep02 = series / "episodes" / "ep02"
    _put_still(ep01, kind="portrait", asset_id="a", data=b"orig")
    skipped = copy_sibling_still_refs(
        ep02,
        portrait_ids=["a"],
        retry_ids={"portrait/a"},
        manifest={"items": [], "reference_assets": []},
    )
    assert skipped["reference_assets"] == []
    assert not (ep02 / "assets" / "images" / "portrait_a.png").exists()

    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "project.json").write_text("{}", encoding="utf-8")
    noop = copy_sibling_still_refs(
        flat,
        portrait_ids=["a"],
        manifest={"items": [], "reference_assets": []},
    )
    assert noop["reference_assets"] == []


def test_copy_sibling_ignores_non_series_parent_parent(tmp_path):
    fake = tmp_path / "other" / "episodes" / "ep02"
    fake.mkdir(parents=True)
    (fake / "project.json").write_text(
        json.dumps({"episode_id": "ep02", "parent_id": "other"}),
        encoding="utf-8",
    )
    sib = tmp_path / "other" / "episodes" / "ep01"
    sib.mkdir(parents=True)
    _put_still(sib, kind="portrait", asset_id="a", data=b"nope")
    out = copy_sibling_still_refs(
        fake,
        portrait_ids=["a"],
        manifest={"items": [], "reference_assets": []},
    )
    assert out["reference_assets"] == []
    assert not (fake / "assets" / "images" / "portrait_a.png").exists()


def test_ep02_dry_run_reuses_ep01_portrait(tmp_path):
    series = _seed_series(tmp_path)
    materialize_episodes(series)
    ep01 = series / "episodes" / "ep01"
    ep02 = series / "episodes" / "ep02"
    src = _put_still(ep01, kind="portrait", asset_id="a", data=b"orig-bytes")
    tool = ShotRunner(image_estimate=lambda i: 0.04, video_estimate=lambda i: 0.2)
    result = tool.execute({
        "project_dir": str(ep02),
        "dry_run": True,
        "record_ledger": False,
    })
    assert result.success, result.error
    kinds = [j["kind"] for j in result.data["jobs"]]
    assert "portrait" not in kinds
    dest = ep02 / "assets" / "images" / "portrait_a.png"
    assert dest.is_file()
    assert dest.read_bytes() == b"orig-bytes"
    src.write_bytes(b"changed")
    assert dest.read_bytes() == b"orig-bytes"


def test_ep02_dry_run_reuses_url_portrait(tmp_path):
    series = _seed_series(tmp_path)
    materialize_episodes(series)
    ep01 = series / "episodes" / "ep01"
    ep02 = series / "episodes" / "ep02"
    _put_still(ep01, kind="portrait", asset_id="a", url="https://example.test/a.png")
    tool = ShotRunner(image_estimate=lambda i: 0.04, video_estimate=lambda i: 0.2)
    result = tool.execute({
        "project_dir": str(ep02),
        "dry_run": True,
        "record_ledger": False,
    })
    assert result.success, result.error
    assert "portrait" not in [j["kind"] for j in result.data["jobs"]]
    man = ArtifactStore(ep02).read("asset_manifest")
    portraits = [r for r in man["reference_assets"] if r.get("kind") == "portrait"]
    assert portraits[0]["url"].startswith("http")
    assert not (ep02 / "assets" / "images" / "portrait_a.png").exists()


def test_ep02_retry_portrait_still_queued(tmp_path):
    series = _seed_series(tmp_path)
    materialize_episodes(series)
    ep01 = series / "episodes" / "ep01"
    ep02 = series / "episodes" / "ep02"
    _put_still(ep01, kind="portrait", asset_id="a", data=b"orig")
    tool = ShotRunner(image_estimate=lambda i: 0.04, video_estimate=lambda i: 0.2)
    result = tool.execute({
        "project_dir": str(ep02),
        "dry_run": True,
        "record_ledger": False,
        "retry_ids": ["portrait/a"],
    })
    assert result.success, result.error
    portraits = [j for j in result.data["jobs"] if j["kind"] == "portrait"]
    assert portraits and portraits[0]["character_id"] == "a"
    assert not (ep02 / "assets" / "images" / "portrait_a.png").exists()


def test_copy_sibling_prop_file(tmp_path):
    series = _seed_series(tmp_path)
    materialize_episodes(series)
    ep01 = series / "episodes" / "ep01"
    ep02 = series / "episodes" / "ep02"
    _put_still(ep01, kind="prop", asset_id="lighter", data=b"prop-bytes")
    copy_sibling_still_refs(
        ep02,
        prop_ids=["lighter"],
        retry_ids=set(),
        manifest={"items": [], "reference_assets": []},
    )
    dest = ep02 / "assets" / "images" / "prop_lighter.png"
    assert dest.is_file()
    assert dest.read_bytes() == b"prop-bytes"


# ---- v8.2 P0-6: 分层合成 short→segment→longform ----

def _chaptered_bible() -> dict:
    """3 集 × 4 场，章边界与集边界对齐（每集一章，章 BGM 各异）。"""
    bible = _three_by_four_bible()
    bible["chapters"] = [
        {"id": "ch01", "title": "第一章·雨夜", "start_scene": "sc01", "bgm_id": "rain_theme"},
        {"id": "ch02", "title": "第二章·追击", "start_scene": "sc05", "bgm_id": "chase_theme"},
        {"id": "ch03", "title": "第三章·了结", "start_scene": "sc09", "bgm_id": "calm_theme"},
    ]
    return bible


def test_chapter_layers_from_scene_plan():
    from montage.engine.story_outline import chapter_layers

    plan = {
        "chapters": [
            {"id": "ch1", "bgm_id": "b1"},
            {"id": "ch2", "bgm_id": ""},
        ],
        "scenes": [
            {"id": "sc01", "chapter_id": "ch1"},
            {"id": "sc02", "chapter_id": "ch1"},
            {"id": "sc03", "chapter_id": "ch2"},
            {"id": "sc04"},  # 未归属章：不进任何段
        ],
    }
    layers = chapter_layers(plan)
    assert [l["chapter_id"] for l in layers] == ["ch1", "ch2"]
    assert layers[0]["scene_ids"] == ["sc01", "sc02"]
    assert layers[1]["scene_ids"] == ["sc03"]
    assert chapter_layers({"scenes": [{"id": "sc01"}]}) == []


def test_layer_bgm_ownership_rules():
    from montage.engine.story_outline import layer_bgm_ownership

    layers = [
        {"chapter_id": "ch1", "bgm_id": "b1", "scene_ids": ["sc01", "sc02"]},
        {"chapter_id": "ch2", "bgm_id": "", "scene_ids": ["sc03"]},
    ]
    rows = layer_bgm_ownership(layers, {"sc02": "sx"})
    assert rows[0] == {
        "chapter_id": "ch1", "bgm_id": "b1",
        "scene_bgm_ids": [{"scene_id": "sc02", "bgm_id": "sx"}], "has_music": True,
    }
    assert rows[1]["has_music"] is False
    assert rows[1]["scene_bgm_ids"] == []


def test_compile_bible_drops_foreign_chapters_in_episode():
    """子集物化：集边界外的章不进子集 scene_plan 快照（分层锚沿层传递）。"""
    series = _seed_series(tmp_path := __import__("pathlib").Path(
        __import__("tempfile").mkdtemp()
    ))
    store = ArtifactStore(series)
    bible = _chaptered_bible()
    # 加一章不属于任何子集的场（sc13 只在第 4 集，但 episodes 只有 3 集）
    bible["scenes"].append(_scene("sc13", "a", "talk"))
    bible["chapters"].append({"id": "ch04", "start_scene": "sc13", "bgm_id": "x"})
    store.write("series_bible", bible)
    store.write("episodes", {
        "episodes": [
            {"episode_id": "ep01", "scene_ids": ["sc01", "sc02", "sc03", "sc04"], "character_ids": ["a"]},
        ]
    })
    mat = materialize_episodes(series)
    assert mat["runnable"]
    plan = ArtifactStore(series / "episodes" / "ep01").read("scene_plan")
    ids = [str(c.get("id")) for c in (plan.get("chapters") or [])]
    assert ids == ["ch01"]  # 只带本集覆盖的章


def test_season_concat_segments_when_chaptered(tmp_path):
    """有 chapters 的系列：季拼按章段产出 segment 层，longform 由段拼。"""
    series = init_project(tmp_path, "show", "分层演示", "cinematic")
    store = ArtifactStore(series)
    store.write("series_bible", _chaptered_bible())
    store.write("episodes", {
        "episodes": [
            {"episode_id": "ep01", "scene_ids": ["sc01", "sc02", "sc03", "sc04"], "character_ids": ["a"]},
            {"episode_id": "ep02", "scene_ids": ["sc05", "sc06", "sc07", "sc08"], "character_ids": ["a", "b"]},
        ]
    })
    tools, _, _ = _series_bag()
    result = _run(series, tools, review="none")
    assert result["progress"]["status"] == "ok"
    tools2, _, _ = _series_bag()
    second = _run(series, tools2, resume=True, season_concat=True)
    assert second["success"], second.get("error")
    assert (series / "renders" / "season.mp4").is_file()
    # segment 层：每集一段（章边界=集边界），段文件 + 段清单都落盘
    seg1 = series / "renders" / "segments" / "ep01__ch01.mp4"
    seg2 = series / "renders" / "segments" / "ep02__ch02.mp4"
    assert seg1.is_file() and seg2.is_file()
    seg_plan = ArtifactStore(series).read("segment_plan")
    kinds = [s.get("kind") for s in seg_plan["segments"]]
    assert kinds == ["chapter", "chapter"]
    bgms = [s.get("bgm_id") for s in seg_plan["segments"]]
    assert bgms == ["rain_theme", "chase_theme"]
    # longform 的 concat 输入是段文件（不是集 final）
    concat_calls = [
        c for c in tools2["ffmpeg_compose"].calls
        if c.get("operation") == "concat"
    ]
    season_call = next(
        c for c in concat_calls
        if "season.mp4" in str(c.get("output_path") or "")
    )
    season_clips = [str(Path(p).name) for p in season_call.get("clips") or []]
    assert season_clips == ["ep01__ch01.mp4", "ep02__ch02.mp4"]


def test_season_concat_unchaptered_keeps_episode_finals(tmp_path):
    """无 chapters 的系列（旧路径）：集 finals 直接拼，不产 segment 层。"""
    series = _seed_series(tmp_path)
    tools, _, _ = _series_bag()
    _run(series, tools, review="none")
    tools2, _, _ = _series_bag()
    second = _run(series, tools2, resume=True, season_concat=True)
    assert second["success"], second.get("error")
    assert (series / "renders" / "season.mp4").is_file()
    assert not (series / "renders" / "segments").exists()
    concat_calls = [
        c for c in tools2["ffmpeg_compose"].calls
        if c.get("operation") == "concat"
    ]
    season_call = next(
        c for c in concat_calls
        if "season.mp4" in str(c.get("output_path") or "")
    )
    season_clips = [str(Path(p).name) for p in season_call.get("clips") or []]
    assert season_clips == ["final.mp4", "final.mp4", "final.mp4"]
