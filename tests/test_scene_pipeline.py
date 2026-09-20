"""scene_pipeline 测试（V41 P0-2：语义聚合层）。

关键回归：
- **LLM 只回索引**：模型吐的时间码一律丢弃，单元时间戳只从确定性切点推导。
- 零密钥可用：无 DASHSCOPE_API_KEY → deterministic 分组，不抛不挡。
- 越界/非 JSON/分组不完整 → 降级不整体失败。
- 指纹缺失只降权，不把相邻镜误判成边界。
"""

from __future__ import annotations

import json

from montage.tools.auto_edit import shots_from_cuts
from montage.tools.scene_pipeline import (
    NARRATIVE_ROLES,
    ScenePipeline,
    adjacency_similarity,
    attach_transcript,
    build_scene_index,
    content_tokens,
    deterministic_groups,
    groups_from_boundaries,
    load_scene_index,
    merge_boundaries,
    narrative_cuts_for_source,
    narrative_roles,
    normalize_transcript,
    parse_boundaries,
    scene_cuts_by_source,
    shot_hints,
    text_similarity,
    vlm_boundary_windows,
)


def _shots(spans, texts=None, fps_texts=None):
    """spans=[[start,end],...] → shot dict（与 auto_edit.shots_from_cuts 同形）。"""
    out = []
    for i, (start, end) in enumerate(spans):
        row = {"shot_id": f"src_shot_{i}", "start": start, "end": end, "speed": 1.0}
        if texts is not None:
            row["text"] = texts[i]
        out.append(row)
    return out


def _chat(content):
    return {"choices": [{"message": {"content": content}}]}


# ---------------------------------------------------------------------------
# 转写归一 + 落到镜
# ---------------------------------------------------------------------------


def test_normalize_transcript_accepts_both_shapes():
    nested = {"output": {}, "sentences": [{"text": "甲", "start_seconds": 1.0, "end_seconds": 2.0}]}
    flat = [{"text": "甲", "start": 1.0, "end": 2.0}]
    assert normalize_transcript(nested) == normalize_transcript(flat)
    assert normalize_transcript(nested)[0]["text"] == "甲"


def test_normalize_transcript_sorts_and_keeps_words():
    raw = [
        {"text": "后", "start_seconds": 5.0, "end_seconds": 6.0},
        {"text": "前", "start_seconds": 1.0, "end_seconds": 2.0,
         "words": [{"text": "前", "start_seconds": 1.0, "end_seconds": 2.0}]},
    ]
    rows = normalize_transcript(raw)
    assert [r["text"] for r in rows] == ["前", "后"]
    assert rows[0]["words"][0]["text"] == "前"


def test_normalize_transcript_drops_empty():
    assert normalize_transcript({"sentences": [{"text": "  "}, "junk", None]}) == []


def test_normalize_transcript_accepts_dashscope_root_data():
    assert normalize_transcript({"sentences": [{"text": "甲", "begin_time": 0}]})[0]["text"] == "甲"


def test_attach_transcript_uses_time_overlap_in_order():
    shots = _shots([[0.0, 2.0], [2.0, 4.0]])
    sents = normalize_transcript([
        {"text": "B", "start_seconds": 2.1, "end_seconds": 3.5},
        {"text": "A", "start_seconds": 0.2, "end_seconds": 1.0},
    ])
    out = attach_transcript(shots, sents)
    assert out[0]["text"] == "A"          # 按时间序拼
    assert out[1]["text"] == "B"
    assert shots[0].get("text") is None    # 原列表不被就地改动


def test_attach_transcript_sentence_straddling_cut_goes_to_one_shot():
    """一句跨切点只归重叠最多的那镜，否则同句进两个单元会把边界相似度虚高。"""
    shots = _shots([[0.0, 2.0], [2.0, 4.0]])
    sents = normalize_transcript([{"text": "跨句", "start_seconds": 1.5, "end_seconds": 3.0}])
    out = attach_transcript(shots, sents)
    assert [s["text"] for s in out] == ["", "跨句"]


