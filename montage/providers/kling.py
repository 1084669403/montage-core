"""kling — 快手可灵 图/视频生成（自创实现）。

默认仍走 v1 text2video/image2video（Bearer KLING_API_KEY）。
有 KLING_API_SECRET 时改签 JWT（stdlib HMAC-SHA256）。
api_id=kling_omni_30 → POST {OPEN}/omni-video/kling-3.0-omni + GET /tasks；
api_id=kling_t2v_30 / kling_i2v_30 / kling_motion_30 同构新面。
api_id=kling_i2v_21_pro → /v1/videos/image2video（无首帧禁发）。
KlingImage → Image Omni（model 默认 kling-v3-omni，禁止 kling-v1；默认 2k）；Element/Voice 是内部函数，不注册 tool。
默认 execute 的 api_id 仍是 kling_v1（显式传入或 FORCE）；shot_runner 路由默认 Omni。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from montage.providers._kling_looksheet import (
    LOOK_SHEET_CELLS,
    crop_look_sheet,
    element_slots_from_crops,
    look_sheet_next_action,
    qc_look_sheet_cell,
)
from montage.providers.http import HttpError, get_json, post_json
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_DEFAULT_OPEN_BASE = "https://api-beijing.klingai.com"
_DEFAULT_MEDIA_MAX_BYTES = 10_000_000
# 兼容旧引用；运行时请用 _v1_base()（默认字符串与今天相同）。
_BASE = "https://api-beijing.klingai.com/v1"
_DONE = {"succeed", "succeeded", "failed", "canceled", "cancelled"}
_OK = {"succeed", "succeeded"}
_OPEN_DONE = {"succeeded", "failed"}
_OPEN_OK = {"succeeded"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

# 路径即模型（拼在 _open_base() 上，不要再拼 /v1/videos/）。execute 第 1 波才接线。
KLING_VIDEO_PATHS = {
    "kling_omni_30": "/omni-video/kling-3.0-omni",
    "kling_t2v_30": "/text-to-video/kling-3.0",
    "kling_i2v_30": "/image-to-video/kling-3.0",
    "kling_motion_30": "/motion-control/kling-3.0",
}
KLING_IMAGE_OMNI_PATH_DEFAULT = "/images/omni-image"
KLING_IMAGE_OMNI_MODEL_DEFAULT = "kling-v3-omni"  # 控制台名 Kling Image 3.0 Omni
_IMAGE_OMNI_O1 = frozenset({"klingimageo1", "klingo1", "imageo1", "o1"})
_IMAGE_OMNI_V3 = frozenset({
    "klingv3omni",
    "klingimage30omni",
    "kling30omni",
    "image30omni",
    "klingimageomni",
    "klingomniimage",
    "klingomni30",
    "omni30",
    "klingimageomni30",
})
KLING_ELEMENT_PATH = "/general/advanced-custom-elements"
KLING_VOICE_PATH = "/general/custom-voices"
# @image_N 只给 refer_image；first/last 不写 id（注入器第 3 波才改）。
KLING_OMNI_CITE_FRAMES = False


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def kling_jwt(ak: str, sk: str, *, now: int | None = None, ttl: int = 1800) -> str:
    """可灵开放平台 JWT（HS256）。now 仅供单测冻结时间。"""
    issued = int(now if now is not None else time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode("utf-8"))
    payload = _b64url(json.dumps(
        {"iss": ak, "exp": issued + ttl, "nbf": issued - 5},
        separators=(",", ":"),
    ).encode("utf-8"))
    sig = hmac.new(sk.encode("utf-8"), f"{header}.{payload}".encode("ascii"), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(sig)}"


def kling_authorization(*, now: int | None = None) -> str:
    key = os.environ.get("KLING_API_KEY")
    if not key:
        raise ValueError("缺少 KLING_API_KEY")
    secret = os.environ.get("KLING_API_SECRET")
    if secret:
        return "Bearer " + kling_jwt(key, secret, now=now)
    return "Bearer " + key


def _headers() -> dict[str, str]:
    return {"Authorization": kling_authorization()}


def _open_base() -> str:
    return str(os.environ.get("KLING_OPEN_BASE") or _DEFAULT_OPEN_BASE).rstrip("/")


def _v1_base() -> str:
    return f"{_open_base()}/v1"


def canonical_kling_image_omni_model(raw: str) -> str:
    """控制台「Kling Image 3.0 Omni」→ kling-v3-omni。密钥/乱填回默认，不发给 API。"""
    text = str(raw or "").strip()
    if not text:
        return KLING_IMAGE_OMNI_MODEL_DEFAULT
    compact = "".join(ch for ch in text.lower() if ch.isalnum())
    if compact in {"klingv1", "v1"}:
        return "kling-v1"
    if compact in _IMAGE_OMNI_O1:
        return "kling-image-o1"
    if compact in _IMAGE_OMNI_V3:
        return "kling-v3-omni"
    return KLING_IMAGE_OMNI_MODEL_DEFAULT


def kling_image_omni_model(inputs: dict[str, Any] | None = None) -> str:
    """静图默认 Kling Image 3.0 Omni（kling-v3-omni）。env / model_name 可覆盖到 o1。"""
    raw = ""
    if inputs:
        raw = str(inputs.get("model_name") or "").strip()
    if not raw:
        raw = str(os.environ.get("KLING_IMAGE_OMNI_MODEL") or "").strip()
    return canonical_kling_image_omni_model(raw)


def kling_image_omni_url() -> str:
    override = str(os.environ.get("KLING_IMAGE_OMNI_PATH") or "").strip()
    if override.startswith("http://") or override.startswith("https://"):
        return override.rstrip("/")
    if override.startswith("/"):
        if override.startswith("/omni-image"):
            return f"{_open_base()}{override}"
        return f"{_v1_base()}{override}"
    return f"{_v1_base()}{KLING_IMAGE_OMNI_PATH_DEFAULT}"


def kling_image_omni_poll_mode() -> str:
    mode = str(os.environ.get("KLING_IMAGE_OMNI_POLL") or "v1").strip().lower()
    return mode if mode in ("v1", "tasks") else "v1"


def kling_image_resolution(inputs: dict[str, Any] | None = None) -> str:
    raw = ""
    if inputs:
        raw = str(inputs.get("resolution") or "").strip().lower()
    if not raw:
        raw = str(os.environ.get("KLING_IMAGE_RESOLUTION") or "2k").strip().lower()
    if raw not in ("1k", "2k", "4k"):
        return "2k"
    return raw


def kling_media_max_bytes() -> int:
    raw = str(os.environ.get("KLING_MEDIA_MAX_BYTES") or "").strip()
    if raw.isdigit():
        return max(int(raw), 1)
    return _DEFAULT_MEDIA_MAX_BYTES


def kling_media_b64_format() -> str:
    val = str(os.environ.get("KLING_MEDIA_B64_FORMAT") or "raw").strip().lower()
    return "data_uri" if val == "data_uri" else "raw"


def kling_attempt_id(shot_id: Any, attempt: Any = 1) -> str | None:
    sid = str(shot_id or "").strip()
    if not sid:
        return None
    att = str(attempt if attempt is not None else "1").strip() or "1"
    return f"{sid}:{att}"


def _omit_blank_external_id(options: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(options or {})
    ext = out.get("external_task_id")
    if ext is None or str(ext).strip() == "":
        out.pop("external_task_id", None)
    return out


def _is_https_url(src: str) -> bool:
    return src.startswith("https://")


def _is_http_url(src: str) -> bool:
    return src.startswith("https://") or src.startswith("http://")


def _encode_image_b64(raw: bytes, *, mime: str) -> str:
    body = base64.b64encode(raw).decode("ascii")
    if kling_media_b64_format() == "data_uri":
        return f"data:{mime};base64,{body}"
    return body


def _jpeg_bytes_under_limit(src: Path, max_bytes: int) -> bytes | None:
    """ffmpeg 转 jpeg；单测可 monkeypatch。无 ffmpeg 则 None。"""
    from montage.compose.ffmpeg_engine import check_ffmpeg

    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        return None
    last: bytes | None = None
    for quality in (8, 12, 16, 24, 31):
        fd, dest = tempfile.mkstemp(suffix=".jpg")
        os.close(fd)
        out = Path(dest)
        try:
            proc = subprocess.run(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(src), "-frames:v", "1", "-q:v", str(quality), str(out)],
                capture_output=True, timeout=60, check=False,
            )
            if proc.returncode != 0 or not out.is_file():
                continue
            blob = out.read_bytes()
            last = blob
            if len(blob) <= max_bytes:
                return blob
        except (OSError, subprocess.TimeoutExpired):
            return last
        finally:
            try:
                out.unlink(missing_ok=True)
            except OSError:
                pass
    return last if last and len(last) <= max_bytes else None


def pack_kling_media(
    src: str,
    *,
    kind: str = "image",
    allow_base64: bool = True,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """装箱 contents.url：https 原样；本地图转官方允许的 base64。不造上传器。"""
    notes: list[str] = []
    text = str(src or "").strip()
    cap = int(max_bytes) if max_bytes is not None else kling_media_max_bytes()
    kind = str(kind or "image").strip().lower() or "image"
    if not text:
        return {"ok": False, "url": "", "notes": ["装箱源为空"], "via": ""}
    if kind in ("video", "audio"):
        if _is_https_url(text):
            return {"ok": True, "url": text, "notes": notes, "via": "https"}
        notes.append("可灵音视频参考只收 https，不装箱本地文件")
        return {"ok": False, "url": "", "notes": notes, "via": ""}
    if _is_http_url(text):
        via = "https" if _is_https_url(text) else "http"
        return {"ok": True, "url": text, "notes": notes, "via": via}
    if not allow_base64:
        notes.append("不允许 base64 且源不是 URL")
        return {"ok": False, "url": "", "notes": notes, "via": ""}
    path = Path(text)
    if not path.is_file():
        notes.append(f"本地文件不存在: {text}")
        return {"ok": False, "url": "", "notes": notes, "via": ""}
    suffix = path.suffix.lower()
    if suffix not in _IMAGE_SUFFIXES:
        notes.append(f"不支持的图片后缀: {suffix or '(无)'}")
        return {"ok": False, "url": "", "notes": notes, "via": ""}
    raw = path.read_bytes()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    if suffix == ".webp":
        mime = "image/webp"
    if len(raw) > cap:
        compressed = _jpeg_bytes_under_limit(path, cap)
        if not compressed:
            notes.append(f"图片超过 {cap} 字节且无法压到上限以下")
            return {"ok": False, "url": "", "notes": notes, "via": ""}
        notes.append("超上限已转 jpeg 再编码")
        raw = compressed
        mime = "image/jpeg"
    return {
        "ok": True,
        "url": _encode_image_b64(raw, mime=mime),
        "notes": notes,
        "via": "base64",
    }


def _omni_task_id(resp: dict[str, Any] | None) -> str:
    """新面创建返回 data.id。禁止把旧 task_id 当成新面 id。"""
    if not isinstance(resp, dict):
        return ""
    data = resp.get("data")
    if isinstance(data, dict) and data.get("id"):
        return str(data["id"])
    if isinstance(data, list) and data:
        first = data[0]
        if isinstance(first, dict) and first.get("id"):
            return str(first["id"])
    return ""


def _open_task_item(resp: dict[str, Any] | None, task_id: str) -> dict[str, Any]:
    if not isinstance(resp, dict):
        return {}
    data = resp.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        want = str(task_id or "")
        for item in data:
            if isinstance(item, dict) and str(item.get("id") or "") == want:
                return item
        if len(data) == 1 and isinstance(data[0], dict):
            return data[0]
    return {}


def _open_task_video_url(task: dict[str, Any] | None) -> str:
    if not isinstance(task, dict):
        return ""
    for item in task.get("outputs") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("type") or "").lower() != "video":
            continue
        url = str(item.get("url") or "").strip()
        if url:
            return url
    return ""


def _open_task_image_urls(task: dict[str, Any] | None) -> list[str]:
    """tasks 面静图 URL。与视频抽取分函数，禁止混 type。"""
    if not isinstance(task, dict):
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for item in task.get("outputs") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").lower()
        if kind not in ("image", "images"):
            continue
        url = str(item.get("url") or "").strip()
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _poll_open_tasks(task_id: str, timeout: int, interval: float) -> dict[str, Any]:
    url = f"{_open_base()}/tasks"
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(max(interval, 0.0))
        resp = get_json(url, headers=_headers(), timeout=60, params={"task_ids": task_id})
        item = _open_task_item(resp if isinstance(resp, dict) else {}, task_id)
        status = str(item.get("status") or "").lower()
        if status in _OPEN_DONE:
            if status not in _OPEN_OK:
                raise HttpError(0, url, f"任务状态: {status}: {str(item)[:200]}")
            return item
    raise HttpError(0, url, f"轮询超时（>{timeout}s）")


def _task_id(resp: dict[str, Any] | None) -> str:
    if not isinstance(resp, dict):
        return ""
    if resp.get("task_id"):
        return str(resp["task_id"])
    data = resp.get("data")
    if isinstance(data, dict) and data.get("task_id"):
        return str(data["task_id"])
    if resp.get("id"):
        return str(resp["id"])
    return ""


def _task_status(resp: dict[str, Any] | None) -> str:
    if not isinstance(resp, dict):
        return ""
    if resp.get("task_status"):
        return str(resp["task_status"])
    data = resp.get("data")
    if isinstance(data, dict) and data.get("task_status"):
        return str(data["task_status"])
    return str(resp.get("status") or "")


def _poll_v1(url: str, task_id: str, timeout: int, interval: float) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(max(interval, 0.0))
        resp = get_json(f"{url}/{task_id}", headers=_headers(), timeout=60)
        task_status = _task_status(resp).lower()
        if task_status in _DONE:
            if task_status not in _OK:
                raise HttpError(0, url, f"任务状态: {task_status}: {str(resp)[:200]}")
            return resp or {}
    raise HttpError(0, url, f"轮询超时（>{timeout}s）")


_poll = _poll_v1


def _poll_image_omni(task_id: str, timeout: int, interval: float) -> dict[str, Any]:
    """静图 poll：默认 v1 succeed；KLING_IMAGE_OMNI_POLL=tasks 才走 succeeded。"""
    if kling_image_omni_poll_mode() == "tasks":
        return _poll_open_tasks(task_id, timeout, interval)
    return _poll_v1(kling_image_omni_url(), task_id, timeout, interval)


_OPEN_VIDEO_APIS = frozenset({
    "kling_omni_30", "kling_t2v_30", "kling_i2v_30", "kling_motion_30",
})
_DEFAULT_USD_PER_CREDIT = 0.02
# 1080p native ≈ 12 credits/s；其余按保守偏高，禁止 0。
_CREDITS_PER_SEC = {
    ("720p", "off"): 5.0,
    ("720p", "native"): 8.0,
    ("720p", "original"): 5.0,
    ("1080p", "off"): 8.0,
    ("1080p", "native"): 12.0,
    ("1080p", "original"): 8.0,
    ("4k", "off"): 16.0,
    ("4k", "native"): 24.0,
    ("4k", "original"): 16.0,
}


def kling_usd_per_credit() -> float:
    raw = str(os.environ.get("KLING_USD_PER_CREDIT") or "").strip()
    try:
        val = float(raw) if raw else _DEFAULT_USD_PER_CREDIT
    except ValueError:
        val = _DEFAULT_USD_PER_CREDIT
    if val <= 0:
        return _DEFAULT_USD_PER_CREDIT
    return val


def kling_video_resolution(api_id: str = "kling_omni_30") -> tuple[str, list[str]]:
    notes: list[str] = []
    raw = str(os.environ.get("KLING_VIDEO_RESOLUTION") or "1080p").strip().lower()
    if raw not in ("720p", "1080p", "4k"):
        raw = "1080p"
    if api_id == "kling_motion_30" and raw == "4k":
        notes.append("动作控制无 4k，回落 1080p")
        raw = "1080p"
    return raw, notes


def _duration_int(inputs: dict[str, Any]) -> int:
    raw = inputs.get("duration")
    if raw is None or str(raw).strip() == "":
        raw = inputs.get("seconds")
    try:
        val = int(float(str(raw if raw is not None else "5")))
    except (TypeError, ValueError):
        val = 5
    return max(3, min(15, val))


def _speaker_count(inputs: dict[str, Any]) -> int:
    speakers = inputs.get("speaker_ids") or inputs.get("speakers")
    if isinstance(speakers, str) and speakers.strip():
        return 1
    if isinstance(speakers, list):
        return len([s for s in speakers if str(s).strip()])
    if str(inputs.get("speaker_id") or "").strip():
        return 1
    return 0


def _resolve_audio(inputs: dict[str, Any], *, surface: str) -> str:
    explicit = str(inputs.get("audio") or "").strip().lower()
    if explicit in ("native", "off", "original"):
        audio = explicit
    elif str(inputs.get("sound") or "").strip().lower() == "on":
        audio = "native"
    elif str(inputs.get("dialogue_audio_mode") or "").strip().lower() == "native":
        audio = "native"
    elif _speaker_count(inputs) == 1 and str(inputs.get("dialogue") or "").strip():
        audio = "native"
    else:
        audio = "off"
    if surface == "feature":
        return "off"
    if surface == "base":
        return "original"
    if surface == "motion":
        return "original" if audio == "original" else "off"
    if audio == "original" and surface in ("omni", "i2v", "t2v"):
        return "off"
    return audio


def _content_image_url(src: str) -> str:
    text = str(src or "").strip()
    if not text:
        return ""
    if _is_http_url(text):
        return text
    packed = pack_kling_media(text, kind="image")
    if not packed["ok"]:
        raise ValueError(str((packed.get("notes") or ["图片装箱失败"])[0]))
    return str(packed["url"])


def _content_video_url(src: str) -> str:
    text = str(src or "").strip()
    if not text:
        return ""
    if _is_http_url(text):
        return text
    raise ValueError("可灵视频参考只收 https URL，不装箱本地 mp4")


def _options(inputs: dict[str, Any]) -> dict[str, Any]:
    ext = str(inputs.get("external_task_id") or "").strip()
    if not ext:
        ext = kling_attempt_id(inputs.get("shot_id"), inputs.get("attempt")) or ""
    return _omit_blank_external_id({
        "watermark_info": {"enabled": False},
        "external_task_id": ext,
    })


def _input_refs(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    refs = list(inputs.get("refs") or [])
    if refs:
        return [r for r in refs if isinstance(r, dict)]
    return [{"url": u} for u in (inputs.get("reference_urls") or []) if u]


def _input_elements(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    raw = inputs.get("elements") or inputs.get("element_list") or []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        eid = item.get("element_id") if item.get("element_id") is not None else item.get("id")
        if eid is None or str(eid).strip() == "":
            continue
        out.append(item)
    return out


def _omni_contents(
    prompt: str,
    inputs: dict[str, Any],
    *,
    surface: str,
) -> list[dict[str, Any]]:
    from montage.providers.capabilities import apply_kling_omni_refs, apply_kling_omni_videos

    payload: dict[str, Any] = {}
    if surface in ("feature", "base"):
        video = _content_video_url(str(inputs.get("video_url") or ""))
        if not video:
            raise ValueError("Omni 视频参考需要 URL")
        apply_kling_omni_videos(
            payload, video_url=video, refer_type=surface, prompt=prompt,
        )
        return list(payload.get("contents") or [])
    first = _content_image_url(str(inputs.get("image_url") or inputs.get("image") or ""))
    last = _content_image_url(str(inputs.get("last_frame_url") or ""))
    if last and not first:
        raise ValueError("可灵 Omni 有尾帧必须同时有首帧")
    packed_refs: list[dict[str, Any]] = []
    for item in _input_refs(inputs):
        url = _content_image_url(str(item.get("url") or ""))
        if not url or url in {first, last}:
            continue
        packed_refs.append({"url": url, "type": "refer_image"})
    apply_kling_omni_refs(
        payload,
        prompt=prompt,
        first_url=first,
        last_url=last,
        refs=packed_refs,
        elements=_input_elements(inputs),
    )
    return list(payload.get("contents") or [])


def _open_video_payload(api_id: str, inputs: dict[str, Any], prompt: str) -> tuple[str, dict[str, Any]]:
    path = KLING_VIDEO_PATHS[api_id]
    resolution, _res_notes = kling_video_resolution(api_id)
    duration = _duration_int(inputs)
    options = _options(inputs)
    refer = str(inputs.get("refer_type") or "").strip().lower()
    video_url = str(inputs.get("video_url") or "").strip()
    rework = str(inputs.get("rework_mode") or "").strip().lower()

    if api_id == "kling_t2v_30":
        audio = _resolve_audio(inputs, surface="t2v")
        payload: dict[str, Any] = {
            "prompt": prompt,
            "settings": {
                "multi_shot": False,
                "audio": audio,
                "resolution": resolution,
                "aspect_ratio": inputs.get("aspect_ratio") or "16:9",
                "duration": duration,
            },
        }
        if options:
            payload["options"] = options
        return path, payload

    if api_id == "kling_i2v_30":
        first = _content_image_url(str(inputs.get("image_url") or inputs.get("image") or ""))
        if not first:
            raise ValueError("Kling 图生 3.0 无首帧不可选")
        last = _content_image_url(str(inputs.get("last_frame_url") or ""))
        audio = _resolve_audio(inputs, surface="i2v")
        contents: list[dict[str, Any]] = [
            {"type": "prompt", "text": prompt},
            {"type": "first_frame", "url": first},
        ]
        if last:
            contents.append({"type": "last_frame", "url": last})
        for idx, item in enumerate(_input_elements(inputs)[:3], start=1):
            eid = item.get("element_id") if item.get("element_id") is not None else item.get("id")
            contents.append({"type": "element", "element_id": str(eid), "id": f"element_{idx}"})
        payload = {
            "contents": contents,
            "settings": {
                "audio": audio,
                "resolution": resolution,
                "duration": duration,
            },
        }
        if options:
            payload["options"] = options
        return path, payload

    if api_id == "kling_motion_30":
        image = _content_image_url(str(
            inputs.get("image_url") or inputs.get("image") or inputs.get("character_image_url") or ""
        ))
        video = _content_video_url(str(inputs.get("motion_video_url") or inputs.get("video_url") or ""))
        if not image:
            raise ValueError("动作控制需要形象图")
        if not video:
            raise ValueError("动作控制需要 motion_video_url")
        orientation = str(inputs.get("character_orientation") or "image").strip().lower()
        if orientation not in ("image", "video"):
            orientation = "image"
        audio = _resolve_audio(inputs, surface="motion")
        contents = [
            {"type": "prompt", "text": prompt},
            {"type": "image", "url": image},
            {"type": "video", "url": video},
        ]
        elements = _input_elements(inputs)[:1]
        if elements:
            eid = elements[0].get("element_id")
            if eid is None:
                eid = elements[0].get("id")
            contents.append({"type": "element", "element_id": str(eid), "id": "element_1"})
        payload = {
            "contents": contents,
            "settings": {
                "character_orientation": orientation,
                "audio": audio,
                "resolution": resolution,
            },
        }
        if options:
            payload["options"] = options
        return path, payload

    # Omni
    surface = "omni"
    if video_url and (refer == "feature" or rework == "feature"):
        surface = "feature"
        if duration > 10:
            raise ValueError("成片 11–15s 不能 feature")
        prompt = f"镜头 1, {duration}, {prompt};"
    elif video_url:
        surface = "base"
    audio = _resolve_audio(inputs, surface=surface)
    contents = _omni_contents(prompt, inputs, surface=surface)
    settings: dict[str, Any] = {
        "multi_shot": True if surface == "feature" else False,
        "audio": audio,
        "resolution": resolution,
    }
    if surface != "base":
        settings["duration"] = min(duration, 10) if surface == "feature" else duration
    has_first = any(c.get("type") == "first_frame" for c in contents)
    has_video_ref = any(c.get("type") in ("feature_video", "base_video") for c in contents)
    if not has_first and not has_video_ref:
        settings["aspect_ratio"] = inputs.get("aspect_ratio") or "16:9"
    payload = {"contents": contents, "settings": settings}
    if options:
        payload["options"] = options
    return path, payload


def _media_urls(resp: dict[str, Any], kind: str) -> list[str]:
    blob = resp.get("task_result") or (resp.get("data") or {}).get("task_result") or {}
    items = blob.get(kind) or []
    urls: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("url"):
            urls.append(str(item["url"]))
    return urls


def _dedupe_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        text = str(url or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _image_v1_urls(resp: dict[str, Any] | None) -> list[str]:
    if not isinstance(resp, dict):
        return []
    urls = _media_urls(resp, "images")
    blob = resp.get("task_result") or (resp.get("data") or {}).get("task_result") or {}
    extra = blob.get("series_images") or []
    for item in extra:
        if isinstance(item, dict):
            url = item.get("url") or item.get("image_url")
            if url:
                urls.append(str(url))
        elif isinstance(item, list):
            for sub in item:
                if isinstance(sub, dict):
                    url = sub.get("url") or sub.get("image_url")
                    if url:
                        urls.append(str(url))
    return _dedupe_urls(urls)


_IMAGE_CREDITS = {"1k": 2.0, "2k": 4.0, "4k": 8.0}


def kling_image_estimate_cost(inputs: dict[str, Any] | None = None) -> float:
    payload = inputs or {}
    resolution = kling_image_resolution(payload)
    count = 1
    if str(payload.get("result_type") or "single").strip().lower() == "series":
        try:
            count = max(1, int(payload.get("n") or payload.get("series_amount") or 4))
        except (TypeError, ValueError):
            count = 4
    credits = _IMAGE_CREDITS.get(resolution, 4.0)
    return round(count * credits * kling_usd_per_credit(), 4)


def _image_omni_image_list(inputs: dict[str, Any]) -> list[dict[str, str]]:
    urls: list[str] = []
    if inputs.get("image_url"):
        urls.append(str(inputs["image_url"]))
    for item in _input_refs(inputs):
        url = str(item.get("url") or "").strip()
        if url:
            urls.append(url)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for src in urls:
        packed = _content_image_url(src)
        if packed and packed not in seen:
            seen.add(packed)
            out.append({"image": packed})
        if len(out) >= 10:
            break
    return out


def _image_omni_payload(inputs: dict[str, Any], prompt: str) -> dict[str, Any]:
    model = kling_image_omni_model(inputs)
    if not model or model == "kling-v1":
        raise ValueError("Image Omni 禁止 kling-v1（用 kling-v3-omni 或 kling-image-o1）")
    result_type = str(inputs.get("result_type") or "single").strip().lower()
    if result_type not in ("single", "series"):
        result_type = "single"
    payload: dict[str, Any] = {
        "model_name": model,
        "prompt": prompt,
        "resolution": kling_image_resolution(inputs),
        "aspect_ratio": inputs.get("aspect_ratio") or "16:9",
        "result_type": result_type,
        "watermark_info": {"enabled": False},
    }
    if result_type == "series":
        try:
            payload["n"] = max(1, int(inputs.get("n") or inputs.get("series_amount") or 4))
        except (TypeError, ValueError):
            payload["n"] = 4
    image_list = _image_omni_image_list(inputs)
    if image_list:
        payload["image_list"] = image_list
    elements = []
    for item in _input_elements(inputs):
        eid = item.get("element_id") if item.get("element_id") is not None else item.get("id")
        if eid is None or str(eid).strip() == "":
            continue
        elements.append({"element_id": str(eid)})
        if len(elements) >= 10:
            break
    if elements:
        payload["element_list"] = elements
    return payload


def _asset_field(resp: dict[str, Any] | None, key: str) -> str:
    """从响应里定向取 `key`，兼容扁平与嵌套（dict/list）两种形状。

    官方主体/音色查询把 id 放在 `data.task_result.elements[0].element_id`
    （或 `voices[0].voice_id`）—— 数组元素内；旧 mock / 部分接口则是扁平的
    `data.element_id`。此处定向递归（命中即返回），两者皆可。
    """
    if not isinstance(resp, dict):
        return ""

    def _find(node: Any) -> str:
        if isinstance(node, dict):
            val = node.get(key)
            if val is not None and str(val).strip() != "":
                return str(val)
            for value in node.values():
                found = _find(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = _find(item)
                if found:
                    return found
        return ""

    return _find(resp)


def kling_create_element(
    *,
    element_name: str,
    frontal_image: str,
    refer_images: list[str] | None = None,
    element_description: str = "",
    element_voice_id: str = "",
    timeout_seconds: int = 300,
    poll_interval_seconds: float = 5,
) -> dict[str, Any]:
    """内部 CRUD，不注册 BaseTool。video_refer 本轮不做。"""
    notes: list[str] = []
    name = str(element_name or "").strip()
    if not name:
        return {"ok": False, "element_id": "", "task_id": "", "notes": ["element_name 必填"]}
    if len(name) > 20:
        name = name[:20]
    desc = str(element_description or name).strip() or name
    if len(desc) > 100:
        desc = desc[:100]
    try:
        frontal = _content_image_url(frontal_image)
    except ValueError as exc:
        return {"ok": False, "element_id": "", "task_id": "", "notes": [str(exc)]}
    refs: list[dict[str, str]] = []
    for src in (refer_images or [])[:3]:
        try:
            packed = _content_image_url(src)
        except ValueError as exc:
            notes.append(str(exc))
            continue
        if packed:
            refs.append({"image_url": packed})
    payload: dict[str, Any] = {
        "element_name": name,
        "element_description": desc,
        "reference_type": "image_refer",
        "element_image_list": {
            "frontal_image": frontal,
            "refer_images": refs,
        },
    }
    voice = str(element_voice_id or "").strip()
    if voice:
        payload["element_voice_id"] = voice
    url = f"{_v1_base()}{KLING_ELEMENT_PATH}"
    try:
        headers = _headers()
        resp = post_json(url, payload, headers=headers, timeout=120)
        task_id = _task_id(resp)
        if not task_id:
            return {"ok": False, "element_id": "", "task_id": "", "notes": notes + [f"响应无 task_id: {str(resp)[:300]}"]}
        result = _poll_v1(url, task_id, timeout_seconds, poll_interval_seconds)
        element_id = _asset_field(result, "element_id") or _asset_field(resp, "element_id")
    except (HttpError, ValueError) as exc:
        return {"ok": False, "element_id": "", "task_id": "", "notes": notes + [str(exc)]}
    if not element_id:
        return {"ok": False, "element_id": "", "task_id": task_id, "notes": notes + ["任务完成但无 element_id"]}
    return {"ok": True, "element_id": element_id, "task_id": task_id, "notes": notes}


def kling_create_voice(
    *,
    voice_name: str,
    voice_url: str,
    timeout_seconds: int = 300,
    poll_interval_seconds: float = 5,
) -> dict[str, Any]:
    """内部 CRUD，不注册 BaseTool。无 https 则跳过克隆，不装箱本地音频。"""
    name = str(voice_name or "").strip()
    src = str(voice_url or "").strip()
    if not _is_https_url(src):
        return {
            "ok": False,
            "skipped": True,
            "voice_id": "",
            "task_id": "",
            "notes": ["音色只认 https，跳过克隆"],
        }
    if not name:
        return {"ok": False, "skipped": False, "voice_id": "", "task_id": "", "notes": ["voice_name 必填"]}
    payload = {"voice_name": name, "voice_url": src}
    url = f"{_v1_base()}{KLING_VOICE_PATH}"
    try:
        headers = _headers()
        resp = post_json(url, payload, headers=headers, timeout=120)
        task_id = _task_id(resp)
        if not task_id:
            return {"ok": False, "skipped": False, "voice_id": "", "task_id": "", "notes": [f"响应无 task_id: {str(resp)[:300]}"]}
        result = _poll_v1(url, task_id, timeout_seconds, poll_interval_seconds)
        voice_id = _asset_field(result, "voice_id") or _asset_field(resp, "voice_id")
    except (HttpError, ValueError) as exc:
        return {"ok": False, "skipped": False, "voice_id": "", "task_id": "", "notes": [str(exc)]}
    if not voice_id:
        return {"ok": False, "skipped": False, "voice_id": "", "task_id": task_id, "notes": ["任务完成但无 voice_id"]}
    return {"ok": True, "skipped": False, "voice_id": voice_id, "task_id": task_id, "notes": []}


class KlingImage(BaseTool):
    """可灵 Image Omni 静图（拼板 / 空镜 / 首帧）。series 仅工牌拒收降级。"""

    name = "kling_image"
    version = "0.2.0"
    capability = "image_generation"
    provider = "kling"
    runtime = ToolRuntime.API
    env_keys = ("KLING_API_KEY",)
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "resolution": {"type": "string", "enum": ["1k", "2k", "4k"], "default": "2k"},
            "aspect_ratio": {"type": "string", "default": "16:9"},
            "result_type": {"type": "string", "enum": ["single", "series"], "default": "single"},
            "series_amount": {"type": "number", "default": 4},
            "image_url": {"type": "string", "description": "参考图 https 或本地路径（装箱进 image_list）"},
            "reference_urls": {"type": "array", "items": {"type": "string"}},
            "output_path": {"type": "string"},
            "timeout_seconds": {"type": "number", "default": 300},
            "poll_interval_seconds": {"type": "number", "default": 5},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("KLING_API_KEY") else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return kling_image_estimate_cost(inputs)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        prompt = inputs.get("prompt")
        if not prompt:
            return ToolResult(success=False, error="'prompt' 必填")
        try:
            headers = _headers()
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        try:
            payload = _image_omni_payload(inputs, str(prompt))
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        url = kling_image_omni_url()
        poll_mode = kling_image_omni_poll_mode()
        try:
            resp = post_json(url, payload, headers=headers, timeout=120)
            if poll_mode == "tasks":
                task_id = _omni_task_id(resp)
                if not task_id:
                    return ToolResult(success=False, error=f"响应无 data.id: {str(resp)[:300]}")
                result = _poll_image_omni(
                    task_id,
                    int(inputs.get("timeout_seconds", 300)),
                    float(inputs.get("poll_interval_seconds", 5)),
                )
                urls = _open_task_image_urls(result)
            else:
                task_id = _task_id(resp)
                if not task_id:
                    return ToolResult(success=False, error=f"响应无 task_id: {str(resp)[:300]}")
                result = _poll_image_omni(
                    task_id,
                    int(inputs.get("timeout_seconds", 300)),
                    float(inputs.get("poll_interval_seconds", 5)),
                )
                urls = _image_v1_urls(result)
        except (HttpError, KeyError, ValueError) as exc:
            return ToolResult(success=False, error=str(exc))

        if not urls:
            return ToolResult(success=False, error=f"任务完成但无 url: {str(result)[:300]}")
        local = None
        if inputs.get("output_path"):
            try:
                from montage.tools.downloader import download_file

                local = str(download_file(urls[0], inputs["output_path"], timeout=120))
            except Exception:  # noqa: BLE001
                local = None
        return ToolResult(
            success=True,
            data={"urls": urls, "task_id": task_id, "output": local, "result_type": payload.get("result_type")},
            meta={"provider": "kling", "contract": "image-omni", "poll": poll_mode},
            cost_usd=self.estimate_cost(inputs),
        )


class KlingVideo(BaseTool):
    """可灵视频：默认 v1；api_id 选择 Omni / 2.1 Pro。"""

    name = "kling_video"
    version = "0.1.0"
    capability = "video_generation"
    provider = "kling"
    runtime = ToolRuntime.API
    env_keys = ("KLING_API_KEY",)
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string"},
            "api_id": {
                "type": "string",
                "enum": [
                    "kling_v1", "kling_omni_30", "kling_i2v_21_pro",
                    "kling_t2v_30", "kling_i2v_30", "kling_motion_30",
                ],
                "default": "kling_v1",
            },
            "model_name": {"type": "string", "default": "kling-v1"},
            "image_url": {"type": "string", "description": "图生视频参考图 / 首帧 URL"},
            "last_frame_url": {"type": "string"},
            "duration": {"type": "string", "default": "5"},
            "aspect_ratio": {"type": "string", "default": "16:9"},
            "mode": {"type": "string", "default": "pro"},
            "sound": {"type": "string", "enum": ["on", "off"], "default": "off"},
            "audio": {"type": "string", "enum": ["native", "off", "original"]},
            "negative_prompt": {"type": "string"},
            "video_url": {"type": "string", "description": "Omni 编辑/特征参考成片 URL"},
            "refer_type": {"type": "string", "enum": ["base", "feature"], "default": "base"},
            "output_path": {"type": "string"},
            "timeout_seconds": {"type": "number", "default": 600},
            "poll_interval_seconds": {"type": "number", "default": 5},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("KLING_API_KEY") else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        api_id = str(inputs.get("api_id") or "kling_v1")
        seconds = float(_duration_int(inputs))
        if api_id in _OPEN_VIDEO_APIS:
            resolution, _ = kling_video_resolution(api_id)
            if api_id == "kling_motion_30":
                audio = _resolve_audio(inputs, surface="motion")
            elif str(inputs.get("refer_type") or "").strip().lower() == "feature":
                audio = "off"
            elif str(inputs.get("refer_type") or "").strip().lower() == "base":
                audio = "original"
            else:
                audio = _resolve_audio(inputs, surface="omni" if api_id == "kling_omni_30" else "t2v")
            credits = _CREDITS_PER_SEC.get((resolution, audio), 12.0)
            return round(seconds * credits * kling_usd_per_credit(), 4)
        rates = {"kling_v1": 0.3, "kling_i2v_21_pro": 0.5}
        return round(float(rates.get(api_id, 0.3)) * max(seconds, 5.0) / 5.0, 4)

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        prompt = inputs.get("prompt")
        if not prompt:
            return ToolResult(success=False, error="'prompt' 必填")
        try:
            headers = _headers()
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        api_id = str(inputs.get("api_id") or "kling_v1").strip() or "kling_v1"
        try:
            endpoint, payload = _video_payload(api_id, inputs, str(prompt))
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        try:
            if endpoint.startswith("/"):
                url = f"{_open_base()}{endpoint}"
                resp = post_json(url, payload, headers=headers, timeout=120)
                task_id = _omni_task_id(resp)
                if not task_id:
                    return ToolResult(success=False, error=f"响应无 data.id: {str(resp)[:300]}")
                result = _poll_open_tasks(
                    task_id,
                    int(inputs.get("timeout_seconds", 600)),
                    float(inputs.get("poll_interval_seconds", 5)),
                )
                video_url = _open_task_video_url(result) or None
            else:
                resp = post_json(f"{_v1_base()}/videos/{endpoint}", payload, headers=headers, timeout=120)
                task_id = _task_id(resp)
                if not task_id:
                    return ToolResult(success=False, error=f"响应无 task_id: {str(resp)[:300]}")
                result = _poll(
                    f"{_v1_base()}/videos/{endpoint}",
                    task_id,
                    int(inputs.get("timeout_seconds", 600)),
                    float(inputs.get("poll_interval_seconds", 5)),
                )
                urls = _media_urls(result, "videos")
                video_url = urls[0] if urls else None
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
            data={"video_url": video_url, "task_id": task_id, "output": local, "api_id": api_id},
            meta={"provider": "kling", "api_id": api_id, "contract": "pending-verify"},
            cost_usd=self.estimate_cost(inputs),
        )


def _video_payload(api_id: str, inputs: dict[str, Any], prompt: str) -> tuple[str, dict[str, Any]]:
    if api_id in _OPEN_VIDEO_APIS:
        return _open_video_payload(api_id, inputs, prompt)
    duration = str(inputs.get("duration") or "5")
    if api_id == "kling_i2v_21_pro":
        first = str(inputs.get("image_url") or inputs.get("image") or "").strip()
        if not first:
            raise ValueError("Kling 2.1 Pro 无首帧不可选（纯文生请改 Omni 或 v1）")
        model = str(inputs.get("model_name") or os.environ.get("KLING_I2V_MODEL") or "").strip()
        if not model or model == "kling-v1":
            model = str(os.environ.get("KLING_I2V_MODEL") or "").strip()
        if not model:
            raise ValueError("缺少 KLING_I2V_MODEL 或 model_name（2.1 Pro，控制台核对）")
        payload: dict[str, Any] = {
            "model_name": model,
            "prompt": prompt,
            "mode": inputs.get("mode") or "pro",
            "duration": duration,
            "image": first,
            "watermark_info": {"enabled": False},
        }
        if inputs.get("last_frame_url"):
            payload["image_tail"] = inputs["last_frame_url"]
        if inputs.get("negative_prompt"):
            payload["negative_prompt"] = inputs["negative_prompt"]
        return "image2video", payload

    payload = {
        "model_name": inputs.get("model_name", "kling-v1"),
        "prompt": prompt,
        "duration": duration,
        "aspect_ratio": inputs.get("aspect_ratio", "16:9"),
    }
    endpoint = "text2video"
    if inputs.get("image_url"):
        payload["image"] = inputs["image_url"]
        endpoint = "image2video"
    return endpoint, payload
