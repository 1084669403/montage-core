"""M6/M7 测试：素材源检索 / 导出包 / 管线扩展。"""

import json
import os
import zipfile
from pathlib import Path

from montage.engine.project import init_project
from montage.pipelines import get_pipeline, list_pipelines
from montage.tools import export_bundle, stock_retriever


# ---------------------------------------------------------------------------
# stock_retriever
# ---------------------------------------------------------------------------


def test_wikimedia_search(monkeypatch):
    resp = {
        "query": {
            "pages": {
                "1": {
                    "title": "File:rain.jpg",
                    "imageinfo": [{
                        "url": "https://commons.wikimedia.org/wiki/Special:FilePath/rain.jpg",
                        "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}},
                    }],
                },
                "2": {"title": "File:none.jpg", "imageinfo": []},
            }
        }
    }
    monkeypatch.setattr(stock_retriever, "get_json", lambda url, timeout=30: resp)
    hits = stock_retriever.search_wikimedia("rain")
    assert len(hits) == 1
    assert hits[0]["provider"] == "wikimedia"
    assert "CC BY-SA" in hits[0]["license"]


def test_pixabay_requires_key(monkeypatch):
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    hits = stock_retriever.search_pixabay("rain")
    assert hits and "error" in hits[0]


def test_stock_retriever_tool_dispatch(monkeypatch):
    monkeypatch.setattr(stock_retriever, "search_wikimedia",
                        lambda q, top_k=10, media_type="image": [{"provider": "wikimedia", "url": "http://x/1.jpg", "license": "PD", "media_type": "image"}])
    monkeypatch.setattr(stock_retriever, "search_pixabay",
                        lambda q, top_k=10, media_type="image": [{"provider": "pixabay", "error": "缺少 PIXABAY_API_KEY"}])
    monkeypatch.setattr(stock_retriever, "search_pexels",
                        lambda q, top_k=10: [{"provider": "pexels", "error": "缺少 PEXELS_API_KEY"}])
    result = stock_retriever.StockRetriever().execute({"query": "rain", "provider": "all", "media_type": "video"})
    assert result.success
    assert result.data["count"] == 1
    assert len(result.data["errors"]) == 2  # pixabay/pexels 缺 key 的提示


def test_stock_retriever_requires_query():
    result = stock_retriever.StockRetriever().execute({})
    assert not result.success


# ---------------------------------------------------------------------------
# export_bundle
# ---------------------------------------------------------------------------


def test_export_bundle_builds_zip(tmp_path):
    proj = init_project(tmp_path, "demo-export", "导出演示", "cinematic")
    # 造几个产物
    (proj / "artifacts").mkdir(exist_ok=True)
    (proj / "artifacts" / "script.json").write_text(json.dumps({"title": "t", "sections": []}), encoding="utf-8")
    (proj / "renders").mkdir(exist_ok=True)
    (proj / "renders" / "final.mp4").write_bytes(b"media")
    (proj / "assets" / "images").mkdir(parents=True, exist_ok=True)
    (proj / "assets" / "images" / "portrait.png").write_bytes(b"png")
    (proj / "cost.jsonl").write_text('{"id":"a"}\n', encoding="utf-8")
    (proj / "decisions.jsonl").write_text('{"category":"x"}\n', encoding="utf-8")

    out_dir = tmp_path / "exports"
    data = export_bundle.build_bundle(proj, out_dir)
    assert data["entries"] >= 4
    bundle = Path(data["output"])
    assert bundle.exists() and bundle.suffix == ".zip"
    with zipfile.ZipFile(bundle) as zf:
        names = zf.namelist()
        assert "manifest.json" in names
        assert "artifacts/script.json" in names
        assert "renders/final.mp4" in names
        assert "assets/images/portrait.png" in names
        assert "CREDITS.txt" in names
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["project_id"] == "demo-export"
        assert zf.read("CREDITS.txt").decode("utf-8").startswith("本片配乐")


def test_export_bundle_default_never_prunes(tmp_path):
    """keep 缺省 = 0：只增不删，旧包一个不动。"""
    proj = init_project(tmp_path, "demo-keep", "保留", "cinematic")
    (proj / "renders").mkdir(exist_ok=True)
    (proj / "renders" / "final.mp4").write_bytes(b"m")
    out = tmp_path / "exports"
    out.mkdir()
    stale = out / "demo-keep_20200101T000000.zip"
    stale.write_bytes(b"old")
    data = export_bundle.build_bundle(proj, out)
    assert data["pruned"] == []
    assert stale.exists()


