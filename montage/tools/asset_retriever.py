"""asset_retriever — 媒体资产目录检索 + 按 source_url 落地。

operation=search（默认）：查 assets/*/INDEX.md。
  remote=true：额外查 Jamendo（bgm）/ Freesound（sfx），结果进 remote_hits，
  不写仓库 INDEX；下载落到项目 assets/。
operation=resolve：按 asset_id 或 soundtrack.events 下载到 file 路径
（原子写 + 校验），不改 INDEX.md。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from lib.asset_catalog import AssetCatalog, english_search_terms, get_catalog
from lib.licensing import allowed, attribution_text, normalize_license
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

JAMENDO_TRACKS = "https://api.jamendo.com/v3.0/tracks/"
FREESOUND_SEARCH = "https://freesound.org/apiv2/search/text/"

_AMBIENCE_TERMS = {
    "rain",
    "drizzle",
    "storm",
    "ambience",
    "ambient",
    "crowd",
    "birds",
    "dawn",
    "room",
    "雨",
    "暴雨",
    "细雨",
    "环境音",
    "底噪",
    "人群",
    "鸟",
}


def resolve_hit(
    hit: dict[str, Any],
    *,
    assets_root: Path,
    download_fn: Callable[..., Path] | None = None,
    probe_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """把一条目录 hit 落到磁盘；已存在则跳过。"""
    from montage.tools.downloader import download_verified

    out = dict(hit)
    file_val = str(hit.get("file") or "").strip()
    if not file_val or file_val.startswith("("):
        out["available"] = False
        out["error"] = "条目没有目标 file 路径"
        return out
    dest = assets_root / file_val
    if dest.is_file():
        out["available"] = True
        out["path"] = str(dest)
        out["resolved"] = "exists"
        return out
    url = str(hit.get("source_url") or "").strip().strip('"')
    if not url:
        out["available"] = False
        out["path"] = str(dest)
        out["error"] = "缺少 source_url"
        return out
    try:
        kwargs: dict[str, Any] = {}
        if download_fn is not None:
            kwargs["download_fn"] = download_fn
        if probe_fn is not None:
            kwargs["probe_fn"] = probe_fn
        path = download_verified(url, dest, **kwargs)
    except Exception as exc:  # noqa: BLE001
        out["available"] = False
        out["path"] = str(dest)
        out["error"] = str(exc)
        return out
    out["available"] = True
    out["path"] = str(path)
    out["resolved"] = "downloaded"
    out.pop("error", None)
    return out


def _is_remote_hit(hit: dict[str, Any]) -> bool:
    if hit.get("remote"):
        return True
    hid = str(hit.get("id") or hit.get("asset_id") or "")
    return hid.startswith("jamendo/") or hid.startswith("freesound/")


def assets_root_for(
    hit: dict[str, Any],
    catalog_root: Path,
    project_dir: str | None,
) -> Path | None:
    """INDEX 钉选写仓库 assets/；远程换货写项目 assets/。"""
    if not _is_remote_hit(hit):
        return catalog_root
    if not (project_dir or "").strip():
        return None
    return Path(project_dir) / "assets"


def search_jamendo(
    query: str,
    *,
    client_id: str,
    top_k: int = 5,
    get_json_fn: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Jamendo 器乐曲；仅 CC0/CC-BY 且 audiodownload 可用。"""
    from montage.providers.http import get_json

    fetcher = get_json_fn or get_json
    q = english_search_terms(query) or query
    limit = min(max(top_k * 4, top_k), 20)
    resp = fetcher(
        JAMENDO_TRACKS,
        timeout=30,
        params={
            "client_id": client_id,
            "format": "json",
            "limit": str(limit),
            "search": q,
            "vocalinstrumental": "instrumental",
            "include": "musicinfo",
            "audioformat": "mp32",
        },
    )
    hits: list[dict[str, Any]] = []
    for item in (resp or {}).get("results") or []:
        if not isinstance(item, dict):
            continue
        duration = int(float(item.get("duration") or 0) or 0)
        if duration < 30:
            continue
        if item.get("audiodownload_allowed") is False:
            continue
        url = str(item.get("audiodownload") or "").strip()
        if not url:
            continue
        license_raw = str(item.get("license_ccurl") or item.get("license") or "")
        if not allowed(license_raw):
            continue
        short = normalize_license(license_raw)
        tid = str(item.get("id") or "")
        title = str(item.get("name") or "Untitled")
        author = str(item.get("artist_name") or "")
        hit = {
            "id": f"jamendo/{tid}",
            "category": "bgm",
            "title": title,
            "tags": [],
            "emotion": "",
            "mood": "",
            "bpm": 0,
            "duration_seconds": duration,
            "license": short,
            "source": "Jamendo",
            "source_url": url,
            "file": f"bgm/jamendo_{tid}.mp3",
            "available": False,
            "author": author,
            "attribution": "",
            "notes": "远程换货；resolve 到项目 assets/，不写仓库 INDEX",
            "download_hint": "asset_retriever operation=resolve + project_dir + events",
            "remote": True,
            "provider": "jamendo",
            "page": str(item.get("shareurl") or item.get("shorturl") or ""),
        }
        hit["attribution"] = attribution_text(hit)
        hits.append(hit)
        if len(hits) >= top_k:
            break
    return hits