def test_attach_transcript_multiple_sentences_in_one_shot_keep_order():
    shots = _shots([[0.0, 4.0]])
    sents = normalize_transcript([
        {"text": "后", "start_seconds": 2.0, "end_seconds": 3.0},
        {"text": "前", "start_seconds": 0.5, "end_seconds": 1.0},
    ])
    assert attach_transcript(shots, sents)[0]["text"] == "前后"


def test_attach_transcript_no_sentences_is_passthrough():
    shots = _shots([[0.0, 1.0]], texts=["原"])
    assert attach_transcript(shots, [])[0]["text"] == "原"


# ---------------------------------------------------------------------------
# 相似度
# ---------------------------------------------------------------------------


def test_content_tokens_cjk_bigram_and_ascii_words():
    tokens = content_tokens("雨夜 rain 3")
    assert "雨夜" in tokens and "rain" in tokens


def test_text_similarity_no_evidence_is_zero():
    """空台词不是中性证据：给 0 才不会被当成「相似」把边界黏住。"""
    assert text_similarity("", "") == 0.0
    assert text_similarity("甲", "") == 0.0


def test_text_similarity_of_identical_text_is_one():
    assert text_similarity("雨还在下", "雨还在下") == 1.0


def test_adjacency_llm_different_lines_are_not_boundary_evidence():
    """普通连续台词的 2-gram 重叠≈0，不能因此断单元（非对称判定的由来）。"""
    shots = _shots([[0.0, 2.0], [2.0, 4.0]], texts=["你来了", "我等你很久了"])
    row = adjacency_similarity(shots[0], shots[1])
    assert row["parts"]["text"] == 0.0
    assert row["boundary"] is False


def test_adjacency_visual_change_without_text_hold_is_boundary():
    shots = _shots([[0.0, 2.0], [2.0, 4.0]], texts=["你来了", "我等你很久了"])
    shots[0]["fingerprint"] = "00" * 192
    shots[1]["fingerprint"] = "ff" * 192
    row = adjacency_similarity(shots[0], shots[1])
    assert row["visual_change"] is True and row["boundary"] is True


def test_adjacency_text_hold_suppresses_mild_visual_change():
    """台词高度重合（同一句反复/一段话跨镜）能黏住同场戏。"""
    shots = _shots([[0.0, 2.0], [2.0, 4.0]], texts=["雨还在下", "雨还在下"])
    shots[0]["fingerprint"] = "00" * 192
    shots[1]["fingerprint"] = ("55" * 192)  # RGB 各差 85/255 → sim 0.667 > visual_cut… 
    row = adjacency_similarity(shots[0], shots[1], visual_cut=0.8)
    assert row["visual_change"] is True
    assert row["text_hold"] is True
    assert row["boundary"] is False


def test_adjacency_same_scene_is_not_boundary():
    shots = _shots([[0.0, 2.0], [2.0, 4.0]], texts=["雨还在下", "雨还在下"])
    shots[0]["fingerprint"] = "00" * 192
    shots[1]["fingerprint"] = "11" * 192
    row = adjacency_similarity(shots[0], shots[1])
    assert row["visual_change"] is False and row["boundary"] is False


def test_adjacency_missing_fingerprint_never_cuts():
    """无视觉证据时不猜（缺指纹只留 warning，不乱切）。"""
    shots = _shots([[0.0, 2.0], [2.0, 4.0]], texts=["甲", "乙"])
    row = adjacency_similarity(shots[0], shots[1])
    assert "visual" not in row["parts"]
    assert row["boundary"] is False
    assert row["warnings"]


def test_adjacency_long_gap_is_hard_boundary():
    """同台词但隔 30 秒（静默/插入素材）必须硬断，不能被台词连读救回来。"""
    shots = _shots([[0.0, 2.0], [30.0, 32.0]], texts=["雨还在下", "雨还在下"])
    shots[0]["fingerprint"] = "00" * 192
    shots[1]["fingerprint"] = "00" * 192
    row = adjacency_similarity(shots[0], shots[1])
    assert row["parts"]["gap_seconds"] == 28.0
    assert row["hard_boundary"] is True and row["boundary"] is True
    assert deterministic_groups(shots)["groups"] == [[0], [1]]


def test_adjacency_short_gap_is_not_hard():
    shots = _shots([[0.0, 2.0], [2.5, 4.0]], texts=["雨还在下", "雨还在下"])
    assert adjacency_similarity(shots[0], shots[1])["hard_boundary"] is False


