"""prompt_retriever — 中文提示词库检索工具。

封装 lib.prompt_library 的确定性检索，供 Agent（scene/asset/edit 导演）在
生成画面/关键帧/视频提示词前检索专业参考词条。词条只作参考，LLM 须改编不得照抄。

纯本地、确定性、零网络副作用。本文件为全新原创代码。
"""

from __future__ import annotations

from typing import Any

from lib.prompt_library import PromptLibrary
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_CATEGORIES = (
    "scenes", "effects", "actions", "shots", "lighting", "styles",
    "directors", "scripts", "edits", "screenplays", "characters", "dialogue",
)


class PromptLibraryRetriever(BaseTool):
    name = "prompt_library_retriever"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["query"],
        "properties": {
            "query": {
                "type": "string",
                "description": "检索意图，空格分隔关键词，含 场景/题材/情绪/动作",
            },
            "category": {
                "type": "string",
                "enum": list(_CATEGORIES),
                "description": "限定分类；不传则全库检索",
            },
            "shot_kind": {
                "type": "string",
                "enum": ["image", "video"],
                "description": "image=静态首帧图，video=视频动态镜头；不传返回全部",
            },
            "emotion": {"type": "string", "description": "情绪过滤"},
            "action_density": {
                "type": "string",
                "enum": ["low", "medium", "high"],
            },
            "medium": {"type": "string", "description": "可选；条目未标注则不过滤"},
            "genre": {"type": "string", "description": "可选；条目未标注则不过滤"},
            "format_card": {
                "type": "object",
                "description": "可选；若提供则用 chosen.medium/genres 作默认过滤，不强制",
            },
            "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        },
    }

    def __init__(self) -> None:
        self._library = PromptLibrary()

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        query = inputs.get("query")
        if not query or not str(query).strip():
            return ToolResult(success=False, error="'query' 必填且不能为空字符串")
        top_k = int(inputs.get("top_k", 5) or 5)
        top_k = max(1, min(10, top_k))
        medium = str(inputs.get("medium") or "").strip() or None
        genre = str(inputs.get("genre") or "").strip() or None
        card = inputs.get("format_card")
        if isinstance(card, dict):
            chosen = card.get("chosen") if isinstance(card.get("chosen"), dict) else {}
            if not medium:
                medium = str(chosen.get("medium") or card.get("medium") or "").strip() or None
            if not genre:
                genres = chosen.get("genres") or card.get("genres") or []
                if isinstance(genres, list) and genres:
                    genre = str(genres[0])
        results = self._library.search(
            str(query),
            category=inputs.get("category"),
            shot_kind=inputs.get("shot_kind"),
            emotion=inputs.get("emotion"),
            action_density=inputs.get("action_density"),
            medium=medium,
            genre=genre,
            top_k=top_k,
            include_prompt=True,
        )
        return ToolResult(
            success=True,
            data={
                "query": query,
                "top_k": top_k,
                "filters": {
                    "category": inputs.get("category"),
                    "shot_kind": inputs.get("shot_kind"),
                    "emotion": inputs.get("emotion"),
                    "action_density": inputs.get("action_density"),
                    "medium": medium,
                    "genre": genre,
                },
                "hits": results,
                "count": len(results),
                "usage": "仅作参考上下文：改编后使用，禁止整段照抄；first_frame 词条只用于首帧图，video 词条只用于视频动态提示词",
            },
        )