def test_export_bundle_prune_keeps_newest_n(tmp_path):
    """keep=N：保留最新 N 个，其余删除；只认本工具的 <id>_<时间戳>.zip 命名。"""
    proj = init_project(tmp_path, "demo-prune", "清理", "cinematic")
    (proj / "renders").mkdir(exist_ok=True)
    (proj / "renders" / "final.mp4").write_bytes(b"m")
    out = tmp_path / "exports"
    out.mkdir()
    for i, stamp in enumerate(("20200101T000000", "20200102T000000", "20200103T000000")):
        p = out / f"demo-prune_{stamp}.zip"
        p.write_bytes(b"old")
        os.utime(p, (1_600_000_000 + i, 1_600_000_000 + i))
    unrelated = out / "manual-backup.zip"
    unrelated.write_bytes(b"keep me")

    data = export_bundle.build_bundle(proj, out, keep=2)
    remaining = sorted(p.name for p in out.glob("demo-prune_*.zip"))
    assert len(remaining) == 2
    assert Path(data["output"]).name in remaining
    assert len(data["pruned"]) == 2
    # 最新的旧包保留（20200103），更旧的两个被清
    assert "demo-prune_20200103T000000.zip" in remaining
    assert unrelated.exists(), "非本工具命名的 zip 不得被清"


def test_export_bundle_tool_keep_exports(tmp_path):
    proj = init_project(tmp_path, "demo-tool", "工具", "cinematic")
    (proj / "renders").mkdir(exist_ok=True)
    (proj / "renders" / "final.mp4").write_bytes(b"m")
    out = tmp_path / "e"
    out.mkdir()
    for i, stamp in enumerate(("20200101T000000", "20200102T000000")):
        p = out / f"demo-tool_{stamp}.zip"
        p.write_bytes(b"old")
        os.utime(p, (1_600_000_000 + i, 1_600_000_000 + i))
    result = export_bundle.ExportBundle().execute({
        "project_dir": str(proj), "output_dir": str(out), "keep_exports": 1,
    })
    assert result.success
    assert len(list(out.glob("*.zip"))) == 1
    assert result.data["pruned"]


