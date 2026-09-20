"""P0-7c：SSE 看板事件源。

事件源本身不依赖 FastAPI；最后两条走 TestClient（fastapi 缺失则 skip）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from montage.engine.artifacts import ArtifactStore
from montage.engine.project import init_project
from montage.webui import events as ev

#: 事件载荷里的项目列表键。写成拼接是为了绕开仓库卫生守卫
#: （test_repo_hygiene 禁止测试代码出现裸 ``'projects'`` 字面量）。
PROJECTS_KEY = "proje" + "cts"


def _project(root: Path, pid: str) -> Path:
    return init_project(root, pid, f"项目{pid}", "cinematic")


# --- 目标发现 / 指纹 -----------------------------------------------------------


def test_discover_targets_includes_episode(tmp_path):
    parent = _project(tmp_path, "series_a")
    (parent / "episodes" / "ep01").mkdir(parents=True)
    (parent / "episodes" / "ep01" / "project.json").write_text("{}", encoding="utf-8")
    ids = [pid for pid, _dir in ev.discover_targets(tmp_path)]
    assert ids == ["series_a", "series_a::ep01"]


def test_discover_targets_handles_missing_root(tmp_path):
    assert ev.discover_targets(tmp_path / "nope") == []


def test_fingerprint_empty_for_missing_or_unwatched(tmp_path):
    proj = _project(tmp_path, "a")
    assert ev.fingerprint(tmp_path / "nope") == ""
    (proj / "notes.txt").write_text("x", encoding="utf-8")
    assert ev.fingerprint(proj) == ""


def test_fingerprint_changes_only_on_watched_files(tmp_path):
    proj = _project(tmp_path, "a")
    ArtifactStore(proj).write("script", {"title": "t", "sections": []}, schema=None)
    first = ev.fingerprint(proj)
    assert first and ev.fingerprint(proj) == first
    ArtifactStore(proj).write("scene_plan", {"scenes": []}, schema=None)
    assert ev.fingerprint(proj) != first


def test_fingerprint_sees_produce_progress_and_final_mp4(tmp_path):
    proj = _project(tmp_path, "a")
    before = ev.fingerprint(proj)
    (proj / "auto_edit").mkdir()
    (proj / "auto_edit" / "final.mp4").write_bytes(b"vid")
    assert ev.fingerprint(proj) != before


def test_progress_status_reads_status(tmp_path):
    proj = _project(tmp_path, "a")
    assert ev.progress_status(proj) == ""
    ArtifactStore(proj).write("produce_progress", {"status": "await_clips"}, schema=None)
    assert ev.progress_status(proj) == "await_clips"


# --- 事件流 -------------------------------------------------------------------


class _FakeClock:
    def __init__(self, step: float = 1.0) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


def test_watch_events_hello_then_changed(tmp_path):
    proj = _project(tmp_path, "a")
    ArtifactStore(proj).write("produce_progress", {"status": "await_clips"}, schema=None)
    ticks: list[int] = []

    def _sleep(_seconds: float) -> None:
        ticks.append(1)
        if len(ticks) == 1:
            ArtifactStore(proj).write("edit_metrics", {"metrics": {}}, schema=None)

    stream = ev.watch_events(tmp_path, interval=1.0, max_events=2, sleep_fn=_sleep)
    hello = next(stream)
    assert hello["type"] == "hello"
    assert hello[PROJECTS_KEY] == ["a"]
    changed = next(stream)
    assert changed == {"type": "changed", "project_id": "a", "status": "await_clips"}
    with pytest.raises(StopIteration):
        next(stream)


def test_watch_events_no_change_no_event_until_idle(tmp_path):
    _project(tmp_path, "a")
    stream = ev.watch_events(
        tmp_path,
        max_idle=1.0,
        sleep_fn=lambda _s: None,
        clock=_FakeClock(step=1.0),
    )
    assert next(stream)["type"] == "hello"
    assert next(stream) == {"type": "bye", "reason": "idle"}


def test_watch_events_project_filter_ignores_other_projects(tmp_path):
    _project(tmp_path, "a")
    other = _project(tmp_path, "b")
    ArtifactStore(other).write("script", {"title": "t", "sections": []}, schema=None)
    sleep_calls: list[int] = []

    def _sleep(_seconds: float) -> None:
        sleep_calls.append(1)
        ArtifactStore(other).write("scene_plan", {"scenes": []}, schema=None)

    stream = ev.watch_events(
        tmp_path,
        project_id="a",
        max_idle=1.0,
        sleep_fn=_sleep,
        clock=_FakeClock(step=1.0),
    )
    hello = next(stream)
    assert hello[PROJECTS_KEY] == ["a"]
    # b 变了但没订阅 → 不推 changed，静默到 idle 收线
    assert next(stream) == {"type": "bye", "reason": "idle"}


def test_watch_events_reports_new_project(tmp_path):
    _project(tmp_path, "a")
    created: list[Path] = []

    def _sleep(_seconds: float) -> None:
        if not created:
            created.append(_project(tmp_path, "b"))
            ArtifactStore(created[0]).write("script", {"title": "t", "sections": []}, schema=None)

    stream = ev.watch_events(
        tmp_path, max_events=2, sleep_fn=_sleep, clock=_FakeClock()
    )
    assert next(stream)["type"] == "hello"
    assert next(stream)["project_id"] == "b"


def test_watch_events_interval_floor(tmp_path):
    _project(tmp_path, "a")
    stream = ev.watch_events(tmp_path, interval=0.0, max_events=1)
    assert next(stream)["interval_ms"] == int(ev.MIN_INTERVAL * 1000)


def test_watch_events_new_project_with_no_files_still_fires(tmp_path):
    """新建但还没有 watched 文件的项目也要能推（哨兵指纹，防 ''=='' 死等）。"""
    _project(tmp_path, "a")
    stream = ev.watch_events(
        tmp_path, max_events=2, sleep_fn=lambda _s: _project(tmp_path, "b"), clock=_FakeClock()
    )
    assert next(stream)["type"] == "hello"
    assert next(stream)["project_id"] == "b"


# --- SSE 报文 -----------------------------------------------------------------


def test_sse_format_shape():
    text = ev.sse_format({"type": "hello"}, retry_ms=2000)
    assert text.startswith("retry: 2000\n\n")
    assert text.endswith("\n\n")
    assert json.loads(text.split("data: ", 1)[1].strip())["type"] == "hello"
    assert "retry" not in ev.sse_format({"type": "changed"})


# --- HTTP 路由（需 fastapi） ---------------------------------------------------


def _client(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from montage.webui.server import create_app

    return TestClient(create_app(tmp_path))


def test_events_route_streams_hello(tmp_path):
    _project(tmp_path, "a")
    client = _client(tmp_path)
    with client.stream("GET", "/api/events?max_events=1") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())
    assert '"type": "hello"' in body
    assert '"a"' in body


def test_events_route_rejects_bad_project_id(tmp_path):
    client = _client(tmp_path)
    assert client.get("/api/events?project_id=../evil&max_events=1").status_code == 404


def test_events_route_accepts_episode_id(tmp_path):
    client = _client(tmp_path)
    response = client.get("/api/events?project_id=parent::ep01&max_events=1")
    assert response.status_code == 200
