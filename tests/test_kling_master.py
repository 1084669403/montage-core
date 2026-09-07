"""母带三段式：lib.kling_master + build_kling_prompt 契约块 + 注入链。零真实 HTTP。"""

from __future__ import annotations

from lib.kling_master import (
    CAPABILITY_GUARDRAILS,
    KLING_MASTER_MODES,
    pick_master,
)
from lib.shot_prompt_builder import build_kling_prompt


def _shot(shot_id: str = "sc01_01", subjects: list | None = None) -> dict:
    return {
        "shot_id": shot_id,
        "shot_kind": "video",
        "shot_language": {"shot_size": "medium"},
        "visual_details": {
            "subjects": subjects
            or [{"name": "水獭", "blocking": "left", "action": {"verb": "追"}}]
        },
    }


def test_seven_modes_available():
    assert set(KLING_MASTER_MODES) == {
        "film_spectacle",
        "chase_axis",
        "signature_orbit",
        "underwater_rescue",
        "vertical_fall",
        "space_dive",
        "celebration_carnival",
    }
    for mid in KLING_MASTER_MODES:
        m = pick_master(mid)
        assert m is not None
        assert m.get("master_prompt")
        assert m.get("hard_constraints")
        assert m.get("audio_principle")
    assert pick_master("nope") is None
    assert pick_master(None) is None


def test_no_master_keeps_legacy_shape():
    """不带母带时，输出与旧版逐镜骨架一致（无母带块）。"""
    out = build_kling_prompt(_shot(), cite="omni", max_chars=2500)
    assert "中景" in out
    assert "@element_1" in out
    assert "追" in out
    assert "照片级" not in out  # 无母带时不引入模式句


def test_master_prompt_prepended_and_video_only():
    shot = _shot()
    vid = build_kling_prompt(
        shot, cite="omni", max_chars=2500,
        master_prompt="照片级真人实拍，21:9 变形宽银幕，180° 快门运动模糊",
    )
    assert vid.startswith("照片级真人实拍")
    assert "中景" in vid
    # 静帧（首帧图）不带母带
    still = build_kling_prompt(
        shot, cite="omni", max_chars=2500,
        master_prompt="照片级真人实拍", still=True,
    )
    assert "照片级" not in still
    assert "定格静帧" in still


def test_master_pattern_expands_hard_constraints_and_audio():
    shot = _shot()
    out = build_kling_prompt(shot, cite="omni", max_chars=2500, master_pattern="chase_axis")
    assert "屏幕方向恒左→右推进" in out
    assert "唯一一处慢动作" in out
    assert "声音：无对白无人声" in out
    assert "中景" in out


def test_explicit_master_prompt_overrides_pattern_sentence_keeps_constraints():
    shot = _shot()
    out = build_kling_prompt(
        shot, cite="omni", max_chars=2500,
        master_prompt="自定义覆盖母带句", master_pattern="chase_axis",
    )
    assert out.startswith("自定义覆盖母带句")
    assert "屏幕方向恒左→右推进" in out  # 模式的硬约束仍并入


def test_contract_shot_type_by_index():
    from montage.tools._shot_contracts import shot_type_for

    assert shot_type_for("space_dive", 7)["type"] == "第一视角 FPV 冲进花心"
    assert shot_type_for("space_dive", 7)["duration"] == "16-17s"
    assert shot_type_for("underwater_rescue", 8)["note"] == "黑点↔黑✗首次切换"
    # 镜头序号解析：sc01_07 → 第 7 镜（_ 后序号即镜头序号）
    shot = _shot("sc01_07")
    out = build_kling_prompt(shot, cite="omni", max_chars=2500, master_pattern="space_dive")
    assert "本镜类型：第一视角 FPV 冲进花心" in out
    # sc01_01 → 第 1 镜
    out1 = build_kling_prompt(_shot("sc01_01"), cite="omni", max_chars=2500, master_pattern="space_dive")
    assert "本镜类型：分层建立·强俯视+荷兰角" in out1
    # vertical_fall 补表：镜头 1 一镜到底走位进契约块
    out_vf = build_kling_prompt(_shot("sc01_01"), cite="omni", max_chars=2500, master_pattern="vertical_fall")
    assert "一镜到底：0-11s" in out_vf
    assert "走位：" in out_vf
    assert "首次切镜：镜头 2 老板特写首次切换" in out_vf


