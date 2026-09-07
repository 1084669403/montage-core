"""AutoEditor 纯逻辑层：plan / replan / 转场覆写 / 只读校验（不调 ffmpeg）。"""

import json

from montage.schemas import get_schema
from montage.style_packs import get_style_pack
from montage.tools.auto_edit import (
    AutoEdit,
    assert_output_inside,
    build_plan,
    ensure_dirs,
    ensure_project_meta,
    hash_file,
    plan_differs,
    rebuild_edits,
    replan,
    shots_from_cuts,
    verify_integrity,
)


def _decl(duration: float = 20.0, *, is_speech: bool = False, n: int = 1) -> dict:
    files = [
        {"path": f"/abs/src{i}.mp4", "sha256": f"ab{i}", "duration": duration}
        for i in range(n)
    ]
    return {
        "kind": "video",
        "has_bgm": False,
        "is_speech": is_speech,
        "language": "zh",
        "files": files,
    }


def test_shots_merge_short_and_split_long():
    shots = shots_from_cuts(
        12.0, [0.1, 0.2, 6.0], min_hold=2.0, max_hold=5.0, segment_id="seg_0",
    )
    assert shots[0]["start"] == 0.0
    assert shots[-1]["end"] == 12.0
    spans = [s["end"] - s["start"] for s in shots]
    assert all(sp >= 1.9 for sp in spans)
    assert all(sp <= 5.1 for sp in spans)


def test_build_plan_required_fields():
    plan = build_plan(
        source_decl=_decl(16.0),
        style_pack_id="documentary",
        scene_changes_by_source=[[4.0, 10.0]],
        session_id="20260818T000000Z",
    )
    required = get_schema("auto_edit_plan")["required"]
    for key in required:
        assert key in plan
    assert plan["session_id"] == "20260818T000000Z"
    assert plan["style_pack"] == "documentary"
    assert plan["bind_playbook"] == "documentary_restraint"
    assert len(plan["segments"]) == 1
    assert plan["segments"][0]["shots"]
    assert plan["params"]["transitions"] == ["cut"]


def test_rebuild_edits_overwrites_advisor_and_drops_negative_gap():
    plan = build_plan(
        source_decl=_decl(20.0),
        style_pack_id="cinematic",
        scene_changes_by_source=[[5.0, 10.0, 15.0]],
        session_id="s1",
    )
    edits = rebuild_edits(plan)
    assert edits
    pack = get_style_pack("cinematic")
    for edit in edits:
        assert edit["transition"] == pack["transitions"][0]
        assert "negative_gap_seconds" not in edit
        assert edit["from_segment"] == "seg_0"
        assert edit["to_segment"] == "seg_0"


def test_multi_clip_sequential_segments():
    plan = build_plan(
        source_decl=_decl(8.0, n=2),
        style_pack_id="beat",
        scene_changes_by_source=[[2.0], [3.0]],
        session_id="s2",
    )
    assert [s["source_index"] for s in plan["segments"]] == [0, 1]
    assert plan["segments"][0]["src_end"] == 8.0
    assert plan["edits"]


def test_speech_forbids_speed_change():
    plan = build_plan(
        source_decl=_decl(10.0, is_speech=True),
        style_pack_id="fresh",
        scene_changes_by_source=[[4.0]],
        session_id="s3",
    )
    nxt = replan(plan, {"seg_0_shot_0": {"speed": 1.2}})
    assert nxt["segments"][0]["shots"][0]["speed"] == 1.0


def test_non_speech_speed_clamped():
    plan = build_plan(
        source_decl=_decl(10.0, is_speech=False),
        style_pack_id="fresh",
        scene_changes_by_source=[[4.0]],
        session_id="s4",
    )
    nxt = replan(plan, {"seg_0_shot_0": {"speed": 3.0}})
    assert nxt["segments"][0]["shots"][0]["speed"] == 1.2
    nxt2 = replan(plan, {"seg_0_shot_0": {"speed": 0.1}})
    assert nxt2["segments"][0]["shots"][0]["speed"] == 0.8


def test_replan_drop_rebuilds_edits_idempotent():
    plan = build_plan(
        source_decl=_decl(10.0, n=2),
        style_pack_id="classic",
        scene_changes_by_source=[[], []],
        session_id="s5",
    )
    assert plan["edits"]
    once = replan(plan, {"seg_1": {"action": "drop"}})
    twice = replan(once, {"seg_1": {"action": "drop"}})
    assert plan_differs(plan, once)
    assert not plan_differs(once, twice)
    assert all(e["from_segment"] != "seg_1" and e["to_segment"] != "seg_1" for e in once["edits"])
    assert once["segments"][1]["action"] == "drop"


def test_target_duration_drops_trailing_shots():
    plan = build_plan(
        source_decl=_decl(30.0),
        style_pack_id="beat",
        scene_changes_by_source=[[2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28]],
        session_id="s6",
        target_duration=3.0,
    )
    total = 0.0
    for seg in plan["segments"]:
        for shot in seg["shots"]:
            total += shot["end"] - shot["start"]
    assert total <= 3.2


def test_hash_and_integrity(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"hello-auto-edit")
    b.write_bytes(b"hello-auto-edit")
    ha = hash_file(a)
    assert ha == hash_file(b)
    files = [{"path": str(a), "sha256": ha}, {"path": str(b), "sha256": ha}]
    assert verify_integrity(files)["unchanged"] is True
    b.write_bytes(b"changed")
    result = verify_integrity(files)
    assert result["unchanged"] is False
    assert result["files"][1]["match"] is False


