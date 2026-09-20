"""voice_director — 对白 → 音色分配（默认不合成）。

对白读取顺序：镜头 ``audio_prompt.dialogue`` → 回退 ``script.sections[].lines[]``。
音色来自 ``characters[].voice_id`` 或 ``montage.providers.voices`` 表。
默认返回 JSON；``synthesize=true`` 才调 tts_selector 写音频。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from montage.engine.artifacts import ArtifactStore
from montage.providers.voices import resolve_voice
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus
from montage.tools.shot_runner import collect_shots
from lib.shot_prompt_builder import dialogue_line_role, dialogue_line_text


def shots_with_timeline(scene_plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    """嵌套镜头补 scene_id，并按场景 start + 镜长累加绝对时间。"""
    shots = collect_shots(scene_plan, None)
    if not shots:
        return []
    timed: list[dict[str, Any]] = []
    by_scene: dict[str, list[dict[str, Any]]] = {}
    for shot in shots:
        by_scene.setdefault(str(shot.get("scene_id") or ""), []).append(shot)
    for scene in (scene_plan or {}).get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        sid = str(scene.get("id") or "")
        cursor = float(scene.get("start_seconds") or 0)
        group = by_scene.pop(sid, [])
        if not group:
            continue
        for shot in group:
            dur = float(shot.get("duration_seconds") or 0)
            item = dict(shot)
            item["start_seconds"] = round(cursor, 3)
            item["end_seconds"] = round(cursor + dur, 3)
            timed.append(item)
            cursor += dur
    for leftovers in by_scene.values():
        timed.extend(leftovers)
    return timed


def collect_dialogue(
    scene_plan: dict[str, Any] | None,
    script: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """镜头对白优先；该场镜头全无 dialogue 时才回退 script.lines。"""
    lines: list[dict[str, Any]] = []
    covered: set[str] = set()
    for shot in shots_with_timeline(scene_plan):
        scene_id = str(shot.get("scene_id") or "")
        shot_id = str(shot.get("shot_id") or "")
        dialogue = ((shot.get("audio_prompt") or {}) if isinstance(shot.get("audio_prompt"), dict) else {}).get("dialogue") or []
        added = False
        for item in dialogue:
            if not isinstance(item, dict):
                continue
            text = dialogue_line_text(item)
            if not text:
                continue
            lines.append({
                "scene_id": scene_id,
                "shot_id": shot_id,
                "speaker_id": dialogue_line_role(item) or "narrator",
                "text": text,
                "delivery": str(item.get("delivery") or ""),
                "start_seconds": float(shot.get("start_seconds") or 0),
                "end_seconds": float(shot.get("end_seconds") or 0),
                "source": "shot.audio_prompt.dialogue",
            })
            added = True
        if added:
            covered.add(scene_id)

    if not isinstance(script, dict):
        return lines
    sections = script.get("sections") or []
    scene_starts = {
        str(s.get("id") or ""): float(s.get("start_seconds") or 0)
        for s in ((scene_plan or {}).get("scenes") or [])
        if isinstance(s, dict)
    }
    for section in sections:
        if not isinstance(section, dict):
            continue
        sid = str(section.get("id") or "")
        if sid in covered:
            continue
        start = scene_starts.get(sid, 0.0)
        for item in section.get("lines") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            lines.append({
                "scene_id": sid,
                "shot_id": "",
                "speaker_id": str(item.get("speaker_id") or "narrator"),
                "text": text,
                "delivery": str(item.get("delivery") or ""),
                "start_seconds": start,
                "end_seconds": start + float(section.get("duration_seconds") or 0),
                "source": "script.sections.lines",
            })
        if any(ln["scene_id"] == sid for ln in lines):
            covered.add(sid)
    return lines


def _character_index(script: dict[str, Any] | None, scene_plan: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for char in (script or {}).get("characters") or []:
        if isinstance(char, dict) and char.get("id"):
            out[str(char["id"])] = char
    for char in (scene_plan or {}).get("character_registry") or []:
        if isinstance(char, dict) and char.get("id"):
            out.setdefault(str(char["id"]), char)
    return out


def assign_voices(
    lines: list[dict[str, Any]],
    *,
    characters: dict[str, dict[str, Any]] | None = None,
    provider: str = "doubao",
) -> list[dict[str, Any]]:
    chars = characters or {}
    segments: list[dict[str, Any]] = []
    for line in lines:
        speaker = str(line.get("speaker_id") or "narrator")
        char = chars.get(speaker) or {}
        gender = str(char.get("gender") or "")
        role = str(char.get("role") or "")
        if speaker in ("narrator", "旁白"):
            role = role or "narrator"
        resolved = resolve_voice(
            voice_id=str(char.get("voice_id") or ""),
            speaker_id=speaker,
            gender=gender,
            role=role,
            provider=provider,
        )
        seg = dict(line)
        seg["voice_id"] = resolved["id"]
        seg["voice"] = resolved["native"]
        seg["tts_provider"] = resolved["provider"]
        segments.append(seg)
    return segments


class VoiceDirector(BaseTool):
    name = "voice_director"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.HYBRID
    input_schema = {
        "type": "object",
        "properties": {
            "scene_plan": {"type": "object"},
            "script": {"type": "object"},
            "project_dir": {"type": "string"},
            "provider": {"type": "string", "description": "doubao / edge_tts / piper"},
            "synthesize": {
                "type": "boolean",
                "default": False,
                "description": "默认 false：只分配音色。true 才调用 tts_selector",
            },
        },
    }

    def __init__(
        self,
        *,
        tts_execute: Callable[[dict[str, Any]], ToolResult] | None = None,
    ) -> None:
        self._tts_execute = tts_execute

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        project_dir = str(inputs.get("project_dir") or "")
        store = ArtifactStore(project_dir) if project_dir else None
        scene_plan = inputs.get("scene_plan") if isinstance(inputs.get("scene_plan"), dict) else None
        script = inputs.get("script") if isinstance(inputs.get("script"), dict) else None
        if store:
            scene_plan = scene_plan or store.read("scene_plan")
            script = script or store.read("script")
        if not scene_plan and not script:
            return ToolResult(success=False, error="需要 scene_plan 或 script")

        provider = str(inputs.get("provider") or "doubao")
        lines = collect_dialogue(scene_plan, script)
        chars = _character_index(script, scene_plan)
        segments = assign_voices(lines, characters=chars, provider=provider)
        findings: list[dict[str, str]] = []
        if not segments:
            findings.append({
                "severity": "warning",
                "field": "dialogue",
                "message": "没有对白（镜头 audio_prompt.dialogue 与 script.lines 皆空）",
                "proposed_fix": "补 lines[] 或镜头 dialogue",
            })
        fallbacks = [s for s in segments if s.get("source") == "script.sections.lines"]
        if fallbacks:
            findings.append({
                "severity": "warning",
                "field": "dialogue",
                "message": f"{len(fallbacks)} 句回退自 script.sections[].lines[]",
                "proposed_fix": "在镜头 audio_prompt.dialogue 写对白以免两套台词",
            })

        data: dict[str, Any] = {
            "segments": segments,
            "count": len(segments),
            "provider": provider,
            "findings": findings,
            "narration_sections": [],
        }
        if not inputs.get("synthesize"):
            return ToolResult(success=True, data=data)

        tts_fn = self._tts_execute
        if tts_fn is None:
            from montage.providers.selectors import TtsSelector

            tts_fn = TtsSelector().execute
        out_dir = Path(project_dir) / "assets" / "audio" if project_dir else Path("assets/audio")
        out_dir.mkdir(parents=True, exist_ok=True)
        sections: list[dict[str, Any]] = []
        for i, seg in enumerate(segments):
            path = str(out_dir / f"{seg.get('shot_id') or seg.get('scene_id') or 'line'}_{i:02d}.mp3")
            result = tts_fn({
                "text": seg["text"],
                "voice": seg["voice"],
                "voice_id": seg["voice"],
                "output_path": path,
                "project_dir": project_dir,
            })
            if not result.success:
                findings.append({
                    "severity": "warning",
                    "field": f"segments[{i}]",
                    "message": result.error or "TTS 失败",
                    "proposed_fix": "检查 tts_selector 密钥或改 provider=edge_tts",
                })
                continue
            audio = ""
            if isinstance(result.data, dict):
                audio = str(result.data.get("output") or result.data.get("path") or path)
            else:
                audio = path
            seg["audio"] = audio
            sections.append({
                "id": seg.get("shot_id") or f"{seg.get('scene_id')}_{i:02d}",
                "narration_audio": audio,
                "speaker_id": seg.get("speaker_id"),
                "voice": seg.get("voice"),
            })
        data["narration_sections"] = sections
        data["findings"] = findings
        # narration_path 修复（v8.2 P1）：sections 落盘产物。原实现只挂在返回
        # data 里，produce 的 voice 步骤标 ok 后不保存，assemble 拿不到旁白轨
        # ——TTS 对白永远混不进成片。落盘后 assemble 步骤从产物读取。
        if store and sections:
            store.write("narration_sections", {"version": "1", "sections": sections}, schema=None)
        return ToolResult(success=True, data=data)
