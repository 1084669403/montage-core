"""seedance_ark — 火山方舟视频生成（Seedance 2.5 / 2.0 Pro）。

与 jimeng.py 视觉智能 HMAC / v30 req_key 分离：本模块只走方舟 Bearer。
provider=ark，避免 video_loop=volcengine 把选型器切到本工具。

创建：POST {base}/api/v3/contents/generations/tasks
查询：GET  {base}/api/v3/contents/generations/tasks/{id}
成功态 succeeded；视频在 content.video_url（约 24h TTL，立刻下载）。

model ID 必须由 SEEDANCE_MODEL / ARK_VIDEO_MODEL 或 inputs.model 提供，禁止猜测默认。
密钥：ARK_API_KEY。ARK_API_BASE 可覆盖。
"""

from __future__ import annotations

import os
import time
from typing import Any

from montage.providers.capabilities import (
    apply_seedance_content,
    snap_duration_seconds,
    video_surface,
)
from montage.providers.http import HttpError, get_json, post_json
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_DEFAULT_BASE = "https://ark.cn-beijing.volces.com"
_DONE = frozenset({"succeeded", "failed", "expired", "cancelled", "canceled"})
_OK = frozenset({"succeeded"})

# dry_run 占位（美元/秒）；禁止套用 jimeng v30 刊例。可用 SEEDANCE_USD_PER_SEC 覆盖。
_USD_PER_SEC = {
    "seedance_25": 0.06,
    "seedance_20_pro": 0.10,
}


def _base() -> str:
    return str(os.environ.get("ARK_API_BASE") or _DEFAULT_BASE).rstrip("/")


def _headers() -> dict[str, str]:
    key = os.environ.get("ARK_API_KEY")
    if not key:
        raise ValueError("缺少 ARK_API_KEY（火山方舟 API Key，不是 VOLC_ACCESSKEY）")
    return {"Authorization": f"Bearer {key}"}


def resolve_model(inputs: dict[str, Any]) -> str:
    return str(
        inputs.get("model")
        or os.environ.get("SEEDANCE_MODEL")
        or os.environ.get("ARK_VIDEO_MODEL")
        or ""
    ).strip()


def resolve_api_id(inputs: dict[str, Any]) -> str:
    raw = str(inputs.get("api_id") or "seedance_25").strip()
    return raw if raw in ("seedance_25", "seedance_20_pro") else "seedance_25"


def _usd_per_sec(api_id: str) -> float:
    override = os.environ.get("SEEDANCE_USD_PER_SEC")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    return float(_USD_PER_SEC.get(api_id) or _USD_PER_SEC["seedance_25"])


def _task_url(task_id: str) -> str:
    return f"{_base()}/api/v3/contents/generations/tasks/{task_id}"


def _video_url(resp: dict[str, Any]) -> str:
    content = resp.get("content") if isinstance(resp, dict) else None
    if isinstance(content, dict) and content.get("video_url"):
        return str(content["video_url"])
    if isinstance(resp, dict) and resp.get("video_url"):
        return str(resp["video_url"])
    return ""


def _last_frame_url(resp: dict[str, Any]) -> str:
    content = resp.get("content") if isinstance(resp, dict) else None
    if isinstance(content, dict):
        for key in ("last_frame_url", "last_frame"):
            val = content.get(key)
            if isinstance(val, str) and val:
                return val
            if isinstance(val, dict) and val.get("url"):
                return str(val["url"])
    return ""