def test_contract_state_timeline_full_shot_coverage():
    """状态演进全镜覆盖：切换点前输出禁止态，切换点起输出新态。"""
    shot1 = _shot("sc01_01")
    out1 = build_kling_prompt(
        shot1, cite="omni", max_chars=2500, master_pattern="underwater_rescue"
    )
    # 镜头 1：禁止态（负向约束）
    assert "种子状态约束" in out1
    assert "仅纯黑圆点" in out1
    assert "禁止" in out1
    assert "黑✗" in out1
    shot8 = _shot("sc01_08")
    out8 = build_kling_prompt(
        shot8, cite="omni", max_chars=2500, master_pattern="underwater_rescue"
    )
    # 镜头 8：切换点，输出新态演进
    assert "种子状态演进" in out8
    assert "自本镜起" in out8
    assert "种子状态约束" not in out8  # 切换点后不再输出禁止态


def test_contract_first_shot_emits_arc_cast_crowd_sync_curve():
    shot = _shot("sc01_01")
    out = build_kling_prompt(
        shot, cite="omni", max_chars=2500, master_pattern="celebration_carnival"
    )
    assert "本镜类型：种子背后 OTS 过山车滑梯" in out
    assert "角色弧线" in out
    assert "卡司构成" in out
    assert "标签唯一" in out
    assert "人群：" in out
    assert "声画同步" in out
    assert "情绪曲线" in out
    assert "声音：环境音+台词" in out


def test_contract_iron_laws_every_shot():
    """色域/尺度/方向/辉光铁律每镜输出（生成器易漂移需重申）。"""
    out = build_kling_prompt(
        _shot("sc01_03"), cite="omni", max_chars=2500, master_pattern="celebration_carnival"
    )
    assert "色域：" in out
    assert "严禁廉价蓝滤镜" in out
    assert "尺度：" in out
    assert "辉光：" in out
    # vertical_fall 的方向/尺度/色域铁律
    out_vf = build_kling_prompt(
        _shot("sc01_02"), cite="omni", max_chars=2500, master_pattern="vertical_fall"
    )
    assert "方向：" in out_vf
    assert "严禁向上攀爬观感" in out_vf
    assert "尺度：" in out_vf
    assert "两层楼高以上" in out_vf
    assert "色域：" in out_vf
    assert "严禁暖金橙" in out_vf
    # space_dive 的尺度铁律
    out_sd = build_kling_prompt(
        _shot("sc01_02"), cite="omni", max_chars=2500, master_pattern="space_dive"
    )
    assert "尺度：" in out_sd
    assert "每只昆虫至少四倍大" in out_sd


def test_shot_timestamp_prefix():
    """逐镜块带 [Ns-Ms] 时间戳：契约时间窗 > one_take first_shot > shot 时长 > 无。"""
    # celebration_carnival 契约时间窗 0-5s
    out = build_kling_prompt(
        _shot("sc01_01"), cite="omni", max_chars=2500, master_pattern="celebration_carnival"
    )
    assert "[0s-5s]" in out
    # space_dive 镜头 7 契约整片时间窗 16-17s
    out7 = build_kling_prompt(
        _shot("sc01_07"), cite="omni", max_chars=2500, master_pattern="space_dive"
    )
    assert "[16s-17s]" in out7
    # space_dive 镜头 2 → [3s-6s]；underwater_rescue 镜头 5 → [11s-13s]
    out2 = build_kling_prompt(
        _shot("sc01_02"), cite="omni", max_chars=2500, master_pattern="space_dive"
    )
    assert "[3s-6s]" in out2
    out_u5 = build_kling_prompt(
        _shot("sc01_05"), cite="omni", max_chars=2500, master_pattern="underwater_rescue"
    )
    assert "[11s-13s]" in out_u5
    # vertical_fall 镜头 1：契约 duration 0-11s → [0s-11s]
    out_vf = build_kling_prompt(
        _shot("sc01_01"), cite="omni", max_chars=2500, master_pattern="vertical_fall"
    )
    assert "[0s-11s]" in out_vf
    # 无契约模式 + shot 自带时长 → [0-Ns]
    out_dur = build_kling_prompt(
        {"shot_id": "sc01_01", "shot_kind": "video", "duration_seconds": 4,
         "shot_language": {"shot_size": "medium"},
         "visual_details": {"subjects": [{"name": "x", "blocking": "center",
                                          "action": {"verb": "跑"}}]}},
        cite="omni", max_chars=2500, master_pattern="film_spectacle",
    )
    assert "[0s-4s]" in out_dur
    # 静帧无时间戳
    still = build_kling_prompt(
        _shot("sc01_01"), cite="omni", max_chars=2500, master_pattern="celebration_carnival", still=True,
    )
    assert "[0s-5s]" not in still


