"""Seedream 5.0 lite 生图契约：allowlist / 尺寸 / 装箱 / payload / 解析 / 费用 / 解耦。零真实 HTTP。"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from montage.providers.capabilities import (
    IMAGE_BY_PROVIDER,
    IMAGE_BY_TOOL,
    apply_image_refs,
    image_caps,
)
from montage.providers.prompt_probe import build_seedream_prompt_checked
from montage.providers.seedream_image import (
    SeedreamImage,
    pack_seedream_image,
    resolve_seedream_model,
    size_for_aspect,
    validate_seedream_size,
)
from montage.engine.policy import load_loop_policy, resolve_allowed_providers
from montage.toolbase import ToolStatus

# 1×1 PNG，体积远小于上限。
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# ---- model allowlist（写死 lite，禁止降级） ----

def test_model_allowlist_accepts_lite_family():
    assert resolve_seedream_model({}) == "doubao-seedream-5-0-260128"
    assert resolve_seedream_model({"model": "doubao-seedream-5-0-260128"}) == "doubao-seedream-5-0-260128"
    assert resolve_seedream_model({"model": "doubao-seedream-5-0-lite-260128"}) == "doubao-seedream-5-0-lite-260128"


def test_model_allowlist_accepts_endpoint_ids(monkeypatch):
    assert resolve_seedream_model({"model": "ep-20260905173000-abcde"}) == "ep-20260905173000-abcde"
    monkeypatch.setenv("SEEDREAM_IMAGE_MODEL", "ep-20260905173000-abcde")
    assert resolve_seedream_model({}) == "ep-20260905173000-abcde"


@pytest.mark.parametrize("bad", [
    "doubao-seedream-4-5-251128",
    "doubao-seedream-4-0-250828",
    "doubao-seedream-5-0-pro-260628",
    "kling-v3-omni",
    "garbage",
])
def test_model_allowlist_rejects_non_lite(bad):
    with pytest.raises(ValueError, match="只允许 Seedream 5.0 lite"):
        resolve_seedream_model({"model": bad})


def test_tool_rejects_non_lite_without_request(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    tool = SeedreamImage()
    result = tool.execute({"prompt": "一只猫", "model": "doubao-seedream-4-5-251128"})
    assert result.success is False
    assert "只允许 Seedream 5.0 lite" in (result.error or "")


# ---- 尺寸校验（官方示例校验组） ----

def test_size_official_valid_examples_pass():
    assert validate_seedream_size("3750x1250") == "3750x1250"  # 468.75 万像素 ∈ 区间
    assert validate_seedream_size("2048x2048") == "2048x2048"
    assert validate_seedream_size("1600x2848") == "1600x2848"


@pytest.mark.parametrize("bad", ["1500x1500", "8000x8000", "abc", "2048", "x"])
def test_size_official_invalid_examples_rejected(bad):
    with pytest.raises(ValueError):
        validate_seedream_size(bad)


def test_size_aspect_mapping_table():
    assert size_for_aspect("9:16") == "1600x2848"
    assert size_for_aspect("16:9") == "2848x1600"
    assert size_for_aspect("1:1") == "2048x2048"
    assert size_for_aspect("unknown") == "2048x2048"


# ---- 参考图装箱（失败硬报错，不降级纯文生） ----

def test_pack_local_image_to_data_uri(tmp_path: Path):
    p = tmp_path / "look_face_a.png"
    p.write_bytes(_PNG_BYTES)
    packed, _ = pack_seedream_image([str(p)])
    assert packed == ["data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode("ascii")]


def test_pack_url_passthrough_and_dedupe():
    packed, _ = pack_seedream_image([
        "https://example.com/a.png",
        "https://example.com/a.png",
        "https://example.com/b.png",
    ])
    assert packed == ["https://example.com/a.png", "https://example.com/b.png"]


def test_pack_mixed_url_and_local(tmp_path: Path):
    p = tmp_path / "ref.jpg"
    p.write_bytes(_PNG_BYTES)
    packed, _ = pack_seedream_image([f"https://example.com/a.png", str(p)])
    assert packed[0] == "https://example.com/a.png"
    assert packed[1].startswith("data:image/jpeg;base64,")


def test_pack_missing_file_hard_fails(tmp_path: Path):
    with pytest.raises(ValueError, match="禁止降级纯文生"):
        pack_seedream_image([str(tmp_path / "missing.png")])


def test_pack_bad_suffix_hard_fails(tmp_path: Path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4")
    with pytest.raises(ValueError, match="不受支持"):
        pack_seedream_image([str(p)])


def test_pack_oversize_hard_fails(tmp_path: Path):
    p = tmp_path / "big.png"
    p.write_bytes(b"\x00" * (30 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="30MB"):
        pack_seedream_image([str(p)])


def test_pack_over_14_hard_fails():
    urls = [f"https://example.com/{i}.png" for i in range(15)]
    with pytest.raises(ValueError, match="14"):
        pack_seedream_image(urls)


# ---- payload 组装与响应解析（dry-run，post_json 打桩） ----

class _FakeResp:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def __call__(self, url, payload, headers=None, timeout=180):
        self.calls += 1
        self.seen_url = url
        self.seen_payload = payload
        self.seen_headers = headers
        return self.payload


def _patch_post(monkeypatch, payload):
    fake = _FakeResp(payload)
    monkeypatch.setattr("montage.providers.seedream_image.post_json", fake)
    return fake


def _ok_resp():
    return {
        "model": "doubao-seedream-5-0-260128",
        "created": 1789000000,
        "data": [{"url": "https://img.example.com/out.png", "size": "1600x2848"}],
        "usage": {"generated_images": 1, "output_tokens": 17600, "total_tokens": 17600},
    }


def test_payload_shape_and_image_always_array(monkeypatch, tmp_path):
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    monkeypatch.delenv("SEEDREAM_IMAGE_MODEL", raising=False)
    ref = tmp_path / "r.png"
    ref.write_bytes(_PNG_BYTES)
    fake = _patch_post(monkeypatch, _ok_resp())
    tool = SeedreamImage()
    result = tool.execute({
        "prompt": "国风插画",
        "aspect_ratio": "9:16",
        "image": ["https://example.com/a.png", str(ref)],
    })
    assert result.success is True
    body = fake.seen_payload
    assert body["model"] == "doubao-seedream-5-0-260128"
    assert body["size"] == "1600x2848"
    assert body["sequential_image_generation"] == "disabled"
    assert body["watermark"] is False
    assert body["response_format"] == "url"
    assert body["output_format"] == "png"
    assert isinstance(body["image"], list)  # 统一 string[]（官方两态的数组化）
    assert body["image"][0] == "https://example.com/a.png"
    assert body["image"][1].startswith("data:image/png;base64,")
    assert "sequential_image_generation_options" not in body
    assert "optimize_prompt_options" not in body
    assert fake.seen_url.endswith("/api/v3/images/generations")
    assert fake.seen_headers["Authorization"] == "Bearer test-key"
    assert result.data["urls"] == ["https://img.example.com/out.png"]
    assert result.data["size"] == "1600x2848"
    # 无 output_path 时 output 为空但不落盘失败
    assert result.data["output"] is None


def test_tool_treats_blank_refs_as_no_refs(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    fake = _patch_post(monkeypatch, _ok_resp())
    # 空白引用过滤后视为无参考图（纯文生），payload 不带 image 字段
    result = SeedreamImage().execute({"prompt": "x", "image": ["", "  "]})
    assert result.success is True
    assert "image" not in fake.seen_payload


def test_tool_top_level_error(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    _patch_post(monkeypatch, {"error": {"code": "InvalidParameter", "message": "bad size"}})
    result = SeedreamImage().execute({"prompt": "x"})
    assert result.success is False
    assert "InvalidParameter" in (result.error or "")


def test_tool_no_url_hard_error(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    _patch_post(monkeypatch, {"data": [], "usage": {"generated_images": 0}})
    result = SeedreamImage().execute({"prompt": "x"})
    assert result.success is False
    assert "未返回任何图片" in (result.error or "")


def test_tool_item_error_tolerated_and_missing_pro_fields_ok(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    # data[].error（组图场景才出现）与 usage.input_images / data[].output_format 缺省不炸
    resp = _ok_resp()
    resp["data"][0]["error"] = {"code": "OutputImageSensitiveContentDetected", "message": "审核"}
    _patch_post(monkeypatch, resp)
    result = SeedreamImage().execute({"prompt": "x"})
    assert result.success is True
    assert any("审核" in e for e in result.meta["item_errors"])


def test_tool_size_out_of_range_rejected_before_request(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    fake = _patch_post(monkeypatch, _ok_resp())
    result = SeedreamImage().execute({"prompt": "x", "size": "1500x1500"})
    assert result.success is False
    assert "总像素" in (result.error or "")
    assert fake.calls == 0  # 不发请求


def test_tool_long_prompt_warns_not_truncates(monkeypatch):
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    _patch_post(monkeypatch, _ok_resp())
    result = SeedreamImage().execute({"prompt": "墨" * 301})
    assert result.success is True
    assert any("300" in w for w in result.meta["warnings"])


def test_tool_requires_prompt_and_key(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    tool = SeedreamImage()
    assert tool.execute({"prompt": "x"}).success is False
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    assert tool.execute({"prompt": "  "}).success is False


def test_get_status(monkeypatch):
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    assert SeedreamImage().get_status() is ToolStatus.NEEDS_CONFIG
    monkeypatch.setenv("ARK_API_KEY", "test-key")
    assert SeedreamImage().get_status() is ToolStatus.AVAILABLE


# ---- estimate_cost（¥0.22 ÷ 7.2；SEEDREAM_USD_PER_IMAGE 覆盖） ----

def test_estimate_cost_default(monkeypatch):
    monkeypatch.delenv("SEEDREAM_USD_PER_IMAGE", raising=False)
    monkeypatch.delenv("SEEDREAM_CNY_PER_USD", raising=False)
    assert SeedreamImage().estimate_cost({}) == pytest.approx(0.22 / 7.2, abs=1e-4)


def test_estimate_cost_rate_and_override(monkeypatch):
    monkeypatch.delenv("SEEDREAM_USD_PER_IMAGE", raising=False)
    monkeypatch.setenv("SEEDREAM_CNY_PER_USD", "8.0")
    assert SeedreamImage().estimate_cost({}) == pytest.approx(0.0275, abs=1e-4)
    monkeypatch.setenv("SEEDREAM_USD_PER_IMAGE", "0.05")
    assert SeedreamImage().estimate_cost({}) == pytest.approx(0.05, abs=1e-9)


# ---- capabilities / apply_image_refs ----

def test_capabilities_registered():
    caps = image_caps(tool="seedream_image")
    assert caps["image_reference"] is True
    assert caps["ref_url_fields"] == ("image",)
    assert IMAGE_BY_PROVIDER["ark"] is IMAGE_BY_TOOL["seedream_image"]


def test_apply_image_refs_merges_url_and_path():
    payload: dict = {}
    notes = apply_image_refs(
        payload,
        [
            {"url": "https://example.com/a.png", "path": ""},
            {"url": "", "path": "assets/x/look_face_b.png"},
        ],
        image_caps(tool="seedream_image"),
    )
    assert notes == []
    assert payload["image"] == ["https://example.com/a.png", "assets/x/look_face_b.png"]


def test_apply_image_refs_caps_at_14():
    payload: dict = {}
    refs = [{"url": f"https://example.com/{i}.png"} for i in range(20)]
    apply_image_refs(payload, refs, image_caps(tool="seedream_image"))
    assert len(payload["image"]) == 14


# ---- policy 解耦与项目包 ----

def test_resolve_image_providers_overrides_video_lock():
    pol = {"image_providers": ["ark"], "allowed_providers": ["kling"], "video_loop": "kling"}
    assert resolve_allowed_providers(pol, {}, capability="image_generation") == ["ark"]
    assert resolve_allowed_providers(pol, {}, capability="video_generation") == ["kling"]


def test_ark_loop_no_longer_redirects_images_to_jimeng():
    pol = {"allowed_providers": ["ark"], "video_loop": "ark"}
    assert resolve_allowed_providers(pol, {}, capability="image_generation") == ["ark"]


def test_inputs_image_providers_wins():
    pol = {"image_providers": ["kling"]}
    assert resolve_allowed_providers(pol, {"image_providers": ["ark"]}, capability="image_generation") == ["ark"]


def test_huapi_packet_has_image_providers():
    pol = load_loop_policy("projects/huapi_liaozhai")
    assert pol.get("image_providers") == ["ark"]
    assert pol.get("video_loop") == "kling"


# ---- prompt_probe 五段式 ----

def test_prompt_builder_order_and_negative_tail():
    prompt, warn = build_seedream_prompt_checked(
        {
            "style_lock": "画风硬性要求，优先级最高：这是白描连环画，绝不是照片",
            "subject": "一位清瘦书生立于桥头",
            "outfit": "青衫布履",
            "scene": "黄昏渡口，光从左侧打来",
            "composition": "人物居右黄金分割位，头顶不裁切",
            "style_anchor": "刘继卣笔意，铁线描",
        },
        style_family="woodcut",
    )
    assert warn == []
    order = [
        "画风硬性要求",
        "清瘦书生",
        "青衫布履",
        "黄昏渡口",
        "黄金分割位",
        "刘继卣笔意",
        "负向：",
    ]
    pos = [prompt.index(s) for s in order]
    assert pos == sorted(pos)  # 五段式顺序固定，负向收尾


def test_prompt_builder_composition_optional_no_default_injection():
    prompt, warn = build_seedream_prompt_checked({"subject": "人物特写"})
    assert warn == []
    assert "黄金分割" not in prompt  # 构图段无默认注入
    assert prompt == "人物特写"


def test_prompt_builder_warns_missing_style_lock():
    _, warn = build_seedream_prompt_checked({"subject": "x"}, style_family="animation")
    assert any("硬性画风前置" in w for w in warn)


def test_prompt_builder_family_tails():
    assert build_seedream_prompt_checked({}, style_family="photoreal")[0].startswith("负向：")
    assert "赛璐璐" not in build_seedream_prompt_checked({}, style_family="lineart")[0]
