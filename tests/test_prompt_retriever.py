"""prompt_library_retriever 工具测试。"""

from montage.registry import ToolRegistry
from montage.toolbase import ToolResult
from montage.tools.prompt_retriever import PromptLibraryRetriever


def test_execute_returns_hits():
    tool = PromptLibraryRetriever()
    result = tool.execute({"query": "雨夜 战斗 赛博朋克", "shot_kind": "video", "top_k": 3})
    assert result.success
    assert 1 <= result.data["count"] <= 3


def test_empty_query_rejected():
    tool = PromptLibraryRetriever()
    result = tool.execute({"query": "  "})
    assert not result.success


def test_category_filter():
    tool = PromptLibraryRetriever()
    result = tool.execute({"query": "爆炸", "category": "effects"})
    assert result.success
    assert all(h["category"] == "effects" for h in result.data["hits"])


def test_cost_is_zero():
    assert PromptLibraryRetriever().estimate_cost({}) == 0.0


def test_discovered_by_registry():
    reg = ToolRegistry()
    reg.discover()
    assert reg.get("prompt_library_retriever") is PromptLibraryRetriever
