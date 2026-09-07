"""M4 增强工具测试：超分/去背/人脸修复（可选依赖 + 降级路径）。"""

import sys
import types
from pathlib import Path

from montage.tools import enhance
from montage.tools.enhance import BgRemover, FaceRestorer, Upscaler


def test_upscaler_uses_realesrgan(monkeypatch, tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"x")
    captured: list[list[str]] = []
    monkeypatch.setattr(enhance, "_realesrgan_binary", lambda: "realesrgan")
    monkeypatch.setattr(enhance, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    result = Upscaler().execute({"path": str(src), "output_path": str(tmp_path / "u.png"), "scale": 4})
    assert result.success
    assert result.data["engine"] == "realesrgan-ncnn-vulkan"
    assert "-s" in captured[0] and "4" in captured[0]


def test_upscaler_fallback_ffmpeg(monkeypatch, tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"x")
    captured: list[list[str]] = []
    monkeypatch.setattr(enhance, "_realesrgan_binary", lambda: None)
    monkeypatch.setattr(enhance, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(enhance, "check_ffmpeg", lambda: "ffmpeg")
    result = Upscaler().execute({"path": str(src), "output_path": str(tmp_path / "u.png"), "scale": 2})
    assert result.success
    assert result.data["engine"] == "ffmpeg-lanczos"
    assert "lanczos" in " ".join(captured[0])


def test_upscaler_nothing_available(monkeypatch, tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"x")
    monkeypatch.setattr(enhance, "_realesrgan_binary", lambda: None)
    monkeypatch.setattr(enhance, "check_ffmpeg", lambda: None)
    result = Upscaler().execute({"path": str(src), "output_path": str(tmp_path / "u.png")})
    assert not result.success
    assert "realesrgan" in result.error


def _fake_rembg_module() -> types.ModuleType:
    mod = types.ModuleType("rembg")
    mod.remove = lambda data: b"REMOVED"
    return mod


def test_bg_remover_uses_rembg(monkeypatch, tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"PNG-DATA")
    monkeypatch.setattr(enhance, "_rembg_available", lambda: True)
    monkeypatch.setattr(sys, "modules", {**sys.modules, "rembg": _fake_rembg_module()})
    result = BgRemover().execute({"path": str(src), "output_path": str(tmp_path / "r.png")})
    assert result.success
    assert result.data["engine"] == "rembg"
    assert (tmp_path / "r.png").read_bytes() == b"REMOVED"


def test_bg_remover_chromakey_fallback(monkeypatch, tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    captured: list[list[str]] = []
    monkeypatch.setattr(enhance, "_rembg_available", lambda: False)
    monkeypatch.setattr(enhance, "check_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(enhance, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    result = BgRemover().execute({
        "path": str(src), "output_path": str(tmp_path / "r.webm"),
        "method": "chromakey", "key_color": "0x00FF00",
    })
    assert result.success
    assert result.data["engine"] == "chromakey"
    assert "chromakey=0x00FF00" in " ".join(captured[0])


def test_bg_remover_nothing_available(monkeypatch, tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"x")
    monkeypatch.setattr(enhance, "_rembg_available", lambda: False)
    monkeypatch.setattr(enhance, "check_ffmpeg", lambda: None)
    result = BgRemover().execute({"path": str(src), "output_path": str(tmp_path / "r.png")})
    assert not result.success
    assert "rembg" in result.error


def test_face_restorer_unavailable(monkeypatch, tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"x")
    monkeypatch.setattr(enhance, "_gfpgan_available", lambda: False)
    result = FaceRestorer().execute({"path": str(src), "output_path": str(tmp_path / "f.png")})
    assert not result.success
    assert "gfpgan" in result.error
    assert FaceRestorer().get_status().value == "unavailable"


def test_face_restorer_missing_input(tmp_path):
    result = FaceRestorer().execute({"path": str(tmp_path / "nope.png"), "output_path": str(tmp_path / "f.png")})
    assert not result.success


def test_m4_tools_discovered():
    from montage.registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    for name in ("upscaler", "bg_remover", "face_restorer"):
        assert reg.get(name) is not None, name
