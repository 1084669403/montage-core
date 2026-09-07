"""licensing — 免费媒体许可证规则（纯逻辑，零依赖）。

短名 → 可否商用 / 是否署名；Creative Commons URL → 短名。
默认成片路径只放行 CC0 / CC-BY（以及 INDEX 里 Sonniss 商用约定）；
NC / SA / ND / unknown / Sampling+ 需显式打开开关。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

# 短名 → (可商用, 需署名)
LICENSE_RULES: dict[str, tuple[bool, bool]] = {
    "CC0": (True, False),
    "PDM": (True, False),  # 公有领域标识
    "CC-BY": (True, True),
    "CC-BY-SA": (True, True),
    "CC-BY-ND": (True, True),
    "CC-BY-NC": (False, True),
    "CC-BY-NC-SA": (False, True),
    "CC-BY-NC-ND": (False, True),
    "pixabay": (True, False),
    "sonniss": (True, False),
    "incompetech": (True, True),
    "sampling": (False, True),
    "unknown": (False, True),
}

_CC_LICENSE_RE = re.compile(
    r"creativecommons\.org/licenses/([a-z0-9-]+)/([0-9.]+)?",
    re.IGNORECASE,
)
_CC_ZERO_RE = re.compile(
    r"creativecommons\.org/publicdomain/zero/",
    re.IGNORECASE,
)
_CC_MARK_RE = re.compile(
    r"creativecommons\.org/publicdomain/mark/",
    re.IGNORECASE,
)
_SAMPLING_RE = re.compile(
    r"creativecommons\.org/licenses/sampling",
    re.IGNORECASE,
)

_KIND_TO_SHORT: dict[str, str] = {
    "zero": "CC0",
    "by": "CC-BY",
    "by-sa": "CC-BY-SA",
    "by-nd": "CC-BY-ND",
    "by-nc": "CC-BY-NC",
    "by-nc-sa": "CC-BY-NC-SA",
    "by-nc-nd": "CC-BY-NC-ND",
}

_ALIAS: dict[str, str] = {
    "cc0": "CC0",
    "cc-0": "CC0",
    "publicdomain": "CC0",
    "public-domain": "CC0",
    "pdm": "PDM",
    "cc-by": "CC-BY",
    "cc by": "CC-BY",
    "cc-by-sa": "CC-BY-SA",
    "cc-by-nd": "CC-BY-ND",
    "cc-by-nc": "CC-BY-NC",
    "cc-by-nc-sa": "CC-BY-NC-SA",
    "cc-by-nc-nd": "CC-BY-NC-ND",
    "by": "CC-BY",
    "by-sa": "CC-BY-SA",
    "by-nd": "CC-BY-ND",
    "by-nc": "CC-BY-NC",
    "by-nc-sa": "CC-BY-NC-SA",
    "by-nc-nd": "CC-BY-NC-ND",
}


def normalize_license(raw: str | None) -> str:
    """URL 或自由文本 → 短名；无法识别则为 unknown。"""
    text = (raw or "").strip()
    if not text:
        return "unknown"
    if "://" in text or "creativecommons.org" in text.lower():
        return license_from_url(text)
    token = text.split("(")[0].strip()
    token = token.replace("4.0", "").replace("3.0", "").replace("2.0", "").replace("1.0", "")
    token = re.sub(r"\s+", "-", token).strip("-").lower()
    if token in LICENSE_RULES:
        return token.upper() if token.startswith("cc") or token in ("pdm",) else token
    if token in _ALIAS:
        return _ALIAS[token]
    upper = token.upper()
    if upper in LICENSE_RULES:
        return upper
    return "unknown"


def license_from_url(url: str) -> str:
    """https://creativecommons.org/licenses/by/4.0/ → CC-BY。"""
    text = (url or "").strip()
    if not text:
        return "unknown"
    if _SAMPLING_RE.search(text):
        return "sampling"
    if _CC_ZERO_RE.search(text):
        return "CC0"
    if _CC_MARK_RE.search(text):
        return "PDM"
    m = _CC_LICENSE_RE.search(text)
    if not m:
        return "unknown"
    kind = m.group(1).lower()
    if kind == "zero":
        return "CC0"
    return _KIND_TO_SHORT.get(kind, "unknown")


def can_commercial(license: str) -> bool:
    short = normalize_license(license)
    return LICENSE_RULES.get(short, LICENSE_RULES["unknown"])[0]


def need_attribution(license: str) -> bool:
    short = normalize_license(license)
    return LICENSE_RULES.get(short, LICENSE_RULES["unknown"])[1]


def is_share_alike(license: str) -> bool:
    return normalize_license(license) in ("CC-BY-SA", "CC-BY-NC-SA")


def is_no_derivatives(license: str) -> bool:
    return normalize_license(license) in ("CC-BY-ND", "CC-BY-NC-ND")


def allowed(
    license: str,
    *,
    commercial_only: bool = True,
    allow_share_alike: bool = False,
    allow_no_derivatives: bool = False,
) -> bool:
    """默认成片路径：可商用且非 SA/ND/unknown。"""
    short = normalize_license(license)
    if short == "unknown" or short == "sampling":
        return False
    commercial, _need = LICENSE_RULES.get(short, LICENSE_RULES["unknown"])
    if commercial_only and not commercial:
        return False
    if not allow_share_alike and is_share_alike(short):
        return False
    if not allow_no_derivatives and is_no_derivatives(short):
        return False
    return True


def attribution_text(hit: dict[str, Any] | None) -> str:
    """优先用条目自带 attribution；否则 {title} by {author} — {license}。"""
    item = hit or {}
    existing = str(item.get("attribution") or "").strip()
    if existing and "<曲名>" not in existing:
        return existing
    title = str(item.get("title") or "").strip() or "Untitled"
    author = str(item.get("author") or item.get("creator") or "").strip() or "Unknown"
    short = normalize_license(str(item.get("license") or ""))
    if not need_attribution(short):
        return existing
    return f"{title} by {author} — {short}"


def collect_attributions(events: Iterable[dict[str, Any]] | None) -> list[str]:
    """按 (title, author) 去重，保留首次出现顺序。"""
    seen: set[tuple[str, str]] = set()
    out: list[str] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        text = attribution_text(ev)
        if not text:
            continue
        key = (
            str(ev.get("title") or ev.get("asset_id") or text).strip().lower(),
            str(ev.get("author") or ev.get("creator") or "").strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out
