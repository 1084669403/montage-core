"""asset_catalog — 零依赖本地媒体资产目录检索（assets/*/INDEX.md）。

与 prompt_library 同构但面向**二进制媒体资产**（音效/音乐/LUT/字体）：
- 解析 ``assets/<category>/INDEX.md`` 的 ``- id: ...`` 条目（字段见下）；
- 按 query tokens / category / emotion / mood / bpm 检索；
- 返回条目时标注 ``available``（``file`` 相对路径是否存在）与下载指引，
  让 Agent 先检索、再决定是否需要触发下载（``assets/scripts/fetch_assets.py``）。

字段约定（与 assets 各 INDEX.md 对齐）：
    id / category / title / tags[] / emotion / mood / bpm / duration_seconds
    / license / source / source_url（可选直链）/ file（相对 assets/ 的目标路径）
    / attribution / notes

许可证是权威依据（license / attribution 字段原样返回），CC-BY 条目发布成片
时必须使用 attribution 字段署名（见 assets/LICENSE.md）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

_ASSETS_ROOT = Path(__file__).resolve().parent.parent / "assets"

# 参与索引的子库（目录名 = category）。
_CATEGORIES = ("sfx", "bgm", "luts", "fonts")

_CATEGORY_TITLES = {
    "sfx": "音效（Sonniss GDC）",
    "bgm": "背景音乐（FreePD / incompetech）",
    "luts": "调色 LUT（原创 .cube）",
    "fonts": "字幕字体（思源黑体等）",
}

# 情绪/用途别名（中文检索词 → 补全 token）。
_ALIASES: dict[str, list[str]] = {
    "雨": ["雨夜", "暴雨", "rain"],
    "雨夜": ["雨", "暴雨", "rain"],
    "追逐": ["追逃", "奔跑", "chase"],
    "打斗": ["战斗", "动作", "combat"],
    "悬疑": ["惊悚", "紧张", "suspense", "thriller"],
    "恐怖": ["惊悚", "诡异", "horror"],
    "惊悚": ["恐怖", "悬疑", "horror"],
    "紧张": ["压迫", "急促", "tension"],
    "治愈": ["温馨", "温暖", "healing"],
    "温馨": ["治愈", "温暖", "cozy"],
    "离别": ["悲伤", "告别", "sad"],
    "悲情": ["哀伤", "离别", "sad"],
    "高潮": ["史诗", "决战", "climax", "epic"],
    "史诗": ["高潮", "管弦", "epic"],
    "国风": ["古风", "传统", "chinese"],
    "古风": ["国风", "传统", "chinese"],
    "电影": ["电影感", "cinematic"],
    "纪录片": ["纪实", "克制", "documentary"],
    "黑白": ["单色", "monochrome"],
    "卡点": ["节拍", "对拍", "beat"],
    "节奏": ["bpm", "卡点", "beat"],
    "转场": ["whoosh", "过渡", "transition"],
    "背景": ["环境音", "氛围", "ambience"],
    "环境音": ["氛围", "铺底", "ambience"],
    "氛围": ["环境音", "铺底", "ambient"],
    "爆炸": ["冲击", "破坏", "explosion"],
    "脚步": ["footsteps", "脚步声"],
    "钢琴": ["piano", "抒情"],
    "弦乐": ["strings", "抒情"],
    "门": ["door", "creak"],
    "摔门": ["door", "slam"],
    "心跳": ["heartbeat"],
    "细雨": ["drizzle", "rain"],
}


def _tokenize(text: str) -> list[str]:
    """与 prompt_library 相同的零依赖分词：ASCII 词 + CJK 单字/二元/三元。"""
    if not text:
        return []
    tokens: list[str] = []
    for ascii_word in re.findall(r"[a-zA-Z][a-zA-Z0-9\-_]*", text.lower()):
        tokens.append(ascii_word)
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk:
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.append(run)
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
            tokens.extend(run[i : i + 3] for i in range(len(run) - 2))
    return tokens


def _expand_tokens(tokens: list[str]) -> set[str]:
    out: set[str] = set()
    for tok in tokens:
        out.add(tok)
        for alias in _ALIASES.get(tok, ()):
            out.add(alias)
            out.update(_tokenize(alias))
    return out


def english_search_terms(text: str) -> str:
    """中文配方词 → 英文检索串（Jamendo / Freesound）。"""
    seen: list[str] = []
    for tok in _tokenize(text or ""):
        aliases = _ALIASES.get(tok, ())
        english = [a for a in aliases if a.isascii()]
        candidates = english or ([tok] if tok.isascii() else [])
        for item in candidates:
            if item not in seen:
                seen.append(item)
    return " ".join(seen) if seen else (text or "").strip()


@dataclass
class AssetEntry:
    """一条资产条目（从 assets/*/INDEX.md 解析）。"""

    id: str
    category: str
    title: str = ""
    tags: list[str] = field(default_factory=list)
    emotion: str = ""
    mood: str = ""
    bpm: int = 0
    duration_seconds: int = 0
    license: str = ""
    source: str = ""
    source_url: str = ""
    file: str = ""
    attribution: str = ""
    notes: str = ""

    def to_dict(self, assets_root: Path) -> dict[str, Any]:
        """返回检索结果 dict；available = 目标 file 已落盘。"""
        file_val = (self.file or "").strip()
        available = bool(file_val) and not file_val.startswith("(") and (assets_root / file_val).is_file()
        if available:
            hint = ""
        elif self.source_url:
            hint = f"asset_retriever operation=resolve asset_id={self.id}"
        else:
            hint = "asset_retriever operation=resolve（条目需有 source_url）"
        return {
            "id": self.id,
            "category": self.category,
            "title": self.title,
            "tags": list(self.tags),
            "emotion": self.emotion,
            "mood": self.mood,
            "bpm": self.bpm,
            "duration_seconds": self.duration_seconds,
            "license": self.license,
            "source": self.source,
            "source_url": self.source_url,
            "file": file_val or "(未下载)",
            "available": available,
            "attribution": self.attribution,
            "notes": self.notes,
            "download_hint": hint,
        }


class AssetCatalog:
    """零依赖资产目录：解析 + 检索。"""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else _ASSETS_ROOT
        self._entries: list[AssetEntry] | None = None

    # -- 加载 ----------------------------------------------------------

    def _load(self) -> list[AssetEntry]:
        if self._entries is not None:
            return self._entries
        entries: list[AssetEntry] = []
        for category in _CATEGORIES:
            index_path = self.root / category / "INDEX.md"
            if not index_path.is_file():
                continue
            entries.extend(self._parse_index_file(category, index_path))
        self._entries = entries
        return entries

    @staticmethod
    def _parse_index_file(category: str, path: Path) -> list[AssetEntry]:
        entries: list[AssetEntry] = []
        current: dict[str, str] | None = None
        key_re = re.compile(r"^([a-z_]+):\s*(.*)$")

        def flush() -> None:
            if current is None or "id" not in current:
                return
            tags = [
                t.strip().strip("`[]")
                for t in str(current.get("tags", "")).split(",")
                if t.strip().strip("`[]")
            ]
            entry = AssetEntry(
                id=current["id"].strip("`"),
                category=current.get("category") or category,
                title=current.get("title", "").strip("`"),
                tags=tags,
                emotion=current.get("emotion", "").strip(),
                mood=current.get("mood", "").strip(),
                bpm=int(float(current.get("bpm", 0) or 0)),
                duration_seconds=int(float(current.get("duration_seconds", 0) or 0)),
                license=current.get("license", "").strip(),
                source=current.get("source", "").strip(),
                source_url=current.get("source_url", "").strip().strip('"'),
                file=current.get("file", "").strip(),
                attribution=current.get("attribution", "").strip().strip('"'),
                notes=current.get("notes", "").strip(),
            )
            entries.append(entry)

        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.rstrip()
            if line.startswith("- id:"):
                flush()
                current = {"id": line[len("- id:"):].strip()}
                continue
            if current is None or not line.startswith("  "):
                continue
            m = key_re.match(line.strip())
            if m:
                current[m.group(1)] = m.group(2).strip()
        flush()
        return entries

    @property
    def entries(self) -> list[AssetEntry]:
        return self._load()

    def __len__(self) -> int:
        return len(self._load())

    # -- 检索 ----------------------------------------------------------

    def _score(self, query_tokens: set[str], entry: AssetEntry) -> int:
        haystack = _expand_tokens(
            entry.tags
            + _tokenize(entry.title)
            + _tokenize(entry.emotion)
            + _tokenize(entry.mood)
            + _tokenize(entry.category)
        )
        tag_tokens = _expand_tokens(entry.tags + _tokenize(entry.title))
        score = 0
        for tok in query_tokens:
            if tok in haystack:
                score += 1
                if tok in tag_tokens:
                    score += 2
        return score

    def search(
        self,
        query: str = "",
        *,
        category: Optional[str] = None,
        emotion: Optional[str] = None,
        mood: Optional[str] = None,
        min_bpm: Optional[int] = None,
        max_bpm: Optional[int] = None,
        top_k: int = 5,
        available_only: bool = False,
    ) -> list[dict[str, Any]]:
        """检索资产条目。

        过滤：category / emotion（子串）/ mood（子串）/ bpm 区间 / available_only。
        排序：query 命中得分（无 query 时按 id 顺序）。
        """
        entries = self._load()
        query_tokens = _expand_tokens(_tokenize(query)) if query else set()

        scored: list[tuple[int, AssetEntry]] = []
        for entry in entries:
            if category and entry.category != category:
                continue
            if emotion and emotion not in (entry.emotion + "".join(entry.tags)):
                continue
            if mood and mood not in (entry.mood + "".join(entry.tags)):
                continue
            if min_bpm is not None and entry.bpm and entry.bpm < min_bpm:
                continue
            if max_bpm is not None and entry.bpm and entry.bpm > max_bpm:
                continue
            if available_only:
                file_val = (entry.file or "").strip()
                if not file_val or file_val.startswith("(") or not (self.root / file_val).is_file():
                    continue
            score = self._score(query_tokens, entry) if query_tokens else 1
            if query_tokens and score == 0:
                continue
            scored.append((score, entry))

        scored.sort(key=lambda x: (-x[0], x[1].id))
        return [entry.to_dict(self.root) for _, entry in scored[:top_k]]

    def get(self, asset_id: str) -> dict[str, Any] | None:
        """按 id 取一条（含 available / source_url）。"""
        wanted = (asset_id or "").strip().strip("`")
        if not wanted:
            return None
        for entry in self._load():
            if entry.id == wanted:
                return entry.to_dict(self.root)
        return None

    def by_category(self, category: str) -> list[dict[str, Any]]:
        return [e.to_dict(self.root) for e in self._load() if e.category == category]

    def categories(self) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for e in self._load():
            counts[e.category] = counts.get(e.category, 0) + 1
        return [
            {"id": c, "title": _CATEGORY_TITLES.get(c, c), "count": counts.get(c, 0)}
            for c in _CATEGORIES
        ]


_default_catalog: Optional[AssetCatalog] = None


def get_catalog() -> AssetCatalog:
    global _default_catalog
    if _default_catalog is None:
        _default_catalog = AssetCatalog()
    return _default_catalog


def search(
    query: str = "",
    *,
    category: Optional[str] = None,
    emotion: Optional[str] = None,
    mood: Optional[str] = None,
    min_bpm: Optional[int] = None,
    max_bpm: Optional[int] = None,
    top_k: int = 5,
    available_only: bool = False,
) -> list[dict[str, Any]]:
    """模块级便捷包装（单例目录）。"""
    return get_catalog().search(
        query,
        category=category,
        emotion=emotion,
        mood=mood,
        min_bpm=min_bpm,
        max_bpm=max_bpm,
        top_k=top_k,
        available_only=available_only,
    )
