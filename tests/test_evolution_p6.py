"""进化方案 P6：导演确认卡进看板。零真实调用、不跑全片 produce。"""

from __future__ import annotations

import json
from pathlib import Path

from montage.engine.bible import write_bible
from montage.engine.director import apply_review_fields, write_director_review
from montage.engine.project import init_project
from montage.webui.state import project_detail, review_board

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "montage" / "webui" / "index.html"
SERVER = ROOT / "montage" / "webui" / "server.py"


def test_index_has_collapsible_card_not_one_click_gen():
    html = INDEX.read_text(encoding="utf-8")
    assert "展开全部可改项" in html
    assert "saveReviewFields" in html
    assert "/review" in html
    assert "id=\"review-fields\"" in html
    assert "#review-fields { display:none" in html
    assert "一键 produce" not in html
    assert "一键 GEN" not in html
    assert "确认并出片" not in html
    src = SERVER.read_text(encoding="utf-8")
    apply = src.split("def _apply_review", 1)[1].split("def _dispatch_retry", 1)[0]
    assert "run_produce" not in apply
    assert '@app.patch("/api/projects/{project_id}/review")' in src
    assert "method: 'PATCH'" in html


def test_review_board_none_without_card(tmp_path):
    proj = init_project(tmp_path, "p6-empty", "空", "cinematic")
    assert review_board(proj) is None
    detail = project_detail(tmp_path, "p6-empty")
    assert detail is not None
    assert detail["review"] is None


def test_review_board_strips_interpreter(tmp_path):
    proj = init_project(tmp_path, "p6-argv", "卡", "cinematic")
    art = proj / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    (art / "review_card.json").write_text(
        json.dumps({
            "step": "setup",
            "status": "await_setup",
            "summary": [{"label": "标题", "value": "雨夜"}],
            "fields": [],
            "choices": {},
        }),
        encoding="utf-8",
    )
    (art / "produce_progress.json").write_text(
        json.dumps({
            "status": "await_setup",
            "next": {
                "argv": [r"C:\Python\python.exe", "-m", "montage", "produce", str(proj), "--resume"],
                "note": "确认后再 --resume",
            },
        }),
        encoding="utf-8",
    )
    board = review_board(proj)
    assert board is not None
    assert board["status"] == "await_setup"
    assert board["next"]["argv"][0] == "produce"
    assert "--resume" in board["next"]["argv"]
    assert not str(board["next"]["argv"][0]).endswith(".exe")
    detail = project_detail(tmp_path, "p6-argv")
    assert detail["review"]["card"]["summary"][0]["value"] == "雨夜"


def test_apply_title_rewrites_bible_and_card(tmp_path):
    proj = tmp_path / "film"
    (proj / "artifacts").mkdir(parents=True)
    write_bible(proj, {"title": "旧标题", "playbook": "cyberpunk_neon", "synopsis": "巷口"})
    write_director_review(proj, "await_setup")
    result = apply_review_fields(proj, [{"path": "bible.title", "value": "新标题"}])
    assert "bible.title" in result["applied"]
    bible = json.loads((proj / "artifacts" / "series_bible.json").read_text(encoding="utf-8"))
    assert bible["title"] == "新标题"
    card = json.loads((proj / "artifacts" / "review_card.json").read_text(encoding="utf-8"))
    titles = [row["value"] for row in card["summary"] if row.get("label") == "标题"]
    assert titles == ["新标题"]


def test_apply_rejects_retry_and_clip_path(tmp_path):
    proj = tmp_path / "film"
    (proj / "artifacts").mkdir(parents=True)
    write_bible(proj, {"title": "雨夜", "playbook": "cyberpunk_neon"})
    write_director_review(proj, "await_setup")
    before = (proj / "artifacts" / "series_bible.json").read_text(encoding="utf-8")
    result = apply_review_fields(proj, [
        {"path": "retry:sh01", "value": "retry"},
        {"path": "edit_decisions.cuts[].clip_path", "value": "x.mp4"},
        {"path": "scene_plan.scenes[].shots[]", "value": "hack"},
        {"path": "bible.pipeline_type", "value": "clip_factory"},
    ])
    assert result["applied"] == []
    assert len(result["skipped"]) == 4
    after = (proj / "artifacts" / "series_bible.json").read_text(encoding="utf-8")
    assert after == before
    assert not (proj / "artifacts" / "edit_decisions.json").exists()


def test_skip_turnaround_writes_character(tmp_path):
    proj = tmp_path / "film"
    (proj / "artifacts").mkdir(parents=True)
    write_bible(proj, {
        "title": "雨夜",
        "playbook": "cyberpunk_neon",
        "characters": [{"id": "a", "name": "阿宁", "appearance": "黑发短寸"}],
    })
    write_director_review(proj, "await_cast")
    result = apply_review_fields(proj, [
        {"path": "bible.characters[a].skip_turnaround", "value": "skip"},
    ])
    assert "bible.characters[a].skip_turnaround" in result["applied"]
    bible = json.loads((proj / "artifacts" / "series_bible.json").read_text(encoding="utf-8"))
    assert bible["characters"][0]["skip_turnaround"] is True


