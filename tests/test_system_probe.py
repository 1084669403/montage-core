"""SystemProbe：可注入探针 + T2 一律 False + 低内存 encode 建议。"""

from montage.registry import ToolRegistry
from montage.tools import system_probe as sp
from montage.tools.system_probe import (
    T2_FEATURES,
    SystemProbe,
    build_hardware_profile,
    can_hardware_encode,
    can_run,
)


def test_t2_list_stable():
    assert T2_FEATURES == {
        "clip_search",
        "word_timestamps",
        "semantic_search",
        "single_image_matting",
        "super_resolution",
    }


def test_can_run_always_false():
    for name in T2_FEATURES:
        assert can_run(name) is False
    assert can_run("not_a_feature") is False


def test_low_ram_conservative_encode():
    enc = can_hardware_encode(
        mem={"total_bytes": 2 * 1024 ** 3},
        gpu={"available": False},
    )
    assert enc["preset"] == "veryfast"
    assert enc["crf"] == 23
    assert enc["low_ram"] is True


def test_normal_ram_medium_encode():
    enc = can_hardware_encode(
        mem={"total_bytes": 16 * 1024 ** 3},
        gpu={"available": True, "name": "mock"},
    )
    assert enc["preset"] == "medium"
    assert enc["crf"] == 18
    assert enc["low_ram"] is False


def test_build_profile_injectable():
    profile = build_hardware_profile(
        probe_cpu_fn=lambda: {"logical_cpus": 4, "machine": "x86_64"},
        probe_memory_fn=lambda: {"total_bytes": 8 * 1024 ** 3, "source": "mock"},
        probe_gpu_fn=lambda: {"available": False, "name": "", "source": "none"},
    )
    assert profile["cpu"]["logical_cpus"] == 4
    assert profile["encode"]["preset"] == "medium"
    assert profile["t2"]["clip_search"] is False
    assert set(profile["t2"]) == set(T2_FEATURES)


def test_win_linux_mac_os_field(monkeypatch):
    for name in ("Windows", "Linux", "Darwin"):
        monkeypatch.setattr(sp.platform, "system", lambda n=name: n)
        profile = build_hardware_profile(
            probe_cpu_fn=lambda: {"logical_cpus": 2, "machine": "arm"},
            probe_memory_fn=lambda: {"total_bytes": 8 * 1024 ** 3, "source": "mock"},
            probe_gpu_fn=lambda: {"available": False, "name": "", "source": "none"},
        )
        assert profile["os"] == name


def test_tool_status_and_execute():
    tool = SystemProbe()
    assert tool.get_status().value == "available"
    assert tool.provider == "openmontage"
    assert tool.capability == "system"
    assert tool.estimate_cost({}) == 0.0
    result = tool.execute({})
    assert result.success
    assert "hardware_profile" in result.data
    feature = tool.execute({"feature": "clip_search"})
    assert feature.success and feature.data["can_run"] is False


def test_discover_finds_system_probe():
    reg = ToolRegistry()
    reg.discover()
    assert reg.get("system_probe") is SystemProbe
