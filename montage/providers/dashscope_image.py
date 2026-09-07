"""dashscope_image — 阿里云百炼（DashScope）万相生图/万相视频（自创实现）。

契约（与项目 dashscope_asr 相同的官方异步任务模式）：
- 提交：POST {base}/api/v1/services/{service}/{endpoint}
  header: X-DashScope-Async: enable, Authorization: Bearer <key>
- 轮询：GET {base}/api/v1/tasks/{task_id} 直到 task_status=SUCCEEDED
- 生图（wanx 万相）：service=aigc/text2image, endpoint=image-synthesis
  → output.results[].url
- 视频（wan 万相）：service=aigc/video-generation, endpoint=video-synthesis
  → output.video_url

密钥：DASHSCOPE_API_KEY。模型名可配（wan2.5-t2i-turbo / wan2.1-t2v-turbo 等）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from montage.providers.http import HttpError, get_json, post_json
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_BASE = "https://dashscope.aliyuncs.com"
_DONE = {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}


def _headers() -> dict[str, str]:
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        raise ValueError("缺少 DASHSCOPE_API_KEY")
    return {"Authorization": f"Bearer {key}", "X-DashScope-Async": "enable"}


def _poll(task_id: str, timeout: int, interval: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(interval)
        resp = get_json(
            f"{_BASE}/api/v1/tasks/{task_id}",
            headers={"Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}"},
            timeout=60,
        )
        output = (resp or {}).get("output") or {}
        state = output.get("task_status") or output.get("status")
        if state in _DONE:
            if state != "SUCCEEDED":
                raise HttpError(0, f"task/{task_id}", f"任务状态: {state}: {str(resp)[:200]}")
            return output
    raise HttpError(0, f"task/{task_id}", f"轮询超时（>{timeout}s）")


class DashscopeImage(BaseTool):
    """万相文生图（异步任务模式）。"""

    name = "dashscope_image"
    version = "0.1.0"
    capability = "image_generation"
    provider = "dashscope"
    runtime = ToolRuntime.API
    env_keys = ("DASHSCOPE_API_KEY",)
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {"type": "string", "default": "wan2.5-t2i-turbo"},
            "size": {"type": "string", "default": "1024*1024", "description": "如 1024*1024 / 1280*720"},
            "n": {"type": "integer", "default": 1},
            "seed": {"type": "integer", "default": -1},
            "output_path": {"type": "string"},
            "poll_interval_seconds": {"type": "number", "default": 3},
            "timeout_seconds": {"type": "number", "default": 300},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("DASHSCOPE_API_KEY") else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.02

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        prompt = inputs.get("prompt")
        if not prompt:
            return ToolResult(success=False, error="'prompt' 必填")
        try:
            headers = _headers()
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        payload = {
            "model": inputs.get("model", "wan2.5-t2i-turbo"),
            "input": {"prompt": prompt},
            "parameters": {
                "size": inputs.get("size", "1024*1024"),
                "n": int(inputs.get("n", 1)),
            },
        }
        if int(inputs.get("seed", -1)) >= 0:
            payload["parameters"]["seed"] = int(inputs["seed"])
        try:
            resp = post_json(f"{_BASE}/api/v1/services/aigc/text2image/image-synthesis", payload, headers=headers, timeout=120)
            task_id = ((resp or {}).get("output") or {}).get("task_id")
            if not task_id:
                return ToolResult(success=False, error=f"响应无 task_id: {str(resp)[:300]}")
            output = _poll(task_id, int(inputs.get("timeout_seconds", 300)), float(inputs.get("poll_interval_seconds", 3)))
            urls = [r.get("url") for r in (output.get("results") or []) if r.get("url")]
        except (HttpError, KeyError) as exc:
            return ToolResult(success=False, error=str(exc))

        local = None
        if urls and inputs.get("output_path"):
            try:
                from montage.tools.downloader import download_file

                local = str(download_file(urls[0], inputs["output_path"], timeout=120))
            except Exception as exc:  # noqa: BLE001
                local = None
        return ToolResult(
            success=True,
            data={"urls": urls, "task_id": task_id, "output": local, "model": inputs.get("model")},
            meta={"provider": "dashscope", "model": inputs.get("model")},
            cost_usd=self.estimate_cost(inputs),
        )


class WanVideo(BaseTool):
    """万相文生视频（异步任务模式，走 DashScope）。"""

    name = "wan_video"
    version = "0.1.0"
    capability = "video_generation"
    provider = "dashscope"
    runtime = ToolRuntime.API
    env_keys = ("DASHSCOPE_API_KEY",)
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {"type": "string", "default": "wan2.1-t2v-turbo"},
            "resolution": {"type": "string", "default": "1280*720"},
            "duration": {"type": "integer", "default": 5, "description": "5 或 10 秒"},
            "output_path": {"type": "string"},
            "poll_interval_seconds": {"type": "number", "default": 5},
            "timeout_seconds": {"type": "number", "default": 600},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("DASHSCOPE_API_KEY") else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.2

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        prompt = inputs.get("prompt")
        if not prompt:
            return ToolResult(success=False, error="'prompt' 必填")
        try:
            headers = _headers()
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        payload = {
            "model": inputs.get("model", "wan2.1-t2v-turbo"),
            "input": {"prompt": prompt},
            "parameters": {
                "resolution": inputs.get("resolution", "1280*720"),
                "duration": int(inputs.get("duration", 5)),
            },
        }
        try:
            resp = post_json(f"{_BASE}/api/v1/services/aigc/video-generation/video-synthesis", payload, headers=headers, timeout=120)
            task_id = ((resp or {}).get("output") or {}).get("task_id")
            if not task_id:
                return ToolResult(success=False, error=f"响应无 task_id: {str(resp)[:300]}")
            output = _poll(task_id, int(inputs.get("timeout_seconds", 600)), float(inputs.get("poll_interval_seconds", 5)))
            video_url = output.get("video_url")
        except (HttpError, KeyError) as exc:
            return ToolResult(success=False, error=str(exc))

        if not video_url:
            return ToolResult(success=False, error=f"任务完成但无 video_url: {str(output)[:300]}")
        local = None
        if inputs.get("output_path"):
            try:
                from montage.tools.downloader import download_file

                local = str(download_file(video_url, inputs["output_path"], timeout=300))
            except Exception as exc:  # noqa: BLE001
                local = None
        return ToolResult(
            success=True,
            data={"video_url": video_url, "task_id": task_id, "output": local, "model": inputs.get("model")},
            meta={"provider": "dashscope", "model": inputs.get("model")},
            cost_usd=self.estimate_cost(inputs),
        )
