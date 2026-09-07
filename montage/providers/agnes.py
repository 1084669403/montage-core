"""agnes — Agnes 多模态适配器（图 / 视频；无独立 TTS）。

图片：POST {base}/images/generations
  - 文生 agnes-image-2.1-flash（必填 size 档位 + ratio）
  - 编辑/参考 agnes-image-2.0-flash；参考图放 extra_body.image
  - response_format 只放 extra_body；要进视频必须拿公网 url

视频：POST {base}/videos，再 GET {origin}/agnesapi?video_id=
  - 默认 agnes-video-2.5-flash（中国站）：seconds 为字符串 "4"–"12"；mode 互斥；
    轮询带 model_name=agnes-video-2.5-flash；禁止 negative_prompt / 宽高帧率 / videos[]
  - 回滚 agnes-video-v2.0：num_frames+frame_rate；extra_body.image + mode=keyframes

配音：Agnes 无独立 TTS。AgnesAudio 标记 UNAVAILABLE。

密钥：AGNES_API_KEY 或 AGNES_CN_API_KEY；默认 Base 中国站。
AGNES_API_BASE_URL / AGNES_BASE_URL 可覆盖；只有显式 apihub 才走国际站。
"""

from __future__ import annotations

import base64
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from montage.providers.http import HttpError, get_json, post_json
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_DEFAULT_BASE = "https://apihub.agnes-ai.com/v1"
_CN_BASE = "https://api.agnes-ai.cn/v1"

_T2I_MODEL = "agnes-image-2.1-flash"
_EDIT_MODEL = "agnes-image-2.0-flash"
_VIDEO_MODEL = "agnes-video-2.5-flash"
_VIDEO_MODEL_V20 = "agnes-video-v2.0"
_VIDEO_MODEL_V25 = "agnes-video-2.5-flash"
_MAX_REF_IMAGES = 5
_PROMPT_MAX = 3000

_SIZE_TIERS = ["1K", "2K", "3K", "4K"]
_RATIOS = ["1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9"]
_V25_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
_V20_WH = {
    "21:9": (1680, 720),
    "16:9": (1280, 720),
    "4:3": (960, 720),
    "1:1": (720, 720),
    "3:4": (720, 960),
    "9:16": (720, 1280),
    "3:2": (1152, 768),
    "2:3": (768, 1152),
}
_DONE = frozenset({"completed", "succeed", "success", "done"})
_RUNNING = frozenset({"queued", "in_progress", "processing", "pending", "running"})
_FAILED = frozenset({"failed", "error", "cancelled", "canceled"})


def agnes_credentials() -> tuple[str, str]:
    """返回 (api_key, base_url)；未配置时抛 ValueError。默认中国站。"""
    key = os.environ.get("AGNES_CN_API_KEY") or os.environ.get("AGNES_API_KEY")
    if not key:
        raise ValueError("缺少 AGNES_API_KEY 或 AGNES_CN_API_KEY")
    base = os.environ.get("AGNES_API_BASE_URL") or os.environ.get("AGNES_BASE_URL")
    if not base:
        base = _CN_BASE
    return key, base.rstrip("/")


def api_origin(base: str) -> str:
    """轮询 /agnesapi 用的主机根（去掉末尾 /v1）。"""
    root = base.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    return root.rstrip("/")


def _headers() -> dict[str, str]:
    key, _ = agnes_credentials()
    return {"Authorization": f"Bearer {key}"}


def _save_b64(b64: str, output_path: str | None, hint: str) -> str | None:
    """把 base64 输出落盘，返回本地路径；无 output_path 返回 None。"""
    if not output_path:
        return None
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    path.write_bytes(base64.b64decode(b64))
    return str(path)


def _download(url: str, output_path: str) -> str:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "montage-core/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
        path.write_bytes(resp.read())
    return str(path)


def _clip_prompt(prompt: str, *, fallback: bool = False) -> tuple[str, str]:
    """返回 (prompt, error)。超 3000 且未 fallback 时 error 非空，不截断。"""
    from lib.shot_prompt_builder import apply_agnes_prompt_limit

    text = str(prompt or "")
    trimmed, over = apply_agnes_prompt_limit(text, fallback=fallback)
    if over:
        return text, f"prompt 超过 {_PROMPT_MAX} 字"
    return trimmed, ""


