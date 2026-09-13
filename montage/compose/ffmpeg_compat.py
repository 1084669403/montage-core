"""ffmpeg_compat — FFmpeg 版本与能力探测（进程内缓存）。

本模块集中处理"换机器必炸"的滤镜/选项差异。此前全仓 0 处版本探测，
ffmpeg 9 一等升级（``lut3d file=``、``aformat channel_layouts``、
``xfade`` 无 ``cut``）只能靠人肉排障。这里提供两件事：

- ``ffmpeg_version()``：解析 ``ffmpeg -version`` 首行版本号（缓存）。
- ``supports(cap)``：对少数敏感点做一次真实探测并缓存，调用方据此选择
  滤镜选项名。已知能力见 ``_PROBES``。

探测结果只反映**本机** ffmpeg，不跨进程缓存；测试可用
``reset_cache()`` 清空。若本机没有 ffmpeg，``supports`` 对"现代选项名"
类能力返回 True（默认取现代行为，避免拖垮只构造命令字符串的单元测试）；
真正调用 render 时 ``check_ffmpeg()`` 仍会先报缺 ffmpeg。
"""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import lru_cache
from typing import Callable

_PROBE_TIMEOUT = 20

# 探测结果只对当前进程有效；键与调用方使用的能力名一致。
_PROBES: dict[str, Callable[[str], bool]] = {}


def ffmpeg_binary() -> str | None:
    return shutil.which("ffmpeg")


@lru_cache(maxsize=1)
def ffmpeg_version() -> str | None:
    """返回 ``ffmpeg -version`` 首行的版本号（如 ``9.0``），缺 ffmpeg 返回 None。"""
    ff = ffmpeg_binary()
    if not ff:
        return None
    try:
        proc = subprocess.run(  # noqa: S603
            [ff, "-version"], capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    first = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
    match = re.search(r"ffmpeg version (\S+)", first)
    if match:
        return match.group(1)
    return first.strip() or None


def _run_ok(cmd: list[str]) -> bool:
    try:
        proc = subprocess.run(  # noqa: S603
            cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _filter_has_option(filter_name: str, option: str) -> bool:
    """``ffmpeg -h filter=NAME`` 里是否有某选项（如 lut3d 的 ``file``）。"""
    ff = ffmpeg_binary()
    if not ff:
        return False
    try:
        proc = subprocess.run(  # noqa: S603
            [ff, "-hide_banner", "-h", f"filter={filter_name}"],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    text = (proc.stdout or "") + (proc.stderr or "")
    # 选项行形如 ``  file              <string>     ..FV....... set 3D LUT file name``
    return re.search(rf"^\s*{re.escape(option)}\s+<", text, re.MULTILINE) is not None


def _supports_lut3d_file(_ff: str) -> bool:
    """lut3d 选项名是 ``file``（vf_lut3d.c 自 2.4 起）而非 ``filename``。"""
    return _filter_has_option("lut3d", "file")


def _supports_aformat(_ff: str) -> bool:
    """aformat 的 ``channel_layouts`` 稳定；aresample 的 ``cl``/``ochl`` 多变。"""
    return _run_ok([
        _ff, "-hide_banner", "-v", "error",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-af", "aformat=sample_rates=48000:channel_layouts=stereo",
        "-t", "0.05", "-f", "null", "-",
    ])


def _supports_xfade_cut(ff: str) -> bool:
    """ffmpeg 9 的 xfade 没有 ``transition=cut``（硬切走 concat）。"""
    return _run_ok([
        ff, "-hide_banner", "-v", "error",
        "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1:r=30",
        "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1:r=30",
        "-filter_complex",
        "[0:v][1:v]xfade=transition=cut:duration=0.1:offset=0.9[v]",
        "-map", "[v]", "-frames:v", "1", "-f", "null", "-",
    ])


# 能力名 → 探测函数。新增能力时同时在此登记。
_PROBES.update({
    "lut3d_file": _supports_lut3d_file,
    "aformat": _supports_aformat,
    "xfade_cut": _supports_xfade_cut,
})

# 缺 ffmpeg 时这些能力按"现代选项名可用"处理：单元测试常只断言命令字符串，
# 不应因 CI 无 ffmpeg 就退化到旧选项名。真实渲染前 check_ffmpeg() 会拦。
_DEFAULT_WHEN_MISSING = {"lut3d_file": True, "aformat": True, "xfade_cut": True}


@lru_cache(maxsize=None)
def supports(cap: str) -> bool:
    """``cap`` 在本机 ffmpeg 是否可用（详见 ``_PROBES`` 与 ``_DEFAULT_WHEN_MISSING``）。"""
    probe_fn = _PROBES.get(cap)
    if probe_fn is None:
        raise KeyError(f"未知 ffmpeg 能力: {cap!r}（可选: {sorted(_PROBES)}）")
    ff = ffmpeg_binary()
    if not ff:
        return bool(_DEFAULT_WHEN_MISSING.get(cap, False))
    try:
        return bool(probe_fn(ff))
    except (OSError, subprocess.SubprocessError):
        return bool(_DEFAULT_WHEN_MISSING.get(cap, False))


def reset_cache() -> None:
    """清空进程内缓存（测试用）。"""
    ffmpeg_version.cache_clear()
    supports.cache_clear()


def capabilities_snapshot() -> dict[str, object]:
    """版本 + 能力快照，写进 render_report.json 便于事后归因。"""
    return {
        "ffmpeg_version": ffmpeg_version(),
        "capabilities": {name: supports(name) for name in sorted(_PROBES)},
    }