def _poll(task_id: str, timeout: int, interval: float) -> dict[str, Any]:
    url = _task_url(task_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(max(interval, 0.0))
        resp = get_json(url, headers=_headers(), timeout=60)
        status = str((resp or {}).get("status") or "").lower()
        if status in _DONE:
            if status not in _OK:
                raise HttpError(0, url, f"任务状态: {status}: {str(resp)[:200]}")
            return resp or {}
    raise HttpError(0, url, f"轮询超时（>{timeout}s）")


class SeedanceVideo(BaseTool):
    """方舟 Seedance 文生/图生/首尾帧视频。"""

    name = "seedance_video"
    version = "0.1.0"
    capability = "video_generation"
    provider = "ark"
    runtime = ToolRuntime.API
    env_keys = ("ARK_API_KEY",)
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {"type": "string", "description": "控制台核对的模型/接入点 ID"},
            "api_id": {"type": "string", "enum": ["seedance_25", "seedance_20_pro"]},
            "seconds": {"type": "number", "default": 5},
            "ratio": {"type": "string", "default": "16:9"},
            "resolution": {"type": "string"},
            "generate_audio": {"type": "boolean", "default": True},
            "return_last_frame": {"type": "boolean", "default": True},
            "watermark": {"type": "boolean", "default": False},
            "image_url": {"type": "string"},
            "first_frame_url": {"type": "string"},
            "last_frame_url": {"type": "string"},
            "reference_urls": {"type": "array", "items": {"type": "string"}},
            "video_urls": {"type": "array", "items": {"type": "string"}},
            "audio_urls": {"type": "array", "items": {"type": "string"}},
            "rework_mode": {"type": "string", "enum": ["edit", "extend"]},
            "negative_prompt": {"type": "string"},
            "output_path": {"type": "string"},
            "poll_interval_seconds": {"type": "number", "default": 5},
            "timeout_seconds": {"type": "number", "default": 600},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("ARK_API_KEY") else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        api_id = resolve_api_id(inputs)
        surface = video_surface(api_id)
        if str(inputs.get("rework_mode") or "") == "edit":
            seconds = max(float(inputs.get("seconds") or 5), 1.0)
        else:
            seconds = snap_duration_seconds(float(inputs.get("seconds") or 5), surface.get("duration_policy"))
        return round(_usd_per_sec(api_id) * max(seconds, 1.0), 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        prompt = str(inputs.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(success=False, error="'prompt' 必填")
        model = resolve_model(inputs)
        if not model:
            return ToolResult(
                success=False,
                error="缺少 SEEDANCE_MODEL / ARK_VIDEO_MODEL 或 inputs.model（控制台核对，禁止猜测默认）",
            )
        try:
            headers = _headers()
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        api_id = resolve_api_id(inputs)
        surface = video_surface(api_id)
        rework = str(inputs.get("rework_mode") or "").strip().lower()
        negative = str(inputs.get("negative_prompt") or "").strip()
        text = prompt if not negative else f"{prompt}\nNegative Prompt: {negative}"
        first = str(inputs.get("first_frame_url") or inputs.get("image_url") or "").strip()
        last = str(inputs.get("last_frame_url") or "").strip()
        refs = [{"url": u} for u in (inputs.get("reference_urls") or []) if u]
        videos = [str(u) for u in (inputs.get("video_urls") or []) if u]
        payload: dict[str, Any] = {
            "model": model,
            "generate_audio": bool(inputs.get("generate_audio", True)),
            "return_last_frame": bool(inputs.get("return_last_frame", True)),
        }
        if inputs.get("resolution"):
            payload["resolution"] = inputs["resolution"]
        elif api_id == "seedance_20_pro":
            payload["resolution"] = "1080p"
        else:
            payload["resolution"] = "720p"
        if rework in ("edit", "extend"):
            from montage.providers.capabilities import apply_seedance_rework

            source = videos[0] if videos else ""
            if not source.startswith("http"):
                return ToolResult(success=False, error="edit/extend 需要 video_urls 公网地址")
            notes = apply_seedance_rework(
                payload,
                text=text,
                video_url=source,
                refs=refs,
                mode=rework,
                extra_seconds=float(inputs.get("seconds") or 5),
                caps=surface,
            )
        else:
            seconds = int(snap_duration_seconds(float(inputs.get("seconds") or 5), surface.get("duration_policy")))
            payload["duration"] = seconds
            if inputs.get("ratio"):
                payload["ratio"] = inputs["ratio"]
            notes = apply_seedance_content(
                payload,
                text=text,
                first_url=first,
                last_url=last,
                refs=refs,
                video_urls=videos,
                audio_urls=list(inputs.get("audio_urls") or []),
                caps=surface,
            )
        if inputs.get("watermark") is True:
            payload["watermark"] = True
        create_url = f"{_base()}/api/v3/contents/generations/tasks"
        try:
            resp = post_json(create_url, payload, headers=headers, timeout=120)
            task_id = str((resp or {}).get("id") or "")
            if not task_id:
                return ToolResult(success=False, error=f"响应无 id: {str(resp)[:300]}")
            result = _poll(
                task_id,
                int(inputs.get("timeout_seconds", 600)),
                float(inputs.get("poll_interval_seconds", 5)),
            )
            video_url = _video_url(result)
        except (HttpError, KeyError) as exc:
            return ToolResult(success=False, error=str(exc))
        if not video_url:
            return ToolResult(success=False, error=f"任务完成但无 content.video_url: {str(result)[:300]}")
        local = None
        if inputs.get("output_path"):
            try:
                from montage.tools.downloader import download_file

                local = str(download_file(video_url, inputs["output_path"], timeout=300))
            except Exception:  # noqa: BLE001
                local = None
        return ToolResult(
            success=True,
            data={
                "video_url": video_url,
                "last_frame_url": _last_frame_url(result),
                "task_id": task_id,
                "output": local,
                "api_id": api_id,
                "model": model,
                "notes": notes,
            },
            meta={"provider": "ark", "api_id": api_id, "model": model},
            cost_usd=self.estimate_cost(inputs),
        )
