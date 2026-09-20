"""供应商框架测试：HTTP 助手、选型器、Agnes/DashScope 状态与校验。"""

import pytest

from montage.providers import agnes, dashscope, http, selectors
from montage.registry import ToolRegistry
from montage.toolbase import ToolResult, ToolStatus


def test_http_error_has_status():
    # 本地起一个拒绝连接的地址 → HttpError(status=0)
    with pytest.raises(http.HttpError):
        http.post_json("http://127.0.0.1:1/nope", {"a": 1}, timeout=2)


def test_http_error_redacts_secrets():
    err = http.HttpError(401, "https://api.example/v1?api_key=secret&token=abc", "nope")
    assert "secret" not in str(err)
    assert "abc" not in str(err)
    assert "api_key=***" in str(err)
    assert "token=***" in err.url


def test_agnes_image_missing_key(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("AGNES_CN_API_KEY", raising=False)
    tool = agnes.AgnesImage()
    result = tool.execute({"prompt": "测试"})
    assert not result.success
    assert "AGNES" in result.error


def test_agnes_image_requires_prompt(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "test-key")
    result = agnes.AgnesImage().execute({})
    assert not result.success


def test_agnes_edit_requires_reference(monkeypatch):
    monkeypatch.setenv("AGNES_API_KEY", "test-key")
    result = agnes.AgnesImage().execute({"prompt": "x", "operation": "image_edit"})
    assert not result.success
    assert "reference_urls" in result.error


def test_agnes_status(monkeypatch):
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    assert agnes.AgnesImage().get_status() == ToolStatus.AVAILABLE
    monkeypatch.delenv("AGNES_CN_API_KEY")
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    assert agnes.AgnesImage().get_status() == ToolStatus.NEEDS_CONFIG


def test_agnes_credentials_cn_default(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("AGNES_API_BASE_URL", raising=False)
    monkeypatch.setenv("AGNES_CN_API_KEY", "k")
    key, base = agnes.agnes_credentials()
    assert "cn" in base


def test_dashscope_asr_missing_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    result = dashscope.DashscopeAsr().execute({"file_urls": ["http://x/a.wav"]})
    assert not result.success


def test_dashscope_asr_requires_urls(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")
    result = dashscope.DashscopeAsr().execute({})
    assert not result.success


def test_dashscope_finalize_normalizes_timestamps():
    resp = {
        "output": {
            "results": [
                {
                    "transcripts": [
                        {
                            "sentences": [
                                {
                                    "text": "你好世界",
                                    "begin_time": 0,
                                    "end_time": 2000,
                                    "words": [
                                        {"text": "你好", "begin_time": 0, "end_time": 800},
                                        {"text": "世界", "begin_time": 800, "end_time": 2000},
                                    ],
                                }
                            ]
                        }
                    ]
                }
            ]
        }
    }
    result = dashscope.DashscopeAsr._finalize("SUCCEEDED", resp, "task1")
    assert result.success
    assert result.data["word_count"] == 2
    assert result.data["sentences"][0]["words"][0]["start_seconds"] == 0.0
    assert result.data["sentences"][0]["words"][1]["end_seconds"] == 2.0


def test_selectors_route_or_report_missing(monkeypatch):
    monkeypatch.delenv("AGNES_API_KEY", raising=False)
    monkeypatch.delenv("AGNES_CN_API_KEY", raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    reg = ToolRegistry()
    reg.discover()
    sel = selectors.ImageSelector()
    result = sel.execute({"prompt": "x"})
    # 没有可用图片供应商 → 失败并给出原因
    assert not result.success
    assert "没有可用" in result.error or "AGNES" in result.error
