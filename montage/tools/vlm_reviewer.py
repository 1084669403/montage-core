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


def _prompt(expected: dict[str, Any], mode: str) -> str:
    appearance = str(expected.get("appearance") or "").strip()
    outfit = str(expected.get("outfit") or "").strip()
    location = str(expected.get("location") or "").strip()
    props = expected.get("props") or []
    prop_text = "、".join(str(p) for p in props if p)
    return (
        "你是成片质检。对照「定妆/场记」检查生成画面。"
        "只输出 JSON："
        '{"ok":true/false,"score":0到1,"issues":[{"severity":"critical|warning",'
        '"kind":"人物不一致|道具丢失|场景错位|崩坏|构图","message":"…","proposed_fix":"…"}]}。'
        f"mode={mode}。"
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
) -> dict[str, Any]:
    """返回 {ok, score, issues, skipped}。无密钥 skipped=True 且 ok=False。"""
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        return {"ok": False, "score": None, "issues": [], "skipped": True}
    still = extract_still(media_path, str(Path(media_path).with_suffix("")) + "_vlm.jpg")
    if not still or not Path(still).is_file():
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
        }
    content: list[dict[str, Any]] = [
        {"type": "image_url", "image_url": {"url": encode_image_data_url(still)}},
    ]
    if portrait_path and Path(portrait_path).is_file():
        content.append({
            "type": "image_url",
            "image_url": {"url": encode_image_data_url(portrait_path)},
        })
    content.append({"type": "text", "text": _prompt(expected or {}, mode)})
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
        }
    parsed = parse_vlm_response(raw)
    parsed["skipped"] = False
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
            },
            "expected": {"type": "object"},
            "portrait_path": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("DASHSCOPE_API_KEY") else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.01 if os.environ.get("DASHSCOPE_API_KEY") else 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        data = review_media(
            media_path=str(inputs.get("media_path") or ""),
            expected=inputs.get("expected") if isinstance(inputs.get("expected"), dict) else {},
            mode=str(inputs.get("mode") or "first_frame"),
            portrait_path=str(inputs.get("portrait_path") or ""),
        )
        return ToolResult(success=True, data=data)
