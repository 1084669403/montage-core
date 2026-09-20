"""P0-8 后期特效（post 层 vfx）与 compile 自审测试。

三件特效（impact_flash/zoom_punch/camera_shake）+ apply_post_vfx 分发器 +
bible vfx/hero_moment 合并 + compile 确定性自审。纯函数/命令构造部分不碰
ffmpeg；只有真跑用例需要 ffmpeg，缺失直接 skip（仿 test_energy_wave 模式）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from montage.compose import effects
from montage.compose.ffmpeg_engine import check_ffmpeg
from montage.engine import bible as bible_mod

# --- 造数据 ------------------------------------------------------------------


def _bible_with_shot(vfx: list[dict] | None, *, hero: bool | None = None) -> dict:
    shot: dict = {"shot_id": "sc01_01", "subjects": [{"id": "a", "action": {"verb": "走"}}]}
    if hero is not None:
        shot["hero_moment"] = hero
    if vfx is not None:
        shot["vfx"] = vfx
    return {
        "title": "t",
        "format_card": {"audience": "x"},
        "characters": [{"id": "a", "name": "甲", "appearance": "黑衣"}],
        "locations": [{"id": "loc1", "name": "街", "sensory": "雨夜"}],
        "scenes": [{
            "id": "sc01",
            "summary": "开场",
            "script_text": "甲走进雨夜的街。",
            "shot_language": {},
            "shots": [shot],
        }],
    }


def _plan_with_shots(shots: list[dict]) -> dict:
    return {"scenes": [{"id": "sc01", "description": "开场", "shots": shots}]}


# --- apply_post_vfx 分发器（纯逻辑）------------------------------------------


def test_apply_post_vfx_empty_list_passthrough(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    out = effects.apply_post_vfx(src, tmp_path / "out.mp4", [])
    assert out.exists()


def test_apply_post_vfx_ignores_prompt_layer(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    out = effects.apply_post_vfx(src, tmp_path / "out.mp4", [
        {"layer": "prompt", "kind": "剑气"},
    ])
    assert out.exists()


def test_apply_post_vfx_unknown_kind_raises(tmp_path: Path) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    with pytest.raises(Exception, match="未知 post 层特效"):
        effects.apply_post_vfx(src, tmp_path / "out.mp4", [
            {"layer": "post", "kind": "explosion"},
        ])


def test_apply_post_vfx_no_vfx_env_passthrough(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    monkeypatch.setenv("MONTAGE_NO_VFX", "1")
    # 即使 post 条目存在也直通（不调 ffmpeg；源是假字节，真跑会崩）
    out = effects.apply_post_vfx(src, tmp_path / "out.mp4", [
        {"layer": "post", "kind": "impact_flash", "onset": 0.0},
    ])
    assert out.exists()


def test_apply_post_vfx_sorts_by_onset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """两处 post 特效按 onset 排序链式应用：首跑输出是链式中间产物名。"""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: int = 1800) -> None:
        calls.append(cmd)
        # 模拟 ffmpeg 产出：目标文件落一个假字节（链式下一步 guard 需要）
        Path(cmd[-1]).write_bytes(b"vfx")

    monkeypatch.setattr(effects, "_run", fake_run)
    monkeypatch.setattr(effects, "_ffmpeg", lambda: "ffmpeg")
    effects.apply_post_vfx(src, tmp_path / "out.mp4", [
        {"layer": "post", "kind": "camera_shake", "onset": 0.5},
        {"layer": "post", "kind": "impact_flash", "onset": 0.1},
    ], work_dir=tmp_path)
    assert len(calls) == 2
    # 第一跑 flash（onset 0.1），第二跑 shake（onset 0.5）
    assert any("eq=brightness" in part for part in calls[0])
    assert any("sin(t*80)" in part for part in calls[1])
    # 最终输出名是 out.mp4
    assert calls[-1][-1].endswith("out.mp4")


# --- 特效参数校验（纯逻辑，不碰 ffmpeg）---------------------------------------


@pytest.mark.parametrize("fn_name", ["impact_flash", "zoom_punch", "camera_shake"])
def test_vfx_param_validation(tmp_path: Path, fn_name: str) -> None:
    fn = getattr(effects, fn_name)
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    with pytest.raises(Exception, match="intensity"):
        fn(src, tmp_path / "a.mp4", onset=0.1, intensity=2.0)
    with pytest.raises(Exception, match="duration"):
        fn(src, tmp_path / "b.mp4", onset=0.1, duration=0.0)
    with pytest.raises(Exception, match="onset"):
        fn(src, tmp_path / "c.mp4", onset=-0.5)
    with pytest.raises(Exception, match="不存在"):
        fn(tmp_path / "missing.mp4", tmp_path / "d.mp4", onset=0.1)


def test_impact_flash_uses_eq_not_curves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """eq 有 timeline 支持；curves 没有（会全程生效）——选型锁定。"""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: int = 1800) -> None:
        calls.append(cmd)

    monkeypatch.setattr(effects, "_run", fake_run)
    monkeypatch.setattr(effects, "_ffmpeg", lambda: "ffmpeg")
    effects.impact_flash(src, tmp_path / "out.mp4", onset=0.5, duration=0.12, intensity=0.6)
    vf = calls[0][calls[0].index("-vf") + 1]
    assert vf.startswith("eq=brightness=")
    assert "curves" not in vf
    assert "between(t,0.500,0.620)" in vf


def test_zoom_punch_uses_zoompan_d1_with_ot_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """crop 动态 w/h 触发 filter 重初始化（ffmpeg 实测）——锁定 zoompan d=1 方案。"""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: int = 1800) -> None:
        calls.append(cmd)

    monkeypatch.setattr(effects, "_run", fake_run)
    monkeypatch.setattr(effects, "_ffmpeg", lambda: "ffmpeg")
    effects.zoom_punch(src, tmp_path / "out.mp4", onset=0.2, duration=0.25, intensity=0.5)
    vf = calls[0][calls[0].index("-vf") + 1]
    assert vf.startswith("zoompan=z=")
    assert ":d=1" in vf          # 每输入帧出 1 帧 → 时长守恒
    assert "between(ot" in vf    # 窗口用输出时间戳
    assert "crop=" not in vf


def test_camera_shake_uses_crop_xy_expression(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """震动锁定 crop x/y 表达式（w/h 固定，x/y 动态不触发重初始化）。"""
    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], timeout: int = 1800) -> None:
        calls.append(cmd)

    monkeypatch.setattr(effects, "_run", fake_run)
    monkeypatch.setattr(effects, "_ffmpeg", lambda: "ffmpeg")
    effects.camera_shake(src, tmp_path / "out.mp4", onset=0.2, duration=0.3, intensity=0.5)
    vf = calls[0][calls[0].index("-vf") + 1]
    assert vf.startswith("crop=w=iw:h=ih")
    assert "if(between(t" in vf
    assert "zoompan" not in vf


# --- 真跑（ffmpeg 门控）：时长守恒 -------------------------------------------


def _make_src_clip(tmp_path: Path) -> Path | None:
    """4 秒测试条（testsrc + 静音轨）；缺 ffmpeg 返回 None。"""
    if not check_ffmpeg():
        return None
    src = tmp_path / "src.mp4"
    effects._run([
        effects._ffmpeg(), "-y",
        "-f", "lavfi", "-i", "testsrc=duration=4:size=320x240:rate=25",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", "4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(src),
    ])
    return src if src.exists() else None


def _probe_duration(path: Path) -> float:
    """ffprobe JSON 里 duration 在 format 节点。"""
    info = effects.probe(path)
    return float((info.get("format") or {}).get("duration") or 0)


@pytest.mark.parametrize(
    "apply",
    [
        pytest.param(lambda s, o: effects.impact_flash(s, o, onset=1.0), id="impact_flash"),
        pytest.param(lambda s, o: effects.zoom_punch(s, o, onset=1.0), id="zoom_punch"),
        pytest.param(lambda s, o: effects.camera_shake(s, o, onset=1.0), id="camera_shake"),
        pytest.param(
            lambda s, o: effects.apply_post_vfx(s, o, [
                {"layer": "post", "kind": "impact_flash", "onset": 1.0},
                {"layer": "post", "kind": "camera_shake", "onset": 2.0},
            ]),
            id="chain",
        ),
    ],
)
def test_vfx_duration_conservation(tmp_path: Path, apply) -> None:
    """时长守恒：特效输出时长 == 输入时长（保护 film_health.duration_check）。"""
    src = _make_src_clip(tmp_path)
    if src is None:
        pytest.skip("需要 ffmpeg")
    out = apply(src, tmp_path / "out.mp4")
    assert out.exists()
    src_dur = _probe_duration(src)
    out_dur = _probe_duration(out)
    assert src_dur > 0
    assert abs(out_dur - src_dur) < 0.2, f"时长不守恒: {src_dur} -> {out_dur}"


# --- bible vfx / hero_moment 合并 --------------------------------------------


def test_bible_overlay_transfers_vfx() -> None:
    """bible vfx[] 合并进 scene_plan shots（唯一事实源链路）。"""
    bible = _bible_with_shot([
        {"layer": "post", "kind": "impact_flash", "onset": 0.5, "duration": 0.12},
        {"layer": "prompt", "kind": "青色剑气沿刀锋甩出"},
        {"not_a_vfx": True},
    ], hero=True)
    plan = _plan_with_shots([{"shot_id": "sc01_01", "duration_seconds": 2.0, "visual_details": {"subjects": []}}])
    bible_mod.overlay_visuals(plan, bible["scenes"], [])
    shot = plan["scenes"][0]["shots"][0]
    assert len(shot["vfx"]) == 2
    assert shot["vfx"][0]["kind"] == "impact_flash"
    assert shot["vfx"][1]["layer"] == "prompt"
    assert shot["hero_moment"] is True


def test_bible_overlay_hero_moment_default_keeps_plan_value() -> None:
    """bible 未声明 hero_moment 时保留 plan 原值（不覆盖为 False）。"""
    bible = _bible_with_shot(None)
    plan = _plan_with_shots([{
        "shot_id": "sc01_01", "duration_seconds": 2.0,
        "hero_moment": True, "visual_details": {"subjects": []},
    }])
    bible_mod.overlay_visuals(plan, bible["scenes"], [])
    assert plan["scenes"][0]["shots"][0]["hero_moment"] is True


def test_bible_overlay_vfx_invalid_entries_dropped() -> None:
    """脏 vfx 条目（layer/kind 非法）静默丢弃，合法条目保留。"""
    bible = _bible_with_shot([
        {"layer": "bogus", "kind": "x"},
        {"layer": "post", "kind": ""},
        {"layer": "post", "kind": "impact_flash", "onset": "bad"},
    ])
    plan = _plan_with_shots([{"shot_id": "sc01_01", "duration_seconds": 2.0, "visual_details": {"subjects": []}}])
    bible_mod.overlay_visuals(plan, bible["scenes"], [])
    vfx = plan["scenes"][0]["shots"][0]["vfx"]
    assert len(vfx) == 1
    assert vfx[0]["kind"] == "impact_flash"
    assert "onset" not in vfx[0]  # 非法 onset 被剥掉


# --- compile 确定性自审 -------------------------------------------------------


def test_audit_vfx_flags_out_of_range_onset() -> None:
    plan = _plan_with_shots([{
        "shot_id": "sc01_01", "duration_seconds": 2.0,
        "visual_details": {"subjects": []},
        "vfx": [{"layer": "post", "kind": "impact_flash", "onset": 5.0}],
    }])
    warns = bible_mod._audit_vfx(plan)
    assert any("onset=5.00" in w["message"] for w in warns)


def test_audit_vfx_flags_bad_post_kind() -> None:
    plan = _plan_with_shots([{
        "shot_id": "sc01_01", "duration_seconds": 2.0,
        "visual_details": {"subjects": []},
        "vfx": [{"layer": "post", "kind": "explosion", "onset": 0.5}],
    }])
    warns = bible_mod._audit_vfx(plan)
    assert any("explosion" in w["message"] for w in warns)


def test_audit_vfx_flags_missing_sfx() -> None:
    plan = _plan_with_shots([{
        "shot_id": "sc01_01", "duration_seconds": 2.0,
        "visual_details": {"subjects": []},
        "vfx": [{"layer": "prompt", "kind": "青色剑气"}],
    }])
    warns = bible_mod._audit_vfx(plan)
    assert any("sfx" in w["message"] for w in warns)


def test_audit_vfx_density_red_line_counts_non_hero_post() -> None:
    """非 hero 镜 post 特效 >3 处触发红线；hero 镜不计入。"""
    shots = []
    for i in range(5):
        shots.append({
            "shot_id": f"sc01_{i + 1:02d}", "duration_seconds": 2.0,
            "hero_moment": i == 0,
            "visual_details": {"subjects": []},
            "vfx": [{"layer": "post", "kind": "impact_flash", "onset": 0.5}],
        })
    warns = bible_mod._audit_vfx(_plan_with_shots(shots))
    density = [w for w in warns if "密度红线" in w["message"]]
    assert len(density) == 1
    assert "4 处" in density[0]["message"]  # 5 镜里 4 个非 hero


def test_audit_vfx_clean_plan_no_warnings() -> None:
    plan = _plan_with_shots([{
        "shot_id": "sc01_01", "duration_seconds": 2.0,
        "hero_moment": True, "visual_details": {"subjects": []},
        "audio_prompt": {"sfx": [{"sound": "冲击"}]},
        "vfx": [{"layer": "post", "kind": "impact_flash", "onset": 0.5, "duration": 0.12}],
    }])
    assert bible_mod._audit_vfx(plan) == []


def test_compile_bible_emits_vfx_findings(tmp_path: Path) -> None:
    """端到端：compile_bible 把 vfx 自审 findings 带出来。"""
    from montage.engine.bible import compile_bible

    bible = _bible_with_shot([
        {"layer": "post", "kind": "impact_flash", "onset": 9.9},  # 越界（镜 2s）
    ])
    result = compile_bible(bible)
    assert any("onset=9.90" in f.get("message", "") for f in result["findings"])
    # 合并仍然发生
    shot = result["scene_plan"]["scenes"][0]["shots"][0]
    assert shot["vfx"][0]["kind"] == "impact_flash"


def test_schema_vfx_fields_present() -> None:
    """schema 三处 vfx 字段 + VFX_ITEM 关键约束存在。"""
    from montage.schemas import (
        EDIT_DECISIONS_SCHEMA,
        NESTED_SHOT_SCHEMA,
        SCENE_PLAN_SCHEMA,
        VFX_ITEM_SCHEMA,
    )

    assert VFX_ITEM_SCHEMA["properties"]["layer"]["enum"] == ["prompt", "post"]
    assert NESTED_SHOT_SCHEMA["properties"]["vfx"]["items"] is VFX_ITEM_SCHEMA
    cuts_props = EDIT_DECISIONS_SCHEMA["properties"]["cuts"]["items"]["properties"]
    assert cuts_props["vfx"]["items"] is VFX_ITEM_SCHEMA
    scene_shot = SCENE_PLAN_SCHEMA["properties"]["scenes"]["items"]["properties"]["shots"]["items"]
    assert scene_shot["properties"]["vfx"]["items"] is VFX_ITEM_SCHEMA


def test_json_serializable_vfx_roundtrip(tmp_path: Path) -> None:
    """vfx 数据经 json 往返不丢字段（bible → compile → 落盘链路）。"""
    payload = {"vfx": [{
        "layer": "post", "kind": "impact_flash",
        "onset": 0.5, "duration": 0.12, "intensity": 0.6, "note": "命中瞬间",
    }]}
    raw = json.dumps(payload, ensure_ascii=False)
    assert json.loads(raw) == payload
