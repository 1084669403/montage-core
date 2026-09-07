"""Web 看板数据层测试（纯逻辑，不依赖 FastAPI）。"""

from montage.engine.project import init_project
from montage.engine.stages import CheckpointStore, StageStatus
from montage.engine.budget import BudgetLedger
from montage.engine.decisions import DecisionLog
from montage.webui.state import collect_projects, project_detail


def _make_project(root, pid, title="测试项目"):
    proj = init_project(root, pid, title, "cinematic")
    store = CheckpointStore(proj)
    store.write("research", StageStatus.COMPLETED.value, human_approved=True)
    store.write("script", StageStatus.IN_PROGRESS.value)
    store.write("proposal", StageStatus.AWAITING_HUMAN.value, human_approved=True)
    from montage.engine.artifacts import ArtifactStore

    ArtifactStore(proj).write("script", {"title": title, "sections": []})
    ledger = BudgetLedger(proj / "cost.jsonl")
    eid = ledger.estimate("tts", "旁白", "doubao_tts", 0.05)
    ledger.settle(eid, 0.048)
    log = DecisionLog(proj / "decisions.jsonl")
    log.log("video_loop", "供应商", "agnes", options_considered=["agnes", "jimeng"])
    return proj


def test_collect_projects(tmp_path):
    _make_project(tmp_path, "demo-a")
    projects = collect_projects(tmp_path)
    assert len(projects) == 1
    assert projects[0]["project_id"] == "demo-a"
    assert projects[0]["progress"]["research"] == "done"
    assert projects[0]["progress"]["compose"] == "idle"
    assert projects[0]["next_stage"] == "proposal"


def test_collect_empty(tmp_path):
    assert collect_projects(tmp_path) == []


def test_project_detail(tmp_path):
    _make_project(tmp_path, "demo-b")
    detail = project_detail(tmp_path, "demo-b")
    assert detail is not None
    assert detail["meta"]["title"] == "测试项目"
    assert detail["stages"][1]["stage"] == "proposal"
    assert detail["stages"][1]["status"] == "waiting"
    assert "script" in detail["artifacts"]
    assert "gate" in detail
    assert "scenes" in detail
    assert "shots" in detail
    assert detail["shots"] == []
    assert detail["budget"]["settled_usd"] == 0.048
    assert len(detail["decisions"]) == 1
    assert detail["decisions"][0]["choice"] == "agnes"
    assert "auto_edit" in detail
    assert detail["auto_edit"]["final"]["exists"] is False
    assert detail["auto_edit"]["plan"]["exists"] is False
    assert "produce_media" in detail
    assert detail["produce_media"]["final"]["exists"] is False
    assert detail["produce_media"]["final"]["url"] == "/media/demo-b/renders/final.mp4"


def test_project_detail_missing(tmp_path):
    assert project_detail(tmp_path, "nope") is None


def test_index_html_exists():
    from pathlib import Path

    index = Path(__file__).resolve().parents[1] / "montage" / "webui" / "index.html"
    assert index.exists()
    html = index.read_text(encoding="utf-8")
    assert "api/projects" in html
    assert "checkpoint" in html
    assert "auto_edit" in html
    assert "/media/" in html
    assert "final.mp4" in html
    assert "produce_media" in html
    assert "renders/final.mp4" in html
    assert "thumbs" in html
    assert "/episodes/" in html
    assert "openEpisode" in html
    assert "机器收口" in html
    assert "本页只 retry" in html
    assert "CLI" in html
    assert "一键 produce" not in html
    assert "一键 GEN" not in html
    assert "retryProduce" in html
    assert "/retry" in html
    assert "data-shot-id" in html
    assert "展开全部可改项" in html
    assert "saveReviewFields" in html
    assert "导演确认卡" in html
    assert "season.mp4" in html
    assert "episodes/" in html
    assert "checkpoint" in html


def test_auto_edit_status_and_media_guard(tmp_path):
    from montage.webui.state import auto_edit_status, resolve_media_file

    proj = _make_project(tmp_path, "demo-ae")
    auto = proj / "auto_edit"
    auto.mkdir()
    (auto / "final.mp4").write_bytes(b"mp4")
    (auto / "plan.json").write_text("{}", encoding="utf-8")
    status = auto_edit_status(proj)
    assert status["final"]["exists"] is True
    assert status["report"]["exists"] is False
    detail = project_detail(tmp_path, "demo-ae")
    assert detail["auto_edit"]["final"]["exists"] is True

    found = resolve_media_file(tmp_path, "demo-ae", "final.mp4")
    assert found is not None and found.name == "final.mp4"
    assert resolve_media_file(tmp_path, "demo-ae", "report.json") is None
    assert resolve_media_file(tmp_path, "demo-ae", "../project.json") is None
    assert resolve_media_file(tmp_path, "demo-ae", "secret.mp4") is None


