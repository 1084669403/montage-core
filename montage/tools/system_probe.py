"""system_probe — 本机硬件档案（纯标准库，可注入探针便于测试）。

与 compose.profiles.MediaProfile 分工：后者是「目标平台交付参数」，
本模块只回答两件事：
- can_hardware_encode() → {preset, crf} 建议
- can_run(t2_feature) → bool（MVP 对全部 T2 功能返回 False）

探针 ``probe_gpu`` / ``probe_cpu`` / ``probe_memory`` 是模块级函数，测试可直接替换。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import Any, Callable

from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

T2_FEATURES: frozenset[str] = frozenset({
    "clip_search",
    "word_timestamps",
    "semantic_search",
    "single_image_matting",
    "super_resolution",
})


# ---------------------------------------------------------------------------
# 可注入探针
# ---------------------------------------------------------------------------


def probe_cpu() -> dict[str, Any]:
    return {"logical_cpus": os.cpu_count() or 1, "machine": platform.machine()}


def probe_memory() -> dict[str, Any]:
    """总物理内存（字节）。失败时 total_bytes=0。"""
    try:
        pages = os.sysconf("SC_PHYS_PAGES")  # type: ignore[attr-defined]
        page = os.sysconf("SC_PAGE_SIZE")  # type: ignore[attr-defined]
        if pages and page:
            return {"total_bytes": int(pages) * int(page), "source": "sysconf"}
    except (ValueError, OSError, AttributeError):
        pass
    if platform.system() == "Windows":
        try:
            import ctypes

            class _MEM(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = _MEM()
            stat.dwLength = ctypes.sizeof(_MEM)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
                return {"total_bytes": int(stat.ullTotalPhys), "source": "GlobalMemoryStatusEx"}
        except Exception:  # noqa: BLE001
            pass
    if platform.system() == "Darwin":
        try:
            proc = subprocess.run(  # noqa: S603
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0:
                return {"total_bytes": int(proc.stdout.strip()), "source": "sysctl"}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return {"total_bytes": 0, "source": "unknown"}


def probe_gpu() -> dict[str, Any]:
    """NVIDIA 有则返回 name；否则 empty。不依赖 ctypes 解析 DXGI。"""
    binary = shutil.which("nvidia-smi")
    if not binary:
        return {"available": False, "name": "", "source": "none"}
    try:
        proc = subprocess.run(  # noqa: S603
            [binary, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "name": "", "source": "nvidia-smi-fail"}
    name = (proc.stdout or "").strip().splitlines()
    if proc.returncode == 0 and name:
        return {"available": True, "name": name[0].strip(), "source": "nvidia-smi"}
    return {"available": False, "name": "", "source": "nvidia-smi"}


# ---------------------------------------------------------------------------
# 档案与判定
# ---------------------------------------------------------------------------


def can_hardware_encode(
    mem: dict[str, Any] | None = None,
    gpu: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """低内存更保守（veryfast + 更高 crf）；有独显也不改编码器（MVP 仍 libx264）。"""
    mem = mem if mem is not None else probe_memory()
    gpu = gpu if gpu is not None else probe_gpu()
    total = int(mem.get("total_bytes") or 0)
    low_ram = total > 0 and total < 4 * 1024 ** 3
    if low_ram:
        return {"preset": "veryfast", "crf": 23, "encoder": "libx264", "low_ram": True}
    return {
        "preset": "medium",
        "crf": 18,
        "encoder": "libx264",
        "low_ram": False,
        "gpu": bool(gpu.get("available")),
    }


def can_run(feature: str) -> bool:
    """MVP：T2 清单内一律 False（真实探针映射推迟到 M+2）。未知功能也 False。"""
    if feature not in T2_FEATURES:
        return False
    return False


def build_hardware_profile(
    *,
    probe_cpu_fn: Callable[[], dict[str, Any]] = probe_cpu,
    probe_memory_fn: Callable[[], dict[str, Any]] = probe_memory,
    probe_gpu_fn: Callable[[], dict[str, Any]] = probe_gpu,
) -> dict[str, Any]:
    cpu = probe_cpu_fn()
    mem = probe_memory_fn()
    gpu = probe_gpu_fn()
    encode = can_hardware_encode(mem, gpu)
    return {
        "os": platform.system(),
        "cpu": cpu,
        "memory": mem,
        "gpu": gpu,
        "encode": encode,
        "t2": {name: False for name in sorted(T2_FEATURES)},
    }


class SystemProbe(BaseTool):
    """本机硬件档案：零依赖，恒 AVAILABLE。"""

    name = "system_probe"
    version = "0.1.0"
    capability = "system"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "properties": {
            "feature": {
                "type": "string",
                "description": "可选 T2 功能名，只查 can_run；缺省返回完整 hardware_profile",
            },
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        feature = inputs.get("feature")
        if feature:
            return ToolResult(
                success=True,
                data={"feature": feature, "can_run": can_run(str(feature))},
            )
        profile = build_hardware_profile()
        return ToolResult(success=True, data={"hardware_profile": profile})
