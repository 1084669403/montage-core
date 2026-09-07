"""stock_retriever — 素材源检索（100% 自创实现，国内优先/免费源可用）。

素材源（均为官方公开 API，自创请求构造）：
- ``wikimedia``：维基共享资源（**免费、无需密钥**，公有领域/CC 素材），默认可用；
- ``pixabay``：图片/视频（需 PIXABAY_API_KEY，免费注册）；
- ``pexels``：视频/图片（需 PEXELS_API_KEY，免费注册）。

返回条目含许可证说明（license），供资产清单与成片 credits 使用；
下载素材用 ``downloader`` 工具落盘到项目 assets/。
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

from montage.providers.http import HttpError, get_json
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
PIXABAY_API = "https://pixabay.com/api/"
PEXELS_API = "https://api.pexels.com/videos/search"


def _allowed_commercial(license_short: str, *, default: bool = True) -> bool:
    low = f" {str(license_short or '').lower()} "
    if any(token in low for token in (" noncommercial", " non-commercial", " nc ", "-nc", "nc-")):
        return False
    return default


def search_wikimedia(query: str, *, top_k: int = 10, media_type: str = "image") -> list[dict[str, Any]]:
    """维基共享资源搜索（免费、无需密钥；公有领域/CC 素材）。

    media_type: image（默认）或 video（gsrnamespace 不同）。
    """
    params = {
        "action": "query",
        "generator": "search",
        "gsrsearch": query,
        "gsrnamespace": "6" if media_type == "image" else "14",
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "format": "json",
        "gsrlimit": str(top_k),
    }
    url = f"{WIKIMEDIA_API}?{urllib.parse.urlencode(params)}"
    try:
        resp = get_json(url, timeout=30)
    except HttpError as exc:
        return [{"provider": "wikimedia", "error": str(exc)}]
    pages = ((resp or {}).get("query") or {}).get("pages") or {}
    hits: list[dict[str, Any]] = []
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        url_val = info.get("url")
        if not url_val:
            continue
        ext = (info.get("extmetadata") or {})
        license_short = ((ext.get("LicenseShortName") or {}).get("value") or "Public Domain").strip()
        hits.append({
            "provider": "wikimedia",
            "url": url_val,
            "title": page.get("title", ""),
            "license": license_short,
            "allowed_commercial": _allowed_commercial(license_short),
            "media_type": media_type,
        })
    return hits


def search_pixabay(query: str, *, top_k: int = 10, media_type: str = "image") -> list[dict[str, Any]]:
    """Pixabay 搜索（需 PIXABAY_API_KEY；图片或视频）。"""
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return [{"provider": "pixabay", "error": "缺少 PIXABAY_API_KEY"}]
    params = {"key": key, "q": query, "per_page": str(top_k)}
    if media_type == "video":
        params["video_type"] = "film"
    resp = get_json(f"{PIXABAY_API}?{urllib.parse.urlencode(params)}", timeout=30)
    hits: list[dict[str, Any]] = []
    for item in (resp or {}).get("hits") or []:
        url = item.get("largeImageURL") or item.get("webformatURL") or item.get("videos", {}).get("medium", {}).get("url")
        if not url:
            continue
        hits.append({
            "provider": "pixabay",
            "url": url,
            "title": item.get("tags", ""),
            "license": "Pixabay Content License（免费商用，无需署名）",
            "allowed_commercial": True,
            "media_type": media_type,
        })
    return hits


def search_pexels(query: str, *, top_k: int = 10) -> list[dict[str, Any]]:
    """Pexels 视频搜索（需 PEXELS_API_KEY）。"""
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return [{"provider": "pexels", "error": "缺少 PEXELS_API_KEY"}]
    url = f"{PEXELS_API}?{urllib.parse.urlencode({'query': query, 'per_page': top_k})}"
    resp = get_json(url, headers={"Authorization": key}, timeout=30)
    hits: list[dict[str, Any]] = []
    for item in (resp or {}).get("videos") or []:
        files = item.get("video_files") or []
        best = max(files, key=lambda f: int(f.get("width") or 0), default={})
        url_val = best.get("link") or best.get("url")
        if not url_val:
            continue
        hits.append({
            "provider": "pexels",
            "url": url_val,
            "title": item.get("url", ""),
            "license": "Pexels License（免费商用，无需署名）",
            "allowed_commercial": True,
            "media_type": "video",
        })
    return hits


class StockRetriever(BaseTool):
    """素材源检索：wikimedia（免费无 key）/ pixabay / pexels。"""

    name = "stock_retriever"
    version = "0.1.0"
    capability = "stock_search"
    provider = "openmontage"
    runtime = ToolRuntime.API
    env_keys = ()
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string"},
            "provider": {
                "type": "string",
                "enum": ["wikimedia", "pixabay", "pexels", "all"],
                "default": "all",
                "description": "素材源：wikimedia 免费无需密钥，pixabay/pexels 需对应 API key",
            },
            "media_type": {"type": "string", "enum": ["image", "video"], "default": "image"},
            "top_k": {"type": "integer", "default": 10},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        query = (inputs.get("query") or "").strip()
        if not query:
            return ToolResult(success=False, error="'query' 必填")
        provider = inputs.get("provider", "all")
        media_type = inputs.get("media_type", "image")
        top_k = int(inputs.get("top_k", 10))

        providers = ["wikimedia", "pixabay", "pexels"] if provider == "all" else [provider]
        all_hits: list[dict[str, Any]] = []
        for prov in providers:
            if prov == "wikimedia":
                all_hits.extend(search_wikimedia(query, top_k=top_k, media_type=media_type))
            elif prov == "pixabay":
                all_hits.extend(search_pixabay(query, top_k=top_k, media_type=media_type))
            elif prov == "pexels" and media_type == "video":
                all_hits.extend(search_pexels(query, top_k=top_k))

        errors = [h["error"] for h in all_hits if "error" in h]
        hits = [h for h in all_hits if "url" in h]
        return ToolResult(
            success=True,
            data={
                "query": query,
                "hits": hits,
                "count": len(hits),
                "errors": errors,
                "usage": "条目含 license（供 credits）；下载用 downloader 落盘到项目 assets/",
            },
        )
