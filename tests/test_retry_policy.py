"""Pure retry decision tests for timeout / 5xx / 429 recovery."""

from __future__ import annotations

import pytest

from montage.engine.retry_policy import (
    classify_error,
    decide_retry,
    next_retry_delay,
    provider_meta,
)


@pytest.mark.parametrize(
    ("status", "error", "expected"),
    [
        (429, "", "rate_limited"),
        (502, "", "provider_unavailable"),
        (400, "", "invalid_request"),
        (None, "HTTP 429 too many requests", "rate_limited"),
        (None, "poll timeout", "poll_timeout"),
        (None, "轮询超时", "poll_timeout"),
        (None, "boom", "provider_error"),
    ],
)
def test_classify_error_has_stable_classes(status, error, expected):
    assert classify_error(http_status=status, error=error) == expected


def test_timeout_with_provider_task_id_polls_instead_of_reposting():
    decision = decide_retry(
        state="running",
        attempt=2,
        max_attempts=3,
        provider_task_id="task-1",
        error="poll timeout",
    )
    assert decision.action == "poll"
    assert decision.state == "unknown"
    assert decision.error_class == "poll_timeout"
    assert "polled first" in decision.reason


def test_timeout_without_provider_task_id_may_schedule_retry():
    decision = decide_retry(
        state="unknown",
        attempt=1,
        max_attempts=3,
        error="poll timeout",
    )
    assert decision.action == "retry"
    assert decision.state == "retry_scheduled"
    assert decision.delay_seconds == 2.0


def test_provider_502_uses_exponential_backoff_and_fails_at_budget():
    first = decide_retry(state="submitted", attempt=1, http_status=502)
    second = decide_retry(state="retry_scheduled", attempt=2, http_status=502)
    third = decide_retry(state="retry_scheduled", attempt=3, http_status=502)
    assert (first.action, first.delay_seconds) == ("retry", 2.0)
    assert (second.action, second.delay_seconds) == ("retry", 4.0)
    assert (third.action, third.state) == ("fail", "failed")


def test_rate_limit_honors_retry_after_and_backoff():
    retry_after = decide_retry(
        state="running", attempt=1, http_status=429, retry_after=17.0
    )
    exponential = decide_retry(state="retry_scheduled", attempt=2, http_status=429)
    assert retry_after.action == "wait_retry"
    assert retry_after.delay_seconds == 17.0
    assert exponential.delay_seconds == 4.0


def test_invalid_request_is_not_retried():
    decision = decide_retry(state="submitted", attempt=1, http_status=400)
    assert decision.action == "fail"
    assert decision.error_class == "invalid_request"


def test_provider_meta_structures_task_id_without_error_parsing():
    meta = provider_meta(
        provider="agnes",
        model="agnes-video-2.5-flash",
        provider_task_id="video-123",
        http_status=502,
    )
    assert meta["provider_task_id"] == "video-123"
    assert meta["error_class"] == "provider_unavailable"


def test_next_retry_delay_caps_backoff():
    assert next_retry_delay(error_class="provider_unavailable", attempt=1) == 2.0
    assert next_retry_delay(error_class="provider_unavailable", attempt=10) == 30.0
