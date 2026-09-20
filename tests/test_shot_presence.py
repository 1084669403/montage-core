"""B2.5：逐镜在场清单（presence）与承接表（continuity）。"""

from __future__ import annotations

import json
from pathlib import Path

from lib.shot_presence import (
    build_ledger,
    continuity_for,
    derive_presence,
    normalize_presence,
    presence_prompt_lines,
)
from montage.tools.script_to_scene_plan import convert_script_to_scene_plan
from montage.tools.script_validator import check_presence_and_continuity


def _fixture_script() -> dict:
    path = Path(__file__).parent / "fixtures" / "script_complete.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _shot(shot_id: str, **presence) -> dict:
    return {"shot_id": shot_id, "presence": presence}


def test_normalize_presence_keeps_known_fields():
    row = normalize_presence({
        "location": {"id": "loc_hall", "zone": "中央偏左", "light": "烛光自左前方"},
        "characters": [{"id": "huan_niang", "position": "画面中央偏右", "enters": "是"}],
        "props": [{"prop_id": "prop_guqin", "owner": "huan_niang", "position": "身前"}],
    })
    assert row["location"]["id"] == "loc_hall"
    assert row["characters"][0]["zone"] == "画面中央偏右"
    assert row["characters"][0]["enters"] is True
    assert row["props"][0]["holder"] == "huan_niang"


def test_derive_presence_from_visual_details():
    shot = {
        "shot_id": "sc01_01",
        "location_id": "loc_hall",
        "visual_details": {
            "environment": "内殿，夜色，烛火",
            "subjects": [{
                "id": "huan_niang",
                "blocking": {"x": "中", "z": "近"},
                "action": {"verb": "抚琴"},
            }],
            "objects": [{"id": "prop_guqin", "position": "身前"}],
        },
    }
    row = derive_presence(shot)
    assert row["location"]["id"] == "loc_hall"
    assert row["characters"][0]["id"] == "huan_niang"
    assert row["characters"][0]["zone"] == "中近"
    assert row["props"][0]["id"] == "prop_guqin"


def test_continuity_flags_missing_carried_entity():
    prev = normalize_presence({
        "location": {"id": "loc_hall"},
        "characters": [{"id": "huan_niang"}],
        "props": [{"id": "prop_guqin", "holder": "huan_niang"}],
    })
    current = normalize_presence({
        "location": {"id": "loc_hall"},
        "characters": [{"id": "huan_niang"}],
    })
    ledger = continuity_for(prev, current, prev_shot_id="sc01_01")
    kinds = {(row["kind"], row["id"]) for row in ledger["missing"]}
    assert ("prop", "prop_guqin") in kinds
    assert {row["id"] for row in ledger["must_keep"]} == {"huan_niang"}


def test_continuity_exit_marks_change_not_missing():
    prev = normalize_presence({
        "characters": [{"id": "wen_ruyue", "exits": True}],
    })
    current = normalize_presence({"characters": [{"id": "huan_niang", "enters": True}]})
    ledger = continuity_for(prev, current, prev_shot_id="sc02_01")
    assert ledger["missing"] == []
    ids = {row["id"] for row in ledger["changed"]}
    assert {"wen_ruyue", "huan_niang"} <= ids


def test_presence_prompt_lines_render_inventory_and_ledger():
    presence = normalize_presence({
        "location": {"id": "loc_hall", "zone": "中央偏左", "time_of_day": "夜"},
        "characters": [{"id": "huan_niang", "zone": "画面中央偏右", "state": "抱琴而立"}],
        "props": [{"id": "prop_guqin", "holder": "huan_niang", "zone": "身前"}],
    })
    ledger = {"from_shot": "sc01_01", "must_keep": [{"kind": "prop", "id": "prop_guqin"}],
              "changed": [], "missing": []}
    lines = presence_prompt_lines(presence, ledger, names={"huan_niang": "宦娘", "prop_guqin": "古琴"})
    assert lines[0].startswith("【在场清单】")
    assert "宦娘" in lines[0] and "古琴" in lines[0]
    assert lines[1].startswith("【承接】") and "保留：古琴" in lines[1]


def test_ledger_reports_derived_and_continuity_gaps():
    shots = [
        _shot("sc01_01", location={"id": "loc_hall"},
              characters=[{"id": "huan_niang"}],
              props=[{"id": "prop_guqin", "holder": "huan_niang"}]),
        {"shot_id": "sc01_02", "visual_details": {"subjects": [{"id": "huan_niang"}]}},
    ]
    result = build_ledger(shots)
    assert result["shots"]["sc01_01"]["derived"] is False
    assert result["shots"]["sc01_02"]["derived"] is True
    messages = " ".join(row["message"] for row in result["findings"])
    assert "承接缺失" in messages and "prop_guqin" in messages


def test_compile_writes_presence_and_continuity_into_scene_plan():
    out = convert_script_to_scene_plan(_fixture_script())
    shots = [
        shot
        for scene in out["scene_plan"]["scenes"]
        for shot in scene.get("shots") or []
    ]
    assert shots
    assert all(isinstance(shot.get("presence"), dict) for shot in shots)
    assert all("continuity" in shot for shot in shots)
    assert all(shot["presence"]["location"] for shot in shots)


def test_validator_reports_presence_gaps():
    plan = {
        "scenes": [{
            "id": "sc01",
            "shots": [
                {"shot_id": "sc01_01", "presence": {
                    "location": {"id": "loc_hall"},
                    "characters": [{"id": "huan_niang"}],
                    "props": [{"id": "prop_guqin", "holder": "wen_ruyue"}],
                }},
                {"shot_id": "sc01_02", "presence": {"characters": []}},
            ],
        }],
    }
    fields = {row["field"] for row in check_presence_and_continuity(plan)}
    assert "sc01_01.presence.props" in fields      # 持有者不在场
    assert "sc01_02.presence.location" in fields   # 缺场景
    assert "sc01_02.presence" in fields            # 无人物且无空镜理由
    assert "sc01_02.continuity" in fields          # 上镜人物未退场却缺失
