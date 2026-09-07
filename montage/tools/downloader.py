"""downloader — 素材下载工具（100% 自创实现）。

- ``Downloader``：从 URL 下载素材（图片/音频/视频）到项目 assets 目录，
  带 UA 头（部分 CDN 拒绝无 UA 请求）；可选校验下载后文件可解析。
  用于素材源（pixabay/pexels 等）与供应商返回的临时 URL 落盘。
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_engine import check_ffprobe, probe
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

_USER_AGENT = "montage-core/0.1 (AI video production; +https://github.com/montage-core)"
_MAX_BYTES = 200 * 1024 * 1024
_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.internal",
}


def _host_blocked(host: str) -> bool:
    name = (host or "").strip().lower().rstrip(".")
    if not name or name in _BLOCKED_HOSTS or name.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        ip = None
    if ip is not None:
        return (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        )
    try:
        infos = socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)
    except OSError:
        return False
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def assert_download_url(url: str) -> urllib.parse.ParseResult:
    """只允许公网 http/https；拒绝 file://、环回、元数据与内网。"""
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme not in ("http", "https"):
        raise RuntimeError("只允许 http/https 下载")
    host = parsed.hostname or ""
    if _host_blocked(host):
        raise RuntimeError("拒绝内网或元数据地址")
    return parsed


def download_file(url: str, output: str | Path, *, timeout: int = 120) -> Path:
    """下载 URL 到本地（UA + 超时 + 200MB 上限）；失败抛 RuntimeError。"""
    assert_download_url(url)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310
        try:
            data = resp.read(_MAX_BYTES + 1)
        except TypeError:
            data = resp.read()
        if len(data) > _MAX_BYTES:
            raise RuntimeError("下载超过 200MB")
    output.write_bytes(data)
    return output


def _looks_like_html(path: Path) -> bool:
    head = path.read_bytes()[:512].lstrip().lower()
    return head.startswith(b"<") or b"<html" in head or b"<!doctype" in head


def download_verified(
    url: str,
    dest: str | Path,
    *,
    timeout: int = 120,
    download_fn=download_file,
    probe_fn=None,
) -> Path:
    """下载到临时文件，校验为媒体后再替换到 dest；失败不留半截文件。"""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():
        tmp.unlink()
    try:
        download_fn(url, tmp, timeout=timeout)
        if tmp.stat().st_size < 64 or _looks_like_html(tmp):
            raise RuntimeError("下载结果不是音频/视频文件（可能是网页）")
        checker = probe_fn
        if checker is None and check_ffprobe() is not None:
            checker = probe
        if checker is not None:
            checker(tmp)
        os.replace(tmp, dest)
        return dest
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


class Downloader(BaseTool):
    """URL → 本地素材下载（可校验可解析）。"""

    name = "downloader"
    version = "0.1.0"
    capability = "download"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "required": ["url", "output_path"],
        "properties": {
            "url": {"type": "string"},
            "output_path": {"type": "string"},
            "timeout": {"type": "integer", "default": 120},
            "verify": {"type": "boolean", "default": False},
        },
    }

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        url = str(inputs.get("url") or "")
        output = str(inputs.get("output_path") or "")
        if not url or not output:
            return ToolResult(success=False, error="'url' 与 'output_path' 必填")
        timeout = int(inputs.get("timeout", 120))
        try:
            path = download_file(url, output, timeout=timeout)
            data: dict[str, Any] = {"path": str(path), "size_bytes": path.stat().st_size}
            if inputs.get("verify"):
                probed = probe(path)
                data["verified"] = True
                data["duration_seconds"] = float((probed.get("format") or {}).get("duration") or 0) or None
            return ToolResult(success=True, data=data)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"下载失败: {exc}")
