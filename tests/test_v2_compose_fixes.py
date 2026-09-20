"""v2 阶段 A 的回归用例：画布/时基统一、逐镜配平、字幕样式、整片深检。

真 ffmpeg 用例走 ``MONTAGE_REAL_FFMPEG`` 门控（conftest 自动探测本机 ffmpeg）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from montage.compose import audio_level
from montage.compose.ffmpeg_engine import (
    clips_need_reencode,
    concat_videos,
    detect_freezes,
    detect_pts_gaps,
    probe,
    probe_media_params,
    stitch_with_transitions,
    xfade_offsets,
)
from montage.style_packs import STYLE_PACKS
from montage.tools.film_health import filter_title_card_freezes, inspect_film
from montage.tools.subtitle_builder import timestamps_to_ass
from lib.shot_presence import (
    continuity_for,
    normalize_presence,
    presence_prompt_lines,
)


def _require_ffmpeg() -> str:
    import os
    import shutil

    if os.environ.get("MONTAGE_REAL_FFMPEG") != "1":
        pytest.skip("设置 MONTAGE_REAL_FFMPEG=1 后跑真 ffmpeg 用例")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("缺少 ffmpeg")
    return ffmpeg


def _make_clip(
    ffmpeg: str,
    dest: Path,
    *,
    seconds: float = 1.0,
    fps: int = 24,
    rate: int = 48000,
    size: str = "320x240",
    volume: float = 1.0,
    audio: bool = True,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate={fps}",
    ]
    if audio:
        cmd += [
            "-f", "lavfi", "-i",
            f"sine=frequency=440:duration={seconds}:sample_rate={rate}",
            "-af", f"volume={volume}",
        ]
    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
    ]
    if audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd += [str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest


# --- A4：古风包不再挂全局暖色 LUT ---------------------------------------


def test_classic_pack_lut_removed():
    assert STYLE_PACKS["classic"]["lut"] == ""
    # 其它包不受影响（只有 classic 去 LUT）
    assert STYLE_PACKS["cinematic"]["lut"]


# --- A2/A3：参数一致性与统一画布 ---------------------------------------


def test_clips_need_reencode_detects_param_mismatch(tmp_path):
    ffmpeg = _require_ffmpeg()
    a = _make_clip(ffmpeg, tmp_path / "a.mp4", fps=24)
    b = _make_clip(ffmpeg, tmp_path / "b.mp4", fps=30)
    # 参数探测不到（假文件）也判需要重编码：流拷贝赌不起
    assert clips_need_reencode([tmp_path / "missing.mp4"]) is True
    assert clips_need_reencode([a]) is False
    assert clips_need_reencode([a, b]) is True
    assert clips_need_reencode([a], target_size=(640, 360)) is True
    assert clips_need_reencode([a], fps=30) is True


def test_concat_videos_normalizes_to_target_canvas(tmp_path):
    ffmpeg = _require_ffmpeg()
    a = _make_clip(ffmpeg, tmp_path / "a.mp4", fps=24, rate=48000)
    b = _make_clip(ffmpeg, tmp_path / "b.mp4", fps=30, rate=32000)
    out = concat_videos(
        [a, b], tmp_path / "out.mp4", target_size=(640, 360), fps=24,
    )
    info = probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (640, 360)
    assert float(video["r_frame_rate"].split("/")[0]) == 24
    assert int(audio["sample_rate"]) == 48000
    assert int(audio["channels"]) == 2
    # 音视频流时长差 <0.1s（A2 验收口径）
    assert abs(float(info["format"]["duration"]) - 2.0) < 0.2


def test_stitch_with_transitions_uses_target_canvas(tmp_path):
    ffmpeg = _require_ffmpeg()
    a = _make_clip(ffmpeg, tmp_path / "a.mp4", fps=24)
    b = _make_clip(ffmpeg, tmp_path / "b.mp4", fps=24)
    out = stitch_with_transitions(
        [a, b],
        [{"shot_id": "sh02", "transition": "crossfade", "transition_duration": 0.5}],
        tmp_path / "stitched.mp4",
        target_size=(640, 360),
        fps=24,
    )
    info = probe(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (640, 360)
    assert float(video["r_frame_rate"].split("/")[0]) == 24


def test_xfade_offsets_are_frame_aligned():
    """转场 offset/duration 必须落在帧边界。

    半帧误差会让 xfade 在转场窗口末尾少输出帧，成片出现 0.1s 级 PTS 空洞
    （《宦娘》成片唯一一处断档 217.07s 就是这个成因）。
    """
    durations = [10.144, 10.144, 8.0, 12.256]
    transitions = [
        {"transition": "zoom_punch", "transition_duration": 0.6},
        {"transition": "crossfade", "transition_duration": 0.5},
        {"transition": "cut"},
    ]
    rows = xfade_offsets(durations, transitions, fps=30)
    assert len(rows) == 3
    for row in rows:
        for key in ("offset", "duration"):
            frames = row[key] * 30
            assert abs(frames - round(frames)) < 1e-9, f"{key} 未落在帧边界：{row[key]}"
    # 第一接点：10.144s = 304.32 帧 → 304 帧；0.6s = 18 帧 → offset = 286 帧
    assert rows[0]["offset"] == pytest.approx(286 / 30)
    assert rows[0]["duration"] == pytest.approx(18 / 30)
    # cut 不吃重叠（调用方已拆段）
    assert rows[2]["duration"] == 0.0


def test_stitch_zoom_punch_chain_has_no_pts_gap(tmp_path):
    """全片唯一一段非硬切链的接点不得留 PTS 空洞（真 ffmpeg 复现）。"""
    ffmpeg = _require_ffmpeg()
    clips = [
        _make_clip(ffmpeg, tmp_path / f"c{i}.mp4", seconds=sec, fps=30)
        for i, sec in enumerate((3.017, 2.983, 3.051))
    ]
    out = stitch_with_transitions(
        clips,
        [
            {"transition": "zoom_punch", "transition_duration": 0.6},
            {"transition": "zoom_punch", "transition_duration": 0.6},
        ],
        tmp_path / "chain.mp4",
        target_size=(320, 240),
        fps=30,
    )
    report = detect_pts_gaps(out)
    assert report["checked"] is True
    assert report["gaps"] == []
    # 时长 = 三段之和 − 两次 0.6s 重叠（允许一帧误差）
    expected = 3.017 + 2.983 + 3.051 - 1.2
    assert abs(float(probe(out)["format"]["duration"]) - expected) < 1 / 30 + 0.05


# --- A6：逐镜音频配平 ---------------------------------------------------


def test_plan_gains_median_target_and_clamp():
    plan = audio_level.plan_gains({"a": -20.0, "b": -22.0, "c": -18.0})
    assert plan["target_db"] == -20.0
    assert plan["gains"]["b"]["gain_db"] == 2.0
    quiet = audio_level.plan_gains({"a": -18.0, "b": -55.2, "c": -20.0})
    entry = quiet["gains"]["b"]
    assert entry["gain_db"] == audio_level.MAX_BOOST_DB  # 钳制生效
    assert entry["clamped"] > audio_level.MAX_BOOST_DB
    assert entry["residual_db"] > 0  # 仍达不到目标，如实记账
    missing = audio_level.plan_gains({"a": None, "b": -20.0})
    assert missing["gains"]["a"]["gain_db"] == 0.0


def test_level_clips_reduces_spread(tmp_path):
    ffmpeg = _require_ffmpeg()
    loud = _make_clip(ffmpeg, tmp_path / "loud.mp4", volume=1.0)
    soft = _make_clip(ffmpeg, tmp_path / "soft.mp4", volume=0.03)
    report = audio_level.level_clips([loud, soft], tmp_path / "leveled")
    assert report["disabled"] is False
    assert report["spread_before"] > report["spread_after"]
    assert all(Path(p).is_file() for p in report["paths"])
    # 配平只重编码音轨：视频流保留
    for path in report["paths"]:
        info = probe(path)
        assert any(s["codec_type"] == "video" for s in info["streams"])


# --- A2：整片深检（PTS 断档 / 冻结帧） ---------------------------------


def test_deep_checks_report_structure(tmp_path):
    ffmpeg = _require_ffmpeg()
    clip = _make_clip(ffmpeg, tmp_path / "plain.mp4", seconds=2.0)
    gaps = detect_pts_gaps(clip)
    assert gaps["checked"] is True and gaps["frames"] > 10
    assert gaps["gaps"] == []
    freezes = detect_freezes(clip, min_seconds=0.5)
    assert freezes["checked"] is True
    assert isinstance(freezes["segments"], list)


def test_inspect_film_deep_checks_are_warnings(tmp_path):
    film = tmp_path / "final.mp4"
    film.write_bytes(b"x")

    def fake_probe(_path):
        return {
            "format": {"duration": "10.0", "size": "100"},
            "streams": [
                {"codec_type": "video", "width": 1920, "height": 1080,
                 "codec_name": "h264", "r_frame_rate": "24/1"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }

    report = inspect_film(
        film,
        probe_fn=fake_probe,
        gap_fn=lambda _p: {"checked": True, "gaps": [{"start": 70.2, "duration": 4.9}],
                           "gap_seconds": 4.96},
        freeze_fn=lambda _p: {"checked": True, "segments": [{"start": 266.3, "duration": 1.5}],
                              "total_seconds": 1.5},
    )
    assert report["pass"] is True  # 深检不挡出口
    fields = {w["field"] for w in report["warnings"]}
    assert "pts_gaps" in fields and "freezes" in fields


def test_title_card_freeze_is_not_a_defect():
    report = {
        "segments": [{"start": 0.0, "duration": 2.03}, {"start": 266.3, "duration": 1.5}],
        "total_seconds": 3.53,
    }
    filtered = filter_title_card_freezes(report, 2.0)
    assert [s["start"] for s in filtered["segments"]] == [266.3]
    assert filtered["total_seconds"] == 1.5
    assert filtered["ignored_title_card_seconds"] == 2.0
    # 没有片头时不改动
    untouched = filter_title_card_freezes({"segments": [], "total_seconds": 0.0}, 0.0)
    assert untouched["segments"] == []


# --- 画面/构图/负向词（2026-09-19 用户实测反馈后补的护栏）-----------------


def test_off_frame_characters_stay_in_ledger_but_not_in_frame():
    """画外人物：承接算在场，但**不进**画面主体清单（否则空镜凭空生出人物）。"""
    prev = normalize_presence({
        "location": {"id": "loc_hall"},
        "characters": [{"id": "monk"}, {"id": "wen_ruchun"}],
    })
    current = normalize_presence({
        "location": {"id": "loc_hall"},
        "characters": [{"id": "monk"}],
        "off_frame": ["wen_ruchun"],
    })
    ledger = continuity_for(prev, current, prev_shot_id="sc01_04")
    assert ledger["missing"] == []          # 画外算在场，不报缺失
    off = [row for row in ledger["must_keep"] if row.get("off_frame")]
    assert [row["id"] for row in off] == ["wen_ruchun"]
    lines = presence_prompt_lines(current, ledger, names={"wen_ruchun": "温如春"})
    assert any(line.startswith("【画外】") and "温如春" in line for line in lines)
    assert all("温如春" not in line for line in lines if line.startswith("【在场清单】"))


def test_reference_prompts_pin_framing_and_ban_text():
    """定妆/场景参考图必须带构图与"禁文字/禁错乐器"约束。"""
    from lib.shot_prompt_builder import build_reference_image_prompt

    portrait = build_reference_image_prompt(
        "portrait",
        character={"id": "wen_ruchun", "name": "温如春", "appearance": "清瘦书生"},
    )
    prompt = str(portrait.get("first_frame_prompt") or "")
    # 2026-09-19 用户实测：只写"主体完整"会被裁掉头；"全身/半身到腰"方差大（仍裁头），
    # 定妆改为**近景胸像**（以脸为主体，头部必然完整），全身体态交给四视图。
    assert "近景胸像定妆照" in prompt and "完整发顶" in prompt
    assert "严禁裁切头顶" in prompt

    scene = build_reference_image_prompt(
        "scene_ref",
        scene={"description": "殿内：左侧木鱼与蒲团，中下方青砖反烛光，正中更远处佛像"},
    )
    scene_prompt = str(scene.get("first_frame_prompt") or "")
    assert "纯环境空镜" in scene_prompt and "不得新增匾额" in scene_prompt
    negative = str(scene.get("image_negative_prompt") or "")
    assert "匾额" in negative and "琵琶" in negative


def test_shot_prompts_use_display_names_not_ids():
    """提示词里不许出现 wen_ruchun / loc_gate 这类 id（2026-09-19 用户实测反馈）。

    动作段此前用 subject id；在场清单/画外/承接若拿不到名字也会回落 id。
    """
    from lib.shot_prompt_builder import build_shot_prompt_pair

    shot = {
        "shot_id": "sc01_01",
        "scene_id": "sc01",
        "shot_kind": "video",
        "duration_seconds": 10,
        "location_id": "loc_gate",
        "visual_details": {
            "environment": "雨夜山门，石阶湿滑",
            "subjects": [{
                "id": "wen_ruchun",
                "appearance_anchor": "青灰直裰",
                "action": {"verb": "仰头看门", "manner": "伞面压低"},
            }],
        },
        "presence": {
            "location": {"id": "loc_gate", "zone": "石阶下", "time_of_day": "夜"},
            "characters": [{"id": "wen_ruchun", "zone": "画面中下", "state": "撑伞而立"}],
            "props": [{"id": "prop_lantern", "holder": "monk", "zone": "门环上"}],
            "off_frame": ["huan_niang"],
        },
        "continuity": {"from_shot": "sc01_00", "must_keep": [], "changed": [], "missing": []},
    }
    registry = [
        {"id": "wen_ruchun", "name": "温如春", "appearance": "清瘦书生"},
        {"id": "huan_niang", "name": "宦娘", "appearance": "素衣女子"},
        {"id": "monk", "name": "老僧", "appearance": "灰衣僧人"},
    ]
    pair = build_shot_prompt_pair(
        shot,
        registry,
        None,
        agnes_v25=True,
        presence_names={"loc_gate": "山门·雨夜", "prop_lantern": "灯笼"},
    )
    text = "\n".join(str(pair.get(k) or "") for k in ("first_frame_prompt", "video_prompt"))
    for ident in ("wen_ruchun", "huan_niang", "monk", "loc_gate", "prop_lantern"):
        assert ident not in text, f"提示词里出现了 id: {ident}"
    for label in ("温如春", "宦娘", "老僧", "山门·雨夜", "灯笼"):
        assert label in text, f"提示词缺少中文名: {label}"


# --- A5：字幕样式（PlayRes / 安全区） ----------------------------------


def test_ass_playres_and_margin_follow_film_size():
    text = timestamps_to_ass(
        [{"text": "宦娘夜半抚琴，灯火如豆。", "start_seconds": 1.0, "end_seconds": 4.0}],
        play_res_x=1920,
        play_res_y=1080,
        margin_v=168,
        font="Noto Sans SC",
    )
    assert "PlayResX: 1920" in text and "PlayResY: 1080" in text
    style = next(line for line in text.splitlines() if line.startswith("Style: Default"))
    assert "Noto Sans SC" in style
    assert style.rstrip().endswith("80,80,168,1")
    vertical = timestamps_to_ass(
        [{"text": "一句", "start_seconds": 0, "end_seconds": 1}],
        play_res_x=1080,
        play_res_y=1920,
    )
    assert "PlayResX: 1080" in vertical and "PlayResY: 1920" in vertical


# --- A5：成片字幕时间轴取投影（含转场交叠），不是计划 cue -----------------


def test_subtitle_cues_use_projection_with_overlap():
    from montage.engine.produce import subtitle_cues_for_film

    plan = {
        "shots": [
            {
                "shot_id": "sh01",
                "duration_seconds": 10.0,
                "subtitle_cues": [{"text": "一", "start_seconds": 0.0, "end_seconds": 1.0}],
            },
            {
                "shot_id": "sh02",
                "duration_seconds": 10.0,
                "transition": "crossfade",
                "transition_duration": 1.0,
                "subtitle_cues": [{"text": "二", "start_seconds": 10.0, "end_seconds": 11.0}],
            },
        ],
    }
    cues = subtitle_cues_for_film(plan, offset=2.0)
    assert [c["text"] for c in cues] == ["一", "二"]
    assert cues[0]["start_seconds"] == 2.0
    # 第二镜被 1s 交叠提前 → 投影起点 9.0 + 片头 2.0
    assert cues[1]["start_seconds"] == 11.0
    # 投影不可用时回落计划 cue（只做偏移）
    fallback = subtitle_cues_for_film(None, offset=2.0, fallback=[
        {"text": "一", "start_seconds": 0.0, "end_seconds": 1.0},
    ])
    assert fallback[0]["start_seconds"] == 2.0
