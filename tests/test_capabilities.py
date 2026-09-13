"""供应商能力表：参考图 / 首帧 / 尾帧。"""

from montage.providers.capabilities import (
    agnes_image_ref_entries,
    apply_image_refs,
    apply_video_frames,
    image_caps,
    policy_for_loop,
    video_caps,
    video_surface,
)


def test_jimeng_strongest():
    img = image_caps(tool="jimeng_image")
    vid = video_caps(tool="jimeng_video")
    assert img["image_reference"] is True
    assert "image_urls" in img["ref_url_fields"]
    assert vid["first_frame"] and vid["last_frame"]
    assert vid["mode"] == "i2v_first_tail"
    assert vid["duration_policy"] == {"kind": "enum", "values": [5, 10]}
    assert policy_for_loop("volcengine") == vid["duration_policy"]


def test_agnes_image_ref_video_tail():
    img = image_caps(provider="agnes")
    vid = video_caps(provider="agnes")
    assert img["reference_operation"] == "image_reference"
    assert vid["first_frame"] is False
    assert vid["last_frame"] is False
    assert vid["continuity_mode"] == "image_ref"
    assert vid["max_ref_images"] == 5
    assert vid["video_ref"] is False
    assert vid["duration_policy"]["kind"] == "range"
    assert vid["duration_policy"]["max"] == 12


def test_apply_video_frames_agnes_v20_accepts_tail_url():
    payload: dict = {"prompt": "x"}
    notes = apply_video_frames(
        payload,
        first_url="http://x/f.png",
        last_url="http://x/t.png",
        caps=video_surface("agnes_v20"),
    )
    assert payload.get("image_url") == "http://x/f.png"
    assert payload.get("last_frame_url") == "http://x/t.png"
    assert not any("不支持尾帧" in n for n in notes)


def test_apply_video_frames_agnes_25_skips_first_last():
    payload: dict = {"prompt": "x"}
    notes = apply_video_frames(
        payload,
        first_url="http://x/f.png",
        last_url="http://x/t.png",
        caps=video_caps(tool="agnes_video"),
    )
    assert "image_url" not in payload
    assert "last_frame_url" not in payload
    # 文案不再说"降级纯文生"：Agnes 2.5 由 reference 分支接管，首帧只是不接入。
    assert any("未声明首帧" in n for n in notes)
    assert any("不支持尾帧" in n for n in notes)


def test_wan_no_i2v():
    assert image_caps(tool="dashscope_image")["image_reference"] is False
    assert video_caps(tool="wan_video")["first_frame"] is False
    assert video_caps(provider="dashscope")["last_frame"] is False


def test_measured_output_size_is_nonstandard():
    """实测分辨率表：视频 1280x704、图片 2K 2624x1472 等，均非 720p/1080p。"""
    from montage.providers.capabilities import measured_output_size

    assert measured_output_size("video", ratio="16:9") == (1280, 704)
    assert measured_output_size("video", ratio="9:16") == (720, 1280)
    assert measured_output_size("image", size="2K", ratio="16:9") == (2624, 1472)
    assert measured_output_size("image", size="2K", ratio="9:16") == (1472, 2624)
    assert measured_output_size("image", size="2K", ratio="1:1") == (2048, 2048)
    assert measured_output_size("image", size="1K", ratio="16:9") is None
    assert measured_output_size("video", ratio="4:3") is None


def test_unknown_degrades():
    assert image_caps(tool="nope")["image_reference"] is False
    assert video_caps(provider="nope")["first_frame"] is False


def test_apply_image_refs_agnes_merges_paths_and_urls():
    """agnes op 分支：本地路径不再降级，与 URL 合并（URL 在前）。"""
    payload: dict = {"prompt": "x"}
    notes = apply_image_refs(
        payload,
        [{"path": "a.png"}, {"url": "https://x/p.png"}, {"path": "b.png"}],
        image_caps(tool="agnes_image"),
    )
    assert notes == []
    assert payload["operation"] == "image_reference"
    assert payload["reference_urls"] == ["https://x/p.png", "a.png", "b.png"]


def test_agnes_image_ref_entries_are_single_source_for_legend():
    """实发有序表：URL 优先、路径兜底、去重保序、按上限截断并出 note。"""
    caps = image_caps(tool="agnes_image")
    caps = {**caps, "max_ref_images": 2}
    refs = [
        {"kind": "portrait", "name": "陈氏", "path": "local/p.png"},
        {"kind": "scene_ref", "name": "老宅", "url": "https://x/scene.png"},
        {"kind": "prop", "name": "铜镜", "url": "https://x/mirror.png"},
        {"kind": "portrait", "name": "重复", "url": "https://x/scene.png"},
    ]
    entries, notes = agnes_image_ref_entries(refs, caps)
    assert [e["_sent"] for e in entries] == [
        "https://x/scene.png",
        "https://x/mirror.png",
    ]
    assert [e["kind"] for e in entries] == ["scene_ref", "prop"]
    assert any("截断" in n for n in notes)
    payload: dict = {"prompt": "x"}
    apply_image_refs(payload, refs, caps)
    assert payload["reference_urls"] == [e["_sent"] for e in entries]


def test_apply_image_refs_agnes_empty_refs_degrades():
    payload: dict = {"prompt": "x"}
    notes = apply_image_refs(
        payload,
        [{"kind": "portrait"}],
        image_caps(tool="agnes_image"),
    )
    assert "operation" not in payload
    assert "降级" in notes[0]


def test_apply_image_refs_jimeng_paths():
    payload: dict = {"prompt": "x"}
    notes = apply_image_refs(
        payload,
        [{"path": "a.png"}],
        image_caps(tool="jimeng_image"),
    )
    assert payload["image_paths"] == ["a.png"]
    assert notes == []


def test_apply_image_refs_kling_packs_elements():
    payload: dict = {"prompt": "x"}
    notes = apply_image_refs(
        payload,
        [
            {"kind": "portrait", "element_id": 11, "url": "https://x/p.png"},
            {"kind": "scene_ref", "url": "https://x/s.png"},
        ],
        image_caps(tool="kling_image"),
    )
    assert notes == []
    assert payload["elements"] == [{"element_id": "11"}]
    assert payload["image_list"] == [{"image": "https://x/s.png"}]


def test_apply_video_frames_agnes_path_only_tail_not_uploaded():
    payload: dict = {"prompt": "x"}
    notes = apply_video_frames(
        payload,
        first_url="http://x/f.png",
        last_path="t.png",
        caps=video_surface("agnes_v20"),
    )
    assert payload.get("image_url") == "http://x/f.png"
    assert "last_frame_path" not in payload
    assert any("尾帧未填入" in n for n in notes)


def test_apply_video_frames_jimeng_first_tail():
    payload: dict = {"prompt": "x"}
    apply_video_frames(
        payload,
        first_path="f.png",
        last_path="t.png",
        caps=video_caps(tool="jimeng_video"),
    )
    assert payload["image_path"] == "f.png"
    assert payload["last_frame_path"] == "t.png"
    assert payload["operation"] == "image_to_video"
