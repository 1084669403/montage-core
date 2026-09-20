"""Structured provider task recovery tests (no real network calls)."""

from __future__ import annotations

import pytest

from montage.providers import agnes
from montage.providers.http import HttpError
from montage.toolbase import ToolResult
from montage.tools.shot_runner import ShotRunner


def _pass_quality(path, *, expected_duration=None):
    return {"ok": True, "issues": []}


def _env(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "test-key")


def test_agnes_poll_result_requires_provider_task_id(monkeypatch):
    _env(monkeypatch)
    result = agnes.AgnesVideo().poll_result({"prompt": "x"})
    assert result.success is False
    assert result.meta["provider"] == "agnes"


def test_agnes_poll_result_preserves_existing_task_id(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(
        agnes,
        "_poll_video",
        lambda **_kwargs: ("https://example.test/video.mp4", None),
    )
    result = agnes.AgnesVideo().poll_result({
        "prompt": "x",
        "provider_task_id": "video-123",
    })
    assert result.success is True
    assert result.data["id"] == "video-123"
    assert result.meta["provider_task_id"] == "video-123"


def test_agnes_poll_timeout_keeps_task_id_and_poll_action(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(
        agnes,
        "_poll_video",
        lambda **_kwargs: (None, "poll timeout"),
    )
    result = agnes.AgnesVideo().poll_result({
        "prompt": "x",
        "provider_task_id": "video-123",
    })
    assert result.success is False
    assert result.data["provider_task_id"] == "video-123"
    assert result.meta["provider_task_id"] == "video-123"
    assert result.meta["recovery_action"] == "poll"


def test_agnes_create_http_error_has_structured_meta(monkeypatch):
    _env(monkeypatch)

    def raise_502(*_args, **_kwargs):
        raise HttpError(502, "https://api.example/v1/videos", "bad gateway")

    monkeypatch.setattr(agnes, "post_json", raise_502)
    result = agnes.AgnesVideo().execute({"prompt": "x"})
    assert result.success is False
    assert result.meta["provider"] == "agnes"
    assert result.meta["model"] == "agnes-video-2.5-flash"
    assert result.meta["error_class"] == "provider_unavailable"
    assert result.meta["recovery_action"] == "retry_or_fail"


def test_agnes_success_has_structured_provider_task_id(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setattr(
        agnes,
        "post_json",
        lambda *_args, **_kwargs: {
            "video_id": "video-123",
            "status": "completed",
            "url": "https://example.test/video.mp4",
        },
    )
    result = agnes.AgnesVideo().execute({"prompt": "x"})
    assert result.success is True
    assert result.data["id"] == "video-123"
    assert result.meta["provider_task_id"] == "video-123"
    assert result.meta.get("recovery_action", "") == ""


def test_generate_retry_polls_old_task_instead_of_reposting(monkeypatch):
    tool = ShotRunner(quality_check=_pass_quality)
    calls: list[dict] = []

    def run_video(inputs):
        calls.append(dict(inputs))
        if not inputs.get("recover"):
            return ToolResult(
                success=False,
                error="poll timeout",
                meta={
                    "provider": "agnes",
                    "model": "agnes-video-2.5-flash",
                    "provider_task_id": "video-123",
                    "recovery_action": "poll",
                    "state": "unknown",
                },
            )
        return ToolResult(
            success=True,
            data={
                "url": "https://example.test/video.mp4",
                "model": "agnes-video-2.5-flash",
                "id": "video-123",
            },
            meta={
                "provider": "agnes",
                "model": "agnes-video-2.5-flash",
                "provider_task_id": "video-123",
            },
        )

    tool._run_video = run_video
    tool._cached_or_generate = lambda *, generate, payload, **_kwargs: generate(payload)
    result = tool._generate_with_retry(
        kind="video",
        payload={"seconds": 5},
        output_path="unused.mp4",
        cache=None,
        cache_params={},
        expected_duration=None,
    )
    assert result.success is True
    assert result.meta["recovered_from_task_id"] == "video-123"
    assert len(calls) == 2
    assert calls[0].get("recover") is None
    assert calls[1].get("recover") is True
    assert calls[1].get("provider_task_id") == "video-123"


def test_generate_retry_emits_append_only_success_events():
    events: list[dict] = []
    tool = ShotRunner(
        quality_check=_pass_quality,
        event_emitter=events.append,
    )
    tool._run_video = lambda _inputs: ToolResult(
        success=True,
        data={"url": "https://example.test/video.mp4", "model": "m", "id": "task-1"},
        meta={"provider": "agnes", "model": "m", "provider_task_id": "task-1"},
    )
    tool._cached_or_generate = lambda *, generate, payload, **_kwargs: generate(payload)
    result = tool._generate_with_retry(
        kind="video",
        payload={"seconds": 5},
        output_path="unused.mp4",
        cache=None,
        cache_params={"shot_id": "sh01"},
        expected_duration=None,
    )
    assert result.success is True
    assert [row["event"] for row in events] == [
        "downloaded", "validated", "succeeded"
    ]
    assert all(row["shot_id"] == "sh01" for row in events)
    assert all(row["run_id"] and row["batch_id"] for row in events)
    assert tool._event_errors == []


def test_generate_retry_emits_poll_timeout_and_recovery_events():
    events: list[dict] = []
    tool = ShotRunner(quality_check=_pass_quality, event_emitter=events.append)
    calls: list[dict] = []

    def run_video(inputs):
        calls.append(dict(inputs))
        if not inputs.get("recover"):
            return ToolResult(
                success=False,
                error="poll timeout",
                meta={
                    "provider": "agnes",
                    "model": "m",
                    "provider_task_id": "task-1",
                    "recovery_action": "poll",
                },
            )
        return ToolResult(
            success=True,
            data={"id": "task-1"},
            meta={"provider": "agnes", "model": "m", "provider_task_id": "task-1"},
        )

    tool._run_video = run_video
    tool._cached_or_generate = lambda *, generate, payload, **_kwargs: generate(payload)
    result = tool._generate_with_retry(
        kind="video",
        payload={"seconds": 5},
        output_path="unused.mp4",
        cache=None,
        cache_params={"shot_id": "sh01"},
        expected_duration=None,
    )
    assert result.success is True
    assert [row["event"] for row in events] == [
        "poll_timeout", "downloaded", "validated", "succeeded"
    ]
    assert all(row["provider_task_id"] == "task-1" for row in events)


def test_event_write_failure_is_degraded_not_fatal():
    def broken_emitter(_row):
        raise OSError("disk unavailable")

    tool = ShotRunner(
        quality_check=_pass_quality,
        event_emitter=broken_emitter,
    )
    tool._run_video = lambda _inputs: ToolResult(
        success=True,
        data={"url": "https://example.test/video.mp4", "model": "m", "id": "task-1"},
        meta={"provider": "agnes", "model": "m", "provider_task_id": "task-1"},
    )
    tool._cached_or_generate = lambda *, generate, payload, **_kwargs: generate(payload)
    result = tool._generate_with_retry(
        kind="video",
        payload={"seconds": 5},
        output_path="unused.mp4",
        cache=None,
        cache_params={"shot_id": "sh01"},
        expected_duration=None,
    )
    assert result.success is True
    assert tool._event_errors
    assert all(row["error"] == "disk unavailable" for row in tool._event_errors)


def test_execute_uses_explicit_run_and_batch_identity():
    events: list[dict] = []
    tool = ShotRunner(event_emitter=events.append)
    result = tool.execute({
        "run_id": "run-fixed",
        "batch_id": "batch-fixed",
    })
    assert result.success is False  # no shots, but identity setup must still be auditable
    assert tool._event_context["run_id"] == "run-fixed"
    assert tool._event_context["batch_id"] == "batch-fixed"
    assert tool._event_errors == []


def test_execute_resets_stale_event_errors_on_new_run():
    events: list[dict] = []
    tool = ShotRunner(event_emitter=events.append)
    tool._event_errors = [{"event": "stale", "error": "old run"}]
    tool.execute({"run_id": "run-new"})
    assert tool._event_errors == []


def test_event_telemetry_is_added_to_payload():
    tool = ShotRunner(event_emitter=lambda _row: None)
    tool.execute({"run_id": "run-fixed", "batch_id": "batch-fixed"})
    payload = tool._apply_event_errors({})
    assert payload["generation_run_id"] == "run-fixed"
    assert payload["generation_batch_id"] == "batch-fixed"
    assert payload["generation_event_errors"] == []
