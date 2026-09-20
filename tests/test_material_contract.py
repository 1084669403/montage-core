"""Read-only material contract checks.

The existence and ffprobe dependencies are injected so these tests cover the
contract logic without depending on host-specific temporary directories.
"""

from __future__ import annotations

from pathlib import Path

from montage.engine.material_contract import build_material_contract


def _scene_plan(shot_kinds=("video", "video")):
    return {
        "scenes": [
            {
                "id": "sc01",
                "shots": [
                    {
                        "shot_id": f"sh{index:02d}",
                        "scene_id": "sc01",
                        "duration_seconds": 5,
                        "shot_kind": kind,
                    }
                    for index, kind in enumerate(shot_kinds, start=1)
                ],
            }
        ],
    }


def _manifest(*, second="video"):
    items = [
        {
            "id": "sh01_clip",
            "kind": "video",
            "path": "assets/videos/sh01.mp4",
            "shot_id": "sh01",
            "scene_id": "sc01",
            "duration_seconds": 5.0,
        }
    ]
    if second:
        items.append({
            "id": "sh02_clip",
            "kind": second,
            "path": f"assets/videos/sh02.{second}",
            "shot_id": "sh02",
            "scene_id": "sc01",
            "duration_seconds": 5.0,
        })
    return {"items": items, "reference_assets": []}


def _compose_plan():
    return {
        "shots": [
            {
                "shot_id": "sh01",
                "clip_path": "assets/videos/sh01.mp4",
                "duration_seconds": 5.0,
                "render_kind": "ai_clip",
                "effects": [],
                "vfx": [],
            },
            {
                "shot_id": "sh02",
                "clip_path": "assets/videos/sh02.mp4",
                "duration_seconds": 5.0,
                "render_kind": "ai_clip",
                "effects": [],
                "vfx": [{"layer": "post", "kind": "light"}],
            },
        ]
    }


def _exists_for_paths(*names: str):
    allowed = set(names)

    def exists(path: Path) -> bool:
        return path.name in allowed

    return exists


def _probe(_path: Path):
    return {
        "format": {"duration": "5.0"},
        "streams": [{
            "codec_type": "video",
            "codec_name": "h264",
            "width": 640,
            "height": 360,
            "r_frame_rate": "24/1",
        }],
    }


def _build(manifest, compose=None, *, all_ai_video=True, scene_plan=None):
    known_paths = [
        Path(item["path"]).name
        for item in manifest["items"]
        if item.get("kind") in {"video", "image"}
    ]
    return build_material_contract(
        scene_plan=scene_plan or _scene_plan(),
        asset_manifest=manifest,
        compose_plan=compose,
        project_dir=".",
        all_ai_video=all_ai_video,
        probe_fn=_probe,
        exists_fn=_exists_for_paths(*known_paths),
    )


def test_material_contract_passes_for_ai_video_sources():
    report = _build(_manifest(), _compose_plan())
    assert report["pass"] is True
    assert report["summary"] == {
        "required_shots": 2,
        "actual_ai_video_count": 2,
        "kenburns_count": 0,
        "vfx_total_count": 1,
        "vfx_overlay_count": 1,
        "fallback_count": 0,
    }
    assert report["source_contract"]["items"][0]["path"] == "assets/videos/sh01.mp4"
    assert report["compose_contract"]["items"][1]["vfx_total_count"] == 1
    assert report["compose_contract"]["items"][1]["vfx_post_count"] == 1


def test_material_contract_blocks_missing_and_duplicate_sources():
    manifest = _manifest()
    manifest["items"].append({**manifest["items"][0], "id": "duplicate"})
    manifest["items"][1]["kind"] = "missing_kind"
    report = _build(manifest)
    assert report["pass"] is False
    assert report["source_contract"]["duplicate_manifest_shot_ids"] == ["sh01"]
    assert report["source_contract"]["missing_shot_ids"] == ["sh02"]


def test_material_contract_blocks_kenburns_fallback_under_all_video():
    compose = _compose_plan()
    compose["shots"][1]["effects"] = [{"operation": "ken_burns", "zoom": "in"}]
    compose["shots"][1]["render_kind"] = "ai_clip"
    report = _build(_manifest(), compose)
    assert report["pass"] is False
    assert report["summary"]["kenburns_count"] == 1
    assert report["fallbacks"] == [{
        "shot_id": "sh02",
        "expected_kind": "video",
        "actual_kind": "video",
    }]


def test_material_contract_allows_image_mode_when_not_all_video():
    manifest = _manifest(second="image")
    manifest["items"][1]["duration_seconds"] = 0
    scene_plan = _scene_plan(("video", "image"))
    compose = _compose_plan()
    compose["shots"][1]["effects"] = [{"operation": "ken_burns", "zoom": "in"}]
    compose["shots"][1]["render_kind"] = "image"
    report = _build(manifest, compose, all_ai_video=False, scene_plan=scene_plan)
    assert report["source_contract"]["pass"] is True
    assert report["summary"]["kenburns_count"] == 1
    assert report["fallbacks"] == []


def test_material_contract_reports_compose_gaps():
    compose = _compose_plan()
    compose["shots"] = compose["shots"][:1]
    compose["shots"].append({"shot_id": "unknown", "render_kind": "ai_clip"})
    report = _build(_manifest(), compose)
    assert report["pass"] is False
    assert report["compose_contract"]["missing_shot_ids"] == ["sh02"]
    assert report["compose_contract"]["extra_shot_ids"] == ["unknown"]
