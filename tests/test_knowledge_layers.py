"""知识资产层测试：公有领域剧本库 / 资产目录检索 / asset_retriever / apply_lut。

- screenplays / scripts 扩展：验证解析、检索、类别过滤（纯逻辑，零依赖）。
- asset_catalog：验证解析（sfx/bgm/luts）、情绪/bpm 检索、available 标记。
- asset_retriever 工具：验证参数透传与返回结构。
- apply_lut：mock `_run` 验证 ffmpeg 命令构造（沙箱禁止子进程管道）；
  真实 ffmpeg 冒烟用 MONTAGE_REAL_FFMPEG=1 门控（同 test_compose.py 约定）。
"""

import os
from pathlib import Path

from lib import asset_catalog as ac
from lib import prompt_library as pl
from montage.compose import ffmpeg_engine as fe
from montage.tools.asset_retriever import AssetRetriever


# ---------------------------------------------------------------------------
# 公有领域剧本库（prompt_library/screenplays）
# ---------------------------------------------------------------------------


def test_screenplays_registered():
    lib = pl.get_library()
    cats = {c["id"]: c["count"] for c in lib.categories()}
    assert cats.get("screenplays") == 12
    assert cats.get("scripts") == 15


def test_screenplays_search_by_scene_type():
    hits = pl.search("茶馆 对峙 冲突", category="screenplays", top_k=3)
    assert hits
    ids = [h["id"] for h in hits]
    assert "screenplays/confrontation-ideology" in ids
    assert all(h["category"] == "screenplays" for h in hits)


def test_screenplays_search_shot_kind_filter():
    # shot_kind=image 也应命中（剧本范式 both），并带出处字段
    hits = pl.search("誓言 冤屈", shot_kind="image", top_k=5)
    ids = [h["id"] for h in hits]
    assert "screenplays/vow-impossible" in ids
    entry = next(h for h in hits if h["id"] == "screenplays/vow-impossible")
    assert "关汉卿" in entry["title"] or "窦娥冤" in entry["notes"]


def test_screenplays_entries_have_public_domain_notes():
    lib = pl.get_library()
    for entry in lib.entries:
        if entry.category != "screenplays":
            continue
        # 每条剧本范式都必须在 notes 标注出处与公有领域依据
        assert entry.notes, f"{entry.id} 缺少 notes"
        assert entry.source  # source 字段兜底


def test_scripts_new_structure_entries():
    hits = pl.search("三幕 结构", category="scripts", top_k=3)
    ids = [h["id"] for h in hits]
    assert "scripts/three-act-frame" in ids
    hits2 = pl.search("英雄之旅", category="scripts", top_k=3)
    assert "scripts/hero-journey" in [h["id"] for h in hits2]


# ---------------------------------------------------------------------------
# 资产目录（lib/asset_catalog）
# ---------------------------------------------------------------------------


def test_asset_catalog_parses_all_subdirs():
    cat = ac.get_catalog()
    counts = {c["id"]: c["count"] for c in cat.categories()}
    assert counts.get("sfx", 0) >= 10
    assert counts.get("bgm", 0) >= 10
    assert counts.get("luts") == 6
    assert counts.get("fonts", 0) == 0  # 指引型子库，无条目


def test_asset_catalog_luts_available():
    hits = ac.search("电影感", category="luts", top_k=3)
    assert hits
    assert all(h["available"] for h in hits)  # luts 随仓库分发
    assert any(h["id"] == "luts/teal-orange" for h in hits)


def test_asset_catalog_bpm_filter():
    hits = ac.search("卡点", category="bgm", min_bpm=110, top_k=10)
    assert hits
    assert all(h["bpm"] >= 110 for h in hits)
    assert any(h["id"] == "bgm/techno-beat" for h in hits)


def test_asset_catalog_emotion_mood_filter():
    hits = ac.search("", category="sfx", emotion="紧张", top_k=10)
    ids = [h["id"] for h in hits]
    assert "sfx/rain-heavy" in ids or "sfx/heartbeat-tense" in ids


