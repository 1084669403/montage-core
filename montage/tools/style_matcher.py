"""style_matcher — 主题/风格词 → playbook；W1 增加 operation=cascade。

7 本 playbook 做加权关键词打分，不上向量检索。cascade 写出 format_card 结构
（默认不落盘）。禁止第二个 matcher 工具。
"""

from __future__ import annotations

from typing import Any

from montage.playbooks import get_playbook, list_playbooks
from montage.style_packs import list_style_packs
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

# 显式别名：用户口头风格词 → playbook id（高权重）
_ALIASES: dict[str, str] = {
    "动漫": "anime_shonen",
    "日漫": "anime_shonen",
    "国漫": "anime_shonen",
    "动画": "anime_shonen",
    "漫画": "manga_panel",
    "条漫": "manga_panel",
    "口播": "spoken_explain",
    "讲解": "spoken_explain",
    "科普": "spoken_explain",
    "纪录片": "documentary_restraint",
    "纪实": "documentary_restraint",
    "访谈": "documentary_restraint",
    "赛博": "cyberpunk_neon",
    "赛博朋克": "cyberpunk_neon",
    "霓虹": "cyberpunk_neon",
    "国风": "chinese_elegance",
    "水墨": "chinese_elegance",
    "古风": "chinese_elegance",
    "仙侠": "chinese_elegance",
    "治愈": "healing_japanese",
    "日系": "healing_japanese",
    "清新": "healing_japanese",
}

_MEDIUM_PINS: tuple[tuple[str, str], ...] = (
    ("做成电影", "film"),
    ("做成动漫", "anime"),
    ("做成动画", "anime"),
    ("做成口播", "spoken"),
    ("做成纪录片", "documentary"),
    ("做成漫画", "manga"),
    ("做成短剧", "short_drama"),
    ("做成mv", "mv"),
    ("做成 MV", "mv"),
)

_MEDIUM_WORDS: tuple[tuple[str, str], ...] = (
    ("纪录片", "documentary"),
    ("纪实", "documentary"),
    ("访谈", "documentary"),
    ("口播", "spoken"),
    ("讲解", "spoken"),
    ("科普", "spoken"),
    ("动漫", "anime"),
    ("日漫", "anime"),
    ("国漫", "anime"),
    ("动画", "anime"),
    ("漫画", "manga"),
    ("条漫", "manga"),
    ("短剧", "short_drama"),
    ("电影", "film"),
    ("mv", "mv"),
    ("MV", "mv"),
    ("切片", "clip"),
)

_GENRE_WORDS: tuple[str, ...] = (
    "科幻", "动作", "惊悚", "恐怖", "爱情", "喜剧", "悬疑",
    "武侠", "仙侠", "战争", "历史", "奇幻", "犯罪",
)

_MEDIUM_PLAYBOOK = {
    "spoken": "spoken_explain",
    "documentary": "documentary_restraint",
    "anime": "anime_shonen",
    "manga": "manga_panel",
}

_MEDIUM_PIPELINE = {
    "documentary": "documentary",
    "clip": "clip_factory",
}


def _haystack(playbook: dict[str, Any]) -> str:
    identity = playbook.get("identity") or {}
    visual = playbook.get("visual_language") or {}
    parts = [
        playbook.get("id") or "",
        playbook.get("title") or "",
        identity.get("mood") or "",
        identity.get("best_for") or "",
        identity.get("name") or "",
        visual.get("aesthetic") or "",
    ]
    return " ".join(str(p) for p in parts).lower()


def match_styles(query: str, *, top_k: int = 3) -> list[dict[str, Any]]:
    """返回 [{playbook, style_pack, score, reason}]，按分数降序。"""
    text = (query or "").strip()
    if not text:
        return []
    scores: dict[str, tuple[float, list[str]]] = {}
    for alias, pid in _ALIASES.items():
        if alias in text:
            reasons = scores.get(pid, (0.0, []))[1] + [f"命中风格词「{alias}」"]
            scores[pid] = (scores.get(pid, (0.0, []))[0] + 8.0, reasons)

    tokens = [t for t in text.replace("，", " ").replace(",", " ").split() if t]
    for summary in list_playbooks():
        pid = summary["id"]
        pb = get_playbook(pid) or {}
        hay = _haystack(pb)
        gained = 0.0
        hits: list[str] = []
        for tok in tokens:
            if len(tok) < 2:
                continue
            if tok.lower() in hay or tok in hay:
                gained += 2.0
                hits.append(tok)
        if gained:
            prev, reasons = scores.get(pid, (0.0, []))
            scores[pid] = (prev + gained, reasons + ([f"文本重合：{'/'.join(hits[:4])}"] if hits else []))

    pack_by_playbook = {
        p.get("bind_playbook"): p["id"]
        for p in list_style_packs()
        if p.get("bind_playbook")
    }
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1][0], kv[0]))
    results: list[dict[str, Any]] = []
    for pid, (score, reasons) in ranked[: max(1, int(top_k))]:
        pb = get_playbook(pid)
        if pb is None:
            continue
        results.append({
            "playbook": pid,
            "title": pb.get("title") or pid,
            "style_pack": pack_by_playbook.get(pid),
            "score": round(score, 2),
            "reason": "；".join(reasons) or "弱匹配",
            "script_style": dict(pb.get("script_style") or {}),
        })
    return results