def test_deterministic_groups_splits_on_visual_change():
    shots = _shots([[0.0, 2.0], [2.0, 4.0], [4.0, 6.0]], texts=["甲甲", "甲甲", "乙乙"])
    shots[0]["fingerprint"] = "00" * 192
    shots[1]["fingerprint"] = "00" * 192
    shots[2]["fingerprint"] = "ff" * 192
    out = deterministic_groups(shots)
    assert out["groups"] == [[0, 1], [2]]
    assert [r["boundary"] for r in out["similarities"]] == [False, True]


def test_deterministic_groups_handles_empty():
    assert deterministic_groups([]) == {"groups": [], "similarities": []}


# ---------------------------------------------------------------------------
# LLM 只回索引
# ---------------------------------------------------------------------------


def test_parse_boundaries_reads_indices_with_offset():
    parsed = parse_boundaries(_chat('{"boundaries":[3,7]}'), shot_count=10, offset=20)
    assert parsed["boundaries"] == [23, 27]


def test_parse_boundaries_discards_zero_and_out_of_range():
    parsed = parse_boundaries(_chat('{"boundaries":[0,1,99]}'), shot_count=5)
    assert parsed["boundaries"] == [1]
    assert any("越界" in w for w in parsed["warnings"])


def test_parse_boundaries_drops_timestamps_and_flags_them():
    """时间码是漂移之源：解析层丢弃且必须留痕（审计可见）。"""
    raw = _chat('{"boundaries":[2],"boundaries_seconds":[12.5],"units":[{"start":12.5,"end":20}]}')
    parsed = parse_boundaries(raw, shot_count=6)
    assert parsed["boundaries"] == [2]
    assert "boundaries_seconds" in parsed["time_leaks"]
    assert "units[].start/end" in parsed["time_leaks"]


def test_parse_boundaries_detects_textual_timecodes():
    parsed = parse_boundaries(_chat('{"boundaries":[2],"note":"第 12.5 秒换场"}'), shot_count=6)
    assert any(leak.startswith("text:") for leak in parsed["time_leaks"])


def test_parse_boundaries_non_json_degrades_with_warning():
    parsed = parse_boundaries(_chat("我不知道"), shot_count=4)
    assert parsed["boundaries"] == []
    assert parsed["warnings"]


def test_parse_boundaries_extracts_fenced_json():
    parsed = parse_boundaries(_chat('说明\n```json\n{"boundaries":[1]}\n```'), shot_count=4)
    assert parsed["boundaries"] == [1]


def test_parse_boundaries_maps_summaries_and_characters_by_offset():
    raw = _chat('{"boundaries":[1],"summaries":{"0":"夜巷","1":"天台"},"characters":{"1":["c1"]}}')
    parsed = parse_boundaries(raw, shot_count=4, offset=10)
    assert parsed["summaries"] == {10: "夜巷", 11: "天台"}
    assert parsed["characters"] == {11: ["c1"]}


# ---------------------------------------------------------------------------
# 索引构建：时间戳只从切点推导
# ---------------------------------------------------------------------------


def test_narrative_roles_hook_reveal_landing():
    assert narrative_roles(1) == ["hook"]
    assert narrative_roles(2) == ["hook", "landing"]
    roles = narrative_roles(5)
    assert roles[0] == "hook" and roles[-1] == "landing" and "reveal" in roles
    assert set(roles) <= set(NARRATIVE_ROLES)


def test_build_scene_index_timestamps_come_from_shots_only():
    shots = _shots([[0.0, 2.0], [2.0, 4.0], [10.0, 12.0]], texts=["甲", "甲", "乙"])
    index = build_scene_index(
        shots=shots, groups=[[0, 1], [2]], grouping="deterministic",
        summaries={2: "换场"}, characters={2: ["c1"]},
    )
    assert [u["start_seconds"] for u in index["units"]] == [0.0, 10.0]
    assert [u["end_seconds"] for u in index["units"]] == [4.0, 12.0]
    assert index["units"][1]["summary"] == "换场"
    assert index["units"][1]["characters"] == ["c1"]
    # 首单元不产生切点（片头不用切）
    assert [c["at_seconds"] for c in index["preferred_cuts"]] == [10.0]


