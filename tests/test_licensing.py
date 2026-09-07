"""licensing 纯逻辑测试：CC URL、默认排除 NC/SA/ND、同曲署名去重。"""

from lib.licensing import (
    allowed,
    attribution_text,
    can_commercial,
    collect_attributions,
    license_from_url,
    need_attribution,
    normalize_license,
)


def test_license_from_url_by():
    assert license_from_url("https://creativecommons.org/licenses/by/4.0/") == "CC-BY"


def test_license_from_url_cc0_publicdomain():
    assert license_from_url("https://creativecommons.org/publicdomain/zero/1.0/") == "CC0"
    assert can_commercial("CC0") is True
    assert need_attribution("CC0") is False


def test_license_from_url_publicdomain_mark():
    assert license_from_url("https://creativecommons.org/publicdomain/mark/1.0/") == "PDM"
    assert allowed("PDM") is True
    assert need_attribution("PDM") is False


def test_license_from_url_by_nc_excluded():
    assert license_from_url("http://creativecommons.org/licenses/by-nc/3.0/") == "CC-BY-NC"
    assert can_commercial("CC-BY-NC") is False
    assert allowed("CC-BY-NC", commercial_only=True) is False


def test_sa_and_nd_excluded_by_default():
    assert allowed("CC-BY-SA") is False
    assert allowed("CC-BY-SA", allow_share_alike=True) is True
    assert allowed("CC-BY-ND") is False
    assert allowed("CC-BY-ND", allow_no_derivatives=True) is True


def test_sampling_and_unknown_excluded():
    assert license_from_url("https://creativecommons.org/licenses/sampling+/1.0/") == "sampling"
    assert allowed("sampling") is False
    assert normalize_license("not-a-license") == "unknown"
    assert allowed("unknown") is False
    assert allowed("") is False


def test_cc_by_allowed():
    assert allowed("CC-BY") is True
    assert allowed("cc by 4.0") is True
    assert need_attribution("CC-BY") is True


def test_collect_attributions_dedupes_same_track():
    events = [
        {"title": "Pulse", "author": "Ada", "license": "CC-BY", "attribution": ""},
        {"title": "Pulse", "author": "Ada", "license": "CC-BY", "attribution": ""},
        {"title": "Rain", "author": "Bea", "license": "CC-BY", "attribution": ""},
    ]
    texts = collect_attributions(events)
    assert len(texts) == 2
    assert texts[0] == "Pulse by Ada — CC-BY"
    assert texts[1] == "Rain by Bea — CC-BY"


def test_attribution_prefers_existing_and_skips_placeholder():
    assert attribution_text({"attribution": "Music: Hello by KM — CC BY 4.0"}) == (
        "Music: Hello by KM — CC BY 4.0"
    )
    hit = {
        "title": "Gentle",
        "author": "Kevin MacLeod",
        "license": "CC-BY",
        "attribution": "Music: <曲名> by Kevin MacLeod (incompetech.com) — CC BY 4.0",
    }
    assert "Gentle" in attribution_text(hit)
    assert "<曲名>" not in attribution_text(hit)


def test_cc0_attribution_empty_when_no_field():
    assert attribution_text({"title": "Drone", "author": "X", "license": "CC0"}) == ""
