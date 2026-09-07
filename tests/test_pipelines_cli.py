"""管线配置 + 工具发现 + CLI 冒烟测试。"""

from montage.engine.stages import STAGE_ORDER
from montage.pipelines import CINEMATIC, get_pipeline, list_pipelines
from montage.registry import ToolRegistry


def test_cinematic_stages_match_engine_order():
    names = [s["name"] for s in CINEMATIC["stages"]]
    assert names == list(STAGE_ORDER)


def test_gated_flags_sane():
    gated = {s["name"] for s in CINEMATIC["stages"] if s["gated"]}
    assert "script" in gated and "compose" not in gated


def test_get_pipeline():
    assert get_pipeline("cinematic") is CINEMATIC
    assert get_pipeline("nope") is None
    assert "cinematic" in list_pipelines()


def test_discovery_finds_core_tools():
    reg = ToolRegistry()
    reg.discover()
    for name in (
        "prompt_library_retriever", "visual_prompt_builder", "edit_advisor",
        "auto_edit", "system_probe", "ffmpeg_compose",
    ):
        assert reg.get(name) is not None, name


def test_menu_contains_prompt_engineering():
    reg = ToolRegistry()
    reg.discover()
    caps = {c["capability"] for c in reg.provider_menu_summary()["capabilities"]}
    assert "prompt_engineering" in caps


def test_cli_init(tmp_path):
    from montage.cli import main

    code = main(["init", "demo-run", "--title", "演示", "--root", str(tmp_path)])
    assert code == 0
    assert (tmp_path / "projects" / "demo-run" / "project.json").exists()


def test_cli_check_gate_violation(tmp_path):
    from montage.cli import main

    root = str(tmp_path / "projects" / "demo-run")
    main(["init", "demo-run", "--root", str(tmp_path)])
    code = main(["check", root, "script", "--completed"])  # 未审批
    assert code == 2


def test_assets_stage_includes_retriever():
    from montage.pipelines import CINEMATIC

    assets_tools = next(s["tools"] for s in CINEMATIC["stages"] if s["name"] == "assets")
    assert "asset_retriever" in assets_tools
    scene_tools = next(s["tools"] for s in CINEMATIC["stages"] if s["name"] == "scene_plan")
    assert "edit_advisor" in scene_tools


def test_edit_decisions_in_compose_produces():
    from montage.pipelines import CINEMATIC

    compose = next(s for s in CINEMATIC["stages"] if s["name"] == "compose")
    assert "edit_decisions" in compose["produces"]


def test_publish_tools_export_bundle_not_subtitles():
    from montage.pipelines import CINEMATIC, CLIP_FACTORY, DOCUMENTARY

    cine = next(s["tools"] for s in CINEMATIC["stages"] if s["name"] == "publish")
    doc = next(s["tools"] for s in DOCUMENTARY["stages"] if s["name"] == "publish")
    clip = next(s["tools"] for s in CLIP_FACTORY["stages"] if s["name"] == "publish")
    assert cine == ["export_bundle", "film_health"]
    assert doc == ["export_bundle", "film_health"]
    assert clip == ["export_bundle", "film_health"]
    for tools in (cine, doc, clip):
        assert "subtitle_builder" not in tools


def test_cli_auto_edit_unknown_style(tmp_path):
    from montage.cli import main

    vid = tmp_path / "x.mp4"
    vid.write_bytes(b"x")
    proj = tmp_path / "inplace"
    code = main(["auto_edit", str(proj), "--video", str(vid), "--style", "nope"])
    assert code == 2
    assert not (proj / "project.json").exists()


def test_cli_auto_edit_inplace_missing_video(tmp_path):
    from montage.cli import main

    proj = tmp_path / "cut-here"
    code = main([
        "auto_edit", str(proj),
        "--video", str(tmp_path / "missing.mp4"),
        "--style", "documentary",
    ])
    assert code == 2
    assert (proj / "project.json").exists()
    assert (proj / "auto_edit").is_dir()
    assert (proj / "tmp_autoedit").is_dir()
    # 原地建项，不走到 root/projects/<id>
    assert not (tmp_path / "projects").exists()


def test_cli_auto_edit_bad_overrides(tmp_path):
    from montage.cli import main

    code = main([
        "auto_edit", str(tmp_path / "p"),
        "--video", "a.mp4",
        "--overrides", "{not json",
    ])
    assert code == 2


def test_cli_auto_edit_mutex(tmp_path):
    import pytest
    from montage.cli import main

    with pytest.raises(SystemExit):
        main(["auto_edit", str(tmp_path), "--video", "a.mp4", "--clips", "b.mp4"])