def test_build_scene_index_assigns_every_shot_a_unit():
    shots = _shots([[float(i), float(i) + 1] for i in range(6)])
    index = build_scene_index(shots=shots, groups=[[0, 1], [2], [3, 4, 5]], grouping="vlm")
    assert {s["unit_id"] for s in index["shots"]} == {"u01", "u02", "u03"}
    assert [s["index"] for s in index["shots"]] == list(range(6))


def test_scene_index_passes_own_schema():
    from montage.engine.artifacts import ArtifactStore
    from montage.schemas import get_schema

    shots = _shots([[0.0, 2.0], [5.0, 7.0]], texts=["甲", "乙"])
    index = build_scene_index(shots=shots, groups=[[0], [1]], grouping="deterministic")
    assert ArtifactStore.validate(index, get_schema("scene_index")) == []


def test_shot_hints_marks_unit_start_and_cuts():
    shots = _shots([[0.0, 2.0], [2.0, 4.0], [10.0, 12.0]])
    index = build_scene_index(shots=shots, groups=[[0, 1], [2]], grouping="deterministic")
    hints = shot_hints(index)
    assert hints["unit_count"] == 2
    assert hints["shot_hints"]["src_shot_0"]["unit_start"] is True
    assert hints["shot_hints"]["src_shot_1"]["unit_start"] is False
    assert hints["shot_hints"]["src_shot_2"]["narrative_role"] == "landing"
    assert hints["preferred_cuts"][0]["shot_index"] == 2


def test_shot_hints_tolerates_none():
    assert shot_hints(None)["unit_count"] == 0


# ---------------------------------------------------------------------------
# 与通路 B（auto_edit）对接：叙事切点并进断镜
# ---------------------------------------------------------------------------


def test_narrative_cuts_for_source_requires_matching_path():
    shots = _shots([[0.0, 2.0], [2.0, 4.0], [10.0, 12.0]])
    index = build_scene_index(shots=shots, groups=[[0, 1], [2]], grouping="deterministic",
                              source={"kind": "cuts", "path": "/media/a.mp4"})
    assert narrative_cuts_for_source(index, "/media/a.mp4") == [10.0]
    assert narrative_cuts_for_source(index, "/media/b.mp4") == []


def test_narrative_cuts_for_source_handles_missing_source():
    assert narrative_cuts_for_source(None, "/media/a.mp4") == []
    assert narrative_cuts_for_source({"units": []}, "") == []


def test_scene_cuts_by_source_is_aligned_and_empty_for_unmatched():
    index = {"source": {"path": "/media/a.mp4"},
             "preferred_cuts": [{"at_seconds": 10.0}, {"at_seconds": 20.0}]}
    assert scene_cuts_by_source(index, ["/media/a.mp4", "/media/b.mp4"]) == [[10.0, 20.0], []]


def test_load_scene_index_reads_project_artifact(tmp_path):
    project = tmp_path / "proj"
    (project / "artifacts").mkdir(parents=True)
    (project / "artifacts" / "scene_index.json").write_text('{"units": []}', encoding="utf-8")
    assert load_scene_index(project)["units"] == []
    assert load_scene_index(project, tmp_path / "nope.json") is None
    assert load_scene_index(tmp_path / "empty") is None


def test_auto_edit_plan_merges_narrative_cuts(tmp_path, monkeypatch):
    """草稿的断镜点必须包含情节单元边界（P0-2 的落地价值）。"""
    from montage.tools.auto_edit import AutoEdit

    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    project = tmp_path / "proj"
    monkeypatch.setattr(
        "montage.tools.auto_edit.probe_sources",
        lambda paths, **kw: {"kind": kw.get("kind"), "is_speech": False,
                             "files": [{"path": str(media), "duration": 20.0}]},
    )
    monkeypatch.setattr("montage.tools.auto_edit.analyze_scene_changes", lambda path, threshold=0.3: [5.0])

    index = {
        "source": {"kind": "cuts", "path": str(media)},
        "grouping": "deterministic",
        "units": [{"unit_id": "u01"}, {"unit_id": "u02"}],
        "preferred_cuts": [{"at_seconds": 12.0, "shot_index": 1, "unit_id": "u02"}],
    }
    result = AutoEdit().execute({
        "operation": "plan", "project_dir": str(project), "video": str(media),
        "style": "documentary", "scene_index": index,
    })
    assert result.success, result.error
    note = result.data["scene_index"]
    assert note["used"] is True and note["narrative_cuts"] == [1]
    starts = [s["start"] for seg in result.data["plan"]["segments"] for s in seg["shots"]]
    assert 12.0 in starts and 5.0 in starts


