"""duration_advisor 测试（V19 双路径 / V7 共同输入 / recommend 锚点）。

关键回归：无 shots 路径 A 必须与 compile 侧 _section_duration 同算法（防漂移）；
有 shots 路径 B 显式时长优先；显式 vs 估时偏差出建议；recommend 区间含网格修正。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_bible import _ok_bible

from montage.engine.project import init_project
from montage.tools.duration_advisor import (
    DurationAdvisor,
    chapter_daily_batches,
    estimate_durations,
    estimate_throughput,
    recommend_duration,
)
from montage.tools.script_to_scene_plan import _section_duration


def _scene(sid="sc01", duration=10, chars_text="雨还在下。你看清楚没有。", shots=None):
    scene = {
        "id": sid,
        "environment": {"location": "沿海旧巷", "lighting": "霓虹积水", "atmosphere": "雨夜"},
        "duration_seconds": duration,
        "lines": [
            {"speaker_id": "a", "text": chars_text[:6]},
            {"speaker_id": "b", "text": chars_text[6:]},
        ],
    }
    if shots is not None:
        scene["shots"] = shots
    return scene


def _tool():
    return DurationAdvisor()


def test_path_a_matches_compile_section_duration():
    """路径 A 字数估必须与 compile 侧 _section_duration 同算法（V19 防漂移）。"""
    scenes = [
        _scene("sc01", duration=0, chars_text="五个字"),
        _scene("sc02", duration=0, chars_text="很长的一段口播足足三十个字，用来验证贴网格的向上取整行为是否与编译侧完全一致"),
        _scene("sc03", duration=12, chars_text="显式时长"),
    ]
    for scene in scenes:
        mine = estimate_durations(
            {"scenes": [scene]}, video_loop="none"
        )["per_scene"][0]["effective_estimate_seconds"]
        theirs = _section_duration(scene, 5.0, None)
        assert abs(mine - theirs) < 0.01, f"{scene['id']}: {mine} vs {theirs}"


def test_path_b_shots_explicit_first():
    """有 shots[]：显式 duration 优先，路径标记 shots。"""
    bible = _ok_bible()
    bible["scenes"][0]["duration_seconds"] = 12
    bible["scenes"][0]["shots"] = [
        {"shot_id": "sh01", "duration_seconds": 7},
        {"shot_id": "sh02", "duration_seconds": 5},
    ]
    out = estimate_durations(bible, video_loop="none")
    row = out["per_scene"][0]
    assert row["path"] == "shots"
    assert row["shot_estimate_seconds"] == 12.0
    assert out["total_estimate"] == 12.0


def test_path_b_shots_without_duration_estimates_from_dialogue():
    bible = _ok_bible()
    bible["scenes"][0]["duration_seconds"] = 0
    bible["scenes"][0]["shots"] = [
        {"shot_id": "sh01", "dialogue": [{"dialogue_text": "十个字的一句台词铛铛铛"}]},
        {"shot_id": "sh02", "dialogue": []},
    ]
    out = estimate_durations(bible, video_loop="none")
    row = out["per_scene"][0]
    assert row["path"] == "shots"
    # 10 字 / 5字每秒 = 2s → 贴 5/10 网格 = 5s；无对白镜兜底 5s
    assert row["shot_estimate_seconds"] == 10.0


def test_delta_and_suggestions(tmp_path):
    """显式 vs 估时偏差 → 建议；无对白超长 → add_shots。"""
    # 对白超时：46 字 / 5字每秒 = 9.2s > 显式 5s
    over = _scene("scA", duration=5, chars_text="这句话足足有四十个字，用来验证超时建议触发" * 2)
    out = estimate_durations({"scenes": [over]}, video_loop="none")
    acts = [s["action"] for s in out["suggestions"]]
    assert "trim_dialogue" in acts
    # 无对白且无 shots 长镜
    long_quiet = {
        "id": "scB", "duration_seconds": 15, "lines": [], "narration": "",
        "environment": {"location": "旧巷"},
    }
    out2 = estimate_durations({"scenes": [long_quiet]}, video_loop="none")
    acts2 = [s["action"] for s in out2["suggestions"]]
    assert "add_shots" in acts2


def test_total_against_target_warning():
    bible = {"target_duration_seconds": 10, "scenes": [
        _scene("sc01", duration=20, chars_text="x" * 100),
    ]}
    out = estimate_durations(bible, video_loop="none")
    assert out["target"] == 10
    assert out["total_delta"] == out["total_estimate"] - 10
    assert "20%" in out.get("total_warning", "")


def test_estimate_from_project_dir(tmp_path):
    proj = init_project(tmp_path, "da1", "估时", "cinematic")
    from montage.engine.bible import write_bible

    write_bible(proj, _ok_bible())
    res = _tool().execute({"operation": "estimate", "project_dir": str(proj)})
    assert res.success, res.error
    assert res.data["per_scene"][0]["scene_id"] == "sc01"


def test_recommend_genre_and_media_intersect():
    out = recommend_duration(medium="douyin_vertical", genres=["thriller"], n_scenes=6,
                             video_loop="none")
    assert out["matched_genre"] == "thriller"
    assert out["min"] >= 30
    assert out["max"] <= 180  # 媒介收窄
    # 幕数地板修正：6 幕 × 5s = 30s
    assert out["min"] >= 30


def test_recommend_kling_hard_cap():
    """可灵 3-15s 单镜：6 幕硬上限 6×15=90s。"""
    out = recommend_duration(genres=["drama"], n_scenes=6, video_loop="kling")
    assert out["max"] <= 90


def test_recommend_generic_fallback():
    out = recommend_duration()
    assert {"min", "recommended", "max", "rationale"} <= set(out)


def test_tool_estimate_requires_input():
    res = _tool().execute({"operation": "estimate"})
    assert not res.success


# ---- v8.2 P0-1：longform 档 + 混合档位吞吐 + 章节拆批 ----


def test_recommend_longform_tier():
    out = recommend_duration(medium="longform", n_scenes=40, video_loop="none")
    assert out["matched_genre"] in ("longform", "generic")
    # longform 媒介命中：30/60/120min 锚点；40 幕 × 5s = 200s 地板不抬升
    if out["matched_genre"] == "longform":
        assert out["min"] == 1800 and out["max"] == 7200


def test_estimate_throughput_days():
    t = estimate_throughput(total_seconds=7200)
    assert t["daily_seconds_range"] == [700.0, 1000.0]
    assert t["days_est"] == [8, 11]  # 7200/1000=7.2→8, 7200/700≈10.3→11
    assert "章节粒度" in t.get("note", "")
    # 单天能跑完：days_est 为 int 1（不足一天按一天）
    t1 = estimate_throughput(total_seconds=600)
    assert t1["days_est"] == 1


def test_estimate_throughput_no_input():
    t = estimate_throughput()
    assert t["daily_seconds_est"] == 850.0
    assert {r["tier"] for r in t["tiers"]} == {"tokenplan", "default"}
    assert "days_est" not in t


def test_chapter_daily_batches_greedy():
    # 两章 600s + 一章无声明（估 8s×40=320s）→ 850s/天：ch1+ch3 不超，ch2 独占
    b = chapter_daily_batches([
        {"id": "ch1", "start_scene": "sc01", "target_duration_seconds": 600},
        {"id": "ch2", "start_scene": "sc21", "target_duration_seconds": 600},
        {"id": "ch3", "start_scene": "sc41"},
    ], daily_seconds=850)
    assert b["n_days"] == 3  # 600+600>850 → ch1/ch2 分天；ch3 320s 也开新天（顺序贪心不回填）
    assert b["days"][0]["chapters"] == ["ch1"]
    # 顺序贪心：装得下就同天
    b2 = chapter_daily_batches([
        {"id": "ch1", "target_duration_seconds": 300},
        {"id": "ch2", "target_duration_seconds": 400},
        {"id": "ch3", "target_duration_seconds": 500},
    ], daily_seconds=850)
    assert b2["n_days"] == 2
    assert b2["days"][0]["chapters"] == ["ch1", "ch2"]
    assert b2["days"][1]["chapters"] == ["ch3"]


def test_tool_throughput_operation(tmp_path):
    proj = init_project(tmp_path, "tp1", "吞吐", "cinematic")
    from montage.engine.bible import write_bible

    bible = _ok_bible()
    bible["target_duration_seconds"] = 7200
    bible["chapters"] = [
        {"id": "ch1", "start_scene": "sc01", "target_duration_seconds": 600},
        {"id": "ch2", "start_scene": "sc02", "target_duration_seconds": 600},
    ]
    write_bible(proj, bible)
    res = _tool().execute({"operation": "throughput", "project_dir": str(proj)})
    assert res.success, res.error
    data = res.data
    assert data["total_seconds"] == 5.0  # _ok_bible 路径 B：10字对白贴 5s 网格
    assert data["days_est"] == 1
    assert data["batches"]["n_days"] == 2  # 600+600>850 → 两天
    # 无 bible 输入：只出档位口径
    res2 = _tool().execute({"operation": "throughput"})
    assert res2.success and "days_est" not in res2.data


def test_review_card_outline_throughput_summary():
    """await_outline 卡：bible 有目标时长时 summary 出吞吐预估行。"""
    from montage.engine.director import build_review_card

    card = build_review_card(
        "await_outline",
        bible={"title": "t", "target_duration_seconds": 7200, "characters": []},
    )
    labels = {row["label"]: row["value"] for row in card["summary"]}
    assert "吞吐预估" in labels
    assert "8-11 天" in labels["吞吐预估"]
    # 无目标时长不出该行
    card2 = build_review_card(
        "await_outline", bible={"title": "t", "characters": []},
    )
    labels2 = {row["label"]: row["value"] for row in card2["summary"]}
    assert "吞吐预估" not in labels2