def _is_v25(model: str) -> bool:
    """2.5 家族（含 flash）。endswith('video-2.5') 认不出 *-flash。"""
    return "video-2.5" in str(model or "").strip()


def duration_to_frames(seconds: float) -> tuple[int, int]:
    """语义秒数 → (num_frames, frame_rate=24)，满足 8n+1 且 ≤441。"""
    fps = 24
    sec = float(seconds or 5)
    if sec <= 0:
        return 121, fps
    if sec > 18:
        return 441, fps
    if 7.0 <= sec < 9.0:
        return 193, fps
    targets = ((3.0, 81), (5.0, 121), (10.0, 241), (18.0, 441))
    best = min(targets, key=lambda item: (abs(item[0] - sec), -item[0]))
    return best[1], fps


def _status_of(resp: dict[str, Any]) -> str:
    return str(resp.get("status") or resp.get("task_status") or "").strip().lower()


def video_error(resp: Any) -> str | None:
    """失败态文案；进行中或成功返回 None。"""
    if not isinstance(resp, dict):
        return None
    status = _status_of(resp)
    err = resp.get("error")
    if status in _FAILED or err:
        if isinstance(err, dict):
            return str(err.get("message") or err)
        if err:
            return str(err)
        return status or "failed"
    return None


def parse_video_result(resp: Any) -> str | None:
    """仅当 status 为完成态且 URL 非空时返回视频地址。"""
    if not isinstance(resp, dict):
        return None
    if video_error(resp):
        return None
    status = _status_of(resp)
    if status not in _DONE:
        return None

    meta = resp.get("metadata") if isinstance(resp.get("metadata"), dict) else {}
    data = resp.get("data")
    nested: dict[str, Any] = data if isinstance(data, dict) else {}
    row = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else {}
    candidates = [
        meta.get("url"),
        resp.get("url"),
        resp.get("video_url"),
        nested.get("url"),
        nested.get("video_url"),
        row.get("url"),
        row.get("video_url"),
    ]
    for raw in candidates:
        if isinstance(raw, str) and raw.startswith(("http://", "https://")):
            return raw
    return None


def _http_urls(values: Any) -> list[str]:
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for item in values or []:
        text = str(item or "").strip()
        if text.startswith(("http://", "https://")):
            out.append(text)
    return out


def attach_reference_placeholders(
    prompt: str,
    *,
    images: list[str] | None = None,
    audios: list[str] | None = None,
    videos: list[Any] | None = None,
) -> str:
    """Flash reference：补官方 <Picture N> / <Audio N>。videos 忽略（接口不支持）。"""
    del videos
    text = str(prompt or "")
    tags: list[str] = []
    for i, _ in enumerate(images or [], 1):
        tag = f"<Picture {i}>"
        if tag not in text:
            tags.append(tag)
    for i, _ in enumerate(audios or [], 1):
        tag = f"<Audio {i}>"
        if tag not in text:
            tags.append(tag)
    if not tags:
        return text
    return text.rstrip() + "\n" + " ".join(tags)


def want_video_v20(model: str | None = None) -> bool:
    """显式回滚 2.0 时为 True；默认 2.5 Flash。"""
    name = str(model or os.environ.get("AGNES_VIDEO_MODEL") or "").strip()
    return name in (_VIDEO_MODEL_V20, "agnes-video-2.0") or name.endswith("v2.0")


def resolve_video_model(model: str | None = None) -> str:
    """2.0 回滚除外，一律 agnes-video-2.5-flash。"""
    raw = str(model or os.environ.get("AGNES_VIDEO_MODEL") or _VIDEO_MODEL).strip()
    if want_video_v20(raw):
        return _VIDEO_MODEL_V20
    return _VIDEO_MODEL


