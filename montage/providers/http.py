"""http — 纯标准库 HTTP JSON 助手（无第三方依赖）。

统一超时、统一错误归一化，把网络失败转换成可读错误信息。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


_SECRET_QS = re.compile(r"(?i)(key|api_key|token)=([^&]*)")


def redact_url(url: str) -> str:
    """打码 query 里的 key=/api_key=/token=，避免 HttpError 把密钥打进日志。"""
    return _SECRET_QS.sub(r"\1=***", str(url or ""))


class HttpError(Exception):
    """带状态码与响应体的 HTTP 错误。"""

    def __init__(self, status: int, url: str, body: str) -> None:
        self.status = status
        self.url = redact_url(url)
        self.body = body
        super().__init__(f"HTTP {status} from {self.url}: {body[:300]}")


def _request(
    method: str,
    url: str,
    headers: dict[str, str] | None,
    payload: Any,
    timeout: int,
) -> tuple[int, Any]:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
        try:
            body = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            body = raw.decode("utf-8", errors="replace")[:500]
        raise HttpError(status, url, json.dumps(body, ensure_ascii=False)[:500]) from exc
    except urllib.error.URLError as exc:
        raise HttpError(0, url, f"网络错误: {exc.reason}") from exc

    text = raw.decode("utf-8", errors="replace")
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, {"_raw": text[:500]}


def post_json(
    url: str,
    payload: Any,
    headers: dict[str, str] | None = None,
    timeout: int = 180,
) -> Any:
    return _request("POST", url, headers, payload, timeout)[1]


def post_bytes(
    url: str,
    body: bytes,
    headers: dict[str, str] | None = None,
    timeout: int = 180,
) -> Any:
    """POST 原始字节体（用于需要先对 body 签名再发送的场景）。"""
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
        text = raw.decode("utf-8", errors="replace")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {"_raw": text[:500]}
        raise HttpError(status, url, json.dumps(parsed, ensure_ascii=False)[:500]) from exc
    except urllib.error.URLError as exc:
        raise HttpError(0, url, f"网络错误: {exc.reason}") from exc
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text[:500]}


def get_json(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
    params: dict[str, Any] | None = None,
) -> Any:
    if params:
        parsed = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
        for key, value in params.items():
            if value is not None:
                query[str(key)] = str(value)
        url = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))
    return _request("GET", url, headers, None, timeout)[1]
