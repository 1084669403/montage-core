"""迁移后的提示词库冒烟测试：加载、分类、检索、shot_kind 过滤。"""

from lib.prompt_library import PromptLibrary, get_library, search


def test_library_loads_all_entries():
    lib = get_library()
    entries = lib._load()
    # 170（W1 定版）+ W2 风格族库 14 条 = 184
    assert len(entries) == 184


def test_styles_tree_genre_groups():
    """W2 树形五大族：genre 族字段分组正确（含既有条目补族）。"""
    lib = get_library()
    styles = [e for e in lib._load() if e.category == "styles"]
    genres: dict[str, int] = {}
    for e in styles:
        genres[e.genre] = genres.get(e.genre, 0) + 1
    assert genres.get("chinese-traditional") == 4
    assert genres.get("animation-film") == 4
    assert genres.get("comics-illustration") == 3
    assert genres.get("photoreal") == 4
    assert genres.get("techno-punk") == 3
    # 全中文政策：prompt 正文不含英文风格锚点
    for e in styles:
        import re

        assert not re.search(
            r"[a-z]{3,} [a-z]{3,}", e.prompt
        ), f"{e.id} prompt 正文疑似含英文锚点: {e.prompt[:80]}"


def test_categories_present():
    lib = get_library()
    cats = {e.category for e in lib._load()}
    assert {
        "scenes", "effects", "actions", "shots", "lighting",
        "styles", "directors", "scripts", "screenplays", "edits",
        "characters", "dialogue",
    } <= cats


def test_get_by_id():
    lib = get_library()
    entry = lib.get("scenes/city-cyberpunk")
    assert entry is not None
    assert entry.category == "scenes"


def test_search_shot_kind_filter():
    video_hits = search("雨夜 战斗 赛博朋克", shot_kind="video", top_k=10)
    assert video_hits
    # first_frame 词条（静态）不应混进 video 结果
    for hit in video_hits:
        assert hit["shot_kind"] in ("video", "both")

    image_hits = search("水墨 山水 意境", shot_kind="image", top_k=10)
    for hit in image_hits:
        assert hit["shot_kind"] in ("first_frame", "both")


def test_search_alias_expansion():
    # 别名表：战斗 ↔ 打斗
    hits = search("打斗 动作", top_k=5)
    assert hits


def test_medium_filter_keeps_untagged_and_drops_mismatch():
    lib = PromptLibrary()
    spoken = lib.search("抓 领口", category="actions", medium="spoken", top_k=10)
    ids = {h["id"] for h in spoken}
    assert "actions/beat-grab-collar" not in ids
    film = lib.search("抓 领口", category="actions", medium="film", top_k=10)
    assert any(h["id"] == "actions/beat-grab-collar" for h in film)
    untagged = lib.search("爆炸", category="effects", medium="spoken", top_k=5)
    assert untagged


def test_new_character_and_dialogue_categories():
    lib = PromptLibrary()
    assert lib.get("characters/scar-glasses") is not None
    assert lib.get("characters/scar-glasses").contrast_group == "scar-glasses"
    assert lib.get("dialogue/technical-plain") is not None
    hits = lib.search("讲解 口播", category="dialogue", medium="spoken", top_k=5)
    assert any(h["id"] == "dialogue/technical-plain" for h in hits)


def test_search_returns_prompt_when_requested():
    hits = search("爆炸", top_k=1, include_prompt=True)
    assert hits and hits[0].get("prompt")


def test_deterministic_same_result():
    a = [h["id"] for h in search("赛博朋克 城市", top_k=5)]
    b = [h["id"] for h in search("赛博朋克 城市", top_k=5)]
    assert a == b
