"""hunyuan_video — 腾讯混元视频生成（自创实现，契约待联调）。

契约（腾讯混元开放 API，以官方文档为准）：
- 提交：POST https://api.hunyuan.cloud.tencent.com/v1/videos/generations
  headers: Authorization: Bearer <key>
  body: {model: "hunyuan-video", prompt, image_url(可选图生视频)}
  响应：{id}
- 轮询：GET .../videos/generations/{id} → task_status=SUCCESS → video_result[].url

密钥：HUNYUAN_API_KEY。
"""

from __future__ import annotations

import os
import time
from typing import Any

from montage.providers.http import HttpError, get_json, post_json
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_BASE = "https://api.hunyuan.cloud.tencent.com/v1"
_DONE = {"SUCCESS", "FAIL", "CANCEL"}


class HunyuanVideo(BaseTool):
    """腾讯混元文生视频/图生视频（异步）。"""

    name = "hunyuan_video"
    version = "0.1.0"
    capability = "video_generation"
    provider = "tencent"
    runtime = ToolRuntime.API
    env_keys = ("HUNYUAN_API_KEY",)
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "model": {"type": "string", "default": "hunyuan-video"},
            "image_url": {"type": "string", "description": "图生视频参考图 URL"},
            "output_path": {"type": "string"},
            "timeout_seconds": {"type": "number", "default": 600},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("HUNYUAN_API_KEY") else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.25

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        prompt = inputs.get("prompt")
        if not prompt:
            return ToolResult(success=False, error="'prompt' 必填")
        key = os.environ.get("HUNYUAN_API_KEY")
        if not key:
            return ToolResult(success=False, error="缺少 HUNYUAN_API_KEY")
        headers = {"Authorization": f"Bearer {key}"}
        payload: dict[str, Any] = {
            "model": inputs.get("model", "hunyuan-video"),
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
            result = None
            while time.time() < deadline:
                time.sleep(5)
                status_resp = get_json(f"{_BASE}/videos/generations/{gen_id}", headers=headers, timeout=60)
                if (status_resp or {}).get("task_status") in _DONE:
                    result = status_resp
                    break
            if result is None:
                return ToolResult(success=False, error=f"轮询超时 id={gen_id}")
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
            except Exception:  # noqa: BLE001
                local = None
        return ToolResult(
            success=True,
            data={"video_url": video_url, "task_id": gen_id, "output": local, "model": inputs.get("model")},
            meta={"provider": "tencent", "contract": "pending-verify"},
            cost_usd=self.estimate_cost(inputs),
        )
