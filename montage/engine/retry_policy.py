"""Provider-neutral retry decisions for remote generation tasks.

This is deliberately a pure policy module.  It never issues HTTP calls and
never mutates provider state.  Callers use its decision to choose between
polling an existing provider task and creating a new one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RetryDecision:
    action: str  # succeeded | poll | retry | wait_retry | fail
    state: str  # succeeded | running | unknown | retry_scheduled | failed
    reason: str
    error_class: str
    delay_seconds: float = 0.0


def classify_error(
    *,
    http_status: int | None = None,
    error: str = "",
) -> str:
    """Return a stable error class without parsing a provider's UI text."""
    status = int(http_status or 0)
    if status == 429:
        return "rate_limited"
    if 500 <= status <= 599:
        return "provider_unavailable"
    if 400 <= status <= 499:
        return "invalid_request"
    text = str(error or "")
    if "429" in text:
        return "rate_limited"
    if "timeout" in text.lower() or "轮询超时" in text:
        return "poll_timeout"
    return "provider_error"


def next_retry_delay(
    *,
    error_class: str,
    attempt: int,
    base_delay: float = 2.0,
    max_delay: float = 30.0,
    retry_after: float | None = None,
) -> float:
    """Compute deterministic backoff; jitter belongs to the runtime caller."""
    attempt = max(int(attempt or 1), 1)
    if retry_after is not None and error_class == "rate_limited":
        return max(0.0, float(retry_after))
    delay = max(float(base_delay), 0.0) * (2 ** (attempt - 1))
    return min(delay, max(float(max_delay), 0.0))


def decide_retry(
    *,
    state: str,
    attempt: int,
    max_attempts: int = 3,
    provider_task_id: str | None = None,
    http_status: int | None = None,
    error: str = "",
    retry_after: float | None = None,
) -> RetryDecision:
    """Decide whether to poll, resubmit, or fail without hiding the task id.

    A timeout with ``provider_task_id`` is *not* a failed generation.  The old
    task remains the authority until the provider reports a terminal state.
    """
    provider_task_id = str(provider_task_id or "")
    error_class = classify_error(http_status=http_status, error=error)
    current_state = str(state or "").strip().lower() or "unknown"

    if current_state == "succeeded":
        return RetryDecision("succeeded", "succeeded", "task already succeeded", error_class)

    if error_class == "invalid_request":
        return RetryDecision("fail", "failed", "provider rejected the request", error_class)

    if current_state in ("submitted", "running", "unknown") and provider_task_id:
        return RetryDecision(
            "poll",
            "unknown",
            "existing provider task must be polled first",
            error_class,
        )

    if error_class == "poll_timeout" and provider_task_id:
        return RetryDecision(
            "poll",
            "unknown",
            "poll deadline reached; provider task id is preserved",
            error_class,
        )

    if attempt >= max_attempts:
        return RetryDecision(
            "fail",
            "failed",
            f"retry budget exhausted after {attempt} attempts",
            error_class,
        )

    delay = next_retry_delay(
        error_class=error_class,
        attempt=attempt,
        retry_after=retry_after,
    )
    if error_class == "rate_limited":
        return RetryDecision(
            "wait_retry",
            "retry_scheduled",
            "provider rate limit reached",
            error_class,
            delay,
        )

    return RetryDecision(
        "retry",
        "retry_scheduled",
        "transient provider error",
        error_class,
        delay,
    )


def provider_meta(
    *,
    provider: str = "",
    model: str = "",
    provider_task_id: str | None = None,
    error: str = "",
    http_status: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a stable ToolResult.meta block for task recovery."""
    row: dict[str, Any] = {
        "provider": str(provider or ""),
        "model": str(model or ""),
        "provider_task_id": str(provider_task_id or ""),
        "error_class": classify_error(http_status=http_status, error=error),
    }
    # 透传状态码：上层退避/审计要按 429/5xx 分流，字符串匹配不可靠。
    if http_status is not None:
        try:
            row["http_status"] = int(http_status)
        except (TypeError, ValueError):
            pass
    row.update(extra)
    return row
