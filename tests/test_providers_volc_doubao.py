"""即梦/豆包/Edge TTS 适配器测试（无网络，只测契约与校验逻辑）。"""

import base64
import json

import pytest

from montage.providers import _volc_signer, doubao, edge_tts, jimeng
from montage.toolbase import ToolStatus


# --- 火山签名器 ----------------------------------------------------------

def test_sign_headers_shape():
    body = b'{"a": 1}'
    headers = _volc_signer.sign_headers("POST", "/", {"Action": "CVProcess", "Version": "2022-08-31"}, body, "AK", "SK")
    auth = headers["Authorization"]
    assert auth.startswith("HMAC-SHA256 Credential=AK/")
    assert "SignedHeaders=" in auth and "Signature=" in auth
    assert "X-Content-Sha256" in headers
    assert headers["X-Content-Sha256"] == __import__("hashlib").sha256(body).hexdigest()


def test_sign_deterministic_for_same_input():
    a = _volc_signer.sign_headers("POST", "/", {"Action": "X"}, b"body", "AK", "SK")
    b = _volc_signer.sign_headers("POST", "/", {"Action": "X"}, b"body", "AK", "SK")
    assert a["Authorization"] == b["Authorization"]


def test_sign_changes_with_secret():
    a = _volc_signer.sign_headers("POST", "/", {"Action": "X"}, b"body", "AK", "SK1")
    b = _volc_signer.sign_headers("POST", "/", {"Action": "X"}, b"body", "AK", "SK2")
    assert a["Authorization"] != b["Authorization"]


def test_sign_requires_content_type():
    headers = _volc_signer.sign_headers("GET", "/", {}, b"", "AK", "SK")
    assert headers["Content-Type"] == "application/json"


# --- 即梦 ----------------------------------------------------------------

def test_duration_to_frames():
    assert jimeng.duration_to_frames(None) == 121
    assert jimeng.duration_to_frames(5) == 121
    assert jimeng.duration_to_frames(6) == 241
    assert jimeng.duration_to_frames(10) == 241


def test_req_key_pick_t2v():
    tool = jimeng.JimengVideo()
    assert tool._pick_req_key({"prompt": "x"}) == jimeng.REQ_KEY_T2V_1080
    assert tool._pick_req_key({"prompt": "x", "resolution": "720p"}) == jimeng.REQ_KEY_T2V_720


def test_req_key_pick_i2v():
    tool = jimeng.JimengVideo()
    key = tool._pick_req_key({"prompt": "x", "image_url": "http://a/b.png"})
    assert key == jimeng.REQ_KEY_I2V_FIRST_1080


def test_req_key_pick_recamera():
    tool = jimeng.JimengVideo()
    key = tool._pick_req_key({"prompt": "x", "image_url": "http://a/b.png", "template_id": "orbit"})
    assert key == jimeng.REQ_KEY_RECAMERA


def test_collect_images_last_frame():
    """尾帧图追加到首帧之后（i2v first_tail 首尾帧约束）。"""
    inputs = {"image_path": "first.png", "last_frame_path": "last.png"}
    assert jimeng._collect_images(inputs) == {"paths": ["first.png", "last.png"], "urls": []}
    inputs_url = {"image_url": "http://a/f.png", "last_frame_url": "http://a/l.png"}
    assert jimeng._collect_images(inputs_url) == {"paths": [], "urls": ["http://a/f.png", "http://a/l.png"]}


def test_req_key_pick_first_tail():
    tool = jimeng.JimengVideo()
    key = tool._pick_req_key({
        "prompt": "x", "image_url": "http://a/f.png", "last_frame_url": "http://a/l.png",
    })
    assert key == jimeng.REQ_KEY_I2V_TAIL_1080
    key720 = tool._pick_req_key({
        "prompt": "x", "image_url": "http://a/f.png", "last_frame_url": "http://a/l.png",
        "resolution": "720p",
    })
    assert key720 == jimeng.REQ_KEY_I2V_TAIL_720


def test_seed_field_exposed_in_schema():
    schema = jimeng.JimengVideo.input_schema["properties"]
    assert "last_frame_path" in schema
    assert "last_frame_url" in schema
    assert "seed" in schema


