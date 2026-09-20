import json
from io import StringIO
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from montage.cli import main
from montage.engine.subtitle_timeline import build_regenerated_srt


def _plan():
    return {
        "shots": [
            {
                "shot_id": "s1",
                "duration_seconds": 5.0,
                "subtitle_cues": [{
                    "text": "one",
                    "start_seconds": 0.0,
                    "end_seconds": 5.0,
                }],
            },
            {
                "shot_id": "s2",
                "duration_seconds": 5.0,
                "transition": "fade",
                "transition_duration": 1.0,
                "subtitle_cues": [{
                    "text": "two",
                    "start_seconds": 5.0,
                    "end_seconds": 10.0,
                }],
            },
        ],
    }


def test_build_regenerated_srt_applies_xfade_and_title_offsets():
    rebuilt = build_regenerated_srt(_plan(), title_offset_seconds=2.0)
    assert rebuilt["cue_count"] == 2
    assert rebuilt["projected_duration_seconds"] == 9.0
    assert "00:00:02,000 --> 00:00:07,000" in rebuilt["srt"]
    assert "00:00:06,000 --> 00:00:11,000" in rebuilt["srt"]


def test_build_regenerated_srt_requires_cues():
    with pytest.raises(ValueError, match="has no subtitle cues"):
        build_regenerated_srt({"shots": [{"shot_id": "s1"}]})


def test_srt_rebuild_cli_is_dry_run_by_default_and_keeps_srt(tmp_path):
    # 用 tmp 项目自建夹具（仓库卫生守卫禁止测试读仓库 projects/）。
    root = tmp_path / "proj"
    (root / "artifacts").mkdir(parents=True)
    (root / "renders").mkdir(parents=True)
    (root / "artifacts" / "compose_plan.json").write_text(
        json.dumps(_plan(), ensure_ascii=False), encoding="utf-8",
    )
    output = root / "renders" / "final.srt"
    # 先写一份带漂移的旧 SRT：dry_run 必须**不改**它，但 audit_after 要给出 aligned。
    output.write_text(
        "1\n00:00:09,000 --> 00:00:14,000\none\n", encoding="utf-8",
    )
    original = output.read_text(encoding="utf-8")
    buf = StringIO()
    with redirect_stdout(buf):
        code = main(["srt_rebuild", str(root)])
    assert code == 0
    dry_run = json.loads(buf.getvalue())
    assert dry_run["status"] == "dry_run"
    assert dry_run["write"] is False
    assert dry_run["audit_after"]["status"] == "aligned"
    assert output.read_text(encoding="utf-8") == original