def test_asset_catalog_metadata_fields():
    hits = ac.search("追逐", category="bgm", top_k=1)
    assert hits
    h = hits[0]
    for key in ("license", "source", "source_url", "file", "available", "attribution", "download_hint"):
        assert key in h, f"缺少字段 {key}"
    assert h["file"] == "bgm/tense-pulse.mp3" or h["file"].startswith("bgm/")
    assert not h["tags"][0].startswith("[")
    assert not h["tags"][-1].endswith("]")


# ---------------------------------------------------------------------------
# asset_retriever 工具
# ---------------------------------------------------------------------------


def test_asset_retriever_basic():
    result = AssetRetriever().execute({"query": "雨夜 紧张", "top_k": 3})
    assert result.success
    assert result.data["count"] >= 1
    assert "hits" in result.data


def test_asset_retriever_category_and_bpm():
    result = AssetRetriever().execute(
        {"query": "卡点", "category": "bgm", "min_bpm": 110, "top_k": 3}
    )
    assert result.success
    assert all(h["category"] == "bgm" and h["bpm"] >= 110 for h in result.data["hits"])


def test_asset_retriever_available_only():
    result = AssetRetriever().execute({"query": "电影感", "category": "luts", "available_only": True})
    assert result.success
    assert result.data["hits"] and all(h["available"] for h in result.data["hits"])


def test_asset_retriever_registry_discovered():
    from montage.registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    assert reg.get("asset_retriever") is not None


def _mini_bgm_index(root: Path, *, source_url: str = "https://example.test/drone.mp3") -> None:
    d = root / "bgm"
    d.mkdir(parents=True, exist_ok=True)
    (d / "INDEX.md").write_text(
        f"""
- id: `bgm/dark-drone`
  category: bgm
  title: drone
  tags: [drone]
  emotion: 压抑
  mood: 悬疑
  bpm: 60
  duration_seconds: 120
  license: CC0
  source: test
  file: bgm/dark-drone.mp3
  source_url: "{source_url}"
  attribution: ""
  notes: x
""",
        encoding="utf-8",
    )


def test_resolve_downloads_then_skips_existing(tmp_path):
    _mini_bgm_index(tmp_path)
    cat = ac.AssetCatalog(tmp_path)
    calls = {"n": 0}

    def fake_dl(url, output, timeout=120):
        calls["n"] += 1
        p = Path(output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"ID3" + b"\x00" * 200)
        return p

    tool = AssetRetriever(
        catalog=cat, download_fn=fake_dl, probe_fn=lambda p: {"format": {"duration": "8"}}
    )
    first = tool.execute({"operation": "resolve", "asset_id": "bgm/dark-drone"})
    assert first.success
    assert first.data["hits"][0]["resolved"] == "downloaded"
    assert (tmp_path / "bgm" / "dark-drone.mp3").is_file()
    second = tool.execute({"operation": "resolve", "asset_id": "bgm/dark-drone"})
    assert second.data["hits"][0]["resolved"] == "exists"
    assert calls["n"] == 1
    assert not list(tmp_path.rglob("*.tmp"))


def test_resolve_rejects_html_and_leaves_no_dest(tmp_path):
    _mini_bgm_index(tmp_path)
    cat = ac.AssetCatalog(tmp_path)

    def fake_html(url, output, timeout=120):
        p = Path(output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"<!DOCTYPE html><html>nope</html>")
        return p

    tool = AssetRetriever(catalog=cat, download_fn=fake_html, probe_fn=lambda p: None)
    result = tool.execute({"operation": "resolve", "asset_id": "bgm/dark-drone"})
    assert result.success
    hit = result.data["hits"][0]
    assert not hit["available"]
    assert "网页" in hit["error"]
    assert not (tmp_path / "bgm" / "dark-drone.mp3").exists()


def test_resolve_missing_source_url_real_catalog():
    result = AssetRetriever().execute({"operation": "resolve", "asset_id": "bgm/horror-ambient"})
    assert result.success
    hit = result.data["hits"][0]
    assert hit["available"] is False
    assert "source_url" in hit["error"]


