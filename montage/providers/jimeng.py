"""jimeng — 火山引擎即梦（视觉智能平台）图片/视频生成适配器。

契约（经核对旧实现确认）：
- 提交：Action=CVSync2AsyncSubmitTask → data.task_id
- 轮询：Action=CVSync2AsyncGetResult，body {req_key, task_id, req_json} → data.status=done
- 图片：req_key=jimeng_t2i_v40；结果 data.image_urls 或 binary_data_base64
- 视频：req_key 见 _REQ_KEYS；帧数 5s→121 / ≥6s→241；结果 data.video_url（1 小时 TTL，须立即下载）
- 本地参考图：JPEG/PNG ≤4.7MB、短边 ≥320、长边 ≤4096、长宽比 ≤3，base64 传输

密钥：VOLC_ACCESSKEY / VOLC_SECRETKEY。本文件为全新原创代码。
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from montage.providers._volc_signer import JimengApiError, post_action
from montage.providers.http import HttpError, get_json
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

# --- req_key 表 ----------------------------------------------------------
REQ_KEY_IMAGE = "jimeng_t2i_v40"
REQ_KEY_PRO = "jimeng_ti2v_v30_pro"  # 3.0 Pro（图生视频/文生视频，1080P）
REQ_KEY_T2V_720 = "jimeng_t2v_v30"
REQ_KEY_T2V_1080 = "jimeng_t2v_v30_1080p"
REQ_KEY_I2V_FIRST_720 = "jimeng_i2v_first_v30"
REQ_KEY_I2V_FIRST_1080 = "jimeng_i2v_first_v30_1080"
REQ_KEY_I2V_TAIL_720 = "jimeng_i2v_first_tail_v30"
REQ_KEY_I2V_TAIL_1080 = "jimeng_i2v_first_tail_v30_1080"
REQ_KEY_RECAMERA = "jimeng_i2v_recamera_v30"

# 官方刊例价（元/秒）
COST_CNY_PER_SEC = {
    REQ_KEY_PRO: 1.00,
    REQ_KEY_T2V_1080: 0.63,
    REQ_KEY_I2V_FIRST_1080: 0.63,
    REQ_KEY_I2V_TAIL_1080: 0.63,
    REQ_KEY_T2V_720: 0.28,
    REQ_KEY_I2V_FIRST_720: 0.28,
    REQ_KEY_I2V_TAIL_720: 0.28,
    REQ_KEY_RECAMERA: 0.28,
}

MAX_IMAGE_BYTES = int(4.7 * 1024 * 1024)
MIN_SIDE, MAX_SIDE, MAX_ASPECT = 320, 4096, 3.0


def _credentials() -> tuple[str, str]:
    ak, sk = os.environ.get("VOLC_ACCESSKEY"), os.environ.get("VOLC_SECRETKEY")
    if not ak or not sk:
        raise JimengApiError("缺少 VOLC_ACCESSKEY 或 VOLC_SECRETKEY（火山引擎 IAM 密钥）")
    return ak, sk


def duration_to_frames(duration: float | int | None) -> int:
    """5s → 121 帧；≥6s → 241 帧（即梦仅支持 5/10 秒两种时长）。"""
    return 121 if (duration is None or float(duration) <= 5) else 241


def encode_image_file(path: str | Path) -> str:
    """本地图片 → base64（校验并压缩到即梦限制）。依赖 Pillow，缺失时报清晰错误。"""
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        raise JimengApiError("本地图片编码需要 Pillow：pip install pillow（或用 image_urls 传 URL）") from None
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    image = Image.open(path)
    image.load()
    w, h = image.size
    shortest, longest = min(w, h), max(w, h)
    if shortest < MIN_SIDE or longest > MAX_SIDE or longest / shortest > MAX_ASPECT:
        raise JimengApiError(
            f"图片尺寸不合规: {w}x{h}（需短边≥{MIN_SIDE} 长边≤{MAX_SIDE} 长宽比≤{MAX_ASPECT}）"
        )
    fmt = (image.format or "").upper()
    buf = io.BytesIO()
    if fmt in ("JPEG", "PNG") and path.stat().st_size <= MAX_IMAGE_BYTES:
        buf.write(path.read_bytes())
    else:
        rgb = image.convert("RGB")
        rgb.save(buf, format="JPEG", quality=92)
        if buf.getvalue() and len(buf.getvalue()) > MAX_IMAGE_BYTES:
            buf = io.BytesIO()
            rgb.save(buf, format="JPEG", quality=80)
    data = buf.getvalue()
    if len(data) > MAX_IMAGE_BYTES:
        raise JimengApiError(f"图片超过 {MAX_IMAGE_BYTES} 字节限制")
    return base64.b64encode(data).decode("ascii")


def _collect_images(inputs: dict[str, Any]) -> dict[str, list[str]]:
    """返回 {"paths": [...], "urls": [...]}，本地路径优先。

    首帧图（image_path/image_url 等）在前；尾帧图（last_frame_path/url）追加在后——
    即梦 ``i2v_first_tail``（首尾帧约束）模式按 [首帧, 尾帧] 顺序提交，是防形变
    的最强一致性模式。
    """
    paths: list[str] = []
    urls: list[str] = []
    for key in ("image_path", "first_frame_path", "reference_image_path"):
        if inputs.get(key):
            paths.append(str(inputs[key]))
            break
    for key in ("image_url", "first_frame_url", "reference_image_url"):
        if inputs.get(key) and not paths:
            urls.append(str(inputs[key]))
            break
    if not paths and not urls:
        urls.extend(str(u) for u in (inputs.get("image_urls") or []) if u)
        if not urls:
            paths.extend(str(p) for p in (inputs.get("image_paths") or []) if p)
    # 尾帧追加（i2v first_tail：首尾两图约束）
    if inputs.get("last_frame_path"):
        paths.append(str(inputs["last_frame_path"]))
    elif inputs.get("last_frame_url"):
        urls.append(str(inputs["last_frame_url"]))
    return {"paths": paths, "urls": urls}


def _images_to_payload(inputs: dict[str, Any]) -> dict[str, Any]:
    """binary_data_base64 或 image_urls（二选一）。"""
    collected = _collect_images(inputs)
    if collected["paths"]:
        return {"binary_data_base64": [encode_image_file(p) for p in collected["paths"]]}
    if collected["urls"]:
        return {"image_urls": collected["urls"][:10]}
    return {}


def _submit(payload: dict[str, Any]) -> str:
    ak, sk = _credentials()
    data = post_action("CVSync2AsyncSubmitTask", payload, ak=ak, sk=sk)
    task_id = (data.get("data") or {}).get("task_id")
    if not task_id:
        raise JimengApiError(f"提交无 task_id: {str(data)[:300]}")
    return str(task_id)


def _poll(task_id: str, req_key: str, *, ak: str, sk: str, poll_interval: float, timeout: int, req_json: dict) -> dict:
    body: dict[str, Any] = {"req_key": req_key, "task_id": task_id}
    if req_json:
        body["req_json"] = json.dumps(req_json, ensure_ascii=False)
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        data = post_action("CVSync2AsyncGetResult", body, ak=ak, sk=sk)
        inner = data.get("data") or {}
        if inner.get("status") == "done" or inner.get("video_url") or inner.get("image_urls") or inner.get("binary_data_base64"):
            return inner
        if inner.get("status") in ("not_found", "expired"):
            raise JimengApiError(f"任务无效 status={inner.get('status')}")
    raise JimengApiError(f"轮询超时（>{timeout}s）task_id={task_id}")


def _download(url: str, output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = get_json(url, timeout=120)
        if isinstance(data, dict) and "_raw" in data:
            target.write_bytes(data["_raw"].encode("utf-8", errors="replace"))
        else:
            raise JimengApiError("非二进制响应")
    except HttpError:
        # get_json 解析失败通常是二进制媒体——退回原始字节下载
        import urllib.request

        with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
            target.write_bytes(resp.read())
    return target


class JimengImage(BaseTool):
    """文生图 / 参考图生图（jimeng_t2i_v40）。"""

    name = "jimeng_image"
    version = "0.1.0"
    capability = "image_generation"
    provider = "volcengine"
    runtime = ToolRuntime.API
    env_keys = ("VOLC_ACCESSKEY", "VOLC_SECRETKEY")
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "maxLength": 800},
            "ratio": {"type": "string", "default": "16:9"},
            "image_urls": {"type": "array", "items": {"type": "string"}, "description": "参考图 URL（≤10）"},
            "image_paths": {"type": "array", "items": {"type": "string"}, "description": "参考图本地路径"},
            "output_path": {"type": "string"},
            "poll_interval_seconds": {"type": "number", "default": 5},
            "timeout_seconds": {"type": "number", "default": 600},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self.env_keys and all(os.environ.get(k) for k in self.env_keys) else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.04  # 按张估算，人民币 0.3 元上下

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            ak, sk = _credentials()
            payload: dict[str, Any] = {
                "req_key": REQ_KEY_IMAGE,
                "prompt": inputs["prompt"],
                "aspect_ratio": inputs.get("ratio", "16:9"),
            }
            payload.update(_images_to_payload(inputs))
            task_id = _submit(payload)
            req_json = {"return_url": True, "logo_info": {"add_logo": False}}
            inner = _poll(
                task_id, REQ_KEY_IMAGE, ak=ak, sk=sk,
                poll_interval=float(inputs.get("poll_interval_seconds", 5)),
                timeout=int(inputs.get("timeout_seconds", 600)),
                req_json=req_json,
            )
            urls = inner.get("image_urls") or []
            if isinstance(urls, str):
                urls = [urls]
            output = None
            if urls and inputs.get("output_path"):
                output = str(_download(urls[0], inputs["output_path"]))
            return ToolResult(
                success=True,
                data={"url": urls[0] if urls else "", "urls": urls, "task_id": task_id, "output": output},
                meta={"provider": "volcengine", "model": REQ_KEY_IMAGE},
                cost_usd=self.estimate_cost(inputs),
            )
        except (JimengApiError, ValueError, FileNotFoundError) as exc:
            return ToolResult(success=False, error=str(exc))


class JimengVideo(BaseTool):
    """文生视频 / 图生视频（3.0 Pro 及 3.0 家族）。"""

    name = "jimeng_video"
    version = "0.1.0"
    capability = "video_generation"
    provider = "volcengine"
    runtime = ToolRuntime.API
    env_keys = ("VOLC_ACCESSKEY", "VOLC_SECRETKEY")
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "maxLength": 800},
            "operation": {"type": "string", "enum": ["text_to_video", "image_to_video"], "default": "text_to_video"},
            "video_model": {"type": "string", "enum": ["pro", "v30"], "default": "pro"},
            "resolution": {"type": "string", "enum": ["720p", "1080p"], "default": "1080p"},
            "seconds": {"type": "number", "default": 5, "description": "5 或 10 秒"},
            "image_path": {"type": "string"},
            "image_url": {"type": "string"},
            "last_frame_path": {"type": "string", "description": "尾帧图本地路径（首尾帧约束 i2v first_tail，防形变最强模式）"},
            "last_frame_url": {"type": "string", "description": "尾帧图 URL（同上）"},
            "aspect_ratio": {"type": "string", "default": "16:9"},
            "template_id": {"type": "string", "description": "recamera 运镜模板"},
            "seed": {"type": "integer", "default": -1, "description": "随机种子（-1=随机）；传入相同 seed 可复现，生成结果会返回实际 seed"},
            "output_path": {"type": "string"},
            "poll_interval_seconds": {"type": "number", "default": 5},
            "timeout_seconds": {"type": "number", "default": 600},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if self.env_keys and all(os.environ.get(k) for k in self.env_keys) else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        seconds = float(inputs.get("seconds", 5) or 5)
        req_key = self._pick_req_key(inputs)
        cny = COST_CNY_PER_SEC.get(req_key, 1.00) * max(seconds, 5.0)
        return round(cny / 7.2, 4)  # 元 → 美元粗略换算（≈7.2 CNY/USD）

    def _pick_req_key(self, inputs: dict[str, Any]) -> str:
        images = _collect_images(inputs)
        has_tail = bool(inputs.get("last_frame_path") or inputs.get("last_frame_url"))
        if has_tail:
            return REQ_KEY_I2V_TAIL_1080 if inputs.get("resolution") != "720p" else REQ_KEY_I2V_TAIL_720
        if inputs.get("template_id"):
            return REQ_KEY_RECAMERA
        if images["paths"] or images["urls"] or inputs.get("operation") == "image_to_video":
            return REQ_KEY_I2V_FIRST_1080 if inputs.get("resolution") != "720p" else REQ_KEY_I2V_FIRST_720
        return REQ_KEY_T2V_1080 if inputs.get("resolution") != "720p" else REQ_KEY_T2V_720

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        try:
            ak, sk = _credentials()
            req_key = self._pick_req_key(inputs)
            frames = duration_to_frames(inputs.get("seconds", 5))
            payload: dict[str, Any] = {
                "req_key": req_key,
                "prompt": inputs["prompt"],
                "frames": frames,
                "seed": int(inputs.get("seed", -1)),
            }
            images = _images_to_payload(inputs)
            if images:
                payload.update(images)
            else:
                payload["aspect_ratio"] = inputs.get("aspect_ratio", "16:9")
            if req_key == REQ_KEY_RECAMERA:
                payload["template_id"] = inputs["template_id"]
                payload["camera_strength"] = inputs.get("camera_strength", "medium")
                payload.pop("aspect_ratio", None)

            task_id = _submit(payload)
            req_json: dict[str, Any] = {"return_url": True}
            req_json["aigc_meta"] = {
                "content_producer": "montage-core",
                "producer_id": task_id,
                "content_propagator": "montage-core",
                "propagate_id": str(uuid.uuid4()),
            }
            inner = _poll(
                task_id, req_key, ak=ak, sk=sk,
                poll_interval=float(inputs.get("poll_interval_seconds", 5)),
                timeout=int(inputs.get("timeout_seconds", 600)),
                req_json=req_json,
            )
            video_url = inner.get("video_url")
            if not video_url:
                return ToolResult(success=False, error=f"任务完成但无 video_url: {str(inner)[:300]}")
            output = str(_download(video_url, inputs.get("output_path", f"jimeng_{task_id}.mp4")))
            seed_used = int(inputs.get("seed", -1))
            return ToolResult(
                success=True,
                data={
                    "provider": "volcengine",
                    "model": req_key,
                    "task_id": task_id,
                    "video_url": video_url,
                    "output": output,
                    "frames": frames,
                    "seed": seed_used,
                    "cost_cny": self.estimate_cost(inputs),
                },
                meta={"provider": "volcengine", "model": req_key},
                cost_usd=self.estimate_cost(inputs),
            )
        except (JimengApiError, ValueError, FileNotFoundError) as exc:
            return ToolResult(success=False, error=str(exc))
