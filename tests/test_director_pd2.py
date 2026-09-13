"""P-D2：切开 await_frames；首帧停点不得写 video clip。"""

from __future__ import annotations

import json
import sys
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_director_pd0 import _cast_tools, _director_bible
from test_produce import _run
from test_shot_runner import _fake_image, _fake_video, _pass_quality

from montage.engine.bible import write_bible
from montage.engine.project import init_project
from montage.toolbase import ToolResult
from montage.tools.shot_runner import ShotRunner


def _walk_to_shots(proj: Path, tools):
    first = _run(proj, tools, idea="雨夜巷口对峙", review="director")
    assert first["progress"]["status"] == "await_setup"
    assert _run(proj, tools, resume=True)["progress"]["status"] == "await_outline"
    assert _run(proj, tools, resume=True)["progress"]["status"] == "await_design"
    assert _run(proj, tools, resume=True)["progress"]["status"] == "await_cast"
    shots = _run(proj, tools, resume=True)
    assert shots["progress"]["status"] == "await_shots", shots["progress"].get("findings")
    return shots


def _patch_hero_and_talk(proj: Path) -> None:
    path = proj / "artifacts" / "scene_plan.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    scene = plan["scenes"][0]
    first = dict(scene["shots"][0])
    first["shot_budget_class"] = "hero"
    first["duration_seconds"] = 2
    first["shot_kind"] = "video"
    talk = dict(first)
    talk["shot_id"] = "sc01_02"
    talk["shot_budget_class"] = "talk"
    talk["duration_seconds"] = 8
    talk["shot_kind"] = "image"
    scene["shots"] = [first, talk]
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")