def is_video_v25(model: str | None = None) -> bool:
    if want_video_v20(model):
        return False
    name = str(model or os.environ.get("AGNES_VIDEO_MODEL") or _VIDEO_MODEL)
    return _is_v25(name)


class AgnesImage(BaseTool):
    """文本/参考图 → 图片。操作：text_to_image / image_edit / image_reference。

    n 是空壳（多候选请逐次调用）。图片不传 negative_prompt。
    RPM：免费档 1K=20 / 2K=10 / 3K=1 / 4K=1。size 必填（默认 2K）。
    """

    name = "agnes_image"
    version = "0.2.0"
    capability = "image_generation"
    provider = "agnes"
    runtime = ToolRuntime.API
    env_keys = ("AGNES_API_KEY", "AGNES_CN_API_KEY")
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "operation": {
                "type": "string",
                "enum": ["text_to_image", "image_edit", "image_reference"],
                "default": "text_to_image",
            },
            "reference_urls": {"type": "array", "items": {"type": "string"}},
            "size": {"type": "string", "enum": _SIZE_TIERS, "default": "2K"},
            "ratio": {"type": "string", "enum": _RATIOS},
            "resolution": {"type": "string", "description": "2.0 精确分辨率，如 1024x768"},
            "model": {"type": "string"},
            "output_path": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self.env_keys and any(
            os.environ.get(k) for k in self.env_keys
        ) else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        size = inputs.get("size", "2K")
        return {"1K": 0.01, "2K": 0.02, "3K": 0.03, "4K": 0.04}.get(size, 0.02)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            _, base = agnes_credentials()
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        operation = inputs.get("operation", "text_to_image")
        prompt = inputs.get("prompt")
        if not prompt:
            return ToolResult(success=False, error="'prompt' 必填")

        is_edit = operation in ("image_edit", "image_reference")
        model = inputs.get("model") or (_EDIT_MODEL if is_edit else _T2I_MODEL)
        payload: dict[str, Any] = {"model": model, "prompt": prompt}
        extra: dict[str, Any] = {"response_format": "url"}
        if is_edit:
            refs = inputs.get("reference_urls")
            if not refs:
                return ToolResult(success=False, error=f"{operation} 需要 reference_urls")
            extra["image"] = refs
            if inputs.get("resolution"):
                payload["size"] = inputs["resolution"]
            else:
                payload["size"] = inputs.get("size") or "1024x768"
        else:
            payload["size"] = inputs.get("size") or "2K"
            if inputs.get("ratio"):
                payload["ratio"] = inputs["ratio"]
            if inputs.get("resolution"):
                payload["resolution"] = inputs["resolution"]
        payload["extra_body"] = extra

        try:
            data = post_json(f"{base}/images/generations", payload, headers=_headers(), timeout=300)
        except HttpError as exc:
            return ToolResult(success=False, error=str(exc))

        items = (data or {}).get("data") or []
        if not items:
            return ToolResult(success=False, error=f"响应无 data: {str(data)[:300]}")
        first = items[0]
        url = first.get("url")
        b64 = first.get("b64_json")
        local = _save_b64(b64, inputs.get("output_path"), "image") if b64 else None
        if not local and url and inputs.get("output_path"):
            try:
                local = _download(str(url), str(inputs["output_path"]))
            except (OSError, urllib.error.URLError) as exc:
                return ToolResult(success=False, error=f"下载图片失败: {exc}")
        return ToolResult(
            success=True,
            data={"url": url, "local_path": local, "model": model, "operation": operation},
            meta={"provider": "agnes", "model": model},
            cost_usd=self.estimate_cost(inputs),
        )


