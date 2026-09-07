"""style_matcher 与 playbook.script_style 分档门禁。"""

import pytest

from montage.engine.artifacts import ArtifactStore
from montage.engine.gates import GateError, validate_completion
from montage.engine.project import init_project
from montage.playbooks import get_playbook, list_playbooks
from montage.pipelines import CINEMATIC
from montage.registry import ToolRegistry
from montage.tools.script_validator import check_completeness
from montage.tools.style_matcher import StyleMatcher, match_styles


def test_all_playbooks_have_script_style():
    ids = {p["id"] for p in list_playbooks()}
    assert len(ids) >= 7
    for pid in ids:
        pb = get_playbook(pid)
        assert pb is not None
        style = pb.get("script_style") or {}
        assert "template_id" in style
        assert "require_characters" in style


def test_style_matcher_aliases():
    hits = match_styles("雨夜追杀 赛博朋克")
    assert hits
    assert hits[0]["playbook"] == "cyberpunk_neon"
    assert hits[0]["style_pack"] == "cyber"
    spoken = match_styles("产品讲解口播")
    assert spoken[0]["playbook"] == "spoken_explain"
    assert spoken[0]["style_pack"] == "spoken"
    anime = match_styles("校园动漫对决")
    assert anime[0]["playbook"] == "anime_shonen"


def test_cascade_infers_and_pins():
    from montage.tools.style_matcher import cascade_styles, StyleMatcher

    inferred = cascade_styles("雨夜巷口对峙")
    assert inferred["pinned_medium"] is False
    assert inferred["chosen"]["medium"] == "film"

    pinned = cascade_styles("做成电影 雨夜追凶")
    assert pinned["pinned_medium"] is True
    assert pinned["chosen"]["medium"] == "film"

    spoken = cascade_styles("讲量子计算")
    assert spoken["chosen"]["medium"] == "spoken"
    assert spoken["chosen"]["playbook"] == "spoken_explain"
    assert spoken["chosen"]["pipeline_type"] == "cinematic"

    mixed = cascade_styles("科幻动作")
    assert "科幻" in mixed["chosen"]["genres"]
    assert "动作" in mixed["chosen"]["genres"]

    tool = StyleMatcher().execute({"query": "讲量子计算", "operation": "cascade"})
    assert tool.success
    assert "format_card" in tool.data
    assert tool.data.get("matches") is None

    reg = ToolRegistry()
    reg.discover()
    assert reg.get("style_matcher") is not None
    result = StyleMatcher().execute({"query": "水墨国风茶道"})
    assert result.success
    assert result.data["matches"][0]["playbook"] == "chinese_elegance"


def test_gate_without_playbook_ignores_completeness(tmp_path):
    proj = init_project(tmp_path, "demo", "演示", "cinematic")
    ArtifactStore(proj).write("script", {"title": "t", "sections": [{"id": "s", "narration": "n"}]})
    result = validate_completion(proj, "script", CINEMATIC)
    assert result["ok"] is True


def test_gate_with_cinematic_playbook_blocks_missing_environment(tmp_path):
    proj = init_project(tmp_path, "demo", "演示", "cinematic")
    store = ArtifactStore(proj)
    store.write("proposal_packet", {"concept": "雨夜", "playbook": "cyberpunk_neon"})
    store.write("script", {"title": "t", "sections": [{"id": "s", "narration": "雨还在下"}]})
    with pytest.raises(GateError, match="completeness"):
        validate_completion(proj, "script", CINEMATIC)


def test_gate_documentary_playbook_does_not_require_characters(tmp_path):
    proj = init_project(tmp_path, "demo", "演示", "cinematic")
    store = ArtifactStore(proj)
    store.write("proposal_packet", {"concept": "访谈", "playbook": "documentary_restraint"})
    store.write("script", {"title": "t", "sections": [{"id": "s", "narration": "今天我们来聊一件小事。"}]})
    result = validate_completion(proj, "script", CINEMATIC)
    assert result["ok"] is True


def test_completeness_playbook_upgrades_severity():
    script = {"title": "t", "sections": [{"id": "s", "narration": "雨还在下。"}]}
    loose = check_completeness(script)
    assert all(f["severity"] != "critical" for f in loose)
    strict = check_completeness(script, script_style=get_playbook("anime_shonen")["script_style"])
    assert any(f["severity"] == "critical" and f["field"] == "environment" for f in strict)
    assert any(f["severity"] == "critical" and f["field"] == "characters" for f in strict)