def test_auto_edit_plan_ignores_unmatched_scene_index(tmp_path, monkeypatch):
    from montage.tools.auto_edit import AutoEdit

    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    monkeypatch.setattr(
        "montage.tools.auto_edit.probe_sources",
        lambda paths, **kw: {"kind": kw.get("kind"), "is_speech": False,
                             "files": [{"path": str(media), "duration": 20.0}]},
    )
    monkeypatch.setattr("montage.tools.auto_edit.analyze_scene_changes", lambda path, threshold=0.3: [5.0])
    result = AutoEdit().execute({
        "operation": "plan", "project_dir": str(tmp_path / "proj"), "video": str(media),
        "scene_index": {"source": {"path": str(tmp_path / "other.mp4")},
                        "preferred_cuts": [{"at_seconds": 12.0}]},
    })
    assert result.success, result.error
    assert result.data["scene_index"]["used"] is False
    starts = [s["start"] for seg in result.data["plan"]["segments"] for s in seg["shots"]]
    assert 12.0 not in starts


def test_auto_edit_plan_reports_cuts_dropped_by_pacing(tmp_path, monkeypatch):
    """叙事切点被风格包 min_hold 吃掉时必须显式回报，不能装作成功。"""
    from montage.tools.auto_edit import AutoEdit

    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    monkeypatch.setattr(
        "montage.tools.auto_edit.probe_sources",
        lambda paths, **kw: {"kind": kw.get("kind"), "is_speech": False,
                             "files": [{"path": str(media), "duration": 20.0}]},
    )
    monkeypatch.setattr("montage.tools.auto_edit.analyze_scene_changes", lambda path, threshold=0.3: [])
    result = AutoEdit().execute({
        "operation": "plan", "project_dir": str(tmp_path / "proj"), "video": str(media),
        "style": "documentary",  # min_hold=4.0
        "scene_index": {"source": {"path": str(media)}, "preferred_cuts": [{"at_seconds": 1.0}]},
    })
    assert result.success, result.error
    note = result.data["scene_index"]
    assert note["applied"] == [0] and note["dropped"] == 1
    assert "min_hold" in note["note"]


def test_auto_edit_plan_reports_cuts_applied(tmp_path, monkeypatch):
    from montage.tools.auto_edit import AutoEdit

    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    monkeypatch.setattr(
        "montage.tools.auto_edit.probe_sources",
        lambda paths, **kw: {"kind": kw.get("kind"), "is_speech": False,
                             "files": [{"path": str(media), "duration": 20.0}]},
    )
    monkeypatch.setattr("montage.tools.auto_edit.analyze_scene_changes", lambda path, threshold=0.3: [])
    result = AutoEdit().execute({
        "operation": "plan", "project_dir": str(tmp_path / "proj"), "video": str(media),
        "style": "fresh",  # min_hold=1.5
        "scene_index": {"source": {"path": str(media)},
                        "preferred_cuts": [{"at_seconds": 5.0}, {"at_seconds": 11.0}]},
    })
    assert result.success, result.error
    note = result.data["scene_index"]
    assert note["applied"] == [2] and "dropped" not in note


def test_auto_edit_plan_can_disable_scene_index(tmp_path, monkeypatch):
    from montage.tools.auto_edit import AutoEdit

    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    monkeypatch.setattr(
        "montage.tools.auto_edit.probe_sources",
        lambda paths, **kw: {"kind": kw.get("kind"), "is_speech": False,
                             "files": [{"path": str(media), "duration": 20.0}]},
    )
    monkeypatch.setattr("montage.tools.auto_edit.analyze_scene_changes", lambda path, threshold=0.3: [5.0])
    result = AutoEdit().execute({
        "operation": "plan", "project_dir": str(tmp_path / "proj"), "video": str(media),
        "use_scene_index": False,
        "scene_index": {"source": {"path": str(media)}, "preferred_cuts": [{"at_seconds": 12.0}]},
    })
    assert result.success, result.error
    assert result.data["scene_index"] == {"used": False, "reason": "已显式关闭"}