def test_contract_duration_label_uses_时段_not_时长():
    """契约块 duration 文案用「时段」而非「时长」，与时间戳语义一致。"""
    out = build_kling_prompt(
        _shot("sc01_01"), cite="omni", max_chars=2500, master_pattern="celebration_carnival"
    )
    assert "时段0-5s" in out
    assert "时长0-5s" not in out


def test_capability_guardrails_cover_all_modes():
    assert set(CAPABILITY_GUARDRAILS) == set(KLING_MASTER_MODES)
    for mid, rows in CAPABILITY_GUARDRAILS.items():
        assert rows, mid
        for row in rows:
            assert row.get("instruction")
            assert row.get("risk")
            assert row.get("mitigation")


def test_prompt_inputs_injects_master(tmp_path, monkeypatch):
    """_prompt_inputs 从 proposal_packet.playbook 取 master_pattern/master_prompt。"""
    from montage.engine.artifacts import ArtifactStore
    from montage.engine.project import init_project
    from montage.tools._shot_route import _prompt_inputs

    proj = init_project(tmp_path, "demo", "演示", "cinematic")
    store = ArtifactStore(proj)
    store.write("proposal_packet", {"concept": "x", "playbook": "chase_comedy"})
    out = _prompt_inputs(
        {"shot_id": "sc01_01", "shot_kind": "video", "duration_seconds": 5},
        {"character_registry": [], "locations": []},
        str(proj),
        agnes_loop=False,
        vid_prov="kling",
        api_id="kling_omni_30",
    )
    assert out["kling_prompt"] is True
    assert out["master_pattern"] == "chase_axis"
    assert out["master_prompt"] is None  # chase_comedy 无覆盖字符串
    # 非可灵环不注入
    out_ark = _prompt_inputs(
        {"shot_id": "sc01_01", "shot_kind": "video", "duration_seconds": 5},
        {},
        str(proj),
        agnes_loop=False,
        vid_prov="ark",
        api_id="seedance_25",
    )
    assert out_ark["kling_prompt"] is False
    assert "master_pattern" not in out_ark or out_ark["master_pattern"] is None


def test_capability_findings_on_preview_and_retry(tmp_path):
    """await_retry 与 await_final_prompt 卡都附能力边界 findings；其他状态不加。"""
    from montage.engine.artifacts import ArtifactStore
    from montage.engine.director import _kling_capability_findings, write_director_review
    from montage.engine.project import init_project

    proj = init_project(tmp_path, "demo", "演示", "cinematic")
    store = ArtifactStore(proj)
    store.write("proposal_packet", {"concept": "x", "playbook": "chase_comedy"})
    # 直接测内部函数：首轮生成前与重抽前都给出能力边界
    for status in ("await_final_prompt", "await_retry"):
        finds = _kling_capability_findings(store, status)
        assert finds
        assert all(f.get("message", "").startswith("[能力边界]") for f in finds)
        assert any("唯一一处慢动作" in f["message"] for f in finds)
    # 其他状态不加
    assert _kling_capability_findings(store, "await_clips") == []
    # 完整走一遍 write_director_review 不崩（两张卡）
    for status in ("await_final_prompt", "await_retry"):
        card = write_director_review(proj, status, retry_ids=["sc01_01"])
        msgs = [f.get("message", "") for f in card.get("findings") or []]
        assert any("能力边界" in m for m in msgs)