def test_pinned_recipe_urls_are_https():
    ac._default_catalog = None
    drone = ac.get_catalog().get("bgm/dark-drone")
    assert drone
    assert drone["source_url"].startswith("https://incompetech.com/")
    assert drone["license"] == "CC-BY"
    assert "Darkest Child" in drone["attribution"]
    assert "<曲名>" not in drone["attribution"]
    rain = ac.get_catalog().get("sfx/rain-heavy")
    assert rain["file"].endswith(".ogg")
    assert rain["source_url"].startswith("https://commons.wikimedia.org/")
    assert rain["license"] == "CC0"


def test_english_search_terms_expands_chinese():
    terms = ac.english_search_terms("雨夜 紧张")
    assert "rain" in terms
    assert "tension" in terms


def test_remote_search_filters_jamendo_licenses(monkeypatch):
    monkeypatch.setenv("JAMENDO_CLIENT_ID", "test-client")
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)

    def fake_json(url, timeout=30, params=None, headers=None):
        assert "jamendo.com" in url
        assert params["client_id"] == "test-client"
        assert params["vocalinstrumental"] == "instrumental"
        return {
            "results": [
                {
                    "id": "11",
                    "name": "Dark Pad",
                    "artist_name": "Ada",
                    "duration": 90,
                    "license_ccurl": "https://creativecommons.org/licenses/by/4.0/",
                    "audiodownload": "https://example.test/a.mp3",
                    "audiodownload_allowed": True,
                    "shareurl": "https://www.jamendo.com/track/11",
                },
                {
                    "id": "12",
                    "name": "NC Track",
                    "artist_name": "Bob",
                    "duration": 90,
                    "license_ccurl": "https://creativecommons.org/licenses/by-nc/4.0/",
                    "audiodownload": "https://example.test/b.mp3",
                    "audiodownload_allowed": True,
                },
                {
                    "id": "13",
                    "name": "Short",
                    "artist_name": "C",
                    "duration": 10,
                    "license_ccurl": "https://creativecommons.org/publicdomain/zero/1.0/",
                    "audiodownload": "https://example.test/c.mp3",
                    "audiodownload_allowed": True,
                },
            ]
        }

    tool = AssetRetriever(get_json_fn=fake_json)
    result = tool.execute({"query": "drone", "category": "bgm", "remote": True, "top_k": 5})
    assert result.success
    remote = result.data["remote_hits"]
    assert [h["id"] for h in remote] == ["jamendo/11"]
    assert remote[0]["source_url"] == "https://example.test/a.mp3"
    assert remote[0]["file"] == "bgm/jamendo_11.mp3"
    assert any("FREESOUND" in e for e in result.data["errors"]) is False
    assert result.data["hits"]  # 本地目录仍返回


def test_remote_search_missing_keys_stays_available(monkeypatch):
    monkeypatch.delenv("JAMENDO_CLIENT_ID", raising=False)
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)
    result = AssetRetriever().execute({"query": "piano", "category": "bgm", "remote": True})
    assert result.success
    assert result.data["remote_hits"] == []
    assert any("JAMENDO_CLIENT_ID" in e for e in result.data["errors"])


def test_remote_freesound_skips_sa_and_preview(monkeypatch):
    monkeypatch.setenv("FREESOUND_API_KEY", "fs-test")
    monkeypatch.delenv("JAMENDO_CLIENT_ID", raising=False)

    def fake_json(url, timeout=30, params=None, headers=None):
        assert "freesound.org" in url
        assert "previews" not in (params or {}).get("fields", "")
        return {
            "results": [
                {
                    "id": 9,
                    "name": "rain loop",
                    "username": "alice",
                    "duration": 40,
                    "license": "http://creativecommons.org/publicdomain/zero/1.0/",
                    "url": "https://freesound.org/s/9/",
                    "tags": ["rain"],
                },
                {
                    "id": 10,
                    "name": "sa rain",
                    "username": "bob",
                    "duration": 40,
                    "license": "https://creativecommons.org/licenses/by-sa/3.0/",
                    "url": "https://freesound.org/s/10/",
                },
            ]
        }

    tool = AssetRetriever(get_json_fn=fake_json)
    result = tool.execute({"query": "雨", "category": "sfx", "remote": True})
    assert result.success
    remote = result.data["remote_hits"]
    assert [h["id"] for h in remote] == ["freesound/9"]
    assert remote[0]["source_url"] == ""
    assert "OAuth" in remote[0]["notes"]


