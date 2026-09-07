"""seedream_image — 火山方舟 Seedream 生图（写死 Seedream 5.0 lite，禁止降级）。

与 seedance_ark.py 同构：provider=ark、Bearer ARK_API_KEY（与 Seedance 视频同 key）、
纯 stdlib HTTP。同步接口无轮询（区别于视频异步任务面）：
POST {base}/api/v3/images/generations

硬性约束（用户拍板）：
- 模型只允许 Seedream 5.0 lite 家族（resolve_seedream_model allowlist，
  4.5/4.0/pro 的 Model ID 一律参数校验报错拒发，不发请求）；
  Endpoint ID（ep- 前缀）放行，语义上由用户保证指向 lite。
- 全链路禁止降级：供应商锁定后失败即硬错误，不回退可灵/即梦；
  参考图装箱失败不静默转纯文生（纯文生会丢人物一致性，宁可失败重跑）。
- watermark=false、sequential_image_generation=disabled、size 显式宽x高，写死不可配。

参考图：image 参数统一 string[]（官方单张 string / 多张 string[] 两态，本工具统一数组），
URL 直传或本地图装箱 data:image/<fmt>;base64,<...>；lite 最多 14 张、单张 ≤30MB。

价格：官网 ¥0.22/张（meta.price_cny_per_image 留痕）；记账走 BudgetLedger usd 字段，
estimate_cost 返回 USD（¥0.22 ÷ SEEDREAM_CNY_PER_USD，默认 7.2；
SEEDREAM_USD_PER_IMAGE 直接覆盖优先级最高）。
"""

from __future__ import annotations

import base64
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from montage.providers.http import post_json
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_DEFAULT_BASE = "https://ark.cn-beijing.volces.com"
_GENERATIONS_PATH = "/api/v3/images/generations"

# ---- 模型 allowlist（写死 lite 家族；换模型属新决策需改此处，有意设卡） ----
_SEEDREAM_LITE_MODELS = frozenset({
    "doubao-seedream-5-0-260128",
    "doubao-seedream-5-0-lite-260128",
})
_ENDPOINT_PREFIX = "ep-"
_DEFAULT_MODEL = "doubao-seedream-5-0-260128"


def resolve_seedream_model(inputs: dict[str, Any]) -> str:
    """lite 家族 allowlist：两个官方 Model ID 或 ep- 前缀 Endpoint ID，其余拒发。"""
    raw = str(inputs.get("model") or os.environ.get("SEEDREAM_IMAGE_MODEL") or "").strip()
    if not raw:
        return _DEFAULT_MODEL
    if raw in _SEEDREAM_LITE_MODELS or raw.startswith(_ENDPOINT_PREFIX):
        return raw
    raise ValueError(
        f"SEEDREAM_IMAGE_MODEL 只允许 Seedream 5.0 lite 家族"
        f"（{_DEFAULT_MODEL} / doubao-seedream-5-0-lite-260128 / ep-* 接入点），"
        f"收到：{raw}。禁止降级到其他模型。"
    )


# ---- 尺寸（显式宽x高；总像素与宽高比区间按官方文档） ----
_MIN_PIXELS = 2560 * 1440  # 3_686_400
_MAX_PIXELS = 4096 * 4096  # 16_777_216
_MIN_AR = 1.0 / 16.0
_MAX_AR = 16.0

ASPECT_SIZES: dict[str, str] = {
    "9:16": "1600x2848",
    "16:9": "2848x1600",
    "1:1": "2048x2048",
    "2:3": "1664x2496",
    "3:2": "2496x1664",
    "3:4": "1728x2304",
    "4:3": "2304x1728",
    "21:9": "3136x1344",
}
_DEFAULT_SIZE = "2048x2048"

_SIZE_RE = re.compile(r"^(\d+)[xX](\d+)$")


def validate_seedream_size(size: str) -> str:
    """校验显式尺寸：总像素 ∈ [3686400, 16777216]，宽高比 ∈ [1/16, 16]。越界报错不发请求。"""
    m = _SIZE_RE.match(str(size or "").strip())
    if not m:
        raise ValueError(f"size 必须是 宽x高 形式（如 2048x2048），收到：{size}")
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        raise ValueError(f"size 宽高必须为正整数，收到：{size}")
    total = w * h
    if total < _MIN_PIXELS or total > _MAX_PIXELS:
        raise ValueError(
            f"size {w}x{h} 总像素 {total} 超出 [{_MIN_PIXELS}, {_MAX_PIXELS}]"
            f"（官方示例：1500x1500 不足、8000x8000 超限均拒收）"
        )
    ar = w / h
    if ar < _MIN_AR or ar > _MAX_AR:
        raise ValueError(f"size {w}x{h} 宽高比 {ar:.4f} 超出 [{_MIN_AR}, {_MAX_AR}]")
    return f"{w}x{h}"