def test_jimeng_requires_credentials(monkeypatch):
    monkeypatch.delenv("VOLC_ACCESSKEY", raising=False)
    monkeypatch.delenv("VOLC_SECRETKEY", raising=False)
    result = jimeng.JimengImage().execute({"prompt": "测试"})
    assert not result.success
    assert "VOLC" in result.error


def test_jimeng_status(monkeypatch):
    monkeypatch.delenv("VOLC_ACCESSKEY", raising=False)
    monkeypatch.delenv("VOLC_SECRETKEY", raising=False)
    assert jimeng.JimengImage().get_status() == ToolStatus.NEEDS_CONFIG
    monkeypatch.setenv("VOLC_ACCESSKEY", "ak")
    monkeypatch.setenv("VOLC_SECRETKEY", "sk")
    assert jimeng.JimengImage().get_status() == ToolStatus.AVAILABLE


def test_encode_image_missing_pillow(monkeypatch):
    import sys

    # 模拟 Pillow 缺失：sys.modules 中置 None 会让 import 直接抛 ImportError
    old_pil = sys.modules.get("PIL")
    old_pil_img = sys.modules.get("PIL.Image")
    sys.modules["PIL"] = None
    sys.modules["PIL.Image"] = None
    try:
        with pytest.raises(jimeng.JimengApiError, match="Pillow"):
            jimeng.encode_image_file("nope.png")
    finally:
        for key, old in (("PIL", old_pil), ("PIL.Image", old_pil_img)):
            if old is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = old


# --- 豆包 ----------------------------------------------------------------

def test_doubao_requires_key(monkeypatch):
    monkeypatch.delenv("DOUBAO_SPEECH_API_KEY", raising=False)
    result = doubao.DoubaoTTS().execute({"text": "你好"})
    assert not result.success


def test_doubao_requires_text(monkeypatch):
    monkeypatch.setenv("DOUBAO_SPEECH_API_KEY", "k")
    result = doubao.DoubaoTTS().execute({})
    assert not result.success


def test_doubao_status(monkeypatch):
    monkeypatch.delenv("DOUBAO_SPEECH_API_KEY", raising=False)
    assert doubao.DoubaoTTS().get_status() == ToolStatus.NEEDS_CONFIG


def test_doubao_cost_scales_with_length():
    tool = doubao.DoubaoTTS()
    assert tool.estimate_cost({"text": "x" * 1000}) > tool.estimate_cost({"text": "x"})


# --- 豆包 V3 流式重构（端点/资源路由/流式解析/情感/重试） --------------------

def test_doubao_endpoint_for_key():
    assert doubao.endpoint_for_key("ark-abc123") == doubao.PLAN_UNIDIRECTIONAL_URL
    assert doubao.endpoint_for_key("0f2ffake-uuid-key") == doubao.UNIDIRECTIONAL_URL
    assert doubao.endpoint_for_key("") == doubao.UNIDIRECTIONAL_URL


def test_doubao_resource_id_routing():
    assert doubao.resource_id_for("S_evEGUVU1abc") == doubao.RESOURCE_ICL_2
    assert doubao.resource_id_for("zh_female_vv_uranus_bigtts") == doubao.RESOURCE_TTS_2
    assert doubao.resource_id_for("zh_male_liufei_uranus_bigtts") == doubao.RESOURCE_TTS_2
    # *_saturn_bigtts（中缀）是 1.0 族；saturn_ 前缀才是 2.0（实测 2026-09-04）
    assert doubao.resource_id_for("zh_male_chunhou_saturn_bigtts") == doubao.RESOURCE_TTS_1
    assert doubao.resource_id_for("saturn_zh_female_tianmei_bigtts") == doubao.RESOURCE_TTS_2
    assert doubao.resource_id_for("zh_female_wanwanxiaohe_moon_bigtts") == doubao.RESOURCE_TTS_1
    assert doubao.resource_id_for("zh_male_yunzhou_mars_bigtts") == doubao.RESOURCE_TTS_1
    assert doubao.is_tts2_voice("zh_female_vv_uranus_bigtts")
    assert not doubao.is_tts2_voice("zh_female_wanwanxiaohe_moon_bigtts")
    assert not doubao.is_tts2_voice("zh_male_chunhou_saturn_bigtts")


