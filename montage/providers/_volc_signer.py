"""_volc_signer — 火山引擎 HMAC-SHA256 V4 请求签名（全新原创实现）。

算法为火山引擎公开的 IAM 签名机制（与 AWS SigV4 同族）：
  CanonicalRequest → StringToSign → HMAC 链（date/region/service/request）。

契约（经核对）：
  HOST=visual.volcengineapi.com  REGION=cn-north-1  SERVICE=cv  VERSION=2022-08-31
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from montage.providers.http import HttpError, post_bytes

HOST = "visual.volcengineapi.com"
REGION = "cn-north-1"
SERVICE = "cv"
ALGORITHM = "HMAC-SHA256"
API_VERSION = "2022-08-31"


def sign_headers(
    method: str,
    path: str,
    query_params: dict[str, str],
    body: bytes,
    ak: str,
    sk: str,
    *,
    host: str = HOST,
    region: str = REGION,
    service: str = SERVICE,
) -> dict[str, str]:
    """对请求签名，返回可直发的 headers（含 Authorization）。"""
    now = datetime.now(timezone.utc)
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = x_date[:8]
    body_hash = hashlib.sha256(body).hexdigest()

    headers = {
        "Host": host,
        "X-Date": x_date,
        "X-Content-Sha256": body_hash,
        "Content-Type": "application/json",
    }
    lower = {k.lower(): v.strip() for k, v in headers.items()}
    signed_names = sorted(lower)
    canonical_headers = "".join(f"{k}:{lower[k]}\n" for k in signed_names)
    signed_str = ";".join(signed_names)

    canonical_query = "&".join(
        f"{urllib.parse.quote(str(k), safe='')}={urllib.parse.quote(str(v), safe='')}"
        for k, v in sorted(query_params.items())
    )
    canonical_request = "\n".join([
        method.upper(),
        path,
        canonical_query,
        canonical_headers,
        signed_str,
        body_hash,
    ])
    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join([
        ALGORITHM,
        x_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    k_date = hmac.new(sk.encode(), short_date.encode(), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode(), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode(), hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b"request", hashlib.sha256).digest()
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

    headers["Authorization"] = (
        f"{ALGORITHM} Credential={ak}/{credential_scope}, "
        f"SignedHeaders={signed_str}, Signature={signature}"
    )
    return headers


def post_action(
    action: str,
    payload: dict[str, Any],
    *,
    ak: str,
    sk: str,
    timeout: int = 60,
) -> dict[str, Any]:
    """POST 一个 CVSync2Async* action，返回解析后的 JSON。"""
    query = {"Action": action, "Version": API_VERSION}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = sign_headers("POST", "/", query, body, ak, sk)
    url = f"https://{HOST}/?{urllib.parse.urlencode(sorted(query.items()))}"
    try:
        return post_bytes(url, body, headers=headers, timeout=timeout)
    except HttpError as exc:
        raise JimengApiError(
            f"Action {action} 失败: HTTP {exc.status} {exc.body}"
        ) from exc


class JimengApiError(RuntimeError):
    """即梦 API 错误（带可读信息）。"""
