"""V27 — shot_language schema 声明 + 镜数不齐守卫 + shot_id 纪律。"""

from __future__ import annotations

import jsonschema
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_bible import _ok_bible, _ok_shot

from montage.engine.bible import compile_bible
from montage.schemas import SERIES_BIBLE_SCHEMA


def test_bible_shot_language_passes_schema():
    """V27：bible.scenes[].shots[].shot_language 显式声明后，写入不再是无名契约。"""
    bible = _ok_bible()
    bible["scenes"][0]["shots"] = [{
        "shot_id": "sh01",
        "shot_language": {"shot_size": "close", "camera_movement": "dolly_in"},
    }]
    jsonschema.validate(bible, SERIES_BIBLE_SCHEMA)  # 不抛 ValidationError


def test_bible_shot_language_consumed_by_compile():
    """compile 侧 _overlay_shot 把 bible 的 shot_language 合并进 plan 镜。"""
    bible = _ok_bible()
    bible["scenes"][0]["shots"] = [{
        "shot_id": "sh01",
        "shot_language": {"shot_size": "close"},
    }]
    out = compile_bible(bible)
    plan_shot = out["scene_plan"]["scenes"][0]["shots"][0]
    assert plan_shot["shot_language"]["shot_size"] == "close"


def test_shot_count_mismatch_emits_warning():
    """bible 镜数 ≠ plan 镜数 → warning（V27 守卫，不阻断 compile）。

    _ok_bible 2 句对白/10s → converter 生成 2 个 plan 镜；bible 只给 1 镜即错位。
    """
    bible = _ok_bible()
    bible["scenes"][0]["shots"][0]["shot_id"] = ""
    out = compile_bible(bible)
    warns = [f for f in out["findings"] if f.get("severity") == "warning" and "镜数" in f.get("message", "")]
    assert warns, out["findings"]
    assert "1" in warns[0]["message"] and "2" in warns[0]["message"]
    assert "proposed_fix" in warns[0]


def test_matching_shot_count_no_warning():
    """bible 与 plan 镜数一致（每镜显式 shot_id 对齐）→ 无镜数 warning。"""
    bible = _ok_bible()
    base = _ok_shot()
    bible["scenes"][0]["shots"] = [
        base,
        {**base, "shot_id": "sc01_02", "blocking": {"x": "右", "z": "近"}},
    ]
    out = compile_bible(bible)
    warns = [f for f in out["findings"] if "镜数" in f.get("message", "")]
    assert not warns


def test_explicit_shot_id_still_binds_despite_mismatch():
    """镜数不齐但 plan 镜有显式 shot_id 精确匹配 → 该镜正常覆盖（纪律收益）。"""
    bible = _ok_bible()
    original_shot = _ok_bible()["scenes"][0]["shots"][0]
    sid = original_shot.get("shot_id") or "sh01"
    bible["scenes"][0]["shots"] = [
        {**original_shot, "shot_id": sid, "shot_budget_class": "hero"},
        {"shot_id": "shXX"},
    ]
    out = compile_bible(bible)
    plan_shot = out["scene_plan"]["scenes"][0]["shots"][0]
    assert plan_shot.get("shot_budget_class") == "hero"