def test_apply_does_not_write_human_approved(tmp_path):
    proj = tmp_path / "film"
    (proj / "artifacts").mkdir(parents=True)
    write_bible(proj, {"title": "雨夜", "playbook": "cyberpunk_neon"})
    write_director_review(proj, "await_setup")
    apply_review_fields(proj, [{"path": "bible.title", "value": "新"}])
    progress = proj / "artifacts" / "produce_progress.json"
    assert not progress.exists() or "human_approved" not in progress.read_text(encoding="utf-8")
    bible = json.loads((proj / "artifacts" / "series_bible.json").read_text(encoding="utf-8"))
    assert "human_approved" not in bible


def test_apply_character_appearance_and_gold_lines(tmp_path):
    proj = tmp_path / "film"
    (proj / "artifacts").mkdir(parents=True)
    write_bible(proj, {
        "title": "雨夜",
        "playbook": "cyberpunk_neon",
        "characters": [{"id": "a", "name": "阿宁", "appearance": "黑发短寸"}],
        "locations": [{"id": "alley", "name": "巷口", "sensory": "积水"}],
        "gold_lines": ["旧句"],
    })
    write_director_review(proj, "await_design")
    result = apply_review_fields(proj, [
        {"path": "bible.characters[a].appearance", "value": "金发短寸"},
        {"path": "bible.locations[alley].sensory", "value": "霓虹积水"},
    ])
    # design 卡有 appearance 字段、没有 sensory —— sensory 属大纲（outline）
    assert "bible.characters[a].appearance" in result["applied"]
    write_director_review(proj, "await_outline")
    result = apply_review_fields(proj, [
        {"path": "bible.gold_lines", "value": "雨还在下。\n你看清楚没有。"},
        {"path": "bible.locations[alley].sensory", "value": "霓虹积水"},
        {"path": "bible.structure.hook", "value": "巷口发现尸体"},
    ])
    assert "bible.gold_lines" in result["applied"]
    assert "bible.locations[alley].sensory" in result["applied"]
    bible = json.loads((proj / "artifacts" / "series_bible.json").read_text(encoding="utf-8"))
    assert bible["characters"][0]["appearance"] == "金发短寸"
    assert bible["locations"][0]["sensory"] == "霓虹积水"
    assert bible["gold_lines"] == ["雨还在下。", "你看清楚没有。"]
    assert bible["structure"]["hook"] == "巷口发现尸体"


def test_apply_shot_language_english_ids(tmp_path):
    proj = tmp_path / "film"
    (proj / "artifacts").mkdir(parents=True)
    write_bible(proj, {"title": "雨夜", "playbook": "cyberpunk_neon"})
    plan = {
        "scenes": [{
            "id": "sc01",
            "title": "巷口",
            "shots": [{
                "shot_id": "sh01",
                "title": "站住",
                "shot_language": {"shot_size": "medium", "camera_movement": "static"},
                "shot_budget_class": "talk",
                "cut": "bridge",
            }],
        }],
    }
    (proj / "artifacts" / "scene_plan.json").write_text(
        json.dumps(plan), encoding="utf-8",
    )
    write_director_review(proj, "await_shots")
    result = apply_review_fields(proj, [
        {"path": "scene_plan.shots[sh01].shot_language.camera_movement", "value": "dolly_in"},
        {"path": "scene_plan.shots[sh01].shot_language.shot_size", "value": "wide"},
        {"path": "scene_plan.shots[sh01].title", "value": "输了委屈"},
        {"path": "scene_plan.scenes[sc01].sound_notes", "value": "雨声"},
    ])
    assert "scene_plan.shots[sh01].shot_language.camera_movement" in result["applied"]
    saved = json.loads((proj / "artifacts" / "scene_plan.json").read_text(encoding="utf-8"))
    shot = saved["scenes"][0]["shots"][0]
    assert shot["shot_language"]["camera_movement"] == "dolly_in"
    assert shot["shot_language"]["shot_size"] == "wide"
    assert shot["title"] == "输了委屈"
    assert saved["scenes"][0]["sound_notes"] == "雨声"

    blocked = apply_review_fields(proj, [
        {"path": "scene_plan.shots[sh01].shot_language.camera_movement", "value": "慢推"},
    ])
    assert blocked["applied"] == []
    assert any("非法运镜" in (row.get("reason") or "") for row in blocked["skipped"])
    after = json.loads((proj / "artifacts" / "scene_plan.json").read_text(encoding="utf-8"))
    assert after["scenes"][0]["shots"][0]["shot_language"]["camera_movement"] == "dolly_in"
