"""W5：Cursor 项目 Skill + doctor JSON + AGENTS 指针。"""

from __future__ import annotations

import argparse
import json
import re
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from montage.engine.policy import skill_paths_for_pipeline
from montage.engine.stages import STAGE_ORDER

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".cursor" / "skills" / "montage-produce" / "SKILL.md"
PRODUCE_MD = "docs/skills/meta/produce.md"
SKILL_REL = ".cursor/skills/montage-produce/SKILL.md"


def _parts(text: str) -> tuple[str, str]:
    chunks = text.split("---", 2)
    assert len(chunks) >= 3, "SKILL.md 需要 YAML frontmatter"
    return chunks[1], chunks[2]


def _section(body: str, heading: str) -> str:
    needle = f"## {heading}"
    start = body.find(needle)
    assert start >= 0, f"缺少 {needle}"
    rest = body[start + len(needle) :]
    nxt = rest.find("\n## ")
    return rest if nxt < 0 else rest[:nxt]


def test_skill_file_frontmatter():
    text = SKILL.read_text(encoding="utf-8")
    fm, _body = _parts(text)
    assert "name: montage-produce" in fm
    assert "description:" in fm
    assert "disable-model-invocation" not in fm


def test_skill_allows_core_commands():
    body = _parts(SKILL.read_text(encoding="utf-8"))[1]
    allow = _section(body, "允许")
    for token in (
        "doctor",
        "produce",
        "--resume",
        "--idea",
        "--retry",
        "--review",
        "init",
    ):
        assert token in allow, token


def test_forbid_section_locks():
    body = _parts(SKILL.read_text(encoding="utf-8"))[1]
    forbid = _section(body, "禁止")
    assert "shot_runner" in forbid
    assert "run produce" in forbid
    assert "cuts" in forbid
    two = _section(body, "两段式")
    assert "立刻" in forbid or "立刻" in two
    assert "await_bible" in two or "await_bible" in forbid
    assert "await_prompt" in two or "await_prompt" in forbid


def test_director_stop_table_in_skill():
    body = _parts(SKILL.read_text(encoding="utf-8"))[1]
    director = _section(body, "导演档（`--review director`，CLI 默认已是 director）")
    for token in (
        "await_setup",
        "await_outline",
        "await_design",
        "await_cast",
        "await_shots",
        "await_frames",
        "await_final_prompt",
        "await_clips",
        "摘要",
        "--resume",
        "human_approved",
    ):
        assert token in director, token
    allow = _section(body, "允许")
    assert "director" in allow


def test_skill_kling_director_order_exception():
    director = _section(
        _parts(SKILL.read_text(encoding="utf-8"))[1],
        "导演档（`--review director`，CLI 默认已是 director）",
    )
    assert "可灵环例外" in director
    assert "video_loop=kling" in director
    blob = director.split("可灵环例外", 1)[1].split("| status", 1)[0]
    assert blob.find("await_shots") < blob.find("await_cast")
    produce = (ROOT / PRODUCE_MD).read_text(encoding="utf-8")
    assert "可灵环例外" in produce
    evo = (ROOT / "docs" / "EVOLUTION_PLAN.md").read_text(encoding="utf-8")
    assert "可灵环例外" in evo


def test_skill_points_at_produce_md():
    body = _parts(SKILL.read_text(encoding="utf-8"))[1]
    assert PRODUCE_MD in body
    assert "../" not in body


def test_skill_paths_prefixed_for_all_pipelines():
    for name in ("cinematic", "documentary", "clip_factory"):
        paths = skill_paths_for_pipeline(name)
        assert paths[0] == PRODUCE_MD, name
        assert paths[1] == SKILL_REL, name
    cine = skill_paths_for_pipeline("cinematic")
    for stage in STAGE_ORDER:
        assert f"docs/skills/pipelines/cinematic/{stage}.md" in cine
    assert "docs/skills/pipelines/documentary.md" in skill_paths_for_pipeline("documentary")
    assert "docs/skills/pipelines/clip_factory.md" in skill_paths_for_pipeline("clip_factory")


def test_doctor_json_stdout_is_one_object():
    from montage.cli import main

    buf = StringIO()
    with redirect_stdout(buf):
        code = main(["doctor", "--json"])
    assert code == 0
    raw = buf.getvalue()
    data = json.loads(raw)
    assert "capabilities" in data
    assert "ffmpeg" in data
    assert "skills" not in data


def test_doctor_json_pipeline_lists_produce_skill():
    from montage.cli import main

    buf = StringIO()
    with redirect_stdout(buf):
        code = main(["doctor", "--json", "--pipeline", "cinematic"])
    assert code == 0
    data = json.loads(buf.getvalue())
    assert data["skills"][0] == PRODUCE_MD
    assert data["skills"][1] == SKILL_REL


def test_doctor_json_omits_secret_values(monkeypatch):
    from montage.cli import main

    monkeypatch.setenv("VOLC_ACCESSKEY", "w5-secret-should-not-leak")
    buf = StringIO()
    with redirect_stdout(buf):
        main(["doctor", "--json"])
    assert "w5-secret-should-not-leak" not in buf.getvalue()


def test_doctor_human_banner_without_json():
    from montage.cli import main

    buf = StringIO()
    with redirect_stdout(buf):
        code = main(["doctor"])
    assert code == 0
    assert buf.getvalue().lstrip().startswith("== 工具能力菜单 ==")


def test_agents_md_is_short_pointer():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "montage-produce" in text
    assert "produce" in text
    nonempty = [ln for ln in text.splitlines() if ln.strip()]
    assert 5 <= len(nonempty) <= 10


def test_skill_produce_flags_match_cli():
    from montage.cli import build_parser

    body = _parts(SKILL.read_text(encoding="utf-8"))[1]
    allow = _section(body, "允许")
    line = next(ln for ln in allow.splitlines() if "produce <dir>" in ln)
    flags = re.findall(r"--[a-z][a-z0-9-]*", line)
    assert flags
    parser = build_parser()
    prod = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            prod = action.choices["produce"]
            break
    assert prod is not None
    known = {opt for act in prod._actions for opt in act.option_strings}
    missing = [f for f in flags if f not in known]
    assert not missing, missing


def test_webui_retry_forces_headless_off():
    src = (ROOT / "montage" / "webui" / "server.py").read_text(encoding="utf-8")
    assert "headless=False" in src
