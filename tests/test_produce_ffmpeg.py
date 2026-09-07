"""W0 波次 4：真 ffmpeg 走 montage produce。无 ffmpeg 或未设环境变量则 skip。零网。"""

from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from montage.engine.artifacts import ArtifactStore
from montage.engine.project import init_project


def _require_real_ffmpeg() -> str:
    if os.environ.get("MONTAGE_REAL_FFMPEG") != "1":
        pytest.skip("设置 MONTAGE_REAL_FFMPEG=1 后跑真拼片")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("缺少 ffmpeg")
    return ffmpeg


def _make_clip(ffmpeg: str, dest: Path, seconds: float = 2.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=320x240:rate=24",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
            "-c:a", "aac", "-shortest",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )


def test_produce_real_ffmpeg_zero_net(tmp_path):
    ffmpeg = _require_real_ffmpeg()
    from montage.cli import main

    proj = init_project(tmp_path, "zero-key", "零钥拼片", "documentary")
    clip = proj / "assets" / "videos" / "a.mp4"
    _make_clip(ffmpeg, clip)
    store = ArtifactStore(proj)
    store.write("scene_plan", {
        "scenes": [{
            "id": "sc01",
            "start_seconds": 0,
            "end_seconds": 2,
            "shots": [{
                "shot_id": "sh01",
                "scene_id": "sc01",
                "duration_seconds": 2,
                "shot_kind": "video",
            }],
        }],
    })
    store.write("asset_manifest", {
        "items": [{
            "id": "sh01_video",
            "kind": "video",
            "path": "assets/videos/a.mp4",
            "shot_id": "sh01",
            "scene_id": "sc01",
        }],
        "reference_assets": [],
    })
    code = main(["produce", str(proj)])
    assert code == 0
    film = proj / "renders" / "final.mp4"
    assert film.is_file() and film.stat().st_size > 1000
    cover = proj / "renders" / "cover.jpg"
    assert cover.is_file() and cover.stat().st_size > 0
    pack = ArtifactStore(proj).read("release_pack")
    assert pack and pack.get("cover")
    log = ArtifactStore(proj).read("publish_log")
    assert log and log.get("status") == "exported"
    zips = list((proj / "exports").glob("*.zip"))
    assert zips
    with zipfile.ZipFile(zips[0]) as zf:
        names = zf.namelist()
        assert "CREDITS.txt" in names
        assert any(n.endswith("cover.jpg") or n.endswith("renders/cover.jpg") for n in names)
