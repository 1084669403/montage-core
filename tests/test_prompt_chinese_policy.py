"""2026-09 全中文提示词政策护栏：防英文/枚举回潮 + 风格化标志位冻结。

把「提示词全链路中文化」锁成可执行契约：

- 5 张短语表的每个值必须含 CJK（``contains_cjk`` 的首个真实调用点）；
- 短语值不得命中 ``_ABSTRACT_WORD_RE``，否则 dense 模式会被静默删除；
- ``LEGAL_SHOT_SIZE`` / ``LEGAL_MOVEMENT`` 全键在短语表里有中文值
  （同时抓枚举泄漏，如末镜返回的 ``close``）；
- ``close`` 在双表 + 裁切集合中一致（对齐 ``shot_language.CLOSE_SIZES``）；
- 道具模板与 8 个 playbook 负面词含 CJK；
- ``_is_stylized_playbook`` 冻结 6 真 / 2 假，并锁死「标志位优先 +
  字符串兜底（兼容旧英文产物与中文新值）」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.prompt_english import _is_stylized_playbook, contains_cjk
from lib.prompt_phrases import (
    _ABSTRACT_WORD_RE,
    _CLOSE_SIZES,
    _COLOR_TEMP_PHRASES,
    _DOF_PHRASES,
    _LIGHTING_PHRASES,
    _MOVEMENT_PHRASES,
    _MOVEMENT_ZH,
    _SHOT_SIZE_PHRASES,
    _SHOT_SIZE_ZH,
)
from montage.engine.shot_language import (
    CLOSE_SIZES,
    LEGAL_MOVEMENT,
    LEGAL_SHOT_SIZE,
)
from montage.playbooks import get_playbook
from montage.providers.video_prompts import VIDEO_PROMPT_PROFILES
from montage.tools._shot_refs import _prop_prompt

_PHRASE_TABLES = {
    "_SHOT_SIZE_PHRASES": _SHOT_SIZE_PHRASES,
    "_MOVEMENT_PHRASES": _MOVEMENT_PHRASES,
    "_LIGHTING_PHRASES": _LIGHTING_PHRASES,
    "_DOF_PHRASES": _DOF_PHRASES,
    "_COLOR_TEMP_PHRASES": _COLOR_TEMP_PHRASES,
}

# 现行 `_is_stylized_playbook()` 判定为风格化 / 写实向的 playbook（冻结基线）。
_STYLIZED_PLAYBOOKS = (
    "chinese_elegance",
    "anime_shonen",
    "manga_panel",
    "cyberpunk_neon",
    "chase_comedy",
    "documentary_restraint",
)
_REALISTIC_PLAYBOOKS = ("spoken_explain", "healing_japanese")
_PLAYBOOKS = _STYLIZED_PLAYBOOKS + _REALISTIC_PLAYBOOKS


# ---- 短语表：中文化 + 抽象词过滤 ----

@pytest.mark.parametrize("name", sorted(_PHRASE_TABLES))
def test_phrase_table_values_are_chinese(name):
    for key, value in _PHRASE_TABLES[name].items():
        assert contains_cjk(value), f"{name}[{key!r}] 疑似英文回潮: {value!r}"


@pytest.mark.parametrize("name", sorted(_PHRASE_TABLES))
def test_phrase_table_values_survive_abstract_filter(name):
    for key, value in _PHRASE_TABLES[name].items():
        assert not _ABSTRACT_WORD_RE.search(value), (
            f"{name}[{key!r}] 命中抽象词过滤，dense 模式会被静默删除: {value!r}"
        )


# ---- 枚举可达性：合法键必须有中文值 ----

def test_legal_shot_sizes_have_chinese_phrases():
    missing = sorted(
        key
        for key in LEGAL_SHOT_SIZE
        if not contains_cjk(_SHOT_SIZE_PHRASES.get(key, ""))
    )
    assert not missing, f"景别枚举缺中文短语: {missing}"


def test_legal_movements_have_chinese_phrases():
    missing = sorted(
        key
        for key in LEGAL_MOVEMENT
        if not contains_cjk(_MOVEMENT_PHRASES.get(key, ""))
    )
    assert not missing, f"运镜枚举缺中文短语: {missing}"


def test_zh_labels_cover_legal_enums():
    missing_size = sorted(
        key for key in LEGAL_SHOT_SIZE if not contains_cjk(_SHOT_SIZE_ZH.get(key, ""))
    )
    missing_move = sorted(
        key for key in LEGAL_MOVEMENT if not contains_cjk(_MOVEMENT_ZH.get(key, ""))
    )
    assert not missing_size, f"_SHOT_SIZE_ZH 缺中文标签: {missing_size}"
    assert not missing_move, f"_MOVEMENT_ZH 缺中文标签: {missing_move}"


def test_close_size_is_locked_across_tables():
    """`shot_size_for_index()` 末镜返回 `close`，三处表/集合都必须认它。"""
    assert contains_cjk(_SHOT_SIZE_PHRASES["close"])
    assert contains_cjk(_SHOT_SIZE_ZH["close"])
    assert "close" in _CLOSE_SIZES
    assert "close" in CLOSE_SIZES
    # 裁切集合须与校验侧一致（子集即可：校验侧另含「特写」「近景」中文标签）。
    assert _CLOSE_SIZES <= CLOSE_SIZES


# ---- 道具模板 ----

def test_prop_prompt_is_chinese():
    out = _prop_prompt({"name": "烛台", "appearance": "铜制烛台一支，火苗豆大"})
    assert contains_cjk(out)
    # 全仓库唯一下发给生图模型的英文模板不应回归。
    assert "product still" not in out.lower()


# ---- playbook 负面词 ----

@pytest.mark.parametrize("name", _PLAYBOOKS)
def test_playbook_image_negative_prompt_is_chinese(name):
    gen = get_playbook(name).get("asset_generation") or {}
    neg = str(gen.get("image_negative_prompt") or "")
    assert not neg or contains_cjk(neg), f"{name} 负面词疑似英文回潮: {neg!r}"


# ---- `_is_stylized_playbook`：冻结 + 标志位优先 + 字符串兜底 ----

def test_stylized_playbook_flags_are_frozen():
    for name in _STYLIZED_PLAYBOOKS:
        gen = get_playbook(name).get("asset_generation") or {}
        assert gen.get("stylized_image") is True, f"{name} 应标 stylized_image=True"
        assert _is_stylized_playbook(get_playbook(name)) is True
    for name in _REALISTIC_PLAYBOOKS:
        gen = get_playbook(name).get("asset_generation") or {}
        assert "stylized_image" not in gen, f"{name} 不应标 stylized_image"
        assert _is_stylized_playbook(get_playbook(name)) is False


def test_stylized_flag_overrides_negative_string():
    ctx = {
        "asset_generation": {
            "stylized_image": False,
            "image_negative_prompt": "photorealistic, 3d render",
        }
    }
    assert _is_stylized_playbook(ctx) is False


@pytest.mark.parametrize(
    "neg",
    ["photorealistic, low quality", "3d render", "写实照片, 低画质", "3D渲染"],
)
def test_stylized_string_fallback_accepts_en_and_zh(neg):
    assert _is_stylized_playbook({"asset_generation": {"image_negative_prompt": neg}}) is True


def test_stylized_string_fallback_ignores_unrelated_negative():
    ctx = {"asset_generation": {"image_negative_prompt": "低画质，模糊，水印"}}
    assert _is_stylized_playbook(ctx) is False


# ---- 可灵图片 Omni 引用协议 ----

def test_kling_image_omni_citation_uses_angle_syntax():
    """图片 Omni 引用族是 `<<<...>>>`，profile 不得把自己的引用语法列为 forbidden。"""
    profile = VIDEO_PROMPT_PROFILES["kling_image_omni"]
    assert profile["citation_syntax"] == "<<<image_N>>>"
    assert not any("<<<image_" in token for token in profile["forbidden"])


# ---- 可灵多镜头前缀中文化 ----

def test_kling_multi_shot_prefix_is_chinese():
    """官方多镜格式为 `镜头 n, m, words;`；kling.py 源码不得残留英文前缀。"""
    import montage.providers.kling as kling_mod

    src = Path(kling_mod.__file__).read_text(encoding="utf-8")
    assert "镜头 1, " in src
    assert '"shot ' not in src