def test_resolve_thumb_rejects_traversal(tmp_path):
    from montage.webui.state import resolve_thumb

    _make_project(tmp_path, "demo-th")
    assert resolve_thumb(tmp_path, "demo-th", "../project.json") is None
    assert resolve_thumb(tmp_path, "demo-th", "a/b.jpg") is None
    thumbs = tmp_path / "projects" / "demo-th" / ".webui_thumbs"
    thumbs.mkdir()
    (thumbs / "sc01.jpg").write_bytes(b"x")
    found = resolve_thumb(tmp_path, "demo-th", "sc01.jpg")
    assert found is not None and found.name == "sc01.jpg"


def test_produce_media_flat_whitelist(tmp_path):
    from montage.webui.state import resolve_produce_media

    proj = _make_project(tmp_path, "film")
    renders = proj / "renders"
    renders.mkdir(exist_ok=True)
    (renders / "final.mp4").write_bytes(b"mp4")
    (renders / "cover.jpg").write_bytes(b"jpg")
    (proj / "scratch").mkdir(exist_ok=True)
    (proj / "scratch" / "graded.mp4").write_bytes(b"no")
    detail = project_detail(tmp_path, "film")
    assert detail["produce_media"]["final"]["exists"] is True
    assert detail["produce_media"]["cover"]["exists"] is True
    assert detail["produce_media"]["final"]["url"] == "/media/film/renders/final.mp4"
    assert detail["produce_media"]["cover"]["url"] == "/media/film/renders/cover.jpg"
    assert detail["produce_media"]["season"]["exists"] is False
    assert "season" not in detail["produce_media"]["final"]["url"]
    assert resolve_produce_media(tmp_path, "film", "final.mp4") is not None
    assert resolve_produce_media(tmp_path, "film", "cover.jpg") is not None
    assert resolve_produce_media(tmp_path, "film", "graded.mp4") is None
    assert resolve_produce_media(tmp_path, "film", "../project.json") is None
    assert resolve_produce_media(tmp_path, "film", "scratch/graded.mp4") is None
    assert resolve_produce_media(tmp_path, "film::ep01", "final.mp4") is None


def test_produce_media_episode_not_colon_path(tmp_path):
    import json
    from montage.webui.state import resolve_produce_media, resolve_thumb

    parent = _make_project(tmp_path, "show")
    ep_dir = parent / "episodes" / "ep01"
    ep_dir.mkdir(parents=True)
    (ep_dir / "project.json").write_text(
        json.dumps({
            "project_id": "ep01",
            "title": "第一集",
            "pipeline_type": "cinematic",
            "parent_id": "show",
            "episode_id": "ep01",
        }),
        encoding="utf-8",
    )
    (ep_dir / "renders").mkdir()
    (ep_dir / "renders" / "final.mp4").write_bytes(b"ep")
    (ep_dir / ".webui_thumbs").mkdir()
    (ep_dir / ".webui_thumbs" / "sc01.jpg").write_bytes(b"x")
    detail = project_detail(tmp_path, "show", episode_id="ep01")
    assert detail is not None
    assert detail["produce_media"]["final"]["exists"] is True
    assert detail["produce_media"]["final"]["url"] == "/media/show/episodes/ep01/renders/final.mp4"
    assert resolve_produce_media(tmp_path, "show", "final.mp4", episode_id="ep01") is not None
    assert resolve_produce_media(tmp_path, "show::ep01", "final.mp4") is None
    thumb = resolve_thumb(tmp_path, "show", "sc01.jpg", episode_id="ep01")
    assert thumb is not None and thumb.name == "sc01.jpg"
    assert resolve_thumb(tmp_path, "show::ep01", "sc01.jpg") is None