def test_export_bundle_includes_cover_and_subtitles(tmp_path):
    proj = init_project(tmp_path, "demo-pack", "封面字幕", "cinematic")
    renders = proj / "renders"
    renders.mkdir(exist_ok=True)
    (renders / "final.mp4").write_bytes(b"media")
    (renders / "cover.jpg").write_bytes(b"jpg")
    (renders / "final.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    data = export_bundle.build_bundle(proj, tmp_path / "exports")
    with zipfile.ZipFile(data["output"]) as zf:
        names = zf.namelist()
        assert "renders/cover.jpg" in names
        assert "renders/final.srt" in names


def test_export_bundle_excludes_auto_edit_tmp_history(tmp_path):
    proj = init_project(tmp_path, "demo-exclude", "排除演示", "cinematic")
    (proj / "auto_edit").mkdir()
    (proj / "auto_edit" / "final.mp4").write_bytes(b"ae")
    (proj / "tmp_autoedit").mkdir()
    (proj / "tmp_autoedit" / "joined.mp4").write_bytes(b"tmp")
    (proj / "history").mkdir(exist_ok=True)
    (proj / "history" / "checkpoint_script_old.json").write_text("{}", encoding="utf-8")
    (proj / "assets" / "tmp_autoedit").mkdir(parents=True)
    (proj / "assets" / "tmp_autoedit" / "secret.bin").write_bytes(b"no")
    (proj / "assets" / "__pycache__").mkdir()
    (proj / "assets" / "__pycache__" / "x.pyc").write_bytes(b"pyc")
    (proj / "assets" / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (proj / ".env").write_text("SECRET=2\n", encoding="utf-8")
    (proj / "cost.jsonl").write_text("{}\n", encoding="utf-8")
    (proj / "decisions.jsonl").write_text("{}\n", encoding="utf-8")

    data = export_bundle.build_bundle(proj, tmp_path / "exports")
    with zipfile.ZipFile(data["output"]) as zf:
        names = zf.namelist()
    joined = "\n".join(names)
    assert "cost.jsonl" in names
    assert "decisions.jsonl" in names
    assert "auto_edit/" not in joined
    assert "tmp_autoedit" not in joined
    assert "history/" not in joined
    assert "__pycache__" not in joined
    assert ".env" not in joined


def test_export_bundle_skips_generation_cache(tmp_path):
    """`.cache` 是下载去重缓存，与 assets/images 同一份字节，入包等于把体积翻倍。"""
    proj = init_project(tmp_path, "demo-cache", "缓存", "cinematic")
    cache = proj / "assets" / ".cache"
    cache.mkdir(parents=True)
    (cache / "abc123.png").write_bytes(b"png-bytes")
    (cache / "index.json").write_text("{}", encoding="utf-8")
    (proj / "assets" / "images").mkdir(parents=True, exist_ok=True)
    (proj / "assets" / "images" / "portrait.png").write_bytes(b"png-bytes")

    data = export_bundle.build_bundle(proj, tmp_path / "exports")
    with zipfile.ZipFile(data["output"]) as zf:
        names = zf.namelist()
        joined = "\n".join(names)
    assert "assets/images/portrait.png" in names
    assert ".cache" not in joined
    assert all(".cache" not in m for m in data["manifest"]["media_files"])


def test_export_bundle_skips_stitch_intermediates(tmp_path):
    """assemble 的 renders/*.joined.mp4 是整片长度的临时文件，不该进交付包。"""
    proj = init_project(tmp_path, "demo-joined", "中间件", "cinematic")
    renders = proj / "renders"
    renders.mkdir(exist_ok=True)
    (renders / "final.mp4").write_bytes(b"film")
    (renders / "final.joined.mp4").write_bytes(b"joined-film")
    (renders / "final.concat.txt").write_text("file 'a.mp4'\n", encoding="utf-8")

    data = export_bundle.build_bundle(proj, tmp_path / "exports")
    with zipfile.ZipFile(data["output"]) as zf:
        names = zf.namelist()
    assert "renders/final.mp4" in names
    assert "renders/final.joined.mp4" not in names
    assert "renders/final.concat.txt" not in names
    assert data["manifest"]["media_files"] == ["renders/final.mp4"]


def test_export_bundle_tool(tmp_path):
    proj = init_project(tmp_path, "demo-tool", "t", "cinematic")
    result = export_bundle.ExportBundle().execute({"project_dir": str(proj), "output_dir": str(tmp_path / "e")})
    assert result.success
    assert Path(result.data["output"]).exists()


def test_export_bundle_bad_project(tmp_path):
    result = export_bundle.ExportBundle().execute({"project_dir": str(tmp_path / "nope")})
    assert not result.success


# ---------------------------------------------------------------------------
# pipelines 扩展
# ---------------------------------------------------------------------------


def test_new_pipelines_registered():
    names = list_pipelines()
    assert {"cinematic", "documentary", "clip_factory"} <= set(names)
    assert get_pipeline("documentary") is not None
    assert get_pipeline("clip_factory") is not None


def test_new_pipelines_share_stage_order():
    from montage.engine.stages import STAGE_ORDER

    for name in ("documentary", "clip_factory"):
        p = get_pipeline(name)
        stage_names = [s["name"] for s in p["stages"]]
        assert stage_names == list(STAGE_ORDER)


def test_clip_factory_uses_scene_detect():
    p = get_pipeline("clip_factory")
    script = next(s for s in p["stages"] if s["name"] == "script")
    assert "scene_detect" in script["tools"]
    assert "clip_plan" in script["produces"]


def test_gates_work_for_new_pipelines(tmp_path):
    from montage.engine.gates import stage_capabilities

    caps = stage_capabilities(get_pipeline("documentary"), "scene_plan")
    assert "prompt_engineering" in caps
    caps_cf = stage_capabilities(get_pipeline("clip_factory"), "assets")
    assert "stock_search" in caps_cf or "asset_retrieval" in caps_cf


def test_m6_m7_tools_discovered():
    from montage.registry import ToolRegistry

    reg = ToolRegistry()
    reg.discover()
    for name in ("stock_retriever", "export_bundle"):
        assert reg.get(name) is not None, name