# ---------------------------------------------------------------------------
# 边界合并
# ---------------------------------------------------------------------------


def test_groups_from_boundaries_is_contiguous_cover():
    assert groups_from_boundaries([2, 5], 6) == [[0, 1], [2, 3, 4], [5]]
    assert groups_from_boundaries([], 3) == [[0, 1, 2]]
    assert groups_from_boundaries([0, 9], 3) == [[0, 1, 2]]  # 0 与越界都忽略


def test_merge_boundaries_unions_and_dedupes():
    assert merge_boundaries([2, 5], [5, 7], 9) == [2, 5, 7]


# ---------------------------------------------------------------------------
# VLM 分组：零密钥回落
# ---------------------------------------------------------------------------


def test_vlm_boundary_skips_without_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    shots = _shots([[0.0, 2.0], [2.0, 4.0]], texts=["甲", "乙"])
    out = vlm_boundary_windows(shots, media_path="x.mp4")
    assert out["skipped"] is True and out["boundaries"] == []


def test_vlm_boundary_skips_without_media_path(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    out = vlm_boundary_windows(_shots([[0.0, 2.0]]), media_path="")
    assert out["skipped"] is True


def test_vlm_boundary_windows_windows_and_offsets(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    shots = _shots([[float(i), float(i) + 1] for i in range(5)],
                   texts=["甲", "甲", "乙", "乙", "丙"])

    def sender(url, payload, **kwargs):
        assert url.endswith("/compatible-mode/v1/chat/completions")
        return _chat('{"boundaries":[1]}')

    seen = []

    out = vlm_boundary_windows(
        shots, media_path="x.mp4", window=3, sender=sender,
        encode_fn=lambda p: "data:image/jpeg;base64,x",
        extract_fn=lambda src, at: seen.append(at) or "f.jpg",
    )
    assert out["skipped"] is False
    # 窗口 0=[0,1,2] → 1；窗口 1=[3,4] → 3+1=4（偏移必须加上）
    assert out["boundaries"] == [1, 4]
    assert seen == [0.5, 1.5, 2.5, 3.5, 4.5]  # 每镜抽中点帧


def test_vlm_boundary_single_shot_tail_window_skipped(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    calls = []

    def sender(url, payload, **kwargs):
        calls.append(payload)
        return _chat('{"boundaries":[1]}')

    out = vlm_boundary_windows(
        _shots([[0.0, 1.0], [1.0, 2.0], [2.0, 3.0]]), media_path="x.mp4", window=2, sender=sender,
        encode_fn=lambda p: "d", extract_fn=lambda src, at: "f.jpg",
    )
    assert len(calls) == 1            # 窗口 0=[0,1] 发出；尾窗 [2] 不足 2 镜跳过
    assert out["boundaries"] == [1]


def test_vlm_boundary_window_failure_degrades_not_raises(monkeypatch):
    from montage.providers.http import HttpError

    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")

    def sender(url, payload, **kwargs):
        raise HttpError(500, url, "boom")

    shots = _shots([[float(i), float(i) + 1] for i in range(4)])
    out = vlm_boundary_windows(
        shots, media_path="x.mp4", window=2, sender=sender,
        encode_fn=lambda p: "d", extract_fn=lambda src, at: "f.jpg",
    )
    assert out["boundaries"] == [] and out["warnings"]


# ---------------------------------------------------------------------------
# 工具层
# ---------------------------------------------------------------------------


def test_execute_writes_scene_index_from_precomputed_cuts(tmp_path, monkeypatch):
    monkeypatch.setattr("montage.tools.scene_pipeline.probe", lambda p: {"format": {"duration": "12.0"}})
    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    project = tmp_path / "proj"

    result = ScenePipeline().execute({
        "path": str(media),
        "project_dir": str(project),
        "cuts": [4.0, 8.0],
        "skip_fingerprints": True,
        "transcript": {"sentences": [{"text": "甲", "start_seconds": 0, "end_seconds": 4}]},
        "min_hold": 1.0,
        "max_hold": 8.0,
    })
    assert result.success, result.error
    data = result.data
    assert data["shot_count"] == 3 and data["unit_count"] >= 1
    written = json.loads((project / "artifacts" / "scene_index.json").read_text(encoding="utf-8"))
    assert written["grouping"] == "deterministic"
    assert written["source"]["cut_count"] == 2
    assert len(written["shots"]) == data["shot_count"]


def test_execute_rejects_missing_input(tmp_path):
    result = ScenePipeline().execute({"path": str(tmp_path / "nope.mp4")})
    assert result.success is False


def test_execute_rejects_bad_duration(tmp_path, monkeypatch):
    monkeypatch.setattr("montage.tools.scene_pipeline.probe", lambda p: {"format": {}})
    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    result = ScenePipeline().execute({"path": str(media), "cuts": []})
    assert result.success is False and "时长" in result.error


def test_execute_falls_back_to_single_shot_when_scene_detect_fails(tmp_path, monkeypatch):
    from montage.toolbase import ToolResult

    monkeypatch.setattr("montage.tools.scene_pipeline.probe", lambda p: {"format": {"duration": "5.0"}})
    monkeypatch.setattr("montage.tools.video_probe.SceneDetect.execute",
                        lambda self, inputs: ToolResult(success=False, error="无 ffmpeg"))
    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    result = ScenePipeline().execute({
        "path": str(media), "skip_fingerprints": True, "min_hold": 1.0, "max_hold": 8.0,
    })
    assert result.success, result.error
    assert result.data["unit_count"] == 1
    assert any("scene_detect" in w for w in (result.data["warnings"] or []))


def test_execute_vlm_grouping_falls_back_without_key(tmp_path, monkeypatch):
    monkeypatch.setattr("montage.tools.scene_pipeline.probe", lambda p: {"format": {"duration": "8.0"}})
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    result = ScenePipeline().execute({
        "path": str(media), "cuts": [4.0], "skip_fingerprints": True,
        "grouping": "vlm", "min_hold": 1.0, "max_hold": 8.0,
    })
    assert result.success, result.error
    assert result.meta["grouping"] == "deterministic"
    assert any("VLM 分组跳过" in w for w in (result.data["warnings"] or []))


def test_execute_precomputed_fingerprints_flow_into_similarity(tmp_path, monkeypatch):
    monkeypatch.setattr("montage.tools.scene_pipeline.probe", lambda p: {"format": {"duration": "4.0"}})
    media = tmp_path / "src.mp4"
    media.write_bytes(b"")
    same = "00" * 64
    result = ScenePipeline().execute({
        "path": str(media), "cuts": [2.0], "min_hold": 1.0, "max_hold": 8.0,
        "fingerprints": {"src_shot_0": same, "src_shot_1": same},
    })
    assert result.success, result.error
    visual = [p for s in result.data["scene_index"]["similarities"] for p in s["parts"] if p == "visual"]
    assert visual


def test_estimate_cost_only_charges_vlm():
    tool = ScenePipeline()
    assert tool.estimate_cost({"grouping": "deterministic"}) == 0.0
    assert tool.estimate_cost({"grouping": "vlm"}) > 0.0


def test_get_status_requires_ffmpeg(monkeypatch):
    from montage.toolbase import ToolStatus

    monkeypatch.setattr("montage.tools.scene_pipeline.check_ffmpeg", lambda: None)
    assert ScenePipeline().get_status() == ToolStatus.UNAVAILABLE
    monkeypatch.setattr("montage.tools.scene_pipeline.check_ffmpeg", lambda: "ffmpeg")
    assert ScenePipeline().get_status() == ToolStatus.AVAILABLE


def test_shots_from_cuts_shared_with_auto_edit():
    """复用而非重建：镜切分必须与通路 B 草稿同源，防两套切分漂移。"""
    shots = shots_from_cuts(10.0, [3.0], min_hold=1.0, max_hold=8.0, segment_id="src")
    assert [s["shot_id"] for s in shots] == ["src_shot_0", "src_shot_1"]
    assert shots[1]["start"] == 3.0