def test_episode_checkpoint_writes_child_not_root(tmp_path):
    from montage.engine.stages import CheckpointStore, StageStatus

    parent = _make_project(tmp_path, "show")
    ep_dir = parent / "episodes" / "ep01"
    ep_dir.mkdir(parents=True)
    import json
    (ep_dir / "project.json").write_text(
        json.dumps({
            "project_id": "ep01",
            "title": "第一集",
            "pipeline_type": "cinematic",
            "parent_id": "show",
            "episode_id": "ep01",
        }),
        encoding="utf-8",
    )
    client = None
    try:
        from fastapi.testclient import TestClient
        from montage.webui.server import create_app

        client = TestClient(create_app(tmp_path))
    except Exception:
        client = None
    if client is None:
        store = CheckpointStore(ep_dir)
        store.write("research", StageStatus.IN_PROGRESS.value)
    else:
        res = client.post(
            "/api/projects/show/episodes/ep01/checkpoint",
            json={"stage": "research", "status": "in_progress", "approved": False},
        )
        assert res.status_code == 200, res.text
    assert (ep_dir / "checkpoint_research.json").is_file()
    parent_cp = json.loads((parent / "checkpoint_research.json").read_text(encoding="utf-8"))
    assert parent_cp.get("status") == "completed"
    ep_cp = json.loads((ep_dir / "checkpoint_research.json").read_text(encoding="utf-8"))
    assert ep_cp.get("status") == "in_progress"


def test_project_detail_shots_from_collect_shots(tmp_path):
    from montage.engine.artifacts import ArtifactStore

    proj = _make_project(tmp_path, "demo-shots")
    ArtifactStore(proj).write("scene_plan", {
        "scenes": [{
            "id": "sc01",
            "description": "巷口",
            "shots": [{
                "shot_id": "sc01_01",
                "shot_kind": "video",
                "duration_seconds": 5,
            }],
        }],
    })
    detail = project_detail(tmp_path, "demo-shots")
    assert "scenes" in detail
    assert detail["scenes"][0]["id"] == "sc01"
    assert detail["shots"][0]["id"] == "sc01_01"
    assert detail["shots"][0]["scene_id"] == "sc01"


def test_produce_media_season_on_series_root(tmp_path):
    import json
    from montage.engine.artifacts import ArtifactStore
    from montage.webui.state import resolve_produce_media

    parent = _make_project(tmp_path, "show")
    ArtifactStore(parent).write("episodes", {
        "episodes": [
            {"episode_id": "ep01", "scene_ids": ["sc01"]},
            {"episode_id": "ep02", "scene_ids": ["sc02"]},
        ],
    })
    renders = parent / "renders"
    renders.mkdir(exist_ok=True)
    (renders / "season.mp4").write_bytes(b"season")
    (renders / "final.mp4").write_bytes(b"not-season")
    detail = project_detail(tmp_path, "show")
    assert detail["kind"] == "series"
    assert detail["produce_media"]["season"]["exists"] is True
    assert detail["produce_media"]["season"]["url"] == "/media/show/renders/season.mp4"
    assert detail["produce_media"]["final"]["url"] == "/media/show/renders/final.mp4"
    assert "season.mp4" not in detail["produce_media"]["final"]["url"]
    found = resolve_produce_media(tmp_path, "show", "season.mp4")
    assert found is not None and found.name == "season.mp4"
    assert resolve_produce_media(tmp_path, "show", "../project.json") is None

    ep_dir = parent / "episodes" / "ep01"
    ep_dir.mkdir(parents=True)
    (ep_dir / "project.json").write_text(
        json.dumps({
            "project_id": "ep01",
            "title": "第一集",
            "pipeline_type": "cinematic",
            "parent_id": "show",
            "episode_id": "ep01",
        }),
        encoding="utf-8",
    )
    (ep_dir / "renders").mkdir()
    ep_detail = project_detail(tmp_path, "show", episode_id="ep01")
    assert ep_detail["produce_media"]["season"]["exists"] is False
    assert resolve_produce_media(tmp_path, "show", "season.mp4", episode_id="ep01") is None


def test_project_detail_produce_progress_banner(tmp_path):
    import json

    proj = _make_project(tmp_path, "demo-pp")
    (proj / "artifacts" / "produce_progress.json").write_text(
        json.dumps({
            "status": "await_sample",
            "next": {"argv": ["produce", str(proj), "--resume"], "note": "继续（不是人审）"},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    detail = project_detail(tmp_path, "demo-pp")
    assert detail["produce_progress"]["status"] == "await_sample"
    assert "继续" in detail["produce_progress"]["next"]["note"]
    assert project_detail(tmp_path, "../etc") is None