def size_for_aspect(aspect: str) -> str:
    """画幅 → 官方 2K 档参考尺寸；未知画幅回默认 2048x2048。"""
    return ASPECT_SIZES.get(str(aspect or "").strip(), _DEFAULT_SIZE)


# ---- 参考图装箱（本地 → base64 data URI；失败硬报错，不降级纯文生） ----
_MAX_REF_IMAGES = 14
_MAX_REF_BYTES = 30 * 1024 * 1024
_IMAGE_SUFFIXES = {
    ".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".webp": "webp",
    ".bmp": "bmp", ".tif": "tiff", ".tiff": "tiff", ".gif": "gif",
}


def _is_http_url(raw: str) -> bool:
    try:
        scheme = urlparse(raw).scheme
    except ValueError:
        return False
    return scheme in ("http", "https")


def pack_seedream_image(entries: list[str]) -> tuple[list[str], list[str]]:
    """参考图装箱：https URL 原样直传，本地图读字节转 data URI。

    返回 (packed, notes)。失败项硬报错（ValueError）——纯文生会丢人物一致性，
    宁可失败重跑，禁止静默降级。
    """
    packed: list[str] = []
    seen: set[str] = set()
    for raw in entries:
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        if _is_http_url(item):
            packed.append(item)
            continue
        path = Path(item)
        if not path.is_file():
            raise ValueError(f"参考图本地文件不存在：{item}（禁止降级纯文生，请修复后重跑）")
        suffix = path.suffix.lower()
        fmt = _IMAGE_SUFFIXES.get(suffix)
        if not fmt:
            raise ValueError(
                f"参考图后缀 {suffix or '(无)'} 不受支持（支持 jpeg/png/webp/bmp/tiff/gif）：{item}"
            )
        data = path.read_bytes()
        if len(data) > _MAX_REF_BYTES:
            raise ValueError(f"参考图单张超 30MB（{len(data)} 字节）：{item}")
        packed.append(f"data:image/{fmt};base64," + base64.b64encode(data).decode("ascii"))
    if len(packed) > _MAX_REF_IMAGES:
        raise ValueError(
            f"参考图 {len(packed)} 张超过 lite 上限 {_MAX_REF_IMAGES} 张（禁止静默截断，请精简参考）"
        )
    return packed, []


# ---- 价格与记账 ----
_CNY_PER_IMAGE = 0.22
_DEFAULT_CNY_PER_USD = 7.2


def _usd_per_image() -> float:
    override = os.environ.get("SEEDREAM_USD_PER_IMAGE")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    rate = _DEFAULT_CNY_PER_USD
    rate_env = os.environ.get("SEEDREAM_CNY_PER_USD")
    if rate_env:
        try:
            rate = float(rate_env)
        except ValueError:
            pass
    if rate <= 0:
        rate = _DEFAULT_CNY_PER_USD
    return round(_CNY_PER_IMAGE / rate, 4)


_PROMPT_WARN_HAN = 300