def test_shots_resume_stops_at_frames_without_video(tmp_path):
    proj = init_project(tmp_path, "d2-frames", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    video_calls: list[dict] = []

    def spy_video(inputs):
        video_calls.append(dict(inputs))
        return _fake_video(inputs)

    tools, order, _bgm = _cast_tools(proj)
    tools["shot_runner"] = ShotRunner(
        image_execute=_fake_image,
        video_execute=spy_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    _walk_to_shots(proj, tools)
    frames = _run(proj, tools, resume=True)
    assert frames["success"], frames.get("error")
    assert frames["progress"]["status"] == "await_frames"
    assert frames["progress"].get("review") == "director"
    assert video_calls == []
    assert list((proj / "assets" / "images").glob("*_first.png"))
    assert not (proj / "assets" / "videos").exists() or not any(
        (proj / "assets" / "videos").glob("*.mp4")
    )
    assert "soundtrack" not in order
    review = (proj / "artifacts" / "REVIEW.md").read_text(encoding="utf-8")
    assert "await_frames" in review
    card = json.loads((proj / "artifacts" / "review_card.json").read_text(encoding="utf-8"))
    assert card["step"] == "frames"
    argv = frames["progress"]["next"]["argv"]
    assert "--resume" in argv
    assert "--idea" not in argv


def test_frames_card_shows_bindings(tmp_path):
    """await_frames 卡片附「形态/场景文字/参考数」绑定列；绑定产物落盘。"""
    proj = init_project(tmp_path, "d2-bind", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    tools, order, _bgm = _cast_tools(proj)
    tools["shot_runner"] = ShotRunner(
        image_execute=_fake_image,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    _walk_to_shots(proj, tools)
    frames = _run(proj, tools, resume=True)
    assert frames["success"], frames.get("error")
    assert frames["progress"]["status"] == "await_frames"
    bindings = json.loads(
        (proj / "artifacts" / "image_bindings.json").read_text(encoding="utf-8")
    )
    assert bindings["shots"], "帧停点后应有逐镜绑定"
    sid = next(iter(bindings["shots"]))
    assert "refs" in bindings["shots"][sid]
    card = json.loads(
        (proj / "artifacts" / "review_card.json").read_text(encoding="utf-8")
    )
    notes = " ".join(str(f.get("note") or "") for f in card.get("fields") or [])
    assert "绑定" in notes
    labels = " ".join(str(s.get("label") or "") for s in card.get("summary") or [])
    assert "绑定" in labels


def test_failed_frames_block_video(tmp_path):
    proj = init_project(tmp_path, "d2-fail", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    tools, order, _bgm = _cast_tools(proj)

    def boom_first(inputs):
        out = str(inputs.get("output_path") or "")
        if "_first" in out.replace("\\", "/"):
            return ToolResult(success=False, error="no key")
        return _fake_image(inputs)

    tools["shot_runner"] = ShotRunner(
        image_execute=boom_first,
        video_execute=_fake_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    _walk_to_shots(proj, tools)
    frames = _run(proj, tools, resume=True)
    assert frames["progress"]["status"] == "await_frames"
    stuck = _run(proj, tools, resume=True)
    assert stuck["progress"]["status"] == "await_frames"
    assert not (proj / "assets" / "videos").exists() or not any(
        (proj / "assets" / "videos").glob("*.mp4")
    )
    assert "soundtrack" not in order


def test_frames_retry_does_not_write_video(tmp_path):
    proj = init_project(tmp_path, "d2-retry", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    video_calls: list[dict] = []

    def spy_video(inputs):
        video_calls.append(dict(inputs))
        return _fake_video(inputs)

    tools, _order, _bgm = _cast_tools(proj)
    tools["shot_runner"] = ShotRunner(
        image_execute=_fake_image,
        video_execute=spy_video,
        image_estimate=lambda i: 0.04,
        video_estimate=lambda i: 0.2,
        quality_check=_pass_quality,
        extract_last_frame=lambda *_a, **_k: None,
    )
    _walk_to_shots(proj, tools)
    frames = _run(proj, tools, resume=True)
    assert frames["progress"]["status"] == "await_frames"
    plan = json.loads((proj / "artifacts" / "scene_plan.json").read_text(encoding="utf-8"))
    sid = plan["scenes"][0]["shots"][0]["shot_id"]
    retried = _run(proj, tools, retry_ids=[sid], resume=True)
    assert retried["progress"]["status"] == "await_frames"
    assert video_calls == []
    assert not (proj / "assets" / "videos").exists() or not any(
        (proj / "assets" / "videos").glob("*.mp4")
    )


def test_frames_resume_stops_at_final_prompt_then_generates(tmp_path):
    proj = init_project(tmp_path, "d2-hero", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    tools, order, _bgm = _cast_tools(proj)
    _walk_to_shots(proj, tools)
    _patch_hero_and_talk(proj)
    frames = _run(proj, tools, resume=True)
    assert frames["progress"]["status"] == "await_frames"
    assert not (proj / "assets" / "videos").exists() or not any(
        (proj / "assets" / "videos").glob("*.mp4")
    )
    final = _run(proj, tools, resume=True)
    assert final["success"], final.get("error")
    assert final["progress"]["status"] == "await_final_prompt"
    assert final["progress"].get("review") == "director"
    assert not (proj / "assets" / "videos").exists() or not any(
        (proj / "assets" / "videos").glob("*.mp4")
    )
    assert (proj / "artifacts" / "shot_prompts.json").is_file()
    review = (proj / "artifacts" / "REVIEW.md").read_text(encoding="utf-8")
    assert "await_final_prompt" in review
    card = json.loads((proj / "artifacts" / "review_card.json").read_text(encoding="utf-8"))
    assert card["step"] == "final_prompt"
    sample = _run(proj, tools, resume=True)
    assert sample["success"], sample.get("error")
    assert sample["progress"]["status"] == "await_clips"
    videos = list((proj / "assets" / "videos").glob("*.mp4")) if (proj / "assets" / "videos").exists() else []
    assert videos
    assert "soundtrack" not in order


def test_cli_prints_await_frames(tmp_path):
    from montage.cli import main

    proj = init_project(tmp_path, "d2-cli", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    tools, _order, _bgm = _cast_tools(proj)
    _walk_to_shots(proj, tools)
    frames = _run(proj, tools, resume=True)
    assert frames["progress"]["status"] == "await_frames"
    buf = StringIO()
    with redirect_stdout(buf):
        code = main(["produce", str(proj)])
    assert code == 0
    out = buf.getvalue()
    assert "await_frames" in out
    assert "produce: ok" not in out


def test_leave_for_generate_clips_gate():
    from montage.engine.director import leave_for_generate

    assert leave_for_generate("await_clips", True, clips_ok=True) is True
    assert leave_for_generate("await_clips", True, clips_ok=False) is False
    assert leave_for_generate("await_clips", True, clips_ok=False, retry=True) is True
    assert leave_for_generate("await_clips", False, clips_ok=True) is False


def test_await_clips_resume_without_clips_stays(tmp_path):
    from montage.engine.artifacts import ArtifactStore
    from montage.engine.bible import write_bible
    from test_bible import _ok_bible
    from test_produce_gen import _gen_bag, _seed_gen

    proj = _seed_gen(tmp_path)
    write_bible(proj, _ok_bible())
    (proj / "artifacts" / "produce_progress.json").write_text(
        json.dumps({
            "version": "1",
            "status": "await_clips",
            "mode": "generate",
            "review": "director",
            "steps": {},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    tools, order, _bgm, _ = _gen_bag(proj)
    result = _run(proj, tools, resume=True)
    assert result["progress"]["status"] == "await_clips"
    assert "soundtrack" not in order
    fields = [str(f.get("field") or "") for f in (result["progress"].get("findings") or [])]
    assert "sh01" in fields


class _StageRecorder:
    """记录 shot_runner 收到的 stage，返回成功的最小 payload（不打 HTTP）。"""

    def __init__(self, *, prompt_shots=None):
        self.stages: list[str] = []
        self._prompt_shots = prompt_shots

    def execute(self, inputs):
        self.stages.append(str(inputs.get("stage") or ""))
        data: dict = {"findings": [], "retryable_ids": []}
        if self._prompt_shots:
            data["shot_prompts"] = {"shots": self._prompt_shots}
        return ToolResult(success=True, data=data)


def _write_packet_loop(proj: Path, *, video_loop: str, frames_mode: str) -> None:
    path = proj / "artifacts" / "proposal_packet.json"
    packet = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    packet["video_loop"] = video_loop
    packet["frames_mode"] = frames_mode
    path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")


def test_agnes_preview_skips_frames(tmp_path):
    """Agnes + preview：跳过 await_frames，直接构建最终提示词停 await_final_prompt。"""
    proj = init_project(tmp_path, "d2-preview", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    tools, _order, _bgm = _cast_tools(proj)
    _walk_to_shots(proj, tools)
    _write_packet_loop(proj, video_loop="agnes", frames_mode="preview")
    recorder = _StageRecorder(prompt_shots=[{"shot_id": "sc01_01"}])
    tools["shot_runner"] = recorder

    result = _run(proj, tools, resume=True)
    assert result["success"], result.get("error")
    assert result["progress"]["status"] == "await_final_prompt"
    assert recorder.stages == ["prompt_preview"]
    card = json.loads((proj / "artifacts" / "review_card.json").read_text(encoding="utf-8"))
    assert card["step"] == "final_prompt"
    videos = proj / "assets" / "videos"
    assert not videos.exists() or not any(videos.glob("*.mp4"))


def test_agnes_reference_first_keeps_frames(tmp_path):
    """Agnes + reference_first：不得跳过 frames（反向锁条件不被写宽）。"""
    proj = init_project(tmp_path, "d2-reffirst", "圣经", "cinematic")
    write_bible(proj, _director_bible())
    tools, _order, _bgm = _cast_tools(proj)
    _walk_to_shots(proj, tools)
    _write_packet_loop(proj, video_loop="agnes", frames_mode="reference_first")
    recorder = _StageRecorder()
    tools["shot_runner"] = recorder

    result = _run(proj, tools, resume=True)
    assert recorder.stages[:1] == ["frames"]
    assert "prompt_preview" not in recorder.stages
    # reference_first 停在 await_frames（进入 frames 门），不越过到 await_final_prompt。
    assert result["progress"]["status"] == "await_frames"
    assert result["progress"]["status"] != "await_final_prompt"