def test_doubao_iter_json_objects_no_newlines():
    """官方响应可能无换行分隔，必须用 raw_decode 切分而非按行 split。"""
    text = '{"code":0,"data":"QUJD"}{"code":0,"sentence":{"text":"你","words":[]}}{"code":20000000}'
    objs = list(doubao.iter_json_objects(text))
    assert len(objs) == 3
    assert objs[0]["data"] == "QUJD"
    assert objs[2]["code"] == 20000000
    # 带换行/空白的等价形式
    objs2 = list(doubao.iter_json_objects('\n{"code":0,"data":"QQ=="}\n  {"code":20000000}\n'))
    assert len(objs2) == 2


def test_doubao_normalize_sentences_seconds():
    sents = doubao.normalize_sentences([
        {
            "text": "世间妖邪。",
            "words": [
                {"word": "世", "startTime": 0.10, "endTime": 0.20},
                {"word": "间", "startTime": 0.20, "endTime": 0.30},
                {"word": "妖邪。", "startTime": 0.30, "endTime": 1.50},
            ],
        },
    ])
    assert sents[0]["start_seconds"] == 0.10
    assert sents[0]["end_seconds"] == 1.50
    assert sents[0]["words"][0]["start_seconds"] == 0.10
    assert doubao.normalize_sentences([{"text": "空", "words": []}]) == []


def test_doubao_additions_is_string_and_payload_correct():
    """官方坑：additions 必须是 JSON 字符串，传对象会被静默忽略。"""
    add = doubao.build_additions(voice="zh_female_vv_uranus_bigtts", context_text="阴冷诡异地低语")
    assert isinstance(add, str)
    payload = json.loads(add)
    assert payload["context_texts"] == ["阴冷诡异地低语"]
    assert "model_type" not in payload  # 非复刻音色不带 model_type

    add_icl = doubao.build_additions(voice="S_abc", context_text="用哭腔说")
    payload_icl = json.loads(add_icl)
    assert payload_icl["model_type"] == 4  # ICL2.0 必须 model_type=4
    assert payload_icl["context_texts"] == ["用哭腔说"]

    # 1.0 音色：context_texts 不生效（该通道仅 2.0）
    add_1 = doubao.build_additions(voice="zh_female_wanwanxiaohe_moon_bigtts", context_text="阴冷")
    assert "context_texts" not in json.loads(add_1)


def test_doubao_req_params_emotion_channels():
    """情感按音色族走不同通道：2.0→additions.context_texts；1.0→audio_params.emotion。"""
    rp2 = doubao.build_req_params(
        {"text": "你好", "context_text": "阴冷诡异地低语", "enable_subtitle": True},
        voice="zh_female_vv_uranus_bigtts",
        resource_id=doubao.RESOURCE_TTS_2,
    )
    ap2 = rp2["audio_params"]
    assert "emotion" not in ap2 and "enable_timestamp" not in ap2
    assert ap2["enable_subtitle"] is True
    assert json.loads(rp2["additions"])["context_texts"] == ["阴冷诡异地低语"]

    rp1 = doubao.build_req_params(
        {"text": "你好", "emotion": "angry", "emotion_scale": 5},
        voice="zh_female_wanwanxiaohe_moon_bigtts",
        resource_id=doubao.RESOURCE_TTS_1,
    )
    ap1 = rp1["audio_params"]
    assert ap1["emotion"] == "angry" and ap1["emotion_scale"] == 5
    assert ap1["enable_timestamp"] is True
    assert "context_texts" not in json.loads(rp1["additions"])


