"""提示词词库 —— 对 `prompt_library/` 的本地、确定性检索。

该词库是一组人工整理的结构化提示词条目（场景 / 特效 /
动作 / 镜头 / 光线 / 风格），从 MIT 许可的开源提示词 skill 中抽取并重写
（见 `prompt_library/CREDITS.md`）。

markdown 文件是唯一数据源（人类可读、可 git diff）。
本模块把 `- id: ...` 条目块解析成结构化记录，然后提供关键词检索，
让 LLM（经由 `prompt_library_retriever_tool`）在改编某个镜头的
`visual_details` / `cinematography` / 动作节拍之前，能拉取相关参考条目。

检索刻意做成确定性的、无依赖的（仅标准库，不用 jieba / numpy）：
条目按标签 / 标题 / prompt 子串以及一个内置的小别名表来匹配。
它是一个 *参考* 层，绝不是神谕 —— LLM 是改编，不是照抄。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

# prompt_library 的根目录（REPO_ROOT/prompt_library）。
_LIBRARY_ROOT = Path(__file__).resolve().parent.parent / "prompt_library"

# 只有这些类别会被索引；_CATEGORY_TITLES 保存显示名。
_CATEGORIES = (
    "scenes", "effects", "actions", "shots", "lighting", "styles",
    "directors", "scripts", "screenplays", "edits", "characters", "dialogue",
)

_CATEGORY_TITLES = {
    "scenes": "场景/环境",
    "effects": "特效/粒子/破坏/能量",
    "actions": "动作/姿态/表情",
    "shots": "景别/运镜/角度/焦段/转场",
    "lighting": "光线/色调/风格化影调",
    "styles": "风格预置/去AI味/限制词",
    "directors": "导演风格/类型范式",
    "scripts": "剧本/对白写作范式",
    "screenplays": "公有领域剧本范式（场景/对白/结构参考）",
    "edits": "剪辑/转场/节奏决策范式",
    "characters": "人物外貌锚点（contrast_group 一集不复用）",
    "dialogue": "口吻范式（不是成品台词）",
}

# shot_kind 语义：管线使用 "image"/"video"（shot_prompts schema 里的 shot_kind）；
# 词库使用 first_frame/video/both。
_SHOT_KIND_IMAGE = ("first_frame", "both")
_SHOT_KIND_VIDEO = ("video", "both")

# 小别名表，让常用中文搜索词即使条目标签用了不同措辞也能命中正确的条目。
# 键 -> 额外 token 列表。
_ALIASES: dict[str, list[str]] = {
    "战斗": ["打斗", "搏斗", "动作戏", "fight", "combat"],
    "打斗": ["战斗", "搏斗", "动作戏"],
    "追逐": ["追车", "追赶", "奔跑", "chase", "run"],
    "爆炸": ["爆破", "爆炸碎片", "explosion", "fire"],
    "雨夜": ["下雨", "暴雨", "雨", "rain"],
    "雪": ["飘雪", "雪花", "下雪", "snow"],
    "泪": ["哭", "哭泣", "含泪", "tears", "cry"],
    "笑": ["微笑", "冷笑", "笑容", "smile"],
    "镜头": ["运镜", "景别", "机位", "camera"],
    "特写": ["近景", "大特写", "closeup", "close-up"],
    "情绪": ["emotion", "情感", "氛围"],
    "光": ["光线", "灯光", "光影", "light"],
    "风格": ["style", "画风", "质感"],
    "仙侠": ["修仙", "武侠", "玄幻", "xianxia"],
    "武侠": ["仙侠", "江湖", "刀剑", "wuxia"],
    "末日": ["废土", "丧尸", "灾难", "postapoc"],
    "赛博": ["赛博朋克", "科幻都市", "cyberpunk", "neon"],
    "中国": ["中国风", "国风", "古风", "中式"],
    "古风": ["中国风", "国风", "古典", "汉服"],
    "电影": ["电影感", "电影质感", "cinematic"],
    "动漫": ["动画", "日漫", "anime", "卡通"],
    "恐怖": ["惊悚", "诡异", "吓人", "horror"],
    "悬疑": ["惊悚", "悬念", "mystery", "suspense", "thriller"],
    "浪漫": ["爱情", "恋爱", "唯美", "romance"],
    "治愈": ["温馨", "温暖", "感人", "healing"],
    "史诗": ["宏大", "壮阔", "史诗感", "epic", "战争"],
    "压迫": ["压迫感", "压抑", "紧张", "tension"],
    "宁静": ["安静", "静谧", "平静", "calm"],
    "导演": ["风格", "类型范式", "director", "style-director"],
    "纪实": ["纪录片", "克制", "真实", "documentary"],
    "对白": ["台词", "潜台词", "dialogue", "subtext"],
    "剧本": ["编剧", "结构", "节奏", "script", "screenplay"],
    "钩子": ["悬念钩", "反转钩", "情绪钩", "hook"],
    "转折": ["反转", "意外", "turn", "reversal"],
    "剪辑": ["转场", "节奏", "切点", "edit", "cut"],
    "叠化": ["交叉溶解", "dissolve", "淡入淡出"],
    "卡点": ["节拍", "beat-sync", "音乐节奏", "对拍"],
    "黑场": ["淡入淡出", "fade-black", "章节切换"],
    "音画": ["L-cut", "J-cut", "声音先行", "错位"],
}


@dataclass
class LibraryEntry:
    """从 markdown 词库解析出的单条结构化提示词条目。"""
    id: str
    category: str
    title: str
    tags: list[str] = field(default_factory=list)
    emotion: str = ""
    action_density: str = ""
    shot_kind: str = ""
    prompt: str = ""
    notes: str = ""
    source: str = ""
    medium: str = ""
    genre: str = ""
    contrast_group: str = ""

    def matches_shot_kind(self, shot_kind: Optional[str]) -> bool:
        """当该条目适合所请求的 shot_kind（image/video）时返回 True。

        `both` 条目适合所有类型。`first_frame` 只适合 image，
        `video` 只适合 video。未知/None 类型匹配所有。
        """
        if not shot_kind:
            return True
        kind = (self.shot_kind or "").strip().lower()
        if kind == "both" or kind == "":
            return True
        if shot_kind == "image":
            return kind in _SHOT_KIND_IMAGE
        if shot_kind == "video":
            return kind in _SHOT_KIND_VIDEO
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "title": self.title,
            "tags": list(self.tags),
            "emotion": self.emotion,
            "action_density": self.action_density,
            "shot_kind": self.shot_kind,
            "prompt": self.prompt,
            "notes": self.notes,
            "source": self.source,
            "medium": self.medium,
            "genre": self.genre,
            "contrast_group": self.contrast_group,
        }


def _tokenize(text: str) -> list[str]:
    """把文本拆成小写 token（ASCII 单词 + CJK 二元组/字符）。

    不依赖外部分词：保留 ASCII 单词，并输出 CJK 字符
    以及相邻的 CJK 二元组，让多字符标签仍能匹配。
    """
    if not text:
        return []
    tokens: list[str] = []
    for ascii_word in re.findall(r"[a-zA-Z][a-zA-Z0-9\-_]*", text.lower()):
        tokens.append(ascii_word)
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    for run in cjk:
        # 整段 + 二元组（有助于匹配复合标签）
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.append(run)
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
            tokens.extend(run[i : i + 3] for i in range(len(run) - 2))
    return tokens


def _expand_tokens(tokens: Iterable[str]) -> set[str]:
    """用别名查找（双向）扩展一个 token 集合。"""
    out: set[str] = set()
    for tok in tokens:
        out.add(tok)
        for alias in _ALIASES.get(tok, ()):
            out.add(alias)
            out.update(_tokenize(alias))
    return out


def _field_allows(stored: str, wanted: str) -> bool:
    """条目未标注 medium/genre 时不过滤；已标注则要命中其一。"""
    needle = (wanted or "").strip().lower()
    if not needle:
        return True
    blob = (stored or "").replace("，", ",").replace("/", ",")
    parts = [p.strip().lower() for p in blob.split(",") if p.strip()]
    if not parts:
        return True
    return needle in parts or needle in blob.lower()


class PromptLibrary:
    """确定性的、无依赖的提示词条目存储 + 检索器。"""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else _LIBRARY_ROOT
        self._entries: list[LibraryEntry] | None = None
        self._by_id: dict[str, LibraryEntry] = {}

    # --- 加载 ----------------------------------------------------------

    def _load(self) -> list[LibraryEntry]:
        if self._entries is not None:
            return self._entries
        entries: list[LibraryEntry] = []
        for category in _CATEGORIES:
            index_path = self.root / category / "INDEX.md"
            if not index_path.is_file():
                continue
            entries.extend(self._parse_index_file(category, index_path))
        self._entries = entries
        self._by_id = {e.id: e for e in entries}
        return entries

    @staticmethod
    def _parse_index_file(category: str, path: Path) -> list[LibraryEntry]:
        """解析 `- id: ...` 条目块（id + 后续的 `key: value` 行）。"""
        entries: list[LibraryEntry] = []
        current: dict[str, Any] | None = None

        def flush() -> None:
            if current is None or "id" not in current:
                return
            entry = LibraryEntry(
                id=current["id"].strip("`"),
                category=current.get("category") or category,
                title=current.get("title", "").strip("`"),
                tags=[
                    t.strip().strip("`")
                    for t in str(current.get("tags", "")).split(",")
                    if t.strip()
                ],
                emotion=current.get("emotion", "").strip(),
                action_density=current.get("action_density", "").strip(),
                shot_kind=current.get("shot_kind", "").strip(),
                prompt=current.get("prompt", "").strip(),
                notes=current.get("notes", "").strip(),
                source="openmontage/prompt_library",
                medium=current.get("medium", "").strip(),
                genre=current.get("genre", "").strip(),
                contrast_group=current.get("contrast_group", "").strip(),
            )
            entries.append(entry)

        key_re = re.compile(r"^([a-z_]+):\s*(.*)$")
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.rstrip()
            if line.startswith("- id:") or (line.startswith("- id: `")):
                flush()
                current = {"id": line[len("- id:"):].strip()}
                continue
            if current is None:
                continue
            if line.startswith("  "):
                m = key_re.match(line.strip())
                if m:
                    current[m.group(1)] = m.group(2).strip()
                continue
            # 非缩进非空行 -> 块内的节标题；
            # 只有当它是多行字段的续行时才继续追加到当前块？
            # 该格式是扁平的 key:value，所以忽略。
        flush()
        return entries

    @property
    def entries(self) -> list[LibraryEntry]:
        return self._load()

    def __len__(self) -> int:
        return len(self._load())

    def get(self, entry_id: str) -> Optional[LibraryEntry]:
        self._load()
        return self._by_id.get(entry_id)

    # --- 检索 --------------------------------------------------------

    def _score(self, query_tokens: set[str], entry: LibraryEntry) -> int:
        haystack_tokens = _expand_tokens(
            entry.tags
            + _tokenize(entry.title)
            + _tokenize(entry.emotion)
            + _tokenize(entry.category)
            + _tokenize(entry.prompt)
        )
        # 标题/标签命中给加成；仅 prompt 命中仍计分
        tag_tokens = _expand_tokens(entry.tags + _tokenize(entry.title))
        score = 0
        for tok in query_tokens:
            if tok in haystack_tokens:
                score += 1
                if tok in tag_tokens:
                    score += 2
        return score

    def search(
        self,
        query: str,
        *,
        category: Optional[str] = None,
        shot_kind: Optional[str] = None,
        emotion: Optional[str] = None,
        action_density: Optional[str] = None,
        medium: Optional[str] = None,
        genre: Optional[str] = None,
        top_k: int = 5,
        include_prompt: bool = True,
    ) -> list[dict[str, Any]]:
        """返回 top-k 条匹配条目（dict 形式）。

        过滤器：category、shot_kind、emotion、action_density、medium、genre。
        条目缺 medium/genre 时不过滤掉（旧词条仍可命中）。
        """
        entries = self._load()
        query_tokens = _expand_tokens(_tokenize(query))

        candidates: list[tuple[int, LibraryEntry]] = []
        for entry in entries:
            if category and entry.category != category:
                continue
            if shot_kind and not entry.matches_shot_kind(shot_kind):
                continue
            if emotion and emotion not in (entry.emotion + "".join(entry.tags)):
                continue
            if action_density and entry.action_density != action_density:
                continue
            if medium and not _field_allows(entry.medium, medium):
                continue
            if genre and not _field_allows(entry.genre, genre):
                continue
            score = self._score(query_tokens, entry) if query_tokens else 1
            if score:
                candidates.append((score, entry))

        candidates.sort(key=lambda x: (-x[0], x[1].id))
        results = []
        for score, entry in candidates[:top_k]:
            data = entry.to_dict()
            data["score"] = score
            if not include_prompt:
                data.pop("prompt", None)
            results.append(data)
        return results

    def by_category(self, category: str) -> list[dict[str, Any]]:
        return [
            e.to_dict()
            for e in self._load()
            if e.category == category
        ]

    def categories(self) -> list[dict[str, Any]]:
        counts = {c: 0 for c in _CATEGORIES}
        for e in self._load():
            counts[e.category] = counts.get(e.category, 0) + 1
        return [
            {
                "id": c,
                "title": _CATEGORY_TITLES.get(c, c),
                "count": counts.get(c, 0),
            }
            for c in _CATEGORIES
        ]


# 模块级单例，让导入方共享同一个缓存索引。
_default_library: Optional[PromptLibrary] = None


def get_library() -> PromptLibrary:
    global _default_library
    if _default_library is None:
        _default_library = PromptLibrary()
    return _default_library


def search(
    query: str,
    *,
    category: Optional[str] = None,
    shot_kind: Optional[str] = None,
    emotion: Optional[str] = None,
    action_density: Optional[str] = None,
    medium: Optional[str] = None,
    genre: Optional[str] = None,
    top_k: int = 5,
    include_prompt: bool = True,
) -> list[dict[str, Any]]:
    """对单例词库的模块级便捷封装。"""
    return get_library().search(
        query,
        category=category,
        shot_kind=shot_kind,
        emotion=emotion,
        action_density=action_density,
        medium=medium,
        genre=genre,
        top_k=top_k,
        include_prompt=include_prompt,
    )
