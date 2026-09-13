"""Agnes RPM 分层与 Token Plan 每日配额：纯单元测试，不打 API。"""

from __future__ import annotations

import json

import pytest

from montage.providers import agnes_usage
from montage.providers.capabilities import (
    AGNES_RPM,
    agnes_access_tier,
    agnes_image_rpm,
    agnes_video_rpm,
)


# ---- RPM 分层（R1）----

def test_access_tier_defaults_and_parses(monkeypatch):
    monkeypatch.delenv("AGNES_ACCESS_TYPE", raising=False)
    assert agnes_access_tier() == "default"
    monkeypatch.setenv("AGNES_ACCESS_TYPE", "enterprise")
    assert agnes_access_tier() == "enterprise"
    monkeypatch.setenv("AGNES_ACCESS_TYPE", "tokenplan")
    assert agnes_access_tier() == "tokenplan"
    monkeypatch.setenv("AGNES_ACCESS_TYPE", "nonsense")
    assert agnes_access_tier() == "default"


def test_video_rpm_three_tiers():
    assert agnes_video_rpm("default") == 1
    assert agnes_video_rpm("enterprise") == 2
    assert agnes_video_rpm("tokenplan") == 5


def test_image_rpm_by_size_and_tier():
    assert agnes_image_rpm("1K", "default") == 20
    assert agnes_image_rpm("2K", "default") == 10
    assert agnes_image_rpm("3K", "default") == 1
    assert agnes_image_rpm("4K", "default") == 1
    assert agnes_image_rpm("1K", "enterprise") == 40
    assert agnes_image_rpm("2K", "enterprise") == 20
    assert agnes_image_rpm("3K", "enterprise") == 1
    assert agnes_image_rpm("1K", "tokenplan") == 100
    assert agnes_image_rpm("2K", "tokenplan") == 80
    assert agnes_image_rpm("4K", "tokenplan") == 1


def test_image_rpm_unknown_size_falls_back_2k():
    assert agnes_image_rpm("weird", "default") == agnes_image_rpm("2K", "default")
    assert agnes_image_rpm("", "tokenplan") == agnes_image_rpm("2K", "tokenplan")


def test_rpm_single_source_of_truth():
    import montage.providers.capabilities as caps

    assert not hasattr(caps, "IMAGE_META")
    assert "rpm" not in caps.VIDEO_META["agnes_video"]
    assert set(AGNES_RPM) == {"default", "enterprise", "tokenplan"}


# ---- Token Plan 每日配额（P1）----

@pytest.fixture()
def usage_file(tmp_path, monkeypatch):
    path = tmp_path / "agnes_usage.json"
    monkeypatch.setenv("MONTAGE_AGNES_USAGE_PATH", str(path))
    return path


def test_quota_non_tokenplan_has_no_limit(usage_file):
    assert agnes_usage.quota_status("default", "image") == (0.0, None, None)
    assert agnes_usage.quota_status("enterprise", "video") == (0.0, None, None)


def test_quota_accumulates_and_reports(usage_file):
    agnes_usage.add_images("tokenplan", 3)
    agnes_usage.add_video_seconds("tokenplan", 12.5)
    used, limit, remaining = agnes_usage.quota_status("tokenplan", "image")
    assert (used, limit, remaining) == (3.0, 4000.0, 3997.0)
    used_v, limit_v, remaining_v = agnes_usage.quota_status("tokenplan", "video")
    assert (used_v, limit_v, remaining_v) == (12.5, 500.0, 487.5)


def test_quota_buckets_do_not_cross_contaminate(usage_file):
    agnes_usage.add_images("default", 5)
    agnes_usage.add_images("tokenplan", 2)
    assert agnes_usage.read_usage("default")["images"] == 5
    assert agnes_usage.read_usage("tokenplan")["images"] == 2


def test_quota_corrupt_file_degrades_silently(usage_file):
    usage_file.write_text("{not json", encoding="utf-8")
    assert agnes_usage.read_usage("tokenplan")["images"] == 0
    agnes_usage.add_images("tokenplan", 1)
    assert agnes_usage.read_usage("tokenplan")["images"] == 1


def test_quota_resets_on_new_day(usage_file):
    usage_file.write_text(
        json.dumps({
            "day": "2000-01-01",
            "tiers": {"tokenplan": {"images": 999, "video_seconds": 1.0}},
        }),
        encoding="utf-8",
    )
    assert agnes_usage.read_usage("tokenplan")["images"] == 0
    assert agnes_usage.read_usage("tokenplan")["video_seconds"] == 0


def test_usage_snapshot_tokenplan(usage_file, monkeypatch):
    monkeypatch.setenv("AGNES_ACCESS_TYPE", "tokenplan")
    agnes_usage.add_images("tokenplan", 4)
    snap = agnes_usage.usage_snapshot()
    assert snap["tier"] == "tokenplan"
    assert snap["declared"] is True
    assert snap["image_limit"] == 4000.0
    assert snap["image_remaining"] == 3996.0
    assert snap["video_limit"] == 500.0


def test_usage_snapshot_undeclared_is_free(usage_file, monkeypatch):
    monkeypatch.delenv("AGNES_ACCESS_TYPE", raising=False)
    snap = agnes_usage.usage_snapshot()
    assert snap["tier"] == "default"
    assert snap["declared"] is False
    assert snap["image_limit"] is None
    assert snap["video_limit"] is None


def test_doctor_payload_includes_agnes_usage(usage_file, monkeypatch):
    from argparse import Namespace

    from montage.cli import _doctor_payload
    from montage.registry import ToolRegistry

    monkeypatch.delenv("AGNES_ACCESS_TYPE", raising=False)
    reg = ToolRegistry()
    reg.discover()
    payload = _doctor_payload(reg, Namespace(pipeline=None, project=None))
    usage = payload["agnes_usage"]
    assert usage["tier"] == "default"
    assert usage["declared"] is False
    assert payload["import_errors"] == []
