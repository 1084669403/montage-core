"""P4：voice_director / soundtrack_planner / place_audio。不打真实 TTS/混音 API。"""

import json
from pathlib import Path

from montage.pipelines import CINEMATIC, CLIP_FACTORY, DOCUMENTARY
from montage.playbooks import get_playbook
from montage.providers.voices import resolve_voice
from montage.registry import ToolRegistry
from montage.toolbase import ToolResult
from montage.tools.place_audio import PlaceAudio, events_for_clip, global_music_path, timed_bgm_tracks
from montage.tools.script_to_scene_plan import convert_script_to_scene_plan
from montage.tools.soundtrack_planner import plan_soundtrack
from montage.tools.voice_director import VoiceDirector, assign_voices, collect_dialogue


def _fixture():
    path = Path(__file__).parent / "fixtures" / "script_complete.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _plan():
    return convert_script_to_scene_plan(_fixture())["scene_plan"]


def test_tools_discovered():
    reg = ToolRegistry()
    reg.discover()
    for name in ("voice_director", "soundtrack_planner", "place_audio"):
        assert reg.get(name) is not None, name


def test_resolve_voice_table_and_passthrough():
    soft = resolve_voice(voice_id="female_soft", provider="edge_tts")
    assert soft["native"] == "zh-CN-XiaoxiaoNeural"
    native = resolve_voice(voice_id="zh-CN-YunxiNeural", provider="edge_tts")
    assert native["id"] == "male_low"
    custom = resolve_voice(voice_id="my-custom-voice", provider="doubao")
    assert custom["matched"] == "passthrough"
    assert custom["native"] == "my-custom-voice"
    narrator = resolve_voice(speaker_id="narrator", provider="doubao")
    assert narrator["id"] in ("female_soft", "male_broadcast")


def test_dialogue_prefers_shot_over_script_lines():
    script = _fixture()
    plan = convert_script_to_scene_plan(script)["scene_plan"]
    lines = collect_dialogue(plan, script)
    assert lines
    assert all(ln["source"] == "shot.audio_prompt.dialogue" for ln in lines)
    assert [ln["text"] for ln in lines] == ["雨还在下。", "你看清楚没有。"]


def test_dialogue_falls_back_to_script_lines():
    script = {
        "title": "t",
        "characters": [{"id": "a", "voice_id": "male_low", "role": "protagonist"}],
        "sections": [{
            "id": "sc01",
            "duration_seconds": 5,
            "narration": "你好。",
            "lines": [{"speaker_id": "a", "text": "你好。"}],
        }],
    }
    plan = {
        "scenes": [{
            "id": "sc01",
            "description": "x",
            "start_seconds": 0,
            "end_seconds": 5,
            "shots": [{
                "shot_id": "sc01_01",
                "shot_kind": "video",
                "duration_seconds": 5,
                "visual_details": {"environment": "室内"},
            }],
        }],
    }
    lines = collect_dialogue(plan, script)
    assert len(lines) == 1
    assert lines[0]["source"] == "script.sections.lines"
    assert lines[0]["speaker_id"] == "a"
    segs = assign_voices(lines, characters={"a": script["characters"][0]}, provider="edge_tts")
    assert segs[0]["voice"] == "zh-CN-YunxiNeural"


def test_voice_director_does_not_synthesize_by_default(tmp_path):
    called = {"n": 0}

    def boom(inputs):
        called["n"] += 1
        raise AssertionError("默认不应调用 TTS")

    tool = VoiceDirector(tts_execute=boom)
    result = tool.execute({"scene_plan": _plan(), "script": _fixture(), "project_dir": str(tmp_path)})
    assert result.success
    assert called["n"] == 0
    assert result.data["segments"]
    assert result.data["narration_sections"] == []


