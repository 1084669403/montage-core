"""V13 影响面控制地基回归——重编译后未变镜提示词/缓存键不变。

方案九.1 V2 追加项：改 1 镜 subjects.action → 重编译 → 其余镜
build_shot_prompt_pair 输出逐字不变、generation_cache.cache_key 相等
（缓存命中零成本）；改动镜 prompt 必然不同（否则断言无意义）。

缓存键形状对齐 shot_runner 首帧调用（kind/shot_id/ratio/image_tool 全参数集）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_bible import _ok_bible, _ok_shot

from lib.shot_prompt_builder import build_shot_prompt_pair
from montage.engine.bible import compile_bible
from montage.tools.generation_cache import cache_key

_SIMILAR_WORDS = ("抓", "退", "转", "挥", "推", "夺", "冲", "躲")


def _pairs_by_id(scene_plan: dict) -> dict[str, dict[str, str]]:
    """对 plan 全部镜构建提示词对，按 shot_id 归集。"""
    out: dict[str, dict[str, str]] = {}
    registry = scene_plan.get("character_registry") or []
    for scene in scene_plan.get("scenes") or []:
        for shot in scene.get("shots") or []:
            sid = str(shot.get("shot_id") or "")
            out[sid] = build_shot_prompt_pair(shot, registry)
    return out


def _cache_key_of(sid: str, pair: dict[str, str]) -> str:
    """首帧图缓存键：对齐 shot_runner cache_params 全参数集。"""
    return cache_key(
        prompt=pair.get("first_frame_prompt") or "",
        kind="first_frame",
        shot_id=sid,
        ratio="",
        image_tool="wan",
    )


def test_recompile_keeps_untouched_shot_prompts_stable():
    bible = _ok_bible()
    base = _ok_shot()
    # 两镜显式 shot_id 对齐（V27 纪律），converter 2 句对白生成 2 plan 镜
    bible["scenes"][0]["shots"] = [
        base,
        {**base, "shot_id": "sc01_02", "blocking": {"x": "右", "z": "近"}},
    ]
    first = compile_bible(bible)
    pairs1 = _pairs_by_id(first["scene_plan"])

    # 重编译（无改动）——确定性：全部镜逐字一致
    again = compile_bible(bible)
    pairs2 = _pairs_by_id(again["scene_plan"])
    assert pairs1.keys() == pairs2.keys()
    for sid in pairs1:
        assert pairs1[sid] == pairs2[sid], f"无改动重编译 {sid} 提示词漂移"

    # 改 shot1 的 subjects.action → 重编译
    changed = _ok_bible()
    changed["scenes"][0]["shots"] = [
        {**base, "subjects": [{
            "id": "a",
            "action": {"verb": "推开", "body_part": "双手", "contact": "肩膀"},
        }]},
        {**base, "shot_id": "sc01_02", "blocking": {"x": "右", "z": "近"}},
    ]
    third = compile_bible(changed)
    pairs3 = _pairs_by_id(third["scene_plan"])

    # 未变镜 sc01_02：提示词逐字不变 + 缓存键相等（命中零成本）
    assert pairs3["sc01_02"] == pairs2["sc01_02"]
    assert _cache_key_of("sc01_02", pairs3["sc01_02"]) == _cache_key_of("sc01_02", pairs2["sc01_02"])

    # 改动镜 sc01_01：video_prompt 必须真的变了（新动作词可见），否则断言无意义
    assert pairs3["sc01_01"]["video_prompt"] != pairs2["sc01_01"]["video_prompt"]
    assert any(w in pairs3["sc01_01"]["video_prompt"] for w in _SIMILAR_WORDS)
