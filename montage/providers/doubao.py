"""doubao — 豆包大模型语音合成（火山引擎 TTS V3）适配器。

契约（官方文档 2026-05/08 核对）：
- 端点（HTTP Chunked 单向流式，一次请求返回全部音频，无轮询）：
    POST https://openspeech.bytedance.com/api/v3/tts/unidirectional        （语音控制台 key）
    POST https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional   （方舟 Agent Plan key，ark- 前缀）
  headers: X-Api-Key / X-Api-Resource-Id / X-Api-Request-Id / X-Control-Require-Usage-Tokens-Return: *
- 响应为拼接 JSON 对象流（可能无换行分隔）：
    {"code":0,"data":"<base64 音频块>"}  → 追加音频
    {"code":0,"sentence":{text, words[{word,startTime,endTime}]}} → 词级时间戳（秒）
    {"code":20000000,"message":"ok"}     → 合成结束
  错误码：40402003 文本超限 / 45000000 音色未授权 / 55000000 通用或资源不匹配
- Resource ID 按音色族自动路由（错配报 55000000）：
    S_*                     → seed-icl-2.0（additions 内必须 model_type=4）
    *_uranus_* / *_saturn_* → seed-tts-2.0
    其他（*_moon_* / *_mars_* 等）→ seed-tts-1.0
- 情感控制：
    2.0/ICL2.0 音色 → additions.context_texts（自然语言情感，仅第一个元素生效，不计费）
    1.0 音色        → audio_params.emotion + emotion_scale（关键词式）
  additions 一律 JSON 字符串序列化（传对象会被服务端静默忽略）。
- 时间戳：enable_timestamp 仅 1.0 生效；2.0 用 enable_subtitle。
  输出统一归一化为句级 {text, start_seconds, end_seconds, words[]}（秒，浮点），
  对齐 montage/tools/subtitle_builder.py 与 dashscope 的约定。

密钥：DOUBAO_SPEECH_API_KEY；ark- 前缀自动走 plan 端点。本文件为全新原创代码。
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Iterator

from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

UNIDIRECTIONAL_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"
PLAN_UNIDIRECTIONAL_URL = "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional"

RESOURCE_TTS_1 = "seed-tts-1.0"
RESOURCE_TTS_2 = "seed-tts-2.0"
RESOURCE_ICL_2 = "seed-icl-2.0"

_EXT = {"mp3": "mp3", "ogg_opus": "ogg", "pcm": "pcm"}

_ERROR_HINTS = {
    40402003: "文本超过长度限制（流式接口建议单次 <300 字）",
    45000000: "音色未授权或音色与 Resource ID 不匹配（检查音色授权 / resource_id 路由）",
    45000010: "X-Api-Key 无效（该 key 不属于目标端点；ark- 前缀=方舟/Agent Plan，UUID=语音控制台）",
    45000030: "资源未授权：key 有效但账号未开通语音合成服务（方舟 key 需在方舟控制台为该 key 开通语音合成/Agent Plan TTS；语音控制台 key 需在应用里授予「大模型语音合成」权限）",
    55000000: "服务端通用错误（常见：resource_id 与音色族不匹配）",
}


class DoubaoAPIError(Exception):
    """豆包接口错误：status=0 网络错误；HTTP 状态码原样透出。"""

    def __init__(self, status: int, body: str, logid: str = "") -> None:
        self.status = status
        self.body = body
        self.logid = logid
        suffix = f" logid={logid}" if logid else ""
        super().__init__(f"豆包接口错误 status={status}{suffix}: {body[:300]}")


def endpoint_for_key(api_key: str) -> str:
    """按 key 归属选端点：方舟 ark- 前缀走 Agent Plan 端点，其余走语音控制台。"""
    return PLAN_UNIDIRECTIONAL_URL if str(api_key or "").startswith("ark-") else UNIDIRECTIONAL_URL


def resource_id_for(voice: str) -> str:
    """按音色族路由 Resource ID（官方约束：错配直接 55000000）。

    注意：2.0 族特征是 uranus/saturn 作为**前缀段或中缀段**（*_uranus_*、saturn_*），
    但 `*_saturn_bigtts`（如 zh_male_chunhou_saturn_bigtts）是 1.0 族的"土星"音色，
    与 2.0 的 saturn_ 前缀（如 saturn_zh_female_...）不同族。实测：
    - zh_male_chunhou_saturn_bigtts → seed-tts-1.0（用 2.0 报 55000000）
    - zh_female_vv_uranus_bigtts / zh_male_liufei_uranus_bigtts → seed-tts-2.0
    规则：含 `_uranus_` 段或以 `saturn_` 开头 → 2.0；其余 → 1.0。
    """
    v = str(voice or "")
    if v.startswith("S_"):
        return RESOURCE_ICL_2
    if "_uranus_" in v or v.startswith("saturn_"):
        return RESOURCE_TTS_2
    return RESOURCE_TTS_1


def is_tts2_voice(voice: str) -> bool:
    """是否 2.0/ICL2.0 音色（决定情感通道与字幕参数）。"""
    v = str(voice or "")
    return v.startswith("S_") or "_uranus_" in v or v.startswith("saturn_")


def iter_json_objects(text: str) -> Iterator[dict[str, Any]]:
    """切分拼接 JSON 对象流（官方响应可能无换行分隔，不能按行 split）。"""
    dec = json.JSONDecoder()
    idx, n = 0, len(text)
    while idx < n:
        while idx < n and text[idx] in " \t\r\n":
            idx += 1
        if idx >= n:
            break
        obj, end = dec.raw_decode(text, idx)
        if isinstance(obj, dict):
            yield obj
        idx = end


def normalize_sentences(sentence_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """词级毫秒/秒时间戳 → 句级 {text, start_seconds, end_seconds, words[]}（subtitle_builder 契约）。"""
    out: list[dict[str, Any]] = []
    for sent in sentence_events or []:
        words: list[dict[str, Any]] = []
        for w in sent.get("words") or []:
            try:
                st = float(w.get("startTime"))
                en = float(w.get("endTime"))
            except (TypeError, ValueError):
                continue
            words.append({
                "word": str(w.get("word") or ""),
                "start_seconds": st,
                "end_seconds": en,
            })
        if not words:
            continue
        out.append({
            "text": str(sent.get("text") or "").strip(),
            "start_seconds": words[0]["start_seconds"],
            "end_seconds": words[-1]["end_seconds"],
            "words": words,
        })
    return out


def build_additions(
    *,
    voice: str,
    context_text: str = "",
    enable_subtitle: bool = False,
) -> str:
    """additions 一律 JSON 字符串（官方坑：传对象被静默忽略）。ICL2.0 必须 model_type=4。"""
    payload: dict[str, Any] = {"disable_markdown_filter": False}
    if str(voice or "").startswith("S_"):
        payload["model_type"] = 4
    ctx = str(context_text or "").strip()
    if ctx and is_tts2_voice(voice):
        payload["context_texts"] = [ctx]  # 仅第一个元素生效
    if enable_subtitle and is_tts2_voice(voice):
        payload["enable_subtitle"] = True
    return json.dumps(payload, ensure_ascii=False)


def build_req_params(
    inputs: dict[str, Any],
    *,
    voice: str,
    resource_id: str,
) -> dict[str, Any]:
    """构造 req_params：audio_params 按音色族挂情感/字幕参数。"""
    tts2 = is_tts2_voice(voice)
    audio_params: dict[str, Any] = {
        "format": inputs.get("format", "mp3"),
        "sample_rate": int(inputs.get("sample_rate", 24000)),
        "speech_rate": inputs.get("speech_rate", 0),
    }
    if tts2:
        # 2.0 情感走 context_texts（additions 内）；字幕走 enable_subtitle
        if bool(inputs.get("enable_subtitle", False)):
            audio_params["enable_subtitle"] = True
    else:
        # 1.0：enable_timestamp 有效；情感为关键词式 emotion
        if bool(inputs.get("enable_timestamp", True)):
            audio_params["enable_timestamp"] = True
        emo = str(inputs.get("emotion") or "").strip()
        if emo:
            audio_params["emotion"] = emo
            try:
                audio_params["emotion_scale"] = int(inputs.get("emotion_scale", 4))
            except (TypeError, ValueError):
                pass
    return {
        "text": inputs.get("text"),
        "speaker": voice,
        "audio_params": audio_params,
        "additions": build_additions(
            voice=voice,
            context_text=str(inputs.get("context_text") or ""),
            enable_subtitle=bool(inputs.get("enable_subtitle", False)),
        ),
    }


def _post_raw(
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> tuple[int, str, str]:
    """原始 POST：返回 (status, 响应文本, X-Tt-Logid)。不走共享 http.py（其 json.loads 吃不下拼接流）。"""
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            logid = ""
            try:
                logid = resp.headers.get("X-Tt-Logid", "") or ""
            except Exception:  # noqa: BLE001
                pass
            return resp.status, resp.read().decode("utf-8", errors="replace"), logid
    except urllib.error.HTTPError as exc:
        raw = ""
        logid = ""
        try:
            raw = exc.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        try:
            logid = exc.headers.get("X-Tt-Logid", "") or ""
        except Exception:  # noqa: BLE001
            pass
        raise DoubaoAPIError(exc.code, raw, logid) from exc
    except urllib.error.URLError as exc:
        raise DoubaoAPIError(0, f"网络错误: {exc.reason}") from exc


class DoubaoTTS(BaseTool):
    """中文自然语音合成（V3 单向流式），支持词级时间戳与情感控制。"""

    name = "doubao_tts"
    version = "0.2.0"
    capability = "tts"
    provider = "doubao"
    runtime = ToolRuntime.API
    env_keys = ("DOUBAO_SPEECH_API_KEY",)
    input_schema = {
        "type": "object",
        "required": ["text"],
        "properties": {
            "text": {"type": "string"},
            "voice_id": {"type": "string", "description": "音色；默认读 DOUBAO_SPEECH_VOICE_TYPE"},
            "resource_id": {"type": "string", "description": "覆盖自动路由；音色族错配会 55000000"},
            "endpoint": {"type": "string", "description": "覆盖自动端点选择（ark- 前缀默认 plan 端点）"},
            "format": {"type": "string", "enum": ["mp3", "ogg_opus", "pcm"], "default": "mp3"},
            "sample_rate": {"type": "integer", "default": 24000},
            "speech_rate": {"type": "number", "default": 0},
            "context_text": {"type": "string", "description": "自然语言情感（仅 2.0/ICL2.0 音色生效）"},
            "emotion": {"type": "string", "description": "情感关键词（仅 1.0 多情感音色生效）"},
            "emotion_scale": {"type": "integer", "default": 4},
            "enable_timestamp": {"type": "boolean", "default": True, "description": "词级时间戳（仅 1.0）"},
            "enable_subtitle": {"type": "boolean", "default": False, "description": "字幕事件（仅 2.0）"},
            "output_path": {"type": "string"},
            "timeout_seconds": {"type": "number", "default": 60},
            "retries": {"type": "integer", "default": 1},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("DOUBAO_SPEECH_API_KEY") else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        chars = len(str(inputs.get("text", "")))
        return round(chars / 1000 * 0.002, 6)

    # --- 内部 -----------------------------------------------------------

    def _request_once(
        self,
        inputs: dict[str, Any],
        *,
        api_key: str,
        voice: str,
        resource: str,
    ) -> tuple[bytes, list[dict[str, Any]], dict[str, Any] | None]:
        """单次请求：返回 (音频字节, 句级时间戳, usage)。"""
        endpoint = str(inputs.get("endpoint") or "") or endpoint_for_key(api_key)
        req_id = str(uuid.uuid4())
        headers = {
            "X-Api-Key": api_key,
            "X-Api-Resource-Id": resource,
            "X-Api-Request-Id": req_id,
            # 官方：设置为 * 返回已支持的用量数据（如 text_words 计费字符数）
            "X-Control-Require-Usage-Tokens-Return": "*",
        }
        body = {
            "user": {"uid": inputs.get("user_id", "montage-core")},
            "req_params": build_req_params(inputs, voice=voice, resource_id=resource),
        }
        timeout = float(inputs.get("timeout_seconds", 60))
        status, text, logid = _post_raw(endpoint, body, headers, timeout)
        if status >= 400:
            raise DoubaoAPIError(status, text, logid)

        chunks: list[bytes] = []
        sentence_events: list[dict[str, Any]] = []
        usage: dict[str, Any] | None = None
        final_code: int | None = None
        for obj in iter_json_objects(text):
            code = obj.get("code")
            data = obj.get("data")
            if isinstance(data, str) and data:
                try:
                    chunks.append(base64.b64decode(data))
                except Exception:  # noqa: BLE001
                    pass
            sent = obj.get("sentence")
            if isinstance(sent, dict):
                sentence_events.append(sent)
            if isinstance(obj.get("usage"), dict):
                usage = obj["usage"]
            if code == 20000000:
                final_code = int(code)
                break
            if isinstance(code, int) and code not in (0, 20000000):
                hint = _ERROR_HINTS.get(code, "")
                raise DoubaoAPIError(
                    code,
                    f"{obj.get('message') or '合成失败'}{('；' + hint) if hint else ''}",
                    logid,
                )
        if final_code is None and not chunks:
            raise DoubaoAPIError(55000000, f"响应无音频数据: {text[:300]}", logid)
        return b"".join(chunks), normalize_sentences(sentence_events), usage

    def _request_with_retry(
        self,
        inputs: dict[str, Any],
        *,
        api_key: str,
        voice: str,
        resource: str,
    ) -> tuple[bytes, list[dict[str, Any]], dict[str, Any] | None]:
        """仅网络错误（status=0）与 5xx 重试；401/403/45000000 重试无意义。"""
        retries = max(0, int(inputs.get("retries", 1)))
        attempt = 0
        while True:
            try:
                return self._request_once(inputs, api_key=api_key, voice=voice, resource=resource)
            except DoubaoAPIError as exc:
                # 仅网络错误（status=0）与 HTTP 5xx（500-599）重试；
                # 8 位业务码（45000000 等）与 4xx 一律不重试
                retryable = exc.status == 0 or 500 <= exc.status <= 599
                if retryable and attempt < retries:
                    attempt += 1
                    continue
                raise

    # --- 工具契约 ---------------------------------------------------------

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("DOUBAO_SPEECH_API_KEY")
        if not api_key:
            return ToolResult(success=False, error="缺少 DOUBAO_SPEECH_API_KEY")
        text = inputs.get("text")
        if not text:
            return ToolResult(success=False, error="'text' 必填")
        voice = str(inputs.get("voice_id") or os.environ.get("DOUBAO_SPEECH_VOICE_TYPE")
                    or "zh_female_xiaohe_uranus_bigtts")
        resource = str(inputs.get("resource_id") or resource_id_for(voice))

        try:
            audio, sentences, usage = self._request_with_retry(
                inputs, api_key=api_key, voice=voice, resource=resource,
            )
        except DoubaoAPIError as exc:
            hint = _ERROR_HINTS.get(exc.status, "")
            msg = f"豆包合成失败: {exc}{('；' + hint) if hint else ''}"
            if exc.status in (401, 403):
                msg += "；请确认 key 归属（ark- 前缀=方舟/Agent Plan，UUID=语音控制台）"
            return ToolResult(success=False, error=msg)

        if not audio:
            return ToolResult(success=False, error="豆包返回空音频")

        ext = _EXT.get(inputs.get("format", "mp3"), "mp3")
        output = Path(inputs.get("output_path") or f"doubao_{uuid.uuid4().hex[:12]}.{ext}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(audio)

        return ToolResult(
            success=True,
            data={
                "provider": "doubao",
                "model": resource,
                "voice_id": voice,
                "output": str(output),
                "audio_duration_seconds": sentences[-1]["end_seconds"] if sentences else None,
                "sentences": sentences,
                "usage": usage,
            },
            meta={"provider": "doubao", "model": resource, "endpoint": endpoint_for_key(api_key)},
            cost_usd=self.estimate_cost(inputs),
        )
