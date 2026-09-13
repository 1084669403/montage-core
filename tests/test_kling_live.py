"""可灵第 6 波联调门。默认 pytest 跳过；不发真实请求。

跑真实核对（有 KLING_API_KEY 时）：

    set KLING_LIVE=1
    python -m pytest tests/test_kling_live.py -q

拼板 + 双桥会计费，再加 KLING_LIVE_EXPENSIVE=1。
无密钥则整段 skip，不开新实现波。
"""

from __future__ import annotations

import os

import pytest

from montage.engine.policy import NATIVE_AUDIO_LOOPS
from montage.providers.http import HttpError, post_json
from montage.providers.kling import (
    KlingImage,
    KlingVideo,
    _headers,
    kling_image_omni_model,
    kling_image_omni_url,
)

_LIVE = str(os.environ.get("KLING_LIVE") or "").strip().lower() in {"1", "true", "yes"}
_EXPENSIVE = str(os.environ.get("KLING_LIVE_EXPENSIVE") or "").strip().lower() in {
    "1", "true", "yes",
}

pytestmark = pytest.mark.skipif(
    not _LIVE,
    reason="第6波联调：需 KLING_LIVE=1；无密钥时不要开真实请求",
)


def _require_key() -> None:
    if not str(os.environ.get("KLING_API_KEY") or "").strip():
        pytest.skip("无 KLING_API_KEY，联调跳过")


_PATH_ALIVE = {400, 401, 402, 403, 422, 429}


def _assert_not_404(exc: HttpError, *, url: str, what: str) -> None:
    if exc.status == 404:
        pytest.fail(
            f"{what} 404：{url}。只改 KLING_IMAGE_OMNI_PATH / KLING_IMAGE_OMNI_POLL，不要猜死 contents/tasks"
            if "image" in what.lower()
            else f"{what} 404：{url}；不要降 v1"
        )
    if exc.status == 0:
        pytest.fail(f"{what} 网络错误：{exc}")
    # 余额不足 / 未登录 / 缺字段都算路径还在；不 poll、不下载。
    assert exc.status in _PATH_ALIVE or exc.status >= 400, exc
    print(f"{what} HTTP {exc.status}（路径还在，未生成）")


def test_live_native_audio_loops_exclude_kling():
    assert "kling" not in NATIVE_AUDIO_LOOPS


def test_live_image_omni_default_path_not_404():
    """默认 /v1/images/omni-image 应存在。404 才记路径已切。不提交可计费完整 payload。"""
    _require_key()
    url = kling_image_omni_url()
    try:
        post_json(url, {}, headers=_headers(), timeout=30)
    except HttpError as exc:
        _assert_not_404(exc, url=url, what="Image Omni")
        return
    print(f"Image Omni HTTP 200 {url}（未 poll）")


def test_live_omni_video_path_not_v1():
    """Omni 视频面应在 OPEN_BASE 路径，不得改去 /v1/videos/。"""
    _require_key()
    from montage.providers.kling import _open_base

    url = f"{_open_base()}/omni-video/kling-3.0-omni"
    try:
        post_json(url, {}, headers=_headers(), timeout=30)
    except HttpError as exc:
        assert "/v1/videos/" not in exc.url
        _assert_not_404(exc, url=url, what="Omni video")
        return
    print(f"Omni video HTTP 200 {url}（未 poll）")


@pytest.mark.skipif(not _EXPENSIVE, reason="拼板+双桥计费：需 KLING_LIVE_EXPENSIVE=1")
def test_live_looksheet_and_bridge(tmp_path):
    _require_key()
    if kling_image_omni_model() == "kling-v1":
        pytest.skip("KLING_IMAGE_OMNI_MODEL=kling-v1 禁止用于 Image Omni")
    out = tmp_path / "look.png"
    still = KlingImage().execute({
        "prompt": "纯白背景角色四视图拼板，四个视角，画面无文字",
        "result_type": "single",
        "aspect_ratio": "16:9",
        "output_path": str(out),
        "timeout_seconds": 180,
        "poll_interval_seconds": 5,
    })
    assert still.success, still.error
    first = tmp_path / "first.png"
    last = tmp_path / "last.png"
    first.write_bytes(out.read_bytes() if out.is_file() else b"")
    last.write_bytes(out.read_bytes() if out.is_file() else b"")
    urls = list(still.data.get("urls") or []) if isinstance(still.data, dict) else []
    if len(urls) < 1:
        pytest.skip("拼板未返回公网 URL，无法做双桥")
    video = KlingVideo().execute({
        "prompt": "the person stands still then takes one step forward",
        "api_id": "kling_omni_30",
        "image_url": urls[0],
        "last_frame_url": urls[0],
        "duration": "5",
        "timeout_seconds": 300,
        "poll_interval_seconds": 5,
        "output_path": str(tmp_path / "bridge.mp4"),
    })
    assert video.success, video.error
    assert "/v1/videos/" not in str(video.meta or "")