def _freesound_duration_filter(query: str) -> str | None:
    blob = f"{query} {english_search_terms(query)}".lower()
    if any(term in blob for term in _AMBIENCE_TERMS):
        return "duration:[15 TO 90]"
    if (query or "").strip():
        return "duration:[0.4 TO 8]"
    return None


def search_freesound(
    query: str,
    *,
    api_key: str,
    top_k: int = 5,
    get_json_fn: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Freesound 检索（token）；原文件需 OAuth，v1 不把 preview 当 source_url。"""
    from montage.providers.http import get_json

    fetcher = get_json_fn or get_json
    q = english_search_terms(query) or query
    page_size = min(max(top_k * 4, top_k), 30)
    params: dict[str, Any] = {
        "token": api_key,
        "query": q,
        "page_size": str(page_size),
        "fields": "id,name,duration,license,username,url,tags",
    }
    dur = _freesound_duration_filter(query)
    if dur:
        params["filter"] = dur
    resp = fetcher(FREESOUND_SEARCH, timeout=30, params=params)
    hits: list[dict[str, Any]] = []
    for item in (resp or {}).get("results") or []:
        if not isinstance(item, dict):
            continue
        license_raw = str(item.get("license") or "")
        if not allowed(license_raw):
            continue
        short = normalize_license(license_raw)
        sid = str(item.get("id") or "")
        title = str(item.get("name") or "Untitled")
        author = str(item.get("username") or "")
        duration = int(float(item.get("duration") or 0) or 0)
        hit = {
            "id": f"freesound/{sid}",
            "category": "sfx",
            "title": title,
            "tags": list(item.get("tags") or [])[:8],
            "emotion": "",
            "mood": "",
            "bpm": 0,
            "duration_seconds": duration,
            "license": short,
            "source": "Freesound",
            "source_url": "",
            "file": "",
            "available": False,
            "author": author,
            "attribution": "",
            "notes": "Freesound 原文件下载需要 OAuth；v1 不使用 preview 当成片",
            "download_hint": "换货请改用 INDEX 钉选或 Jamendo；preview 不作 source_url",
            "remote": True,
            "provider": "freesound",
            "page": str(item.get("url") or ""),
        }
        hit["attribution"] = attribution_text(hit)
        hits.append(hit)
        if len(hits) >= top_k:
            break
    return hits


class AssetRetriever(BaseTool):
    name = "asset_retriever"
    version = "0.3.0"
    capability = "asset_retrieval"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["search", "resolve"],
                "description": "search 查目录；resolve 按 source_url 下载到 file 路径",
            },
            "query": {"type": "string", "description": "检索词（情绪/场景/用途），如 雨夜 紧张"},
            "category": {
                "type": "string",
                "enum": ["sfx", "bgm", "luts", "fonts"],
                "description": "资产类别：音效/音乐/调色/字体",
            },
            "emotion": {"type": "string", "description": "情绪过滤（子串）"},
            "mood": {"type": "string", "description": "情境过滤（子串），如 雨夜/追逐/高潮"},
            "min_bpm": {"type": "integer", "description": "BPM 下限（卡点剪辑用）"},
            "max_bpm": {"type": "integer", "description": "BPM 上限"},
            "top_k": {"type": "integer", "default": 5},
            "available_only": {"type": "boolean", "default": False},
            "asset_id": {"type": "string", "description": "resolve 单条目录 id"},
            "events": {
                "type": "array",
                "description": "resolve soundtrack.events（读每条 asset_id）",
            },
            "remote": {
                "type": "boolean",
                "description": "search 时额外查 Jamendo（bgm）/ Freesound（sfx）",
            },
            "project_dir": {
                "type": "string",
                "description": "远程 resolve 落盘目录（projects/<id>/assets）",
            },
        },
    }

    def __init__(
        self,
        catalog: AssetCatalog | None = None,
        download_fn: Callable[..., Path] | None = None,
        probe_fn: Callable[..., Any] | None = None,
        get_json_fn: Callable[..., Any] | None = None,
    ) -> None:
        self._catalog = catalog
        self._download_fn = download_fn
        self._probe_fn = probe_fn
        self._get_json = get_json_fn

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        cat = self._catalog or get_catalog()
        op = str(inputs.get("operation") or "search").strip().lower()
        if op == "resolve":
            return self._execute_resolve(cat, inputs)
        return self._execute_search(cat, inputs)

    def _execute_search(self, cat: AssetCatalog, inputs: dict[str, Any]) -> ToolResult:
        try:
            hits = cat.search(
                inputs.get("query") or "",
                category=inputs.get("category"),
                emotion=inputs.get("emotion"),
                mood=inputs.get("mood"),
                min_bpm=inputs.get("min_bpm"),
                max_bpm=inputs.get("max_bpm"),
                top_k=int(inputs.get("top_k", 5)),
                available_only=bool(inputs.get("available_only", False)),
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"资产目录检索失败: {exc}")
        remote_hits: list[dict[str, Any]] = []
        errors: list[str] = []
        if inputs.get("remote"):
            remote_hits, errors = self._search_remote(inputs)
        return ToolResult(
            success=True,
            data={
                "count": len(hits),
                "hits": hits,
                "remote_hits": remote_hits,
                "errors": errors,
                "usage": (
                    "未下载条目用 operation=resolve + asset_id；"
                    "remote=true 换货进 remote_hits，resolve 需 project_dir；CC-BY 需署名"
                ),
            },
        )

    def _search_remote(self, inputs: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        from montage.providers.http import HttpError

        category = str(inputs.get("category") or "").strip().lower()
        query = str(inputs.get("query") or "").strip()
        top_k = int(inputs.get("top_k", 5))
        want_bgm = category in ("", "bgm")
        want_sfx = category in ("", "sfx")
        if category in ("luts", "fonts"):
            return [], ["remote 仅用于 bgm/sfx"]
        remote_hits: list[dict[str, Any]] = []
        errors: list[str] = []
        if want_bgm:
            jamendo_id = (os.environ.get("JAMENDO_CLIENT_ID") or "").strip()
            if not jamendo_id:
                errors.append("缺少 JAMENDO_CLIENT_ID")
            else:
                try:
                    remote_hits.extend(
                        search_jamendo(
                            query,
                            client_id=jamendo_id,
                            top_k=top_k,
                            get_json_fn=self._get_json,
                        )
                    )
                except HttpError as exc:
                    errors.append(f"Jamendo: {exc}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Jamendo: {exc}")
        if want_sfx:
            fs_key = (os.environ.get("FREESOUND_API_KEY") or "").strip()
            if not fs_key:
                errors.append("缺少 FREESOUND_API_KEY")
            else:
                try:
                    remote_hits.extend(
                        search_freesound(
                            query,
                            api_key=fs_key,
                            top_k=top_k,
                            get_json_fn=self._get_json,
                        )
                    )
                except HttpError as exc:
                    errors.append(f"Freesound: {exc}")
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"Freesound: {exc}")
        return remote_hits, errors

    def _execute_resolve(self, cat: AssetCatalog, inputs: dict[str, Any]) -> ToolResult:
        project_dir = str(inputs.get("project_dir") or "").strip()
        events = [e for e in (inputs.get("events") or []) if isinstance(e, dict)]
        event_by_id: dict[str, dict[str, Any]] = {}
        for ev in events:
            key = str(ev.get("asset_id") or ev.get("id") or "").strip()
            if key:
                event_by_id[key] = ev
        ids: list[str] = []
        asset_id = str(inputs.get("asset_id") or "").strip()
        if asset_id:
            ids.append(asset_id)
        ids.extend(event_by_id.keys())
        seen: set[str] = set()
        uniq: list[str] = []
        for aid in ids:
            if aid not in seen:
                seen.add(aid)
                uniq.append(aid)
        if not uniq:
            return ToolResult(success=False, error="resolve 需要 asset_id 或 events[].asset_id")
        resolved: list[dict[str, Any]] = []
        for aid in uniq:
            hit = cat.get(aid)
            if not hit:
                ev = event_by_id.get(aid)
                if ev and (ev.get("source_url") or ev.get("file")):
                    hit = dict(ev)
                    hit.setdefault("id", aid)
                else:
                    resolved.append({"id": aid, "available": False, "error": "目录中无此 id"})
                    continue
            root = assets_root_for(hit, cat.root, project_dir)
            if root is None:
                out = dict(hit)
                out["available"] = False
                out["error"] = "远程条目需要 project_dir，避免写入仓库 INDEX"
                resolved.append(out)
                continue
            resolved.append(
                resolve_hit(
                    hit,
                    assets_root=root,
                    download_fn=self._download_fn,
                    probe_fn=self._probe_fn,
                )
            )
        missing = [h for h in resolved if not h.get("available")]
        return ToolResult(
            success=True,
            data={
                "count": len(resolved),
                "hits": resolved,
                "missing": missing,
            },
        )
