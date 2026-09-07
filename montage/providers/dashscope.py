"""dashscope — 阿里云百炼（DashScope）适配器。

ASR 异步文件转写（契约确定）：
  POST  {base}/api/v1/services/audio/asr/transcriptions
        header: X-DashScope-Async: enable, Authorization: Bearer <key>
        body:   {"model": "qwen3-asr-flash-filetrans",
                 "input": {"file_urls": ["..."]}}
  →     {"output": {"task_id": "..."}}
  轮询  GET {base}/api/v1/tasks/{task_id} 直到 SUCCEEDED
  →     output.results[].transcripts[].sentences[].words[]（begin/end 毫秒 + text）

本文件为全新原创代码。
"""

from __future__ import annotations

import os
import time
from typing import Any

from montage.providers.http import HttpError, get_json, post_json
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_BASE = "https://dashscope.aliyuncs.com"
_ASR_MODEL = "qwen3-asr-flash-filetrans"

# 转写状态
_DONE = {"SUCCEEDED", "FAILED", "CANCELED"}


class DashscopeAsr(BaseTool):
    """词级时间戳 ASR（音频文件需可公网访问的 URL）。"""

    name = "dashscope_asr"
    version = "0.1.0"
    capability = "analysis"
    provider = "dashscope"
    runtime = ToolRuntime.API
    env_keys = ("DASHSCOPE_API_KEY",)
    input_schema = {
        "type": "object",
        "required": ["file_urls"],
        "properties": {
            "file_urls": {"type": "array", "items": {"type": "string"}},
            "model": {"type": "string", "default": _ASR_MODEL},
            "poll_interval_seconds": {"type": "number", "default": 5},
            "max_wait_seconds": {"type": "number", "default": 600},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("DASHSCOPE_API_KEY") else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.01  # 按时长计费，此处给下限

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        key = os.environ.get("DASHSCOPE_API_KEY")
        if not key:
            return ToolResult(success=False, error="缺少 DASHSCOPE_API_KEY")
        urls = inputs.get("file_urls")
        if not urls:
            return ToolResult(success=False, error="'file_urls' 必填（公网可访问的音频 URL）")
        headers = {"Authorization": f"Bearer {key}", "X-DashScope-Async": "enable"}
        payload = {
            "model": inputs.get("model", _ASR_MODEL),
            "input": {"file_urls": urls},
        }
        try:
            resp = post_json(f"{_BASE}/api/v1/services/audio/asr/transcriptions", payload, headers=headers, timeout=60)
        except HttpError as exc:
            return ToolResult(success=False, error=f"提交转写失败: {exc}")

        task_id = ((resp or {}).get("output") or {}).get("task_id")
        if not task_id:
            return ToolResult(success=False, error=f"响应无 task_id: {str(resp)[:300]}")

        # 轮询
        interval = float(inputs.get("poll_interval_seconds", 5))
        max_wait = float(inputs.get("max_wait_seconds", 600))
        deadline = time.time() + max_wait
        while time.time() < deadline:
            time.sleep(interval)
            try:
                status_resp = get_json(
                    f"{_BASE}/api/v1/tasks/{task_id}", headers={"Authorization": f"Bearer {key}"}, timeout=60
                )
            except HttpError as exc:
                return ToolResult(success=False, error=f"轮询失败: {exc}")
            output = (status_resp or {}).get("output") or {}
            state = output.get("task_status") or output.get("status")
            if state in _DONE:
                return self._finalize(state, status_resp, task_id)
        return ToolResult(success=False, error=f"轮询超时（>{max_wait}s）: task_id={task_id}")

    @staticmethod
    def _finalize(state: str, resp: dict, task_id: str) -> ToolResult:
        if state != "SUCCEEDED":
            return ToolResult(success=False, error=f"转写失败 state={state}: {str(resp)[:300]}")
        # 归一化词级时间戳：毫秒 → 秒
        sentences: list[dict[str, Any]] = []
        words_all: list[dict[str, Any]] = []
        for result in ((resp.get("output") or {}).get("results") or []):
            for transcript in (result.get("transcripts") or []):
                for sent in (transcript.get("sentences") or []):
                    words = [
                        {
                            "text": w.get("text", ""),
                            "start_seconds": (w.get("begin_time", 0) or 0) / 1000.0,
                            "end_seconds": (w.get("end_time", 0) or 0) / 1000.0,
                        }
                        for w in (sent.get("words") or [])
                    ]
                    words_all.extend(words)
                    sentences.append(
                        {
                            "text": sent.get("text", ""),
                            "start_seconds": (sent.get("begin_time", 0) or 0) / 1000.0,
                            "end_seconds": (sent.get("end_time", 0) or 0) / 1000.0,
                            "words": words,
                        }
                    )
        return ToolResult(
            success=True,
            data={
                "task_id": task_id,
                "sentences": sentences,
                "word_count": len(words_all),
                "full_text": "".join(s["text"] for s in sentences),
            },
            meta={"provider": "dashscope", "contract": "verified"},
        )
