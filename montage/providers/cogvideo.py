"""cogvideo — 智谱清影（CogVideoX）视频生成（自创实现）。

契约（智谱 v4 异步 API，官方文档：docs.bigmodel.cn 视频生成异步）：
- 提交：POST https://open.bigmodel.cn/api/paas/v4/videos/generations
  headers: Authorization: Bearer <key>
  body: {model, prompt, image_url(可选图生视频)}
  响应：{id, task_status}
- 轮询：GET .../videos/generations/{id} → task_status=SUCCESS
  → video_result[0].url（生成视频）或 fail_reason

密钥：ZHIPU_API_KEY（智谱开放平台）。模型：cogvideox-flash / cogvideox 等。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from montage.providers.http import HttpError, get_json, post_json
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_BASE = "https://open.bigmodel.cn/api/paas/v4"
_DONE = {"SUCCESS", "FAIL", "CANCEL"}


def _headers() -> dict[str, str]:
    key = os.environ.get("ZHIPU_API_KEY")
    if not key:
        raise ValueError("缺少 ZHIPU_API_KEY")
    return {"Authorization": f"Bearer {key}"}


class CogvideoVideo(BaseTool):
    """智谱清影文生视频/图生视频（异步）。"""

    name = "cogvideo_video"
    version = "0.1.0"
    capability = "video_generation"
    provider = "zhipu"
    runtime = ToolRuntime.API
    env_keys = ("ZHIPU_API_KEY",)
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {"type": "string", "default": "cogvideox-flash"},
            "image_url": {"type": "string", "description": "图生视频参考图 URL"},
            "output_path": {"type": "string"},
            "poll_interval_seconds": {"type": "number", "default": 5},
            "timeout_seconds": {"type": "number", "default": 600},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("ZHIPU_API_KEY") else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.15

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        prompt = inputs.get("prompt")
        if not prompt:
            return ToolResult(success=False, error="'prompt' 必填")
        try:
            headers = _headers()
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        payload: dict[str, Any] = {
            "model": inputs.get("model", "cogvideox-flash"),
            "prompt": prompt,
        }
        if inputs.get("image_url"):
            payload["image_url"] = inputs["image_url"]
        try:
            resp = post_json(f"{_BASE}/videos/generations", payload, headers=headers, timeout=120)
            gen_id = (resp or {}).get("id")
            if not gen_id:
                return ToolResult(success=False, error=f"响应无 id: {str(resp)[:300]}")
            deadline = time.time() + int(inputs.get("timeout_seconds", 600))
            interval = float(inputs.get("poll_interval_seconds", 5))
            result = None
            while time.time() < deadline:
                time.sleep(interval)
                status_resp = get_json(f"{_BASE}/videos/generations/{gen_id}", headers=headers, timeout=60)
                task_status = (status_resp or {}).get("task_status")
                if task_status in _DONE:
                    if task_status != "SUCCESS":
                        return ToolResult(success=False, error=f"生成失败: {str(status_resp)[:300]}")
                    result = status_resp
                    break
            if result is None:
                return ToolResult(success=False, error=f"轮询超时 gen_id={gen_id}")
            video_result = (result.get("video_result") or [{}])[0]
            video_url = video_result.get("url")
        except (HttpError, KeyError) as exc:
            return ToolResult(success=False, error=str(exc))

        if not video_url:
            return ToolResult(success=False, error=f"任务完成但无 url: {str(result)[:300]}")
        local = None
        if inputs.get("output_path"):
            try:
                from montage.tools.downloader import download_file

                local = str(download_file(video_url, inputs["output_path"], timeout=300))
            except Exception as exc:  # noqa: BLE001
                local = None
        return ToolResult(
            success=True,
            data={"video_url": video_url, "task_id": gen_id, "output": local, "model": inputs.get("model")},
            meta={"provider": "zhipu", "model": inputs.get("model")},
            cost_usd=self.estimate_cost(inputs),
        )