class AgnesVideo(BaseTool):
    """文本/图 → 视频。默认 2.5 Flash；显式 AGNES_VIDEO_MODEL=agnes-video-v2.0 回滚。"""

    name = "agnes_video"
    version = "0.2.0"
    capability = "video_generation"
    provider = "agnes"
    runtime = ToolRuntime.API
    env_keys = ("AGNES_API_KEY", "AGNES_CN_API_KEY")
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "maxLength": 3000},
            "seconds": {"type": "number"},
            "image_url": {"type": "string"},
            "image_urls": {"type": "array", "items": {"type": "string"}},
            "last_frame_url": {"type": "string"},
            "first_frame": {"type": "string"},
            "last_frame": {"type": "string"},
            "images": {"type": "array", "items": {"type": "string"}},
            "audios": {"type": "array", "items": {"type": "string"}},
            "videos": {"type": "array"},
            "mode": {"type": "string"},
            "aspect_ratio": {"type": "string"},
            "ratio": {"type": "string"},
            "negative_prompt": {"type": "string"},
            "model": {"type": "string"},
            "output_path": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if any(os.environ.get(k) for k in self.env_keys) else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        model = resolve_video_model(inputs.get("model"))
        sec = float(inputs.get("seconds") or inputs.get("duration") or 5)
        if _is_v25(model):
            sec = max(4, min(12, int(round(sec))))
            # 刊例 ¥0.15/s；Flash 现价促销 ¥0。预算门禁禁止报 0。
            return round(sec * 0.021, 4)
        return 0.2

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            _, base = agnes_credentials()
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        model = resolve_video_model(inputs.get("model"))
        prompt, err = _clip_prompt(
            inputs.get("prompt") or "",
            fallback=bool(inputs.get("prompt_fallback")),
        )
        if err:
            return ToolResult(success=False, error=err, data={"prompt_too_long": True})
        if not prompt:
            return ToolResult(success=False, error="'prompt' 必填")
        payload = (
            _payload_v25(inputs, model, prompt)
            if _is_v25(model)
            else _payload_v20(inputs, model, prompt)
        )
        headers = _headers()
        try:
            created = post_json(f"{base}/videos", payload, headers=headers, timeout=600)
        except HttpError as exc:
            return ToolResult(success=False, error=str(exc))
        if not isinstance(created, dict):
            return ToolResult(success=False, error=f"创建任务响应异常: {str(created)[:300]}")
        fail = video_error(created)
        if fail:
            return ToolResult(success=False, error=fail)
        url = parse_video_result(created)
        video_id = str(created.get("video_id") or created.get("id") or created.get("task_id") or "")
        if not url:
            if not video_id:
                return ToolResult(success=False, error=f"响应无 video_id: {str(created)[:300]}")
            url, err = _poll_video(
                base=base,
                video_id=video_id,
                headers=headers,
                model=model,
            )
            if err:
                return ToolResult(success=False, error=err)
        if not url:
            return ToolResult(success=False, error="轮询结束仍无视频 URL")
        local = None
        out = inputs.get("output_path")
        if out:
            try:
                local = _download(url, str(out))
            except (OSError, urllib.error.URLError) as exc:
                return ToolResult(success=False, error=f"下载视频失败: {exc}")
        return ToolResult(
            success=True,
            data={"url": url, "local_path": local, "model": model, "id": video_id},
            meta={"provider": "agnes", "model": model},
            cost_usd=self.estimate_cost(inputs),
        )


def _payload_v20(inputs: dict[str, Any], model: str, prompt: str) -> dict[str, Any]:
    from lib.shot_prompt_builder import default_english_negative_prompt

    seconds = float(inputs.get("seconds") or inputs.get("duration") or 5)
    frames, fps = duration_to_frames(seconds)
    ratio = str(inputs.get("aspect_ratio") or inputs.get("ratio") or "16:9")
    width, height = _V20_WH.get(ratio, (1280, 720))
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "num_frames": int(inputs.get("num_frames") or frames),
        "frame_rate": int(inputs.get("frame_rate") or fps),
        "width": int(inputs.get("width") or width),
        "height": int(inputs.get("height") or height),
        "negative_prompt": str(inputs.get("negative_prompt") or default_english_negative_prompt()),
    }
    first = (inputs.get("image") or inputs.get("image_url") or inputs.get("first_frame") or "")
    last = str(inputs.get("last_frame_url") or inputs.get("last_frame") or "")
    urls = _http_urls(inputs.get("image_urls") or inputs.get("images"))
    extra_in = inputs.get("extra_body") if isinstance(inputs.get("extra_body"), dict) else {}
    keyframes = _http_urls(extra_in.get("image"))
    if not keyframes and last:
        head = str(first) if str(first).startswith("http") else (urls[0] if urls else "")
        if head:
            keyframes = [head, last]
        elif last:
            keyframes = [last]
    elif not keyframes and len(urls) >= 2:
        keyframes = urls
    if keyframes:
        extra = dict(extra_in)
        extra["image"] = keyframes
        extra["mode"] = "keyframes"
        payload["extra_body"] = extra
    elif str(first).startswith("http"):
        payload["image"] = str(first)
    elif urls:
        payload["image"] = urls[0]
    return payload


