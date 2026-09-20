# -*- coding: utf-8 -*-
"""P0-0 叙事结构模型（v8.2）：chapters 分章 + 章内四拍 + 音乐辅助锚。"""

from montage.engine.bible import compile_bible
from montage.engine.story_outline import (
    AUTO_CHAPTER_SCENE_MIN,
    auto_chapter_plan,
    build_chapter_plan,
    chapter_beat_role,
    chapter_index_map,
    chapter_bgm_fallback,
    normalize_chapters,
)
from montage.tools.script_to_scene_plan import convert_script_to_scene_plan


def _ids(n: int) -> list[str]:
    return [f"sc{i:02d}" for i in range(1, n + 1)]


def test_normalize_chapters_keeps_known_fields_and_warns():
    chapters, findings = normalize_chapters([
        {"id": "ch1", "title": "入局", "hook": "尸体", "start_scene": "sc01",
         "bgm_id": "bgm_a", "role": "开局", "target_duration_seconds": 600},
        {"title": "无 id"},
        "junk",
        {"id": "ch1", "start_scene": "sc09"},
    ])
    assert [c["id"] for c in chapters] == ["ch1", "ch02"]
    assert chapters[0]["target_duration_seconds"] == 600
    assert len(findings) == 2  # 丢非对象 + id 重复


def test_normalize_chapters_non_list():
    chapters, findings = normalize_chapters("nope")
    assert chapters == [] and len(findings) == 1
    chapters2, findings2 = normalize_chapters(None)
    assert chapters2 == [] and findings2 == []


def test_chapter_beat_role_four_beats_in_chapter():
    beats = [chapter_beat_role(i, 10) for i in range(10)]
    assert beats[0] == "hook"
    assert "escalation" in beats[1:6]
    assert "reveal" in beats[6:8]
    assert beats[-2:] == ["landing", "landing"]
    assert chapter_beat_role(0, 1) == "hook"


def test_build_chapter_plan_explicit_region_binding():
    bible = {"chapters": [
        {"id": "ch1", "start_scene": "sc01"},
        {"id": "ch2", "start_scene": "sc05"},
    ]}
    out = build_chapter_plan(bible, _ids(8))
    assert out["scene_chapter"] == {
        **{f"sc0{i}": "ch1" for i in range(1, 5)},
        **{f"sc0{i}": "ch2" for i in range(5, 9)},
    }


def test_build_chapter_plan_warns_unknown_start_scene():
    bible = {"chapters": [{"id": "ch1", "start_scene": "sc99"}]}
    out = build_chapter_plan(bible, _ids(4))
    assert out["scene_chapter"] == {}
    assert any("sc99" in f["message"] for f in out["findings"])


def test_build_chapter_plan_auto_longform_only():
    # 短篇不自动分章
    out = build_chapter_plan({}, _ids(8))
    assert out["chapters"] == [] and out["scene_chapter"] == {}
    # 长片自动等距分章
    out2 = build_chapter_plan({}, _ids(AUTO_CHAPTER_SCENE_MIN))
    assert len(out2["chapters"]) == 1
    assert out2["chapters"][0]["start_scene"] == "sc01"
    assert out2["scene_chapter"]["sc01"] == out2["chapters"][0]["id"]
    # converter 独立路径 auto=False 不触发自动分章
    out3 = build_chapter_plan({}, _ids(AUTO_CHAPTER_SCENE_MIN), auto=False)
    assert out3["chapters"] == []


def test_auto_chapter_plan_splits_evenly():
    plan = auto_chapter_plan(_ids(50))
    assert len(plan) == 2
    assert plan[0]["start_scene"] == "sc01"
    assert plan[1]["start_scene"] == "sc26"


def test_convert_chapter_beats_reset_in_chapter():
    script = {"title": "t", "sections": [
        {"id": f"sc{i:02d}", "narration": f"第{i}场"} for i in range(1, 9)
    ]}
    scene_chapter = {f"sc{i:02d}": ("ch1" if i <= 4 else "ch2") for i in range(1, 9)}
    out = convert_script_to_scene_plan(
        script,
        scene_chapter=scene_chapter,
        chapter_plan=[{"id": "ch1", "start_scene": "sc01"}, {"id": "ch2", "start_scene": "sc05"}],
    )
    plan = out["scene_plan"]
    roles = {s["id"]: s["narrative_role"] for s in plan["scenes"]}
    # 每章独立走四拍弧线
    assert roles["sc01"] == "hook"
    assert roles["sc05"] == "hook"
    assert plan["scenes"][0]["chapter_id"] == "ch1"
    assert plan["scenes"][4]["chapter_id"] == "ch2"
    assert plan["chapters"][0]["id"] == "ch1"