def detect_medium(query: str) -> tuple[str, bool]:
    """返回 (medium, pinned)。pinned=用户明文指定。"""
    text = query or ""
    lowered = text.lower()
    for phrase, medium in _MEDIUM_PINS:
        if phrase.lower() in lowered or phrase in text:
            return medium, True
    for word, medium in _MEDIUM_WORDS:
        if word.lower() in lowered or word in text:
            return medium, True
    if "讲" in text or "量子" in text:
        return "spoken", False
    return "film", False


def detect_genres(query: str) -> list[str]:
    found: list[str] = []
    for word in _GENRE_WORDS:
        if word in (query or ""):
            found.append(word)
    return found


def pipeline_for_medium(medium: str) -> str:
    return _MEDIUM_PIPELINE.get(medium, "cinematic")


def _library_hits(query: str, *, top_k: int = 5) -> list[str]:
    try:
        from lib.prompt_library import PromptLibrary
    except ImportError:
        return []
    hits = PromptLibrary().search(query, top_k=top_k, include_prompt=False)
    ids: list[str] = []
    for row in hits:
        if isinstance(row, dict) and row.get("id"):
            ids.append(str(row["id"]))
    return ids


def cascade_styles(query: str, *, top_k: int = 3) -> dict[str, Any]:
    """介质 → 类型 → 画风。返回 format_card，不写盘。"""
    text = (query or "").strip()
    medium, pinned = detect_medium(text)
    genres = detect_genres(text)
    matches = match_styles(text, top_k=max(3, int(top_k)))
    mapped = _MEDIUM_PLAYBOOK.get(medium)
    playbook = mapped or (matches[0]["playbook"] if matches else "")
    if mapped and not any(m.get("playbook") == mapped for m in matches):
        pb = get_playbook(mapped) or {}
        matches = [{
            "playbook": mapped,
            "title": pb.get("title") or mapped,
            "style_pack": None,
            "score": 8.0,
            "reason": f"介质 {medium} 默认 playbook",
            "script_style": dict(pb.get("script_style") or {}),
        }] + matches
    alternatives = []
    seen = {playbook}
    for row in matches:
        pid = str(row.get("playbook") or "")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        alternatives.append({
            "medium": medium,
            "genres": list(genres),
            "playbook": pid,
            "pipeline_type": pipeline_for_medium(medium),
            "reason": row.get("reason") or "",
        })
        if len(alternatives) >= 3:
            break
    findings: list[dict[str, str]] = []
    if not playbook:
        findings.append({
            "severity": "warning",
            "stage": "format_card",
            "field": "playbook",
            "message": "词库/playbook 无匹配，请写项目内 overlay，不要把在世画师写入仓库",
            "proposed_fix": "在项目 artifacts 写 overlay，或改想法措辞后重跑 cascade",
        })
    card = {
        "query": text,
        "pinned_medium": pinned,
        "chosen": {
            "medium": medium,
            "genres": list(genres),
            "playbook": playbook,
            "pipeline_type": pipeline_for_medium(medium),
        },
        "alternatives": alternatives[:3],
        "user_specified": {
            "medium": pinned,
            "genres": list(genres),
        },
        "library_hit_ids": ([playbook] if playbook else []) + _library_hits(text),
        "findings": findings,
    }
    return card


class StyleMatcher(BaseTool):
    name = "style_matcher"
    version = "0.2.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {"type": "string", "description": "主题 + 风格词，如「雨夜追杀 赛博朋克」"},
            "top_k": {"type": "integer", "default": 3},
            "operation": {
                "type": "string",
                "enum": ["match", "cascade"],
                "default": "match",
            },
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        query = str(inputs.get("query") or "").strip()
        if not query:
            return ToolResult(success=False, error="query 必填")
        top_k = int(inputs.get("top_k") or 3)
        op = str(inputs.get("operation") or "match")
        if op == "cascade":
            card = cascade_styles(query, top_k=top_k)
            return ToolResult(
                success=True,
                data={"format_card": card, "query": query},
                meta={"operation": "cascade", "medium": card["chosen"]["medium"]},
            )
        matches = match_styles(query, top_k=top_k)
        return ToolResult(
            success=True,
            data={"matches": matches, "query": query},
            meta={"count": len(matches), "operation": "match"},
        )