def test_resolve_remote_hit_writes_project_assets(tmp_path):
    calls = {"n": 0}

    def fake_dl(url, output, timeout=120):
        calls["n"] += 1
        p = Path(output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"ID3" + b"\x00" * 200)
        return p

    tool = AssetRetriever(download_fn=fake_dl, probe_fn=lambda p: {"format": {"duration": "8"}})
    event = {
        "id": "jamendo/11",
        "asset_id": "jamendo/11",
        "remote": True,
        "source_url": "https://example.test/a.mp3",
        "file": "bgm/jamendo_11.mp3",
    }
    result = tool.execute({
        "operation": "resolve",
        "events": [event],
        "project_dir": str(tmp_path),
    })
    assert result.success
    dest = tmp_path / "assets" / "bgm" / "jamendo_11.mp3"
    assert dest.is_file()
    assert calls["n"] == 1
    repo_copy = Path(__file__).resolve().parents[1] / "assets" / "bgm" / "jamendo_11.mp3"
    assert not repo_copy.exists()


def test_resolve_remote_without_project_dir_refuses_index_write():
    tool = AssetRetriever()
    result = tool.execute({
        "operation": "resolve",
        "events": [{
            "id": "jamendo/11",
            "asset_id": "jamendo/11",
            "remote": True,
            "source_url": "https://example.test/a.mp3",
            "file": "bgm/jamendo_11.mp3",
        }],
    })
    assert result.success
    assert "project_dir" in result.data["hits"][0]["error"]


# ---------------------------------------------------------------------------
# apply_lut（mock _run 验证命令构造；真实冒烟需 MONTAGE_REAL_FFMPEG=1）
# ---------------------------------------------------------------------------


def _repo_lut() -> Path:
    p = Path(__file__).resolve().parents[1] / "assets" / "luts" / "teal-orange.cube"
    assert p.is_file(), f"仓库 LUT 缺失: {p}"
    return p


def test_apply_lut_full_strength_command(monkeypatch, tmp_path):
    src = tmp_path / "src.mp4"
    out = tmp_path / "out.mp4"
    src.write_bytes(b"fake")
    captured: list[list[str]] = []

    def fake_run(cmd, timeout=1800):
        captured.append(cmd)

    monkeypatch.setattr(fe, "_run", fake_run)
    fe.apply_lut(src, _repo_lut(), out, strength=1.0)
    assert len(captured) == 1
    cmd = " ".join(captured[0])
    assert "-vf" in cmd
    assert "lut3d=file=" in cmd
    assert "teal-orange" in cmd
    assert "interp=tetrahedral" in cmd
    assert "-crf" in cmd


def test_apply_lut_partial_strength_command(monkeypatch, tmp_path):
    src = tmp_path / "src.mp4"
    out = tmp_path / "out.mp4"
    src.write_bytes(b"fake")
    captured: list[list[str]] = []

    def fake_run(cmd, timeout=1800):
        captured.append(cmd)

    monkeypatch.setattr(fe, "_run", fake_run)
    fe.apply_lut(src, _repo_lut(), out, strength=0.6)
    assert len(captured) == 1
    cmd = " ".join(captured[0])
    assert "-filter_complex" in cmd
    assert "blend=all_mode=normal:all_opacity=0.60" in cmd
    assert "[g][0:v]" in cmd