def test_assert_output_inside(tmp_path):
    ensure_dirs(tmp_path)
    ok = tmp_path / "auto_edit" / "final.mp4"
    ok.write_bytes(b"x")
    assert assert_output_inside(tmp_path, ok) == ok.resolve()
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")
    try:
        assert_output_inside(tmp_path, outside)
        assert False, "应拒绝项目外路径"
    except ValueError as exc:
        assert "auto_edit" in str(exc)


def test_ensure_project_meta_in_place(tmp_path):
    root = tmp_path / "inplace-cut"
    ensure_project_meta(root)
    meta = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert meta["project_id"] == "inplace-cut"
    ensure_project_meta(root)  # 幂等不覆盖
    meta2 = json.loads((root / "project.json").read_text(encoding="utf-8"))
    assert meta2 == meta


def test_unknown_style_pack():
    try:
        build_plan(
            source_decl=_decl(),
            style_pack_id="nope",
            scene_changes_by_source=[[]],
        )
        assert False, "未知风格应失败"
    except ValueError as exc:
        assert "风格包" in str(exc)


def test_replan_appends_decision_log(tmp_path):
    from montage.tools.auto_edit import AutoEdit, ensure_dirs, ensure_project_meta

    proj = tmp_path / "audit"
    ensure_project_meta(proj)
    auto, _tmp = ensure_dirs(proj)
    plan = build_plan(
        source_decl=_decl(10.0, n=2),
        style_pack_id="documentary",
        scene_changes_by_source=[[], []],
        session_id="sess-audit",
    )
    (auto / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    tool = AutoEdit()
    first = tool.execute({
        "operation": "replan", "project_dir": str(proj),
        "overrides": {"seg_1": {"action": "drop"}},
    })
    assert first.success and first.data["changed"] is True
    second = tool.execute({
        "operation": "replan", "project_dir": str(proj),
        "overrides": {"seg_1": {"action": "drop"}},
    })
    assert second.success and second.data["changed"] is False
    lines = (proj / "decisions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    row = json.loads(lines[-1])
    assert row["category"] == "auto_edit"
    assert "sess-audit" in row["subject"]
    assert row["revised"] is True
    costs = (proj / "cost.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert costs  # estimate + settle 均为 0
    tool = AutoEdit()
    assert tool.name == "auto_edit"
    assert tool.capability == "auto_edit"
    assert tool.provider == "openmontage"
    assert tool.estimate_cost({}) == 0.0


def test_discover_finds_auto_edit():
    from montage.registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    assert reg.get("auto_edit") is AutoEdit


def test_mutex_inputs_on_tool(tmp_path):
    tool = AutoEdit()
    result = tool.execute({
        "operation": "plan",
        "project_dir": str(tmp_path / "p"),
        "video": "a.mp4",
        "clips": ["b.mp4"],
    })
    assert not result.success
    assert "三选一" in (result.error or "")


def test_render_plan_mocked(tmp_path, monkeypatch):
    from montage.tools import auto_edit as ae

    src = tmp_path / "src.mp4"
    src.write_bytes(b"source-bytes")
    sha = hash_file(src)
    plan = build_plan(
        source_decl={
            "kind": "video", "has_bgm": False, "is_speech": False, "language": "zh",
            "files": [{"path": str(src), "sha256": sha, "duration": 8.0}],
        },
        style_pack_id="beat",
        scene_changes_by_source=[[4.0]],
        session_id="mock1",
        pack_overrides={"lut": ""},
    )
    proj = tmp_path / "proj"

    def fake_trim(src_path, dst, start, duration):
        dst.write_bytes(b"clip")
        return dst

    def fake_concat(clips, output, **kwargs):
        output.write_bytes(b"joined")
        return output

    def fake_stitch(clips, transitions, output, **kwargs):
        output.write_bytes(b"stitched")
        return output

    def fake_profile(src_path, name, output):
        output.write_bytes(b"profiled")
        return output

    from montage.compose import ffmpeg_engine as fe
    from montage.tools import system_probe as sp

    monkeypatch.setattr(ae, "ffprobe", lambda p: {"format": {"duration": "6.0"}})
    monkeypatch.setattr(fe, "trim_clip", fake_trim)
    monkeypatch.setattr(fe, "concat_videos", fake_concat)
    monkeypatch.setattr(fe, "stitch_with_transitions", fake_stitch)
    monkeypatch.setattr(fe, "apply_profile", fake_profile)
    monkeypatch.setattr(fe, "apply_lut", lambda *a, **k: a[2])
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": "6.0"}})
    monkeypatch.setattr(sp, "build_hardware_profile", lambda: {"encode": {"crf": 18, "preset": "medium"}})
    monkeypatch.setattr(ae, "resolve_lut_path", lambda lut_id: None)

    report = ae.render_plan(proj, plan, preview=False, resume=False)
    assert not report.get("failed_step"), report.get("error")
    final = proj / "auto_edit" / "final.mp4"
    assert final.exists()
    assert report["output_path"] == str(final)
    assert report["source_integrity"]["unchanged"] is True
    assert (proj / "auto_edit" / "plan.json").exists()
    assert (proj / "auto_edit" / "report.json").exists()