class SeedreamImage(BaseTool):
    """方舟 Seedream 5.0 lite 文生/参考图生图（同步，单图）。"""

    name = "seedream_image"
    version = "0.1.0"
    capability = "image_generation"
    provider = "ark"
    runtime = ToolRuntime.API
    env_keys = ("ARK_API_KEY",)
    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {"type": "string", "description": "生图提示词；官方建议主体+行为+环境"},
            "model": {"type": "string", "description": "lite 家族 Model ID 或 ep- 接入点（allowlist 校验）"},
            "size": {"type": "string", "description": "显式宽x高（如 1600x2848）；与 aspect_ratio 二选一，显式优先"},
            "aspect_ratio": {"type": "string", "description": "画幅（如 9:16/16:9），映射到官方 2K 档尺寸"},
            "image": {
                "type": "array",
                "items": {"type": "string"},
                "description": "参考图 https URL 或本地路径（本地自动装箱 base64，≤14 张）",
            },
            "output_path": {"type": "string"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if os.environ.get("ARK_API_KEY") else ToolStatus.NEEDS_CONFIG

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return _usd_per_image()

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        prompt = str(inputs.get("prompt") or "").strip()
        if not prompt:
            return ToolResult(success=False, error="'prompt' 必填")
        try:
            model = resolve_seedream_model(inputs)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        key = os.environ.get("ARK_API_KEY")
        if not key:
            return ToolResult(success=False, error="缺少 ARK_API_KEY（火山方舟 API Key）")

        # 尺寸：显式 size > aspect_ratio 映射 > 默认；发送前本地校验，越界不发请求
        explicit_size = str(inputs.get("size") or "").strip()
        aspect = str(inputs.get("aspect_ratio") or "").strip()
        try:
            size = validate_seedream_size(explicit_size) if explicit_size else size_for_aspect(aspect)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))

        # 参考图装箱：有 refs 但装箱失败 → 硬错误（不静默转纯文生）
        raw_refs = [str(x) for x in (inputs.get("image") or []) if str(x or "").strip()]
        try:
            packed, _notes = pack_seedream_image(raw_refs)
        except ValueError as exc:
            return ToolResult(success=False, error=str(exc))
        if raw_refs and not packed:
            return ToolResult(success=False, error="参考图全部为空但 inputs.image 非空，拒绝降级纯文生")

        payload: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "sequential_image_generation": "disabled",
            "watermark": False,
            "response_format": "url",
            "output_format": "png",
        }
        if packed:
            payload["image"] = packed  # 统一 string[]（官方单张 string 两态的数组化）

        url = f"{_base()}{_GENERATIONS_PATH}"
        headers = {"Authorization": f"Bearer {key}"}
        try:
            resp = post_json(url, payload, headers=headers, timeout=300)
        except Exception as exc:  # noqa: BLE001 — 网络错裸透传，工具内不自动重试（防重复计费）
            return ToolResult(success=False, error=f"Seedream 请求失败: {exc}")

        if not isinstance(resp, dict):
            return ToolResult(success=False, error=f"Seedream 响应非对象: {str(resp)[:300]}")
        top_error = resp.get("error")
        if isinstance(top_error, dict):
            return ToolResult(
                success=False,
                error=f"Seedream 顶层错误 {top_error.get('code')}: {top_error.get('message')}",
            )
        data = resp.get("data")
        items = data if isinstance(data, list) else []
        per_item_errors = [
            str((d.get("error") or {}).get("message") or (d.get("error") or {}).get("code"))
            for d in items
            if isinstance(d, dict) and d.get("error")
        ]
        urls = [str(d.get("url") or "") for d in items if isinstance(d, dict) and d.get("url")]
        sizes = [str(d.get("size") or "") for d in items if isinstance(d, dict) and d.get("size")]
        if not urls:
            detail = "; ".join(per_item_errors) or str(resp)[:300]
            return ToolResult(success=False, error=f"Seedream 未返回任何图片: {detail}")

        # URL 仅 24h TTL，立即落盘
        local = None
        if inputs.get("output_path"):
            try:
                from montage.tools.downloader import download_file

                local = str(download_file(urls[0], inputs["output_path"], timeout=300))
            except Exception:  # noqa: BLE001 — 落盘失败不吞生成结果，URL 仍在 data.urls
                local = None

        usage = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
        warnings: list[str] = []
        han_chars = len([ch for ch in prompt if "\u4e00" <= ch <= "\u9fff"])
        if han_chars > _PROMPT_WARN_HAN:
            warnings.append(f"prompt 约 {han_chars} 汉字，超官方建议 300（不截断，仅提醒）")

        return ToolResult(
            success=True,
            data={
                "urls": urls,
                "output": local,
                "size": sizes[0] if sizes else size,
                "model": model,
            },
            meta={
                "provider": "ark",
                "model": model,
                "size": sizes[0] if sizes else size,
                "usage": usage,  # generated_images 按成功张数计费；input_images 仅 pro 返回，容错
                "price_cny_per_image": _CNY_PER_IMAGE,
                "cost_usd": self.estimate_cost(inputs),
                "warnings": warnings,
                "item_errors": per_item_errors,
            },
            cost_usd=self.estimate_cost(inputs),
        )


def _base() -> str:
    return str(os.environ.get("ARK_API_BASE") or _DEFAULT_BASE).rstrip("/")