def test_voice_director_synthesize_mock(tmp_path):
    def fake_tts(inputs):
        p = Path(inputs["output_path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"wav")
        return ToolResult(success=True, data={"output": str(p)})

    tool = VoiceDirector(tts_execute=fake_tts)
    result = tool.execute({
        "scene_plan": _plan(),
        "script": _fixture(),
        "project_dir": str(tmp_path),
        "synthesize": True,
    })
    assert result.success
    assert result.data["narration_sections"]
    assert Path(result.data["narration_sections"][0]["narration_audio"]).exists()


def test_soundtrack_planner_emits_events():
    data = plan_soundtrack(_plan(), get_playbook("cyberpunk_neon"))
    kinds = {e["kind"] for e in data["events"]}
    assert "bgm" in kinds
    assert "sfx" in kinds
    rain = [e for e in data["events"] if e["kind"] == "sfx" and "rain" in e["asset_id"]]
    assert rain
    assert all("download_hint" in e or e["available"] for e in data["events"])
    bgm = [e for e in data["events"] if e["kind"] == "bgm"]
    assert len(bgm) == 1
    assert bgm[0]["asset_id"] == "bgm/dark-drone"
    assert rain[0]["volume"] <= 0.4


def test_documentary_skips_bgm():
    plan = {
        "scenes": [{"id": "sc01", "description": "访", "start_seconds": 0, "end_seconds": 10, "emotion": "克制", "shots": []}],
    }
    data = plan_soundtrack(plan, get_playbook("documentary_restraint"))
    assert not [e for e in data["events"] if e["kind"] == "bgm"]


def test_spoken_explain_keeps_bgm():
    plan = {
        "scenes": [{"id": "sc01", "description": "讲", "start_seconds": 0, "end_seconds": 8, "emotion": "清晰", "shots": []}],
    }
    data = plan_soundtrack(plan, get_playbook("spoken_explain"))
    bgm = [e for e in data["events"] if e["kind"] == "bgm"]
    assert len(bgm) == 1
    assert bgm[0]["asset_id"] == "bgm/calm-piano"


def test_soundtrack_planner_writes_artifact(tmp_path):
    from montage.tools.soundtrack_planner import SoundtrackPlanner

    (tmp_path / "project.json").write_text("{}", encoding="utf-8")
    result = SoundtrackPlanner().execute({
        "scene_plan": _plan(),
        "playbook": "cyberpunk_neon",
        "project_dir": str(tmp_path),
    })
    assert result.success
    saved = tmp_path / "artifacts" / "soundtrack.json"
    assert saved.is_file()
    import json
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["events"]


def test_copy_into_project_music(tmp_path):
    from montage.tools.soundtrack_planner import copy_into_project_music

    src = tmp_path / "catalog" / "song.mp3"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"music-bytes")
    dest = Path(copy_into_project_music(tmp_path, str(src)))
    assert dest.is_file()
    assert dest.parent == tmp_path / "assets" / "music"
    assert dest.read_bytes() == b"music-bytes"


def test_soundtrack_resolve_copies_into_project_music(tmp_path, monkeypatch):
    from montage.tools import asset_retriever
    from montage.tools.soundtrack_planner import SoundtrackPlanner

    catalog_file = tmp_path / "pin.mp3"
    catalog_file.write_bytes(b"pin")
    index = Path(__file__).resolve().parents[1] / "assets" / "bgm" / "INDEX.md"
    before = index.read_text(encoding="utf-8")

    def fake_resolve(hit, assets_root, **kwargs):
        return {"available": True, "path": str(catalog_file), "id": hit.get("id")}

    monkeypatch.setattr(asset_retriever, "resolve_hit", fake_resolve)
    (tmp_path / "project.json").write_text("{}", encoding="utf-8")
    result = SoundtrackPlanner().execute({
        "scene_plan": _plan(),
        "playbook": "cyberpunk_neon",
        "project_dir": str(tmp_path),
        "resolve": True,
    })
    assert result.success
    copied = tmp_path / "assets" / "music" / "pin.mp3"
    assert copied.is_file()
    assert copied.read_bytes() == b"pin"
    assert index.read_text(encoding="utf-8") == before


def test_place_audio_overlays_sfx_and_hints_assemble(tmp_path):
    clip = tmp_path / "c1.mp4"
    sfx = tmp_path / "rain.wav"
    clip.write_bytes(b"vid")
    sfx.write_bytes(b"sfx")
    calls: list = []

    def overlay(src, events, dest):
        Path(dest).write_bytes(Path(src).read_bytes())
        calls.append(events)
        return Path(dest)

    events = [{
        "kind": "sfx",
        "scene_id": "sc01",
        "shot_id": "sc01_01",
        "start_seconds": 0,
        "offset_seconds": 0.2,
        "path": str(sfx),
        "available": True,
        "volume": 0.8,
    }, {
        "kind": "bgm",
        "scene_id": "sc01",
        "shot_id": "",
        "start_seconds": 0,
        "end_seconds": 5,
        "path": str(tmp_path / "song.mp3"),
        "available": False,
    }]
    (tmp_path / "song.mp3").write_bytes(b"m")
    events[1]["available"] = True
    events[1]["path"] = str(tmp_path / "song.mp3")

    tool = PlaceAudio(overlay_fn=overlay)
    result = tool.execute({
        "project_dir": str(tmp_path),
        "edit_decisions": {"cuts": [{"clip_path": str(clip), "shot_id": "sc01_01", "from_scene": "sc01"}]},
        "scene_plan": {
            "scenes": [{
                "id": "sc01",
                "description": "x",
                "start_seconds": 0,
                "end_seconds": 5,
                "shots": [{"shot_id": "sc01_01", "shot_kind": "video", "duration_seconds": 5}],
            }],
        },
        "events": events,
    })
    assert result.success, result.error
    assert calls and calls[0][0]["delay_seconds"] == 0.2
    assert result.data["assemble_hints"]["mix_source_audio"] is True
    assert result.data["music_path"] == str(tmp_path / "song.mp3")
    assert result.data["edit_decisions"]["cuts"][0]["clip_path"] != str(clip)


def test_place_audio_reads_soundtrack_artifact(tmp_path):
    from montage.engine.artifacts import ArtifactStore

    clip = tmp_path / "c1.mp4"
    song = tmp_path / "song.mp3"
    clip.write_bytes(b"vid")
    song.write_bytes(b"m")
    store = ArtifactStore(tmp_path)
    store.write("edit_decisions", {"cuts": [{"clip_path": str(clip), "shot_id": "sc01_01"}]})
    store.write("scene_plan", {
        "scenes": [{"id": "sc01", "start_seconds": 0, "end_seconds": 4, "shots": [
            {"shot_id": "sc01_01", "shot_kind": "video", "duration_seconds": 4},
        ]}],
    })
    store.write("soundtrack", {"events": [{
        "kind": "bgm", "shot_id": "", "path": str(song), "available": True,
        "asset_id": "bgm/dark-drone", "start_seconds": 0, "end_seconds": 4,
    }]})
    result = PlaceAudio(overlay_fn=lambda s, e, d: Path(d).write_bytes(b"x") or Path(d)).execute({
        "project_dir": str(tmp_path),
    })
    assert result.success
    assert result.data["music_path"] == str(song)
    assert result.data["missing"] == []


def test_global_music_ignores_shot_bound_bgm(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(b"x")
    events = [
        {"kind": "bgm", "shot_id": "sc01_01", "path": str(p), "available": True},
        {"kind": "bgm", "shot_id": "", "path": str(p), "available": True},
    ]
    assert global_music_path(events) == str(p)
    clip = {"shot_id": "sc01_01", "start_seconds": 0, "end_seconds": 5}
    local = events_for_clip(clip, events)
    assert any(e.get("shot_id") == "sc01_01" for e in local)


def test_pipelines_wire_p4_not_clip_factory():
    cine_a = next(s["tools"] for s in CINEMATIC["stages"] if s["name"] == "assets")
    cine_c = next(s["tools"] for s in CINEMATIC["stages"] if s["name"] == "compose")
    doc_c = next(s["tools"] for s in DOCUMENTARY["stages"] if s["name"] == "compose")
    clip_a = next(s["tools"] for s in CLIP_FACTORY["stages"] if s["name"] == "assets")
    clip_c = next(s["tools"] for s in CLIP_FACTORY["stages"] if s["name"] == "compose")
    assert "voice_director" in cine_a
    assert "soundtrack_planner" in cine_c and "place_audio" in cine_c
    assert "asset_retriever" in cine_c
    assert "soundtrack_planner" in doc_c
    assert "asset_retriever" in doc_c
    assert "voice_director" not in clip_a
    assert "soundtrack_planner" in clip_c
    assert "place_audio" in clip_c
    assert "asset_retriever" in clip_c
    assert "compose_planner" in clip_c
    assert "voice_director" not in clip_c


def test_mix_source_audio_includes_bed(monkeypatch, tmp_path):
    from montage.compose import ffmpeg_engine as fe

    v, n, m, o = (tmp_path / x for x in ("v.mp4", "n.mp3", "m.mp3", "o.mp4"))
    for f in (v, n, m):
        f.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "_has_audio_stream", lambda p: True)
    fe.mix_audio(v, n, m, o, mix_source_audio=True)
    cmd = " ".join(captured[0])
    assert "[0:a]volume=1.0[bed]" in cmd
    assert "amix=inputs=3" in cmd


def test_place_audio_two_bgm_clears_music_path(tmp_path):
    clip = tmp_path / "c1.mp4"
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    for p in (clip, a, b):
        p.write_bytes(b"x")
    events = [
        {"kind": "bgm", "shot_id": "", "scene_id": "sc01", "start_seconds": 0, "end_seconds": 5,
         "path": str(a), "available": True, "volume": 0.2},
        {"kind": "bgm", "shot_id": "", "scene_id": "sc02", "start_seconds": 5, "end_seconds": 10,
         "path": str(b), "available": True, "volume": 0.2},
    ]
    tracks = timed_bgm_tracks(events)
    assert len(tracks) == 2
    assert global_music_path(events) == ""
    tool = PlaceAudio(overlay_fn=lambda src, ev, dest: Path(dest).write_bytes(b"x") or Path(dest))
    result = tool.execute({
        "edit_decisions": {"cuts": [{"clip_path": str(clip), "shot_id": "sc01_01"}]},
        "events": events,
    })
    assert result.success
    assert result.data["music_path"] == ""
    assert len(result.data["music_segments"]) == 2
    assert result.data["assemble_hints"]["music_path"] == ""
    assert result.data["edit_decisions"]["music_segments"]


def test_mix_timed_bgm_uses_longest(monkeypatch, tmp_path):
    from montage.compose import ffmpeg_engine as fe

    v, a, b, o = (tmp_path / x for x in ("v.mp4", "a.mp3", "b.mp3", "o.mp4"))
    for f in (v, a, b):
        f.write_bytes(b"fake")
    captured: list[list[str]] = []
    monkeypatch.setattr(fe, "_run", lambda cmd, timeout=1800: captured.append(cmd))
    monkeypatch.setattr(fe, "_has_audio_stream", lambda p: False)
    fe.mix_audio(
        v, None, v / "should-ignore.mp3", o,
        music_segments=[
            {"path": str(a), "start_seconds": 0, "end_seconds": 5, "volume": 0.2},
            {"path": str(b), "start_seconds": 5, "end_seconds": 10, "volume": 0.2},
        ],
    )
    cmd = " ".join(captured[0])
    assert "duration=longest" in cmd
    assert "adelay=5000|5000" in cmd
    assert str(v / "should-ignore.mp3") not in cmd


def test_place_audio_skips_overlay_for_agnes_prompt(tmp_path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"vid")
    from montage.engine.artifacts import ArtifactStore

    store = ArtifactStore(tmp_path)
    store.write("shot_prompts", {
        "version": "1",
        "shots": [{"scene_id": "sc01", "shot_kind": "video", "shot_id": "sc01_01", "audio_source": "agnes_prompt"}],
    })
    called = {"n": 0}

    def overlay(src, ev, dest):
        called["n"] += 1
        raise AssertionError("agnes_prompt 不应叠配乐")

    tool = PlaceAudio(overlay_fn=overlay)
    result = tool.execute({
        "project_dir": str(tmp_path),
        "edit_decisions": {"cuts": [{"clip_path": str(clip), "shot_id": "sc01_01"}]},
        "events": [{"kind": "sfx", "path": str(tmp_path / "x.wav"), "start_seconds": 0}],
    })
    assert result.success
    assert called["n"] == 0
    assert result.data["assemble_hints"]["mix_source_audio"] is True
    assert result.data["assemble_hints"]["ducking"] is False
    assert result.data["music_path"] == ""


def test_place_audio_skips_overlay_for_jimeng_prompt(tmp_path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"vid")
    from montage.engine.artifacts import ArtifactStore

    store = ArtifactStore(tmp_path)
    store.write("shot_prompts", {
        "version": "1",
        "shots": [{"scene_id": "sc01", "shot_kind": "video", "shot_id": "sc01_01", "audio_source": "jimeng_prompt"}],
    })

    def overlay(src, ev, dest):
        raise AssertionError("jimeng_prompt 不应叠配乐")

    result = PlaceAudio(overlay_fn=overlay).execute({
        "project_dir": str(tmp_path),
        "edit_decisions": {"cuts": [{"clip_path": str(clip), "shot_id": "sc01_01"}]},
        "events": [{"kind": "sfx", "path": str(tmp_path / "x.wav"), "start_seconds": 0}],
    })
    assert result.success
    assert result.data["assemble_hints"]["mix_source_audio"] is True


def test_place_audio_kling_native_skips_that_cut_only(tmp_path):
    native = tmp_path / "n.mp4"
    talk = tmp_path / "t.mp4"
    sfx = tmp_path / "x.wav"
    native.write_bytes(b"vid")
    talk.write_bytes(b"vid")
    sfx.write_bytes(b"s")
    from montage.engine.artifacts import ArtifactStore

    store = ArtifactStore(tmp_path)
    store.write("shot_prompts", {
        "version": "1",
        "shots": [
            {"shot_id": "a", "audio_source": "kling_prompt"},
            {"shot_id": "b"},
        ],
    })
    called: list[str] = []

    def overlay(src, ev, dest):
        called.append(Path(src).name)
        Path(dest).write_bytes(b"o")
        return Path(dest)

    result = PlaceAudio(overlay_fn=overlay).execute({
        "project_dir": str(tmp_path),
        "edit_decisions": {"cuts": [
            {"clip_path": str(native), "shot_id": "a"},
            {"clip_path": str(talk), "shot_id": "b"},
        ]},
        "scene_plan": {"scenes": [{"id": "sc", "shots": [
            {"shot_id": "a", "duration_seconds": 5, "start_seconds": 0},
            {"shot_id": "b", "duration_seconds": 5, "start_seconds": 5},
        ]}]},
        "events": [
            {"kind": "sfx", "path": str(sfx), "start_seconds": 0, "shot_id": "a"},
            {"kind": "sfx", "path": str(sfx), "start_seconds": 5, "shot_id": "b"},
        ],
    })
    assert result.success
    assert "n.mp4" not in called
    assert result.data["assemble_hints"]["mix_source_audio"] is True
    assert result.data["assemble_hints"]["ducking"] is True


class _FakeCatalog:
    def __init__(self, root, hits):
        self.root = root
        self._hits = hits

    def get(self, asset_id):
        return self._hits.get(str(asset_id or "").strip())

    def search(self, query="", category=None, top_k=5):
        return list(self._hits.values())[:top_k]


def _fake_hit(asset_id: str, path: Path) -> dict:
    return {
        "id": asset_id,
        "title": asset_id,
        "file": path.name,
        "path": str(path),
        "available": True,
        "attribution": "",
        "license": "CC0",
        "bpm": 0,
        "download_hint": "",
    }


def _two_scenes(bgm1="", bgm2=""):
    sc01 = {
        "id": "sc01",
        "description": "巷口",
        "start_seconds": 0,
        "end_seconds": 5,
        "shots": [],
    }
    sc02 = {
        "id": "sc02",
        "description": "天台",
        "start_seconds": 5,
        "end_seconds": 10,
        "shots": [],
    }
    if bgm1:
        sc01["bgm_id"] = bgm1
    if bgm2:
        sc02["bgm_id"] = bgm2
    return {"scenes": [sc01, sc02]}


def test_two_scene_bgm_ids_become_music_segments(tmp_path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    clip = tmp_path / "c.mp4"
    for p in (a, b, clip):
        p.write_bytes(b"x")
    cat = _FakeCatalog(tmp_path, {
        "bgm/dark-drone": _fake_hit("bgm/dark-drone", a),
        "bgm/calm-piano": _fake_hit("bgm/calm-piano", b),
    })
    pb = {"id": "x", "audio": {"bgm_id": "bgm/dark-drone", "skip_bgm": False, "music_volume": 0.25}}
    data = plan_soundtrack(_two_scenes("bgm/dark-drone", "bgm/calm-piano"), pb, catalog=cat)
    bgm = [e for e in data["events"] if e["kind"] == "bgm"]
    assert len(bgm) == 2
    assert all(not e.get("shot_id") for e in bgm)
    tool = PlaceAudio(overlay_fn=lambda src, ev, dest: Path(dest).write_bytes(b"x") or Path(dest))
    result = tool.execute({
        "edit_decisions": {"cuts": [{"clip_path": str(clip), "shot_id": "sc01_01"}]},
        "events": data["events"],
    })
    assert result.success
    assert result.data["music_path"] == ""
    assert len(result.data["music_segments"]) == 2


def test_no_bgm_id_keeps_single_music_path(tmp_path):
    song = tmp_path / "s.mp3"
    clip = tmp_path / "c.mp4"
    song.write_bytes(b"x")
    clip.write_bytes(b"x")
    cat = _FakeCatalog(tmp_path, {"bgm/dark-drone": _fake_hit("bgm/dark-drone", song)})
    pb = {"id": "x", "audio": {"bgm_id": "bgm/dark-drone", "skip_bgm": False, "music_volume": 0.25}}
    data = plan_soundtrack(_two_scenes(), pb, catalog=cat)
    bgm = [e for e in data["events"] if e["kind"] == "bgm"]
    assert len(bgm) == 1
    tool = PlaceAudio(overlay_fn=lambda src, ev, dest: Path(dest).write_bytes(b"x") or Path(dest))
    result = tool.execute({
        "edit_decisions": {"cuts": [{"clip_path": str(clip), "shot_id": "sc01_01"}]},
        "events": data["events"],
    })
    assert result.success
    assert result.data["music_path"] == str(song)
    assert result.data["music_segments"] == []


def test_missing_scene_bgm_id_fills_default_then_collapses(tmp_path):
    song = tmp_path / "s.mp3"
    song.write_bytes(b"x")
    cat = _FakeCatalog(tmp_path, {"bgm/dark-drone": _fake_hit("bgm/dark-drone", song)})
    pb = {"id": "x", "audio": {"bgm_id": "bgm/dark-drone", "skip_bgm": False}}
    data = plan_soundtrack(_two_scenes("bgm/dark-drone", ""), pb, catalog=cat)
    bgm = [e for e in data["events"] if e["kind"] == "bgm"]
    assert len(bgm) == 1
    assert bgm[0]["asset_id"] == "bgm/dark-drone"


def test_missing_scene_bgm_id_fills_other_default(tmp_path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    for p in (a, b):
        p.write_bytes(b"x")
    cat = _FakeCatalog(tmp_path, {
        "bgm/dark-drone": _fake_hit("bgm/dark-drone", a),
        "bgm/calm-piano": _fake_hit("bgm/calm-piano", b),
    })
    pb = {"id": "x", "audio": {"bgm_id": "bgm/calm-piano", "skip_bgm": False}}
    data = plan_soundtrack(_two_scenes("bgm/dark-drone", ""), pb, catalog=cat)
    bgm = [e for e in data["events"] if e["kind"] == "bgm"]
    assert len(bgm) == 2
    assert {e["asset_id"] for e in bgm} == {"bgm/dark-drone", "bgm/calm-piano"}
    assert all(not e.get("shot_id") for e in bgm)


def test_illegal_bgm_id_falls_back_to_one_track(tmp_path):
    song = tmp_path / "s.mp3"
    song.write_bytes(b"x")
    cat = _FakeCatalog(tmp_path, {"bgm/dark-drone": _fake_hit("bgm/dark-drone", song)})
    pb = {"id": "x", "audio": {"bgm_id": "bgm/dark-drone", "skip_bgm": False}}
    data = plan_soundtrack(_two_scenes("bgm/no-such-track", "bgm/dark-drone"), pb, catalog=cat)
    bgm = [e for e in data["events"] if e["kind"] == "bgm"]
    assert len(bgm) == 1
    assert bgm[0]["asset_id"] == "bgm/dark-drone"
    assert any("非法" in (f.get("message") or "") for f in data["findings"])


def test_scene_bgm_does_not_write_index():
    index = Path(__file__).resolve().parents[1] / "assets" / "bgm" / "INDEX.md"
    before = index.read_text(encoding="utf-8")
    plan = _two_scenes("bgm/dark-drone", "bgm/calm-piano")
    plan_soundtrack(plan, get_playbook("cyberpunk_neon"))
    assert index.read_text(encoding="utf-8") == before