def test_convert_without_chapters_keeps_legacy_global_beats():
    script = {"title": "t", "sections": [
        {"id": f"sc{i:02d}", "narration": f"第{i}场"} for i in range(1, 9)
    ]}
    out = convert_script_to_scene_plan(script)
    plan = out["scene_plan"]
    assert "chapter_id" not in plan["scenes"][0]
    assert "chapters" not in plan
    # 旧全局四拍不变（8 场）
    roles = [s["narrative_role"] for s in plan["scenes"]]
    assert roles[0] == "hook" and roles[-1] == "landing"


def test_chapter_index_map_unbound_scenes():
    order, size = chapter_index_map(["a", "b"], {"a": "ch1"})
    assert order["a"] == 0 and size["a"] == 1
    assert order["b"] == -1 and size["b"] == 0


def test_compile_bible_chapters_end_to_end():
    bible = {
        "title": "长夜",
        "chapters": [
            {"id": "ch1", "title": "入局", "hook": "尸体出现", "start_scene": "sc01",
             "bgm_id": "bgm_rain"},
            {"id": "ch2", "title": "对决", "start_scene": "sc05"},
        ],
        "scenes": [
            {"id": f"sc0{i}", "narration": f"第{i}场", "duration_seconds": 5}
            for i in range(1, 9)
        ],
    }
    out = compile_bible(bible, duration_policy={"kind": "none"})
    plan = out["scene_plan"]
    by_id = {s["id"]: s for s in plan["scenes"]}
    # 章内四拍 + chapter_id 落场（4 场/章：首场 frac 0.125 → hook）
    assert by_id["sc01"]["chapter_id"] == "ch1"
    assert by_id["sc05"]["chapter_id"] == "ch2"
    assert by_id["sc01"]["narrative_role"] == "hook"
    assert by_id["sc05"]["narrative_role"] == "hook"
    # 快照
    assert [c["id"] for c in plan["chapters"]] == ["ch1", "ch2"]


def test_compile_bible_small_chapter_beats_reset_differs_from_global():
    # 2 场/章：章内首场 frac 0.25 → escalation；旧全局 4 场首场 0.125 → hook。
    # 断言四拍作用域确实从全片收到章内。
    bible = {
        "title": "长夜",
        "chapters": [
            {"id": "ch1", "start_scene": "sc01"},
            {"id": "ch2", "start_scene": "sc03"},
        ],
        "scenes": [
            {"id": f"sc0{i}", "narration": f"第{i}场", "duration_seconds": 5}
            for i in range(1, 5)
        ],
    }
    out = compile_bible(bible, duration_policy={"kind": "none"})
    by_id = {s["id"]: s for s in out["scene_plan"]["scenes"]}
    assert by_id["sc01"]["narrative_role"] == "escalation"


def test_compile_bible_chapter_bgm_fallback_respects_explicit():
    bible = {
        "title": "长夜",
        "chapters": [
            {"id": "ch1", "start_scene": "sc01", "bgm_id": "bgm_rain"},
        ],
        "scenes": [
            {"id": "sc01", "narration": "雨夜。", "duration_seconds": 5},
            {"id": "sc02", "narration": "追查。", "duration_seconds": 5,
             "bgm_id": "bgm_explicit"},
        ],
    }
    out = compile_bible(bible, duration_policy={"kind": "none"})
    by_id = {s["id"]: s for s in out["scene_plan"]["scenes"]}
    # 章默认曲只兜底无显式曲的场；显式 scene.bgm_id 仍最高优先
    assert by_id["sc01"]["bgm_id"] == "bgm_rain"
    assert by_id["sc02"]["bgm_id"] == "bgm_explicit"


def test_chapter_bgm_fallback_pure():
    chapters = [{"id": "ch1", "bgm_id": "bgm_a"}, {"id": "ch2"}]
    assert chapter_bgm_fallback(chapters, {"s1": "ch1", "s2": "ch2"}) == {"s1": "bgm_a"}
