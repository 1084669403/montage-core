"""StylePack 注册表 / 白名单 / schema。"""

from montage.compose.ffmpeg_engine import TRANSITION_NAMES
from montage.schemas import get_schema
from montage.style_packs import (
    STYLE_PACKS,
    apply_overrides,
    get_style_pack,
    list_style_packs,
    validate_overrides,
)


def test_six_packs_registered():
    ids = {p["id"] for p in list_style_packs()}
    assert {"cinematic", "documentary", "beat", "classic", "fresh", "cyber", "anime", "manga", "spoken"} <= ids
    assert len(STYLE_PACKS) == 9


def test_get_style_pack_copy():
    a = get_style_pack("cinematic")
    b = get_style_pack("cinematic")
    assert a is not None and b is not None
    a["lut"] = "changed"
    assert b["lut"] == "luts/teal-orange"
    assert get_style_pack("nope") is None
    assert get_style_pack("") is None


def test_transitions_are_whitelisted():
    for pack in STYLE_PACKS.values():
        for t in pack["transitions"]:
            assert t in TRANSITION_NAMES, f"{pack['id']} 转场 {t} 不在 TRANSITION_NAMES"


def test_bind_playbook_known_or_none():
    from montage.playbooks import get_playbook

    for pack in STYLE_PACKS.values():
        pb = pack.get("bind_playbook")
        if pb:
            assert get_playbook(pb) is not None, pb


def test_output_profile_known():
    from montage.compose.profiles import get_profile

    for pack in STYLE_PACKS.values():
        assert get_profile(pack["output_profile"]) is not None


def test_validate_overrides_ok():
    assert validate_overrides({"lut": "luts/cool-clean"}) == []
    assert validate_overrides({"transitions": ["cut", "wipe"]}) == []
    assert validate_overrides({"pacing": {"min_hold": 1, "max_hold": 3, "transition_duration": 0.2}}) == []
    assert validate_overrides({"output_profile": "douyin_vertical"}) == []
    assert validate_overrides({"bind_playbook": "chinese_elegance"}) == []
    assert validate_overrides({"lut": ""}) == []


def test_validate_overrides_rejects():
    errs = validate_overrides({"lut": "not-a-lut"})
    assert any("lut" in e for e in errs)
    errs = validate_overrides({"transitions": ["magic_wipe"]})
    assert any("转场" in e for e in errs)
    errs = validate_overrides({"pacing": {"min_hold": 9, "max_hold": 1}})
    assert any("min_hold" in e for e in errs)
    errs = validate_overrides({"output_profile": "vhs_480"})
    assert any("output_profile" in e for e in errs)
    errs = validate_overrides({"bind_playbook": "no_such_book"})
    assert any("bind_playbook" in e for e in errs)


def test_apply_overrides_merges_pacing():
    pack = get_style_pack("documentary")
    merged = apply_overrides(pack, {"pacing": {"max_hold": 20}, "lut": "luts/teal-orange"})
    assert merged["lut"] == "luts/teal-orange"
    assert merged["pacing"]["max_hold"] == 20
    assert merged["pacing"]["min_hold"] == pack["pacing"]["min_hold"]
    assert pack["lut"] == "luts/muted-documentary"  # 原包未改


def test_apply_overrides_raises_on_bad():
    import pytest

    pack = get_style_pack("fresh")
    with pytest.raises(ValueError, match="lut"):
        apply_overrides(pack, {"lut": "nope"})


def test_auto_edit_schemas_registered():
    plan = get_schema("auto_edit_plan")
    report = get_schema("auto_edit_report")
    assert plan is not None and "schema_version" in plan["required"]
    assert "session_id" in plan["required"]
    assert report is not None and "output_path" in report["required"]


def test_transition_names_public():
    assert "cut" in TRANSITION_NAMES
    assert "crossfade" in TRANSITION_NAMES
    assert "wipe" in TRANSITION_NAMES
    assert "fade_black" in TRANSITION_NAMES
