"""vlm_reviewer — 千问 VL 语义质检（P3）。

确定性 gate（asset_quality_gate）过后再跑。缺 DASHSCOPE_API_KEY 则 NEEDS_CONFIG，
不挡成片。禁止根据回复去填 retake_segment。
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from montage.providers.http import HttpError, post_json
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

VLM_KINDS = (
    "人物不一致",
    "道具丢失",
    "场景错位",
    "崩坏",
    "构图",
)
_DEFAULT_MODEL = "qwen-vl-plus"
_DEFAULT_BASE = "https://dashscope.aliyuncs.com"
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

#: 单次请求最多带几帧。千问 VL 多图有上限，且帧越多 token 越贵——审片抽检不需要整段。
MAX_FRAMES = 3


def _api_base() -> str:
    return str(os.environ.get("DASHSCOPE_API_BASE") or _DEFAULT_BASE).rstrip("/")


def _model() -> str:
    return str(os.environ.get("QWEN_VL_MODEL") or _DEFAULT_MODEL).strip() or _DEFAULT_MODEL


def _mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "image/png"


def encode_image_data_url(path: str | Path) -> str:
    target = Path(path)
    raw = target.read_bytes()
    return f"data:{_mime(target)};base64,{base64.b64encode(raw).decode('ascii')}"


def extract_still(path: str | Path, output: str | Path) -> str | None:
    """视频抽一帧；图片原样返回。失败返回 None。"""
    src = Path(path)
    if not src.is_file():
        return None
    if src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return str(src)
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        from montage.compose.ffmpeg_engine import extract_last_frame

        return str(extract_last_frame(src, dest))
    except Exception:  # noqa: BLE001
        return None


def sample_timestamps(duration: float, count: int = MAX_FRAMES) -> list[float]:
    """整段等距取 ``count`` 个采样点（取每段**中点**）。

    避开首尾：首尾常是黑场/台标/尾帧缺帧，抽在那里只会得到「看起来没问题」的假阴性。
    """
    if count <= 0 or duration <= 0:
        return []
    if count == 1:
        return [round(float(duration) / 2, 3)]
    return [round(float(duration) * (i + 0.5) / count, 3) for i in range(count)]


def default_timestamps(media_path: str | Path, count: int = MAX_FRAMES) -> list[float]:
    """视频时长 → 等距采样点。图片/量不出时长 → ``[]``（调用方退回单帧）。"""
    src = Path(media_path)
    if not src.is_file() or src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return []
    try:
        from montage.compose.ffmpeg_engine import probe

        info = probe(src)
    except Exception:  # noqa: BLE001
        return []
    try:
        duration = float((info.get("format") or {}).get("duration") or 0)
    except (TypeError, ValueError):
        return []
    return sample_timestamps(duration, count)


def extract_frames(
    media_path: str | Path,
    timestamps: list[float],
    *,
    prefix: str = "_vlm",
) -> dict[str, Any]:
    """按时间点抽帧。返回 ``{frames, sampled, missed}``。

    抽不出的点**跳过并计数**，不抛：一次抽检少一帧不影响其余帧的结论，
    为它中断整片体检不划算（失败点会带进报告，人能看到少抽了哪一段）。
    """
    src = Path(media_path)
    if not src.is_file():
        return {"frames": [], "sampled": [], "missed": [round(float(t), 3) for t in timestamps]}
    if src.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return {"frames": [str(src)], "sampled": [], "missed": []}
    from montage.compose.ffmpeg_engine import extract_frame_at

    base = str(src.with_suffix("")) + prefix
    frames: list[str] = []
    sampled: list[float] = []
    missed: list[float] = []
    for i, t in enumerate(timestamps):
        dest = Path(f"{base}_{i:02d}.jpg")
        try:
            frames.append(str(extract_frame_at(src, float(t), dest)))
        except Exception:  # noqa: BLE001
            missed.append(round(float(t), 3))
            continue
        sampled.append(round(float(t), 3))
    return {"frames": frames, "sampled": sampled, "missed": missed}


def _sample_frames(
    media_path: str,
    *,
    mode: str,
    timestamps: list[float] | None,
    max_frames: int,
) -> tuple[list[str], dict[str, Any]]:
    """抽要送 VLM 的帧。返回 ``(frames, sampled)``。

    - 显式 ``timestamps``：按点抽（超 ``max_frames`` 截断）
    - ``mode="video_clip"``：整段等距抽 ``max_frames`` 帧（**分段抽帧**）
    - 其余（含图片）：沿用单帧老路径，行为与 P3 完全一致
    """
    limit = max(1, int(max_frames or MAX_FRAMES))
    if timestamps:
        result = extract_frames(media_path, [float(t) for t in timestamps][:limit])
        return result["frames"], {
            "mode": "timestamps",
            "frames": len(result["frames"]),
            "sampled": result["sampled"],
            "missed": result["missed"],
        }
    if str(mode) == "video_clip":
        auto = default_timestamps(media_path, limit)
        if auto:
            result = extract_frames(media_path, auto)
            return result["frames"], {
                "mode": "video_clip",
                "frames": len(result["frames"]),
                "sampled": result["sampled"],
                "missed": result["missed"],
            }
    still = extract_still(media_path, str(Path(media_path).with_suffix("")) + "_vlm.jpg")
    return ([still] if still else []), {
        "mode": "single",
        "frames": 1 if still else 0,
        "sampled": [],
        "missed": [],
    }


def parse_vlm_response(raw: Any) -> dict[str, Any]:
    """把模型输出收成 {ok, score, issues[]}。非 JSON → warning 不挡。"""
    text = ""
    if isinstance(raw, dict):
        choices = raw.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else {}
            if isinstance(msg, dict):
                text = str(msg.get("content") or "")
        if not text and isinstance(raw.get("output"), dict):
            text = json.dumps(raw["output"], ensure_ascii=False)
        if not text:
            text = json.dumps(raw, ensure_ascii=False)
    else:
        text = str(raw or "")
    payload: Any = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_RE.search(text)
        if match:
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                payload = None
    if not isinstance(payload, dict):
        return {
            "ok": True,
            "score": None,
            "issues": [{
                "severity": "warning",
                "kind": "构图",
                "message": "VLM 返回非 JSON，已忽略",
                "proposed_fix": "重跑或人工看片",
            }],
        }
    issues_out: list[dict[str, str]] = []
    for item in payload.get("issues") or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        severity = str(item.get("severity") or "warning").strip().lower()
        if severity not in {"critical", "warning", "info"}:
            severity = "warning"
        if kind not in VLM_KINDS:
            if severity == "critical":
                severity = "warning"
            kind = kind or "构图"
        issues_out.append({
            "severity": severity,
            "kind": kind if kind in VLM_KINDS else "构图",
            "message": str(item.get("message") or ""),
            "proposed_fix": str(item.get("proposed_fix") or "shot_runner retry_ids"),
        })
        if kind not in VLM_KINDS:
            issues_out[-1]["message"] = (
                f"未知 kind={item.get('kind')}；{issues_out[-1]['message']}"
            ).strip("；")
    ok = payload.get("ok")
    if ok is None:
        ok = not any(i["severity"] == "critical" for i in issues_out)
    score = payload.get("score")
    try:
        score_f = float(score) if score is not None else None
    except (TypeError, ValueError):
        score_f = None
    return {"ok": bool(ok), "score": score_f, "issues": issues_out}


def _critical_fail(report: dict[str, Any]) -> bool:
    if report.get("skipped") or report.get("ok"):
        return False
    return any(i.get("severity") == "critical" for i in (report.get("issues") or []))


def _prompt(expected: dict[str, Any], mode: str, *, frame_count: int = 1) -> str:
    appearance = str(expected.get("appearance") or "").strip()
    outfit = str(expected.get("outfit") or "").strip()
    location = str(expected.get("location") or "").strip()
    props = expected.get("props") or []
    prop_text = "、".join(str(p) for p in props if p)
    multi = (
        f"共 {frame_count} 帧按时间顺序给出；同一人物跨帧不一致（发色/服装/脸型/道具）也要报 kind=人物不一致。"
        if frame_count > 1
        else ""
    )
    return (
        "你是成片质检。对照「定妆/场记」检查生成画面。"
        "只输出 JSON："
        '{"ok":true/false,"score":0到1,"issues":[{"severity":"critical|warning",'
        '"kind":"人物不一致|道具丢失|场景错位|崩坏|构图","message":"…","proposed_fix":"…"}]}。'
        f"mode={mode}。"
        f"{multi}"
        f"<appearance>{appearance or '（无）'}</appearance>"
        f"<outfit>{outfit or '（无）'}</outfit>"
        f"<location>{location or '（无）'}</location>"
        f"<props>{prop_text or '（无）'}</props>"
        "人物发色/脸型/服装与定妆明显不符 → kind=人物不一致 severity=critical。"
        "不要输出时间码，不要建议剪某一秒。"
    )


def review_media(
    *,
    media_path: str,
    expected: dict[str, Any] | None = None,
    mode: str = "first_frame",
    portrait_path: str = "",
    post_fn=None,
    timestamps: list[float] | None = None,
    max_frames: int = MAX_FRAMES,
) -> dict[str, Any]:
    """返回 ``{ok, score, issues, skipped, sampled}``。无密钥 skipped=True 且 ok=False。

    ``timestamps`` 显式给点、或 ``mode="video_clip"``（整段等距抽帧）时走**分段抽帧**；
    都不给则仍是「单帧」老行为（P3 调用方零改动）。
    """
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        return {"ok": False, "score": None, "issues": [], "skipped": True}
    frames, sampled = _sample_frames(
        media_path, mode=mode, timestamps=timestamps, max_frames=max_frames,
    )
    if not frames:
        return {
            "ok": True,
            "score": None,
            "issues": [{
                "severity": "warning",
                "kind": "崩坏",
                "message": "无法抽帧供 VLM",
                "proposed_fix": "检查 ffmpeg 或媒体文件",
            }],
            "skipped": False,
            "sampled": sampled,
        }
    content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": encode_image_data_url(frame)}}
        for frame in frames
    ]
    if portrait_path and Path(portrait_path).is_file():
        content.append({
            "type": "image_url",
            "image_url": {"url": encode_image_data_url(portrait_path)},
        })
    content.append({"type": "text", "text": _prompt(expected or {}, mode, frame_count=len(frames))})
    payload = {
        "model": _model(),
        "messages": [{"role": "user", "content": content}],
    }
    url = f"{_api_base()}/compatible-mode/v1/chat/completions"
    headers = {"Authorization": f"Bearer {key}"}
    sender = post_fn or post_json
    try:
        raw = sender(url, payload, headers=headers, timeout=120)
    except HttpError as exc:
        return {
            "ok": True,
            "score": None,
            "issues": [{
                "severity": "warning",
                "kind": "崩坏",
                "message": f"VLM HTTP 失败: {exc}",
                "proposed_fix": "检查 DASHSCOPE_API_KEY / 配额",
            }],
            "skipped": False,
            "sampled": sampled,
        }
    parsed = parse_vlm_response(raw)
    parsed["skipped"] = False
    parsed["sampled"] = sampled
    if _critical_fail(parsed):
        parsed["ok"] = False
    return parsed


class VlmReviewer(BaseTool):
    name = "vlm_reviewer"
    version = "0.1.0"
    capability = "analysis"
    provider = "dashscope"
    runtime = ToolRuntime.API
    env_keys = ("DASHSCOPE_API_KEY",)
    input_schema = {
        "type": "object",
        "required": ["media_path"],
        "properties": {
            "media_path": {"type": "string"},
            "mode": {
                "type": "string",
                "enum": ["first_frame", "video_clip"],
                "default": "first_frame",
                "description": "video_clip = 整段等距分段抽帧（P0-7）；first_frame = 单帧",
            },
            "expected": {"type": "object"},
            "portrait_path": {"type": "string"},
            "timestamps": {
                "type": "array",
                "items": {"type": "number"},
                "description": "P0-7：显式抽帧时间点（秒），优先级高于 mode",
            },
            "max_frames": {"type": "integer", "default": MAX_FRAMES},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("DASHSCOPE_API_KEY") else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.01 if os.environ.get("DASHSCOPE_API_KEY") else 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        raw_ts = inputs.get("timestamps")
        timestamps = (
            [float(t) for t in raw_ts if isinstance(t, (int, float))]
            if isinstance(raw_ts, list)
            else None
        )
        try:
            max_frames = int(inputs.get("max_frames") or MAX_FRAMES)
        except (TypeError, ValueError):
            max_frames = MAX_FRAMES
        data = review_media(
            media_path=str(inputs.get("media_path") or ""),
            expected=inputs.get("expected") if isinstance(inputs.get("expected"), dict) else {},
            mode=str(inputs.get("mode") or "first_frame"),
            portrait_path=str(inputs.get("portrait_path") or ""),
            timestamps=timestamps,
            max_frames=max_frames,
        )
        return ToolResult(success=True, data=data)