def _payload_v25(inputs: dict[str, Any], model: str, prompt: str) -> dict[str, Any]:
    seconds = float(inputs.get("seconds") or inputs.get("duration") or 5)
    seconds_i = max(4, min(12, int(round(seconds))))
    ratio = str(inputs.get("aspect_ratio") or inputs.get("ratio") or "16:9")
    if ratio not in _V25_RATIOS:
        ratio = "16:9"
    first = str(inputs.get("first_frame") or inputs.get("image_url") or inputs.get("image") or "")
    last = str(inputs.get("last_frame") or inputs.get("last_frame_url") or "")
    images = _http_urls(inputs.get("images") or inputs.get("image_urls"))[:_MAX_REF_IMAGES]
    audios = _http_urls(inputs.get("audios"))
    # Flash 不支持 videos[]。有参考图/音频才走 reference，丢掉 first/last。
    if audios or images:
        mode = "reference"
        first = last = ""
    elif first.startswith("http") or last.startswith("http"):
        mode = "keyframe"
    else:
        mode = "text"
    if mode == "reference":
        prompt = attach_reference_placeholders(prompt, images=images, audios=audios)
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "mode": mode,
        "seconds": str(seconds_i),
        "size": "720P",
        "aspect_ratio": ratio,
        "n": 1,
    }
    raw_seed = inputs.get("seed")
    if raw_seed not in (None, ""):
        try:
            payload["seed"] = int(raw_seed)
        except (TypeError, ValueError):
            pass
    if mode == "keyframe":
        if first.startswith("http"):
            payload["first_frame"] = first
        if last.startswith("http"):
            payload["last_frame"] = last
    elif mode == "reference":
        if images:
            payload["images"] = images
        if audios:
            payload["audios"] = audios
    return payload


def _poll_video(
    *,
    base: str,
    video_id: str,
    headers: dict[str, str],
    model: str,
    timeout: float = 600,
    interval: float = 1.5,
) -> tuple[str | None, str | None]:
    origin = api_origin(base)
    deadline = time.time() + timeout
    delay = interval
    while time.time() < deadline:
        time.sleep(delay)
        try:
            params: dict[str, Any] = {"video_id": video_id}
            if _is_v25(model):
                params["model_name"] = model
            resp = get_json(
                f"{origin}/agnesapi",
                headers=headers,
                timeout=60,
                params=params,
            )
        except HttpError as exc:
            if exc.status == 429:
                delay = min(delay * 2, 30)
                continue
            return None, str(exc)
        fail = video_error(resp)
        if fail:
            return None, fail
        url = parse_video_result(resp)
        if url:
            return url, None
        delay = interval
    return None, f"轮询超时（>{int(timeout)}s）video_id={video_id}"


class AgnesAudio(BaseTool):
    """Agnes 无独立 TTS；音频随视频提示词生成（audio_source=agnes_prompt）。"""

    name = "agnes_audio"
    version = "0.2.0"
    capability = "tts"
    provider = "agnes"
    runtime = ToolRuntime.API
    env_keys = ("AGNES_API_KEY", "AGNES_CN_API_KEY")
    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "voice": {"type": "string"},
            "model": {"type": "string"},
            "output_path": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        return ToolResult(
            success=False,
            error=(
                "Agnes 无独立 TTS 通道，音频随视频从提示词生成（audio_source=agnes_prompt），"
                "请改用 edge_tts/doubao_tts/piper_tts"
            ),
        )
