"""W4 波次 1：release_pack 模板简介与 publish_log。"""

from pathlib import Path

from montage.engine.artifacts import ArtifactStore
from montage.engine.project import init_project
from montage.tools.release_pack import ReleasePack, build_blurbs, clip_chars, collect_copy, write_release_pack


def test_clip_chars_truncates_unicode():
    assert clip_chars("abcdefghij", 4) == "abcd"
    assert clip_chars("你好世界", 2) == "你好"
    assert clip_chars("", 10) == ""


def test_blurbs_respect_limits_and_no_invented_plot():
    copy = {
        "title": "A" * 80,
        "body": "B" * 200,
        "hook": "C" * 50,
        "logline": "D" * 120,
    }
    blurbs = build_blurbs(copy)
    assert len(blurbs["douyin"]["title"]) == 30
    assert len(blurbs["douyin"]["body"]) == 100
    assert len(blurbs["douyin"]["hook"]) == 24
    assert len(blurbs["bilibili"]["title"]) == 80
    assert len(blurbs["youtube"]["body"]) == 200
    assert blurbs["youtube"]["hook"].startswith("D")


def test_collect_copy_without_script_uses_project_title(tmp_path):
    proj = init_project(tmp_path, "rel", "仅项目名", "cinematic")
    copy = collect_copy(proj)
    assert copy["title"] == "仅项目名"
    assert copy["body"] == ""
    assert copy["hook"] == ""


def test_extract_cover_falls_back_to_last_frame_when_seek_past_end(tmp_path, monkeypatch):
    import montage.tools.release_pack as rp

    film = tmp_path / "final.mp4"
    film.write_bytes(b"vid")
    dest = tmp_path / "cover.jpg"
    last_calls: list[tuple] = []
    monkeypatch.setattr(rp, "_ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(rp, "_film_duration", lambda _film: 0.4)

    def last(src, out):
        last_calls.append((src, out))
        out.write_bytes(b"jpg")
        return str(out)

    def no_seek(*_a, **_k):
        raise AssertionError("片长短于 seek 不应先 -ss")

    monkeypatch.setattr(rp, "_extract_last_cover", last)
    monkeypatch.setattr(rp, "_ffmpeg_run", no_seek)
    cover = rp._extract_cover(film, dest, seek=3.0)
    assert cover == str(dest)
    assert dest.read_bytes() == b"jpg"
    assert last_calls == [(film, dest)]


def test_extract_cover_seeks_when_film_is_long_enough(tmp_path, monkeypatch):
    import montage.tools.release_pack as rp

    film = tmp_path / "final.mp4"
    film.write_bytes(b"vid")
    dest = tmp_path / "cover.jpg"
    cmds: list[list[str]] = []
    monkeypatch.setattr(rp, "_ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(rp, "_film_duration", lambda _film: 8.0)

    def no_last(*_a, **_k):
        raise AssertionError("片长足够不应抽最后一帧")

    def fake_run(cmd, timeout=60):
        cmds.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"jpg")

    monkeypatch.setattr(rp, "_extract_last_cover", no_last)
    monkeypatch.setattr(rp, "_ffmpeg_run", fake_run)
    cover = rp._extract_cover(film, dest, seek=3.0)
    assert cover == str(dest)
    assert cmds and cmds[0][2:4] == ["-ss", "3.00"]


def test_extract_cover_falls_back_if_seek_yields_empty(tmp_path, monkeypatch):
    import montage.tools.release_pack as rp

    film = tmp_path / "final.mp4"
    film.write_bytes(b"vid")
    dest = tmp_path / "cover.jpg"
    monkeypatch.setattr(rp, "_ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(rp, "_film_duration", lambda _film: 8.0)
    monkeypatch.setattr(rp, "_ffmpeg_run", lambda *_a, **_k: None)

    def last(src, out):
        out.write_bytes(b"jpg")
        return str(out)

    monkeypatch.setattr(rp, "_extract_last_cover", last)
    cover = rp._extract_cover(film, dest, seek=3.0)
    assert cover == str(dest)
    assert dest.read_bytes() == b"jpg"


def test_write_release_pack_skips_cover_keeps_published(tmp_path):
    proj = init_project(tmp_path, "rel2", "演示", "cinematic")
    store = ArtifactStore(proj)
    store.write("script", {
        "title": "雨夜对峙",
        "sections": [{"id": "sc01", "narration": "巷口两人停下"}],
        "structure": {"hook": "一声枪响"},
    })
    store.write("publish_log", {"status": "published", "notes": "already"})
    (proj / "renders").mkdir(exist_ok=True)
    (proj / "renders" / "final.mp4").write_bytes(b"not-a-video")
    data = write_release_pack(proj)
    assert data["blurbs"]["douyin"]["title"] == "雨夜对峙"
    assert "巷口" in data["blurbs"]["douyin"]["body"]
    log = store.read("publish_log")
    assert log["status"] == "published"
    assert log.get("notes") == "already"


def test_release_pack_tool_writes_exported(tmp_path):
    proj = init_project(tmp_path, "rel3", "演示", "cinematic")
    result = ReleasePack().execute({"project_dir": str(proj)})
    assert result.success
    log = ArtifactStore(proj).read("publish_log")
    assert log["status"] == "exported"
    pack = ArtifactStore(proj).read("release_pack")
    assert "douyin" in pack["blurbs"]