def test_apply_lut_missing_lut_fails(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake")
    try:
        fe.apply_lut(src, tmp_path / "nope.cube", tmp_path / "out.mp4")
        assert False, "应抛出 ComposError"
    except fe.ComposError as exc:
        assert "LUT 文件不存在" in str(exc)


def test_apply_lut_operation_dispatch(monkeypatch, tmp_path):
    """FFmpegCompose.execute 的 apply_lut 分发（mock _run 避免真实 ffmpeg）。"""
    src = tmp_path / "src.mp4"
    lut = tmp_path / "g.cube"
    src.write_bytes(b"fake")
    lut.write_text("LUT_3D_SIZE 2\n0 0 0\n1 1 1\n", encoding="utf-8")
    captured: list[list[str]] = []

    def fake_run(cmd, timeout=1800):
        captured.append(cmd)

    monkeypatch.setattr(fe, "_run", fake_run)
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 1.0}})
    result = fe.FFmpegCompose().execute(
        {"operation": "apply_lut", "input_path": str(src), "lut_path": str(lut),
         "output_path": str(tmp_path / "graded.mp4"), "lut_strength": 0.5}
    )
    assert result.success
    assert captured and "lut3d=file=" in " ".join(captured[0])


def test_lut3d_filter_uses_file_option(tmp_path):
    """lut3d 的选项名是 file（vf_lut3d.c 自 ffmpeg 2.4 起），不是 filename。"""
    lut = tmp_path / "l.cube"
    lut.write_text("LUT_3D_SIZE 2\n0 0 0\n1 1 1\n", encoding="utf-8")
    filt = fe.lut3d_filter(lut)
    assert filt.startswith("lut3d=file=")
    assert "filename=" not in filt
    assert filt.endswith(":interp=tetrahedral")


def test_filter_path_double_escapes_drive_and_space(tmp_path):
    """filtergraph 与滤镜选项各吃一层转义，字面冒号/空格需写成两层。"""
    target = tmp_path / "a b.cube"
    posix = target.resolve().as_posix()
    escaped = fe.filter_path(target)
    assert escaped == posix.replace(":", "\\" * 2 + ":").replace(" ", "\\" * 2 + " ")
    assert "a\\\\ b.cube" in escaped
    if ":" in posix:  # Windows 盘符：单层转义不足以解析
        assert escaped.count("\\") == 2 * (posix.count(":") + posix.count(" "))


def test_apply_lut_resolves_catalog_id(monkeypatch, tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "probe", lambda p: {"format": {"duration": 1.0}})
    result = fe.FFmpegCompose().execute({
        "operation": "apply_lut",
        "input_path": str(src),
        "lut_path": "luts/dark-moody",
        "output_path": str(tmp_path / "graded.mp4"),
    })
    assert result.success, result.error
    joined = " ".join(captured[0])
    assert "dark-moody.cube" in joined
    assert fe.resolve_lut_file("luts/dark-moody").name == "dark-moody.cube"


def test_real_ffmpeg_apply_lut_smoke(tmp_path):
    """真实 ffmpeg 冒烟（需 MONTAGE_REAL_FFMPEG=1；沙箱环境自动跳过）。"""
    if os.environ.get("MONTAGE_REAL_FFMPEG") != "1":
        import pytest

        pytest.skip("需设置 MONTAGE_REAL_FFMPEG=1 才运行真实 ffmpeg 冒烟")
    if fe.check_ffmpeg() is None:
        import pytest

        pytest.skip("本机无 ffmpeg")
    src = tmp_path / "src.mp4"
    fe._run([
        fe.check_ffmpeg(), "-y", "-f", "lavfi",
        "-i", "testsrc=size=320x180:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-c:v", "libx264", "-c:a", "aac", "-shortest", str(src),
    ])
    out = tmp_path / "graded.mp4"
    fe.apply_lut(src, _repo_lut(), out, strength=0.8)
    info = fe.probe(out)
    # ffprobe JSON 的 duration 是字符串，必须显式转 float 再比较
    assert float((info.get("format") or {}).get("duration", 0)) > 0.5
