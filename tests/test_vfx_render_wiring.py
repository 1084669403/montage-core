"""P0-8 第 2 刀：渲染接线测试。

覆盖：prompt builder【特效】段三处注册（段序/动态专属/压缩优先级）、
kling 组装器特效段、_shot_route 白名单、compose_planner 透传、
_assemble post 层消费（mock + ffmpeg 门控真跑）。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lib import prompt_english
from lib.prompt_phrases import _SECTION_ORDER
from lib.shot_prompt_builder import build_kling_prompt, build_shot_prompt_pair
from montage.tools._shot_route import lift_shot_prompts
from montage.tools.compose_planner import compile_compose_plan

# --- 造数据 ------------------------------------------------------------------


def _shot(vfx: list[dict] | None = None, **extra) -> dict:
    shot: dict = {
        "shot_id": "sc01_01",
        "shot_kind": "video",
        "duration_seconds": 4.0,
        "visual_details": {
            "subjects": [{"id": "甲", "action": {"verb": "挥剑"}}],
            "environment": "雨夜长街",
        },
    }
    if vfx is not None:
        shot["vfx"] = vfx
    shot.update(extra)
    return shot


def test_section_order_contains_vfx_after_action() -> None:
    keys = [k for _label, k in _SECTION_ORDER]
    assert "vfx" in keys
    assert keys.index("vfx") == keys.index("action") + 1


def test_vfx_is_dynamic_section_key() -> None:
    assert "特效" in prompt_english._DYNAMIC_SECTION_KEYS
    assert "特效" not in prompt_english._STATIC_SECTION_KEYS


def test_compress_priority_contains_vfx() -> None:
    """压缩优先级链含特效（超预算时按序丢弃；特效比镜头/风格更该保）。"""
    from lib.shot_prompt_builder import _compress_prompt

    sections = [
        "【台词】 别动。",
        "【特效】 2.0秒处，青色剑气甩出",
        "【镜头】 特写",
        "【风格】 水墨",
    ]
    out = _compress_prompt("。".join(sections), sections, 30)
    # 特效在「镜头」「风格」之前保留；本预算下风格先被丢
    assert "【特效】" in out
    assert "【风格】" not in out


def test_prompt_pair_video_side_has_vfx_section() -> None:
    shot = _shot([{"layer": "prompt", "kind": "青色剑气沿刀锋甩出", "onset": 2.0, "intensity": 0.8}])
    pair = build_shot_prompt_pair(shot)
    assert pair["video_prompt"] is not None
    assert "【特效】" in pair["video_prompt"]
    assert "2.0秒处" in pair["video_prompt"]
    assert "青色剑气" in pair["video_prompt"]


def test_prompt_pair_first_frame_has_no_vfx_section() -> None:
    """静态侧（首帧图）不携带特效段——特效是特效发生后的动态事件。"""
    shot = _shot([{"layer": "prompt", "kind": "青色剑气", "onset": 2.0}])
    pair = build_shot_prompt_pair(shot)
    assert pair["first_frame_prompt"] is not None
    assert "【特效】" not in pair["first_frame_prompt"]


def test_prompt_pair_post_layer_not_rendered_into_prompt() -> None:
    """post 层特效不进生成提示词（它是 ffmpeg 后期，不是画面描述）。"""
    shot = _shot([
        {"layer": "post", "kind": "impact_flash", "onset": 0.5},
        {"layer": "prompt", "kind": "青色剑气", "onset": 2.0},
    ])
    pair = build_shot_prompt_pair(shot)
    assert "【特效】" in pair["video_prompt"]
    assert "impact_flash" not in pair["video_prompt"]


def test_vfx_dense_mode_keeps_single_item() -> None:
    """dense 下特效段只保主特效 1 条（即梦 400 字预算）。"""
    shot = _shot([
        {"layer": "prompt", "kind": "甲特效", "onset": 0.5},
        {"layer": "prompt", "kind": "乙特效", "onset": 2.0},
    ])
    pair = build_shot_prompt_pair(shot, dense=True)
    assert "【特效】" in pair["video_prompt"]
    assert "甲特效" in pair["video_prompt"]
    assert "乙特效" not in pair["video_prompt"]


def test_vfx_no_vfx_no_section() -> None:
    pair = build_shot_prompt_pair(_shot(None))
    assert "【特效】" not in (pair["video_prompt"] or "")


def test_kling_prompt_includes_vfx_for_video() -> None:
    shot = _shot([{"layer": "prompt", "kind": "青色剑气甩出", "onset": 1.0}], shot_language={"shot_size": "medium"})
    out = build_kling_prompt(shot, still=False, cite="omni")
    assert "特效：" in out
    assert "青色剑气" in out


def test_kling_still_has_no_vfx() -> None:
    shot = _shot([{"layer": "prompt", "kind": "青色剑气甩出", "onset": 1.0}])
    out = build_kling_prompt(shot, still=True, cite="omni")
    assert "特效：" not in out


def test_lift_shot_prompts_carries_vfx() -> None:
    """路由层字段白名单包含 vfx——不加点则 shot_runner 拿不到特效数据。"""
    lifted = lift_shot_prompts([
        _shot([{"layer": "post", "kind": "impact_flash", "onset": 0.5}]),
    ])
    assert lifted["shots"][0]["vfx"] == [{"layer": "post", "kind": "impact_flash", "onset": 0.5}]


def test_compose_plan_passes_vfx_through() -> None:
    from montage.tools.compose_planner import build_compose_plan

    scene_plan = {
        "scenes": [{
            "id": "sc01",
            "description": "开场",
            "shots": [_shot([{"layer": "post", "kind": "impact_flash", "onset": 0.5}])],
        }],
    }
    built = build_compose_plan(scene_plan)
    plan = built["compose_plan"]
    assert plan["shots"][0]["vfx"][0]["kind"] == "impact_flash"
    decisions = compile_compose_plan(plan)
    assert decisions["cuts"][0]["vfx"][0]["kind"] == "impact_flash"


def test_compile_compose_plan_omits_vfx_when_absent() -> None:
    plan = {"shots": [{"shot_id": "a", "clip_path": "x.mp4"}]}
    decisions = compile_compose_plan(plan)
    assert "vfx" not in decisions["cuts"][0]


# --- _assemble post 层消费 ----------------------------------------------------


def _write_clip(path: Path, *, duration: float = 1.0) -> None:
    """用 ffmpeg 造一个纯色测试条；无 ffmpeg 时测试 skip。"""
    import shutil

    ff = shutil.which("ffmpeg")
    if not ff:
        pytest.skip("需要 ffmpeg")
    import subprocess

    subprocess.run(
        [ff, "-y", "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=160x120:rate=25",
         "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, check=True,
    )


def test_assemble_applies_post_vfx(tmp_path: Path) -> None:
    """assemble 端到端：cuts[].vfx 的 post 层被应用，输出含特效产物。"""
    from montage.compose.ffmpeg_engine import FFmpegCompose

    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _write_clip(a)
    _write_clip(b)
    decisions = {
        "cuts": [
            {"from_scene": "a", "to_scene": "a", "shot_id": "a",
             "clip_path": str(a), "transition": "cut", "transition_duration": 0},
            {"from_scene": "a", "to_scene": "b", "shot_id": "b",
             "clip_path": str(b), "transition": "cut", "transition_duration": 0,
             "vfx": [{"layer": "post", "kind": "impact_flash", "onset": 0.2, "duration": 0.1}]},
        ],
        "render_runtime": "ffmpeg",
        "allow_non_cut": False,
    }
    (tmp_path / "edit_decisions.json").write_text(json.dumps(decisions), encoding="utf-8")
    result = FFmpegCompose().execute({
        "operation": "assemble",
        "edit_decisions_path": str(tmp_path / "edit_decisions.json"),
        "output_path": str(tmp_path / "renders" / "final.mp4"),
    })
    assert result.success, result.error
    report = result.data["render_report"]
    assert report["vfx_clips"] == 1
    out_dur = float(report["duration_seconds"])
    # 时长守恒：1 + 1 = 2s（特效不改变时长）
    assert abs(out_dur - 2.0) < 0.3


def test_assemble_without_vfx_reports_zero(tmp_path: Path) -> None:
    from montage.compose.ffmpeg_engine import FFmpegCompose

    a = tmp_path / "a.mp4"
    _write_clip(a)
    decisions = {
        "cuts": [{"shot_id": "a", "clip_path": str(a), "transition": "cut", "transition_duration": 0}],
        "render_runtime": "ffmpeg",
    }
    (tmp_path / "ed.json").write_text(json.dumps(decisions), encoding="utf-8")
    result = FFmpegCompose().execute({
        "operation": "assemble",
        "edit_decisions_path": str(tmp_path / "ed.json"),
        "output_path": str(tmp_path / "renders" / "final.mp4"),
    })
    assert result.success, result.error
    assert result.data["render_report"]["vfx_clips"] == 0


def test_assemble_vfx_before_join_and_lut(tmp_path: Path) -> None:
    """顺序锁（P0-8）：assemble 内 vfx 在 join/LUT 之前——stitch/concat 吃的是
    特效产物而非原始 clip；LUT 在 join 之后统一调色，闪白保持风格一致。
    锁的是数据流顺序：vfx 输出文件名进 join 输入列表。"""
    from montage.compose.ffmpeg_engine import FFmpegCompose

    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    _write_clip(a)
    _write_clip(b)
    decisions = {
        "cuts": [
            {"shot_id": "a", "clip_path": str(a), "transition": "cut", "transition_duration": 0,
             "vfx": [{"layer": "post", "kind": "impact_flash", "onset": 0.1, "duration": 0.1}]},
            {"shot_id": "b", "clip_path": str(b), "transition": "cut", "transition_duration": 0},
        ],
        "render_runtime": "ffmpeg",
    }
    (tmp_path / "ed.json").write_text(json.dumps(decisions), encoding="utf-8")
    result = FFmpegCompose().execute({
        "operation": "assemble",
        "edit_decisions_path": str(tmp_path / "ed.json"),
        "output_path": str(tmp_path / "renders" / "final.mp4"),
    })
    assert result.success, result.error
    # 特效产物落盘（join 的输入是它，不是 a.mp4）
    vfx_out = tmp_path / "a_vfx.mp4"
    assert vfx_out.is_file(), "vfx 产物必须在 join 前生成"
    joined = (tmp_path / "renders" / "final.joined.mp4")
    assert joined.is_file(), "join 产物存在（vfx 后于特效、先于 LUT）"
    # 时长守恒穿过 join：1+1=2s
    info = result.data.get("format") or {}
    assert abs(float(info.get("duration") or 0) - 2.0) < 0.3


def test_bible_schema_declares_vfx() -> None:
    """bible schema 显式声明 vfx/hero_moment（P0-8 收尾）：隐式契约显式化，
    与 V27 shot_language 同纪律——门禁/编辑器据此自描述。"""
    from montage.schemas import SERIES_BIBLE_SCHEMA
    from montage.engine.artifacts import ArtifactStore

    bible_scenes = SERIES_BIBLE_SCHEMA["properties"]["scenes"]["items"]["properties"]
    shot_props = bible_scenes["shots"]["items"]["properties"]
    assert "vfx" in shot_props
    assert "hero_moment" in shot_props
    assert shot_props["vfx"]["items"] is not None
    # 真跑一次校验：带 vfx 的 bible scene 过 schema 不报错
    schema = SERIES_BIBLE_SCHEMA
    sample = {
        "scenes": [{
            "id": "sc01", "description": "开场",
            "shots": [{
                "shot_id": "sc01_01", "hero_moment": True,
                "vfx": [{"layer": "post", "kind": "impact_flash", "onset": 0.5}],
            }],
        }],
    }
    errors = ArtifactStore.validate(sample, schema)
    assert not [e for e in errors if e.startswith("scenes")], errors
