from concurrent.futures import ThreadPoolExecutor

from montage.providers import agnes_usage


def test_usage_additions_are_thread_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("MONTAGE_AGNES_USAGE_PATH", str(tmp_path / "usage.json"))

    def add_one(_index: int) -> None:
        agnes_usage.add_video_seconds("tokenplan", 1)
        agnes_usage.add_images("tokenplan", 1)

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(add_one, range(64)))

    used, limit, remaining = agnes_usage.quota_status("tokenplan", "video")
    assert (used, limit, remaining) == (64.0, 500.0, 436.0)
    used_images, image_limit, image_remaining = agnes_usage.quota_status("tokenplan", "image")
    assert (used_images, image_limit, image_remaining) == (64.0, 4000.0, 3936.0)