class _FakeResponse:
    def __init__(self, status: int, text: str, logid: str = "") -> None:
        self.status = status
        self._text = text
        self.headers = {"X-Tt-Logid": logid}

    def read(self) -> bytes:
        return self._text.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_urlopen(monkeypatch, responses: list[_FakeResponse]):
    calls: list[dict] = []

    def fake_urlopen(req, timeout=0):
        calls.append({"url": req.full_url, "headers": dict(req.header_items())})
        resp = responses.pop(0) if responses else _FakeResponse(500, "boom")
        if resp.status >= 400:
            import urllib.error

            raise urllib.error.HTTPError(req.full_url, resp.status, "err", resp.headers, None)
        return resp

    monkeypatch.setattr(doubao.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_doubao_execute_success_stream(monkeypatch, tmp_path):
    monkeypatch.setenv("DOUBAO_SPEECH_API_KEY", "ark-test-key")
    audio_b64 = base64.b64encode(b"ID3fakeaudio").decode()
    stream = (
        f'{{"code":0,"data":"{audio_b64}"}}'
        '{"code":0,"sentence":{"text":"测试。","words":['
        '{"word":"测","startTime":0.0,"endTime":0.3},'
        '{"word":"试。","startTime":0.3,"endTime":0.9}]}}'
        '{"code":20000000,"message":"ok","usage":{"text_words":2}}'
    )
    calls = _patch_urlopen(monkeypatch, [_FakeResponse(200, stream, "log-1")])
    out = tmp_path / "a.mp3"
    result = doubao.DoubaoTTS().execute({"text": "测试", "output_path": str(out)})
    assert result.success
    assert out.read_bytes() == b"ID3fakeaudio"
    assert result.data["audio_duration_seconds"] == 0.9
    assert result.data["sentences"][0]["end_seconds"] == 0.9
    assert result.data["usage"] == {"text_words": 2}
    assert calls[0]["url"] == doubao.PLAN_UNIDIRECTIONAL_URL  # ark- 前缀 → plan 端点
    hdrs = {k.lower(): v for k, v in calls[0]["headers"].items()}
    assert hdrs["x-control-require-usage-tokens-return"] == "*"
    assert hdrs["x-api-resource-id"] == doubao.RESOURCE_TTS_2  # 默认音色是 xiaohe_uranus (2.0)


def test_doubao_no_retry_on_auth_error(monkeypatch):
    """401/403/45000000 不重试（重试无意义）。"""
    monkeypatch.setenv("DOUBAO_SPEECH_API_KEY", "k")
    responses = [_FakeResponse(401, "unauthorized"), _FakeResponse(401, "unauthorized")]
    _patch_urlopen(monkeypatch, responses)
    result = doubao.DoubaoTTS().execute({"text": "你好", "retries": 1})
    assert not result.success
    assert "401" in result.error
    assert len(responses) == 1  # 只消费第一个响应，未重试


def test_doubao_retry_on_5xx(monkeypatch, tmp_path):
    monkeypatch.setenv("DOUBAO_SPEECH_API_KEY", "k")
    audio_b64 = base64.b64encode(b"audio").decode()
    stream = f'{{"code":0,"data":"{audio_b64}"}}{{"code":20000000}}'
    _patch_urlopen(monkeypatch, [_FakeResponse(500, "boom"), _FakeResponse(200, stream)])
    result = doubao.DoubaoTTS().execute({"text": "你好", "output_path": str(tmp_path / "b.mp3")})
    assert result.success


def test_doubao_stream_error_code_surfaces(monkeypatch):
    """业务错误码（45000000 音色未授权）透出并带提示。"""
    monkeypatch.setenv("DOUBAO_SPEECH_API_KEY", "k")
    stream = '{"code":45000000,"message":"speaker permission denied"}'
    _patch_urlopen(monkeypatch, [_FakeResponse(200, stream)])
    result = doubao.DoubaoTTS().execute({"text": "你好"})
    assert not result.success
    assert "45000000" in result.error
    assert "音色" in result.error


# --- Edge TTS ------------------------------------------------------------

def test_edge_tts_status_reflects_dep():
    # 不检查网络；只验证状态枚举合法且无密钥要求
    status = edge_tts.EdgeTTS().get_status()
    assert status in (ToolStatus.AVAILABLE, ToolStatus.UNAVAILABLE)


def test_edge_tts_requires_text():
    result = edge_tts.EdgeTTS().execute({})
    assert not result.success
