"""director — --review director 的 idea 侧停点（P-D0）。

磁盘上 bible / scene_plan 始终存全量。artifacts/REVIEW.md 与 review_card.json
是展示层，每次停点机器重写。不写 human_approved；步 1～3 只推进 status。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from montage.engine.artifacts import ArtifactStore
from montage.playbooks import get_playbook, list_playbooks

DIRECTOR_AWAIT = (
    "await_setup",
    "await_outline",
    "await_design",
    "await_cast",
    "await_shots",
    "await_frames",
    "await_final_prompt",
    "await_clips",
)

DIRECTOR_STEP = {
    "await_setup": "setup",
    "await_outline": "outline",
    "await_design": "design",
    "await_cast": "cast",
    "await_shots": "shots",
    "await_frames": "frames",
    "await_final_prompt": "final_prompt",
    "await_clips": "clips",
    "await_retry": "retry",
}

DIRECTOR_LABEL = {
    "await_setup": "第 1 步 · 需求确认",
    "await_outline": "第 2 步 · 剧本大纲",
    "await_design": "第 3 步 · 编号设计表",
    "await_cast": "第 4 步 · 视觉资产",
    "await_shots": "第 5 步 · 分镜脚本",
    "await_frames": "第 6 步 · 关键帧",
    "await_final_prompt": "最终提示词总览",
    "await_clips": "第 7 步 · 单镜视频",
    "await_retry": "重抽确认",
}
DIRECTOR_LABEL_KLING = {
    "await_setup": "第 1 步 · 需求确认",
    "await_outline": "第 2 步 · 剧本大纲",
    "await_design": "第 3 步 · 编号设计表",
    "await_shots": "第 4 步 · 分镜脚本",
    "await_cast": "第 5 步 · 视觉资产",
    "await_frames": "第 6 步 · 关键帧",
    "await_final_prompt": "最终提示词总览",
    "await_clips": "第 7 步 · 单镜视频",
    "await_retry": "重抽确认",
}

CAST_ACTION_CHOICES = [
    {"id": "pass", "label": "过"},
    {"id": "retry", "label": "重抽（--retry <id> --resume）"},
]
TURNAROUND_CHOICES = [
    {"id": "generate", "label": "出四视图（默认）"},
    {"id": "skip", "label": "本角色跳过四视图"},
]
SHOT_SIZE_CHOICES = [
    {"id": "wide", "label": "全景"},
    {"id": "medium", "label": "中景"},
    {"id": "medium_close", "label": "中近景"},
    {"id": "close_up", "label": "近景"},
    {"id": "close", "label": "特写"},
]
CAMERA_MOVE_CHOICES = [
    {"id": "static", "label": "固定"},
    {"id": "dolly_in", "label": "慢推"},
    {"id": "dolly_out", "label": "拉"},
    {"id": "pan_right", "label": "摇"},
    {"id": "tracking_right", "label": "跟"},
    {"id": "crane_up", "label": "升"},
    {"id": "crane_down", "label": "降"},
]
ANGLE_CHOICES = [
    {"id": "平视", "label": "平视"},
    {"id": "俯拍", "label": "俯拍"},
    {"id": "仰拍", "label": "仰拍"},
    {"id": "过肩", "label": "过肩"},
]
BUDGET_CHOICES = [
    {"id": "hero", "label": "hero（I2V）"},
    {"id": "talk", "label": "talk（静图 Ken Burns）"},
    {"id": "establishing", "label": "establishing（空镜 Ken Burns）"},
]
CUT_CHOICES = [
    {"id": "bridge", "label": "bridge（尾帧桥）"},
    {"id": "hard", "label": "hard（切断衔接）"},
]
ROLE_CHOICES = [
    {"id": "protagonist", "label": "主角"},
    {"id": "supporting", "label": "配角"},
    {"id": "antagonist", "label": "对手"},
    {"id": "functional", "label": "功能角色"},
    {"id": "narrator", "label": "旁白"},
]
SPLIT_MODE_CHOICES = [
    {"id": "narrative", "label": "对白剧情（保持叙事向 playbook）"},
    {"id": "spoken_explain", "label": "旁白解说（只换 playbook=spoken_explain）"},
    {"id": "documentary_restraint", "label": "纪录克制（只换 playbook=documentary_restraint）"},
]
REWORK_CHOICES = [
    {"id": "regenerate", "label": "整镜重抽（默认）"},
    {"id": "feature", "label": "视频参考优化（≤10s）"},
]

_IDEA_IGNORED = {
    "severity": "warning",
    "field": "idea",
    "message": "导演停点已开始，已忽略 --idea（避免重置进度）",
    "proposed_fix": "改 series_bible.json / scene_plan.json 后 produce --resume",
}


def director_labels(video_loop: str = "") -> dict[str, str]:
    if str(video_loop or "").strip().lower() == "kling":
        return DIRECTOR_LABEL_KLING
    return DIRECTOR_LABEL


def is_director_await(status: str) -> bool:
    return str(status or "") in DIRECTOR_AWAIT


def leave_for_generate(
    status: str,
    resume: bool,
    *,
    frames_ok: bool = False,
    retry: bool = False,
    clips_ok: bool = True,
) -> bool:
    """final_prompt 点头才进 I2V；await_frames 永不放行（先停 await_final_prompt）；clips 点头后进 W0（坏镜未清则留下）。"""
    if not resume:
        return False
    st = str(status or "")
    if st == "await_final_prompt":
        return True
    if st == "await_clips":
        if retry:
            return True
        return bool(clips_ok)
    return False


def has_critical(findings: list[dict[str, Any]]) -> bool:
    return any(str(f.get("severity") or "") == "critical" for f in findings if isinstance(f, dict))


def idea_ignored_finding() -> dict[str, Any]:
    return dict(_IDEA_IGNORED)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _shot_picture(shot: dict[str, Any]) -> str:
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    env = vd.get("environment")
    if isinstance(env, str) and env.strip():
        return env.strip()
    bits: list[str] = []
    for sub in vd.get("subjects") or []:
        if not isinstance(sub, dict):
            continue
        act = sub.get("action") if isinstance(sub.get("action"), dict) else {}
        piece = " ".join(p for p in (_text(act.get("verb")), _text(act.get("manner"))) if p)
        if piece:
            bits.append(piece)
    return "；".join(bits)


def _shot_form_rows(shot: dict[str, Any]) -> list[str]:
    """本镜显式声明的出场形态 "cid:form" 列表（缺省不列）。"""
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    out: list[str] = []
    for sub in vd.get("subjects") or []:
        if not isinstance(sub, dict):
            continue
        cid = _text(sub.get("id"))
        fid = _text(sub.get("form_id"))
        if cid and fid:
            out.append(f"{cid}:{fid}")
    return out


def _shot_dialogue_text(shot: dict[str, Any]) -> str:
    from lib.shot_prompt_builder import dialogue_line_text

    ap = shot.get("audio_prompt") if isinstance(shot.get("audio_prompt"), dict) else {}
    lines = ap.get("dialogue") or []
    texts: list[str] = []
    for item in lines:
        t = dialogue_line_text(item)
        if t:
            texts.append(t)
    return "\n".join(texts)


def _plan_shots(scene_plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for scene in (scene_plan or {}).get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for shot in scene.get("shots") or []:
            if isinstance(shot, dict):
                out.append(shot)
    return out


def _manifest(store: ArtifactStore) -> dict[str, Any]:
    raw = store.read("asset_manifest")
    return raw if isinstance(raw, dict) else {}


def _cast_summary_rows(
    bible: dict[str, Any],
    manifest: dict[str, Any] | None,
    project_dir: str,
    scene_plan: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    from montage.engine.policy import load_loop_policy
    from montage.tools.shot_runner import _cast_ref_for_job, _item_ready, collect_cast_jobs

    labels = {
        "portrait": "全身照",
        "turnaround": "四视图",
        "scene_ref": "场景图",
        "prop": "道具图",
    }
    loop = str(load_loop_policy(project_dir).get("video_loop") or "").strip().lower() if project_dir else ""
    rows: list[dict[str, Any]] = []
    ok_n = 0
    fail_n = 0
    for job in collect_cast_jobs(bible, scene_plan=scene_plan, video_loop=loop):
        kind = str(job.get("kind") or "")
        subject = str(job.get("subject") or "")
        name = (
            str(job.get("character_id") or job.get("location_id") or job.get("prop_id") or "")
        )
        fid = str(job.get("form_id") or "")
        if fid:
            form = job.get("form") if isinstance(job.get("form"), dict) else {}
            name = f"{name}({_text(form.get('name')) or fid})"
        ref = _cast_ref_for_job(job, manifest)
        ready = _item_ready(project_dir, ref)
        if ready:
            ok_n += 1
            status = "过"
        else:
            fail_n += 1
            status = "失败"
        path = ""
        if isinstance(ref, dict):
            path = str(ref.get("path") or ref.get("url") or "")
        rows.append({
            "label": f"{name} {labels.get(kind, kind)}",
            "value": f"{status}" + (f" · {path}" if path else ""),
            "subject": subject,
            "ready": ready,
            "path": path,
        })
    return rows, ok_n, fail_n


def _bible(store: ArtifactStore) -> dict[str, Any]:
    raw = store.read("series_bible")
    return raw if isinstance(raw, dict) else {}


def _proposal(store: ArtifactStore) -> dict[str, Any]:
    raw = store.read("proposal_packet")
    return raw if isinstance(raw, dict) else {}


def _script(store: ArtifactStore) -> dict[str, Any]:
    raw = store.read("script")
    return raw if isinstance(raw, dict) else {}


def _scene_plan(store: ArtifactStore) -> dict[str, Any]:
    raw = store.read("scene_plan")
    return raw if isinstance(raw, dict) else {}


def _playbook_title(playbook_id: str) -> str:
    pb = get_playbook(playbook_id) if playbook_id else None
    if isinstance(pb, dict):
        return _text(pb.get("title")) or playbook_id
    return playbook_id or "（未选）"


def _split_mode(playbook_id: str) -> str:
    if playbook_id == "spoken_explain":
        return "旁白解说"
    if playbook_id == "documentary_restraint":
        return "纪录克制"
    return "对白剧情"


def _profile_choices() -> list[str]:
    from montage.compose.profiles import list_profiles

    return [str(p.get("name") or "") for p in list_profiles() if p.get("name")]


def _playbook_choices() -> list[str]:
    return [str(p.get("id") or "") for p in list_playbooks() if p.get("id")]


def validate_setup(bible: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not _text(bible.get("title")):
        findings.append({
            "severity": "critical",
            "field": "series_bible.title",
            "message": "缺少标题",
            "proposed_fix": "写入 series_bible.title",
        })
    duration = bible.get("target_duration_seconds")
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        seconds = 0.0
    if seconds <= 0:
        findings.append({
            "severity": "critical",
            "field": "series_bible.target_duration_seconds",
            "message": "缺少目标时长",
            "proposed_fix": "写入 series_bible.target_duration_seconds（秒）",
        })
    playbook_id = _text(bible.get("playbook"))
    if not playbook_id:
        findings.append({
            "severity": "critical",
            "field": "series_bible.playbook",
            "message": "缺少 playbook",
            "proposed_fix": "写入 series_bible.playbook（不要改 project.json 的 pipeline）",
        })
    elif get_playbook(playbook_id) is None:
        findings.append({
            "severity": "critical",
            "field": "series_bible.playbook",
            "message": f"未知 playbook {playbook_id}",
            "proposed_fix": "换成 montage.playbooks 里已有的 id",
        })
    return findings


def validate_outline(bible: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    chars = [c for c in (bible.get("characters") or []) if isinstance(c, dict)]
    named = [_text(c.get("name")) for c in chars]
    if not any(named):
        findings.append({
            "severity": "critical",
            "field": "series_bible.characters",
            "message": "大纲需要至少一个有姓名的角色",
            "proposed_fix": "写入 characters[].name",
        })
    locations = [loc for loc in (bible.get("locations") or []) if isinstance(loc, dict)]
    if not locations:
        findings.append({
            "severity": "warning",
            "field": "series_bible.locations",
            "message": "还没有地点卡；scenes[] 是幕，不要把地点写进幕里冒充",
            "proposed_fix": "写入 locations[]（id/name/sensory）；单地点短片可以只有一条",
        })
    return findings


def gold_line_findings(bible: dict[str, Any], script: dict[str, Any] | None) -> list[dict[str, Any]]:
    gold = [_text(line) for line in (bible.get("gold_lines") or [])]
    gold = [line for line in gold if line]
    if not gold:
        return []
    blobs: list[str] = []
    for section in (script or {}).get("sections") or []:
        if not isinstance(section, dict):
            continue
        for line in section.get("lines") or []:
            if isinstance(line, dict):
                blobs.append(_text(line.get("text")))
            elif isinstance(line, str):
                blobs.append(_text(line))
    joined = "\n".join(blobs)
    findings: list[dict[str, Any]] = []
    for line in gold:
        if line not in joined:
            findings.append({
                "severity": "warning",
                "field": "series_bible.gold_lines",
                "message": f"金句尚未写入任何镜头 lines[]：{line}",
                "proposed_fix": "在第 5 步把该句写进对应镜，不要让 compile 盲贴第一镜",
            })
    return findings


def _finding_lines(findings: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in findings:
        if not isinstance(item, dict):
            continue
        sev = _text(item.get("severity") or "info")
        msg = _text(item.get("message"))
        if msg:
            lines.append(f"- [{sev}] {msg}")
    return lines


def _field(
    path: str,
    label: str,
    value: Any,
    *,
    input_kind: str = "text",
    note: str = "",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": path,
        "label": label,
        "value": value if value is not None else "",
        "input": input_kind,
    }
    if note:
        row["note"] = note
    return row


def _kling_rework_fields(shot: dict[str, Any], sid: str) -> list[dict[str, Any]]:
    from montage.engine.rework import _has_dialogue

    title = _text(shot.get("title")) or sid
    note = "未写则 regenerate；feature 仅 ≤10s 且需公网成片 URL；edit 不在默认二选一"
    if _has_dialogue(shot):
        note += "；选 feature 会丢掉 native 口播（audio=off）"
    return [
        _field(
            f"scene_plan.shots[{sid}].rework_mode",
            f"{title} 返工方式",
            _text(shot.get("rework_mode")) or "regenerate",
            input_kind="select",
            note=note,
        ),
        _field(
            f"scene_plan.shots[{sid}].revision_note",
            f"{title} 修正一句",
            _text(shot.get("revision_note")),
            input_kind="textarea",
            note="feature 用这句话；不要贴整段 generate 词",
        ),
    ]


def build_review_card(
    status: str,
    *,
    bible: dict[str, Any],
    proposal: dict[str, Any] | None = None,
    script: dict[str, Any] | None = None,
    scene_plan: dict[str, Any] | None = None,
    findings: list[dict[str, Any]] | None = None,
    manifest: dict[str, Any] | None = None,
    project_dir: str = "",
    retry_ids: list[str] | None = None,
) -> dict[str, Any]:
    step = DIRECTOR_STEP.get(status, "setup")
    from montage.engine.policy import load_loop_policy
    loop_policy = load_loop_policy(project_dir) if project_dir else {}
    loop = str(loop_policy.get("video_loop") or "").strip().lower()
    cast_ref_kind = str(loop_policy.get("cast_ref_kind") or "portrait")
    heading = director_labels(loop).get(status, status)
    proposal = proposal or {}
    findings = [f for f in (findings or []) if isinstance(f, dict)]
    playbook_id = _text(bible.get("playbook"))
    profile = _text(proposal.get("output_profile"))
    duration = bible.get("target_duration_seconds")
    summary: list[dict[str, Any]]
    fields: list[dict[str, Any]]
    choices: dict[str, Any] = {
        "output_profile": _profile_choices(),
        "playbook": _playbook_choices(),
        "shot_split_mode": SPLIT_MODE_CHOICES,
        "frames_mode": ["preview", "reference_first", "keyframe"],
    }
    from montage.engine.policy import normalize_frames_mode

    frames_mode = normalize_frames_mode(proposal.get("frames_mode"))

    if status == "await_setup":
        summary = [
            {"label": "标题", "value": _text(bible.get("title")) or "（空）"},
            {"label": "时长", "value": duration if duration not in (None, "") else "（空）"},
            {"label": "画幅", "value": profile or "（未设 proposal_packet.output_profile）"},
            {"label": "视觉风格", "value": _playbook_title(playbook_id)},
            {"label": "拆分镜模式", "value": _split_mode(playbook_id)},
            {"label": "首帧模式", "value": frames_mode},
            {
                "label": "身份参考",
                "value": cast_ref_kind + ("（可灵强制四视图）" if loop == "kling" else ""),
            },
        ]
        env = bible.get("environment") if isinstance(bible.get("environment"), dict) else {}
        fields = [
            _field("bible.title", "标题", _text(bible.get("title"))),
            _field("bible.synopsis", "梗概", _text(bible.get("synopsis"))),
            _field("bible.target_duration_seconds", "时长秒", duration, input_kind="number"),
            _field("proposal_packet.output_profile", "画幅", profile, input_kind="select"),
            _field(
                "proposal_packet.frames_mode",
                "首帧模式",
                frames_mode,
                input_kind="select",
                note="默认 preview（首帧仅审图，不入视频）；改 reference_first/keyframe 会改变每一镜输入",
            ),
            _field(
                "bible.playbook",
                "视觉风格 / 拆分镜模式",
                playbook_id,
                input_kind="select",
                note="只换 playbook，不要改 project.json 的 pipeline",
            ),
            _field("bible.environment.era", "时代背景", _text(env.get("era")) if isinstance(env, dict) else ""),
            _field("bible.extra_notes", "补充说明", _text(bible.get("extra_notes"))),
        ]
    elif status == "await_outline":
        chars = [c for c in (bible.get("characters") or []) if isinstance(c, dict)]
        names = " / ".join(_text(c.get("name")) or _text(c.get("id")) for c in chars) or "（无角色）"
        locs = [loc for loc in (bible.get("locations") or []) if isinstance(loc, dict)]
        loc_names = " / ".join(_text(loc.get("name")) or _text(loc.get("id")) for loc in locs) or "（无地点卡）"
        scenes = [sc for sc in (bible.get("scenes") or []) if isinstance(sc, dict)]
        blurb = _text(scenes[0].get("narration")) if scenes else ""
        summary = [
            {"label": "角色", "value": names},
            {"label": "地点", "value": loc_names},
            {"label": "主题", "value": _text(bible.get("theme")) or "（空）"},
            {"label": "段落摘要", "value": blurb[:80] or "（空）"},
        ]
        fields = []
        for char in chars:
            cid = _text(char.get("id"))
            if not cid:
                continue
            label = _text(char.get("name")) or cid
            fields.append(_field(f"bible.characters[{cid}].name", f"{label} 姓名", _text(char.get("name"))))
            fields.append(_field(f"bible.characters[{cid}].age", f"{label} 年龄段", _text(char.get("age"))))
            fields.append(_field(
                f"bible.characters[{cid}].personality", f"{label} 性格", _text(char.get("personality")),
            ))
            fields.append(_field(
                f"bible.characters[{cid}].role", f"{label} 定位", _text(char.get("role")),
                input_kind="select",
            ))
        for loc in locs:
            lid = _text(loc.get("id"))
            if not lid:
                continue
            label = _text(loc.get("name")) or lid
            fields.append(_field(f"bible.locations[{lid}].name", f"{label} 地点名", _text(loc.get("name"))))
            fields.append(_field(
                f"bible.locations[{lid}].sensory", f"{label} 感官", _text(loc.get("sensory")),
                input_kind="textarea",
            ))
        music = bible.get("music_direction") if isinstance(bible.get("music_direction"), dict) else {}
        fields.extend([
            _field("bible.music_direction.instruments", "BGM 乐器", _text(music.get("instruments"))),
            _field("bible.music_direction.arc", "BGM 情绪弧", _text(music.get("arc"))),
            _field("bible.music_direction.sync", "BGM 同步点", _text(music.get("sync"))),
            _field("bible.music_direction.bgm_id", "BGM 曲目 id（可选）", _text(music.get("bgm_id"))),
        ])
        for prop in [p for p in (bible.get("props") or []) if isinstance(p, dict)]:
            pid = _text(prop.get("id") or prop.get("name"))
            if not pid:
                continue
            label = _text(prop.get("name")) or pid
            fields.append(_field(f"bible.props[{pid}].name", f"{label} 名称", _text(prop.get("name"))))
            fields.append(_field(f"bible.props[{pid}].purpose", f"{label} 情感作用", _text(prop.get("purpose"))))
        for sc in scenes:
            sid = _text(sc.get("id"))
            if not sid:
                continue
            fields.append(_field(
                f"bible.scenes[{sid}].narration",
                f"{_text(sc.get('title') or sid)} 段落摘要",
                _text(sc.get("narration")),
                input_kind="textarea",
            ))
        gold = bible.get("gold_lines") or []
        gold_text = "\n".join(_text(x) for x in gold if _text(x))
        fields.append(_field(
            "bible.gold_lines", "必选对白（每行一句，最多 6）", gold_text, input_kind="textarea",
        ))
        fields.append(_field("bible.theme", "主题", _text(bible.get("theme"))))
        struct = bible.get("structure") if isinstance(bible.get("structure"), dict) else {}
        for beat in ("hook", "escalation", "reveal", "landing"):
            fields.append(_field(f"bible.structure.{beat}", f"四拍 · {beat}", _text(struct.get(beat))))
        choices["role"] = ROLE_CHOICES
    elif status == "await_design":
        chars = [c for c in (bible.get("characters") or []) if isinstance(c, dict)]
        char_line = "；".join(
            f"{_text(c.get('name')) or _text(c.get('id'))} {_text(c.get('appearance'))[:20]}"
            for c in chars
        ) or "（无）"
        locs = [loc for loc in (bible.get("locations") or []) if isinstance(loc, dict)]
        props = [p for p in (bible.get("props") or []) if isinstance(p, dict)]
        summary = [
            {"label": "角色外观", "value": char_line},
            {"label": "地点", "value": " / ".join(_text(loc.get("name")) or _text(loc.get("id")) for loc in locs) or "（无）"},
            {"label": "道具", "value": " / ".join(_text(p.get("name")) or _text(p.get("id")) for p in props) or "（无）"},
        ]
        fields = []
        for char in chars:
            cid = _text(char.get("id"))
            if not cid:
                continue
            label = _text(char.get("name")) or cid
            fields.append(_field(
                f"bible.characters[{cid}].appearance", f"{label} 可生图外观",
                _text(char.get("appearance")), input_kind="textarea", note="不要改 id",
            ))
            fields.append(_field(
                f"bible.characters[{cid}].outfit", f"{label} 服装", _text(char.get("outfit")),
            ))
        for loc in locs:
            lid = _text(loc.get("id"))
            if not lid:
                continue
            label = _text(loc.get("name")) or lid
            fields.append(_field(
                f"bible.locations[{lid}].appearance", f"{label} 空镜",
                _text(loc.get("appearance")), input_kind="textarea", note="无人环境；绑定 location_id",
            ))
        for prop in props:
            pid = _text(prop.get("id") or prop.get("name"))
            if not pid:
                continue
            label = _text(prop.get("name")) or pid
            fields.append(_field(
                f"bible.props[{pid}].appearance", f"{label} 静物",
                _text(prop.get("appearance")), input_kind="textarea", note="白底单主体，无人物、无手持",
            ))
    elif status == "await_cast":
        from montage.tools._shot_refs import character_forms, effective_skip_turnaround

        rows, ok_n, fail_n = _cast_summary_rows(bible, manifest, project_dir, scene_plan)
        headline = "全部成功" if rows and fail_n == 0 else (f"{fail_n} 项失败" if fail_n else "尚无定妆作业")
        summary = [
            {"label": "清单", "value": "；".join(f"{r['label']} {r['value'].split(' · ')[0]}" for r in rows) or "（空）"},
            {"label": "结果", "value": f"{headline}（{ok_n} 过 / {fail_n} 失败）"},
        ]
        form_bits: list[str] = []
        est_total = 0
        for char in (bible.get("characters") or []):
            if not isinstance(char, dict):
                continue
            cid = _text(char.get("id"))
            if not cid:
                continue
            explicit = [
                f for f in (char.get("forms") or [])
                if isinstance(f, dict) and str(f.get("id") or "").strip()
            ]
            if not explicit:
                continue
            if loop == "kling":
                est = 1  # 可灵忽略 forms，单形态一张拼板
            else:
                est = sum(
                    (1 + (0 if effective_skip_turnaround(char, f) else 1)) for f in character_forms(char)
                )
            est_total += est
            form_bits.append(f"{cid} {len(explicit)} 形态 / 预计 {est} 张")
        if form_bits:
            summary.append({
                "label": "形态",
                "value": "；".join(form_bits) + f"（合计预计 {est_total} 张）",
            })
        summary.append({
            "label": "身份参考",
            "value": (
                "turnaround（可灵强制四视图）" if loop == "kling"
                else f"{cast_ref_kind}（每形态只发一张；turnaround 由 form/character/packet 显式开启）"
            ),
        })
        fields = []
        for row in rows:
            fields.append(_field(
                f"retry:{row['subject']}",
                f"{row['label']} 过/重抽",
                "过" if row["ready"] else "失败",
                input_kind="select",
                note=f"重抽：--retry {row['subject']} --resume；缩略图 {row['path'] or '（无）'}",
            ))
        chars = [c for c in (bible.get("characters") or []) if isinstance(c, dict)]
        for char in chars:
            cid = _text(char.get("id"))
            if not cid:
                continue
            skip = bool(char.get("skip_turnaround"))
            turn_note = (
                "可灵环忽略 skip_turnaround（一张拼板已含四视图）"
                if loop == "kling"
                else "改 characters[].skip_turnaround 后 --resume"
            )
            fields.append(_field(
                f"bible.characters[{cid}].skip_turnaround",
                f"{_text(char.get('name')) or cid} 四视图",
                "跳过" if skip else "出四视图",
                input_kind="select",
                note=turn_note,
            ))
            fields.append(_field(
                f"bible.characters[{cid}].cast_note",
                f"{_text(char.get('name')) or cid} 重抽修正",
                _text(char.get("cast_note")),
                note="自由文本，例：眼镜改成圆框",
            ))
        choices.update({
            "cast_action": CAST_ACTION_CHOICES,
            "turnaround": TURNAROUND_CHOICES,
        })
    elif status == "await_frames":
        from montage.tools.shot_runner import first_frame_item

        bindings: dict[str, Any] = {}
        if project_dir:
            from montage.engine.artifacts import ArtifactStore as _BindStore

            doc = _BindStore(project_dir).read("image_bindings")
            if isinstance(doc, dict) and isinstance(doc.get("shots"), dict):
                bindings = doc["shots"]

        rows: list[dict[str, Any]] = []
        ok_n = 0
        fail_ids: list[str] = []
        bound_n = 0
        for shot in _plan_shots(scene_plan):
            sid = _text(shot.get("shot_id"))
            ref = first_frame_item(shot, manifest, project_dir)
            ready = bool(ref)
            if ready:
                ok_n += 1
            elif sid:
                fail_ids.append(sid)
            binds = bindings.get(sid) if isinstance(bindings.get(sid), dict) else {}
            if binds:
                bound_n += 1
            forms = "/".join(
                f"{_text(f.get('character_id'))}"
                + (f":{_text(f.get('form_id'))}" if _text(f.get("form_id")) else "")
                for f in (binds.get("character_forms") or [])
                if isinstance(f, dict)
            )
            rows.append({
                "sid": sid,
                "title": _text(shot.get("title")) or sid or "（无标题）",
                "klass": _text(shot.get("shot_budget_class")) or "talk",
                "ready": ready,
                "path": _text((ref or {}).get("path")),
                "bind": " · ".join(x for x in (
                    forms or "无形态",
                    f"{len(binds.get('refs') or [])} 参考" if binds else "",
                    _text(binds.get("location_sensory")),
                ) if x),
            })
        summary = [{"label": "首帧", "value": f"{ok_n}/{len(rows)} 成功" if rows else "尚无镜头"}]
        if rows:
            summary.append({"label": "绑定", "value": f"{bound_n}/{len(rows)} 镜"})
        if fail_ids:
            summary.append({"label": "失败镜号", "value": "、".join(fail_ids)})
        long_film = len(rows) > 12
        fields = []
        for row in rows:
            if long_film and row["klass"] != "hero" and row["ready"]:
                continue
            fields.append(_field(
                f"retry:{row['sid']}",
                f"{row['title']} 过/重抽",
                "过" if row["ready"] else "失败",
                input_kind="select",
                note=(
                    f"重抽：--retry {row['sid']} --resume；图 {row['path'] or '（无）'}"
                    f"；绑定 {row['bind'] or '（无）'}"
                ),
            ))
        if long_film:
            summary.append({
                "label": "折叠",
                "value": "超过 12 镜：talk 已折叠，hero 与失败强制展开",
            })
        choices["cast_action"] = CAST_ACTION_CHOICES
    elif status == "await_final_prompt":
        sp_raw = ArtifactStore(Path(project_dir)).read("shot_prompts") if project_dir else {}
        shot_prompts = sp_raw if isinstance(sp_raw, dict) else {}
        prompt_shots = [s for s in (shot_prompts or {}).get("shots") or [] if isinstance(s, dict)]
        if not prompt_shots:
            summary = [{"label": "提示词", "value": "尚未构建（失败或未跑 prompt_preview）"}]
            fields = []
            findings = list(findings)
            findings.append({
                "severity": "critical",
                "field": "shot_prompts",
                "message": "最终提示词尚未构建；请先 produce --resume 跑 prompt_preview 或查 shot_runner 报错",
                "proposed_fix": "produce --resume 重跑提示词预览",
            })
        else:
            ok_n = 0
            fail_ids: list[str] = []
            lines: list[str] = []
            fields = []
            for s in prompt_shots:
                sid = _text(s.get("shot_id"))
                dur = s.get("duration_seconds")
                fp = _text(s.get("first_frame_prompt"))
                vp = _text(s.get("video_prompt"))
                if fp or vp:
                    ok_n += 1
                elif sid:
                    fail_ids.append(sid)
                n_img = len(fp)
                n_vid = len(vp)
                lines.append(
                    f"{sid or '（无号）'} · {dur if dur not in (None, '') else '?'}s · "
                    f"图词 {n_img} 字 / 视频词 {n_vid} 字"
                )
                title = _text(s.get("shot_language")) or sid or "（无标题）"
                fields.append(_field(
                    f"shot_prompts.shots[{sid}].video_prompt" if sid else "shot_prompts",
                    f"{title} 视频提示词",
                    vp,
                    input_kind="textarea",
                    note="只读预览；改提示词请回 scene_plan / series_bible",
                ))
                fields.append(_field(
                    f"shot_prompts.shots[{sid}].first_frame_prompt" if sid else "shot_prompts",
                    f"{title} 首帧提示词",
                    fp,
                    input_kind="textarea",
                    note="只读预览；改提示词请回 scene_plan / series_bible",
                ))
            summary = [
                {"label": "提示词", "value": f"{ok_n}/{len(prompt_shots)} 镜已构建"},
                {"label": "各镜", "value": "；".join(lines) or "（无镜头）"},
            ]
            if fail_ids:
                summary.append({"label": "缺失镜", "value": "、".join(fail_ids)})
            fields.append(_field(
                "shot_prompts", "修改方式", "回 scene_plan / series_bible 改字段后 --resume",
                input_kind="textarea",
                note="本卡只读，不提供改写入口",
            ))
    elif status == "await_retry":
        ids = [str(x).strip() for x in (retry_ids or []) if str(x).strip()]
        by_id = {_text(s.get("shot_id")): s for s in _plan_shots(scene_plan)}
        fields = []
        kling = loop == "kling"
        for sid in ids:
            shot = by_id.get(sid) or {}
            if kling:
                fields.extend(_kling_rework_fields(shot, sid))
        summary = [{"label": "重抽镜", "value": "、".join(ids) or "（空）"}]
        if kling:
            summary.append({
                "label": "默认",
                "value": "未写 rework_mode 则整镜 regenerate；edit 不在默认二选一",
            })
            choices["rework_mode"] = REWORK_CHOICES
        else:
            summary.append({
                "label": "默认",
                "value": "按现网返工启发式（Seedance 有公网 URL 可 edit）",
            })
    elif status == "await_clips":
        from montage.tools.shot_runner import shot_final_ready

        ok_n = 0
        fail_n = 0
        fail_lines: list[str] = []
        fields = []
        kling = loop == "kling"
        for shot in _plan_shots(scene_plan):
            sid = _text(shot.get("shot_id"))
            title = _text(shot.get("title")) or sid or "（无标题）"
            ready = shot_final_ready(shot, manifest, project_dir)
            if ready:
                ok_n += 1
            else:
                fail_n += 1
                fail_lines.append(sid or title)
                if kling and sid:
                    fields.extend(_kling_rework_fields(shot, sid))
            fields.append(_field(
                f"retry:{sid}",
                f"{title} 过/重抽",
                "过" if ready else "失败",
                input_kind="select",
                note=f"重抽：--retry {sid} --resume",
            ))
        summary = [{"label": "成片镜", "value": f"{ok_n} 成功 / {fail_n} 失败"}]
        if fail_lines:
            summary.append({"label": "失败镜", "value": "、".join(fail_lines)})
        choices["cast_action"] = CAST_ACTION_CHOICES
        if kling and fail_n:
            choices["rework_mode"] = REWORK_CHOICES
    else:
        plan = scene_plan or {}
        scenes = [sc for sc in (plan.get("scenes") or []) if isinstance(sc, dict)]
        title = _text((script or {}).get("title")) or _text(bible.get("title")) or "（无标题）"
        total = 0.0
        scene_lines: list[str] = []
        for sc in scenes:
            shots = [sh for sh in (sc.get("shots") or []) if isinstance(sh, dict)]
            start = sc.get("start_seconds")
            end = sc.get("end_seconds")
            dur = None
            try:
                if start is not None and end is not None:
                    dur = float(end) - float(start)
            except (TypeError, ValueError):
                dur = None
            if dur is None:
                try:
                    dur = float(sc.get("duration_seconds") or 0)
                except (TypeError, ValueError):
                    dur = 0.0
            total += float(dur or 0)
            scene_lines.append(
                f"{_text(sc.get('title') or sc.get('id'))} · {dur or '?'}s · {len(shots)} 镜"
            )
        target = bible.get("target_duration_seconds")
        summary = [
            {"label": "集标题", "value": title},
            {"label": "各幕", "value": "；".join(scene_lines) or "（尚未 compile）"},
            {"label": "总时长 vs 目标", "value": f"{total}s / {target if target not in (None, '') else '（未设）'}s"},
        ]
        form_lines = [
            f"{_text(sh.get('shot_id'))} {'、'.join(_shot_form_rows(sh))}"
            for sc in scenes
            for sh in (sc.get("shots") or [])
            if isinstance(sh, dict) and _shot_form_rows(sh)
        ]
        if form_lines:
            summary.append({"label": "出场形态", "value": "；".join(form_lines)[:200]})
        fields = []
        for sc in scenes:
            scid = _text(sc.get("id"))
            if not scid:
                continue
            label = _text(sc.get("title") or scid)
            fields.append(_field(f"scene_plan.scenes[{scid}].title", f"{label} 幕标题", _text(sc.get("title"))))
            fields.append(_field(
                f"scene_plan.scenes[{scid}].sound_notes", f"{label} 音效备注", _text(sc.get("sound_notes")),
            ))
            fields.append(_field(
                f"scene_plan.scenes[{scid}].keyframe_blurb", f"{label} 关键帧概述",
                _text(sc.get("keyframe_blurb")), input_kind="textarea",
            ))
            for shot in [sh for sh in (sc.get("shots") or []) if isinstance(sh, dict)]:
                sid = _text(shot.get("shot_id"))
                if not sid:
                    continue
                sl = shot.get("shot_language") if isinstance(shot.get("shot_language"), dict) else {}
                cine = {}
                vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
                if isinstance(vd.get("cinematography"), dict):
                    cine = vd["cinematography"]
                fields.append(_field(f"scene_plan.shots[{sid}].title", f"{sid} 短标题", _text(shot.get("title"))))
                fields.append(_field(
                    f"scene_plan.shots[{sid}].shot_language.shot_size", f"{sid} 景别",
                    _text(sl.get("shot_size")), input_kind="select",
                ))
                fields.append(_field(
                    f"scene_plan.shots[{sid}].shot_language.camera_movement", f"{sid} 运镜",
                    _text(sl.get("camera_movement")), input_kind="select",
                ))
                fields.append(_field(
                    f"scene_plan.shots[{sid}].visual_details.cinematography.angle", f"{sid} 角度",
                    _text(cine.get("angle")), input_kind="select",
                ))
                fields.append(_field(
                    f"scene_plan.shots[{sid}].picture", f"{sid} 画面",
                    _shot_picture(shot), input_kind="textarea",
                ))
                fields.append(_field(
                    f"scene_plan.shots[{sid}].dialogue", f"{sid} 台词",
                    _shot_dialogue_text(shot), input_kind="textarea", note="无对白标 —",
                ))
                fields.append(_field(
                    f"scene_plan.shots[{sid}].start_seconds", f"{sid} 起秒",
                    shot.get("start_seconds"), input_kind="number",
                ))
                fields.append(_field(
                    f"scene_plan.shots[{sid}].end_seconds", f"{sid} 止秒",
                    shot.get("end_seconds"), input_kind="number",
                ))
                fields.append(_field(
                    f"scene_plan.shots[{sid}].shot_budget_class", f"{sid} 成本档",
                    _text(shot.get("shot_budget_class")) or "talk", input_kind="select",
                ))
                fields.append(_field(
                    f"scene_plan.shots[{sid}].cut", f"{sid} 切法",
                    _text(shot.get("cut")) or "bridge", input_kind="select",
                ))
        choices.update({
            "shot_size": SHOT_SIZE_CHOICES,
            "camera_movement": CAMERA_MOVE_CHOICES,
            "angle": ANGLE_CHOICES,
            "shot_budget_class": BUDGET_CHOICES,
            "cut": CUT_CHOICES,
        })

    if findings:
        summary.append({
            "label": "待处理",
            "value": "；".join(_text(f.get("message")) for f in findings if _text(f.get("message")))[:200],
        })

    return {
        "step": step,
        "status": status,
        "heading": heading,
        "summary": summary,
        "fields": fields,
        "choices": choices,
        "findings": findings,
        "do_not_edit": [
            "供应商 / model id / 密钥",
            "artifacts/REVIEW.md 与 review_card.json（会被覆盖）",
            "edit_decisions.cuts[].clip_path",
            "四视图比例、白底规则、提示词全文",
        ],
    }


def render_review_md(card: dict[str, Any]) -> str:
    status = _text(card.get("status"))
    heading = _text(card.get("heading")) or DIRECTOR_LABEL.get(status, status)
    lines = [
        "# REVIEW",
        "",
        f"当前停点：`{status}`（{heading}）",
        "",
        "改 `series_bible.json` / `scene_plan.json` / `proposal_packet.json`，不要改本文件；每次停点会覆盖。",
        "默认只看摘要；要改某字段再看「全部可改」里的合法选项，然后 `produce --resume`。",
        "",
        "## 摘要",
        "",
    ]
    for row in card.get("summary") or []:
        if isinstance(row, dict):
            lines.append(f"- **{row.get('label')}**：{row.get('value')}")
    lines.extend(["", "## 全部可改", ""])
    for field in card.get("fields") or []:
        if not isinstance(field, dict):
            continue
        extra = f"（{field.get('note')}）" if field.get("note") else ""
        lines.append(
            f"- {field.get('label')} → `{field.get('path')}` 当前：{field.get('value')}{extra}"
        )
    choices = card.get("choices") if isinstance(card.get("choices"), dict) else {}
    if choices:
        lines.extend(["", "### 合法选项", ""])
        for key, values in choices.items():
            if isinstance(values, list) and values and isinstance(values[0], dict):
                shown = "；".join(
                    f"{_text(v.get('id'))}={_text(v.get('label'))}" for v in values if isinstance(v, dict)
                )
            else:
                shown = "、".join(str(v) for v in values)
            lines.append(f"- `{key}`：{shown}")
    findings = _finding_lines(list(card.get("findings") or []))
    if findings:
        lines.extend(["", "## 失败 / 待补", ""])
        lines.extend(findings)
    lines.extend([
        "",
        "## 不要改",
        "",
        "- 供应商、model id、密钥",
        "- 本文件与 `review_card.json`（人手改会被覆盖）",
        "- `edit_decisions.cuts[].clip_path`",
        "- 四视图比例、白底规则、提示词全文",
        "",
    ])
    return "\n".join(lines)


def write_director_review(
    project_dir: str | Path,
    status: str,
    *,
    findings: list[dict[str, Any]] | None = None,
    retry_ids: list[str] | None = None,
) -> dict[str, Any]:
    root = Path(project_dir)
    store = ArtifactStore(root)
    # await_retry 卡：附加母带模式的能力边界提示（哪些指令可灵单次生成可能做不到）
    extra_findings = _kling_capability_findings(store, status)
    all_findings = list(findings or []) + extra_findings
    card = build_review_card(
        status,
        bible=_bible(store),
        proposal=_proposal(store),
        script=_script(store),
        scene_plan=_scene_plan(store),
        findings=all_findings,
        manifest=_manifest(store),
        project_dir=str(root),
        retry_ids=retry_ids,
    )
    art = root / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    (art / "REVIEW.md").write_text(render_review_md(card), encoding="utf-8")
    (art / "review_card.json").write_text(
        json.dumps(card, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return card


def _kling_capability_findings(store: Any, status: str) -> list[dict[str, Any]]:
    """从 playbook 的 master_pattern 取能力边界，转为复核卡的 findings。

    在 await_final_prompt（首轮生成前）与 await_retry（重抽确认前）两张卡上，
    让导演看到哪些母带指令可灵单次生成可能做不到、该用什么返工手段。
    非可灵环 / 无 master_pattern 时返回空列表。
    """
    if status not in ("await_final_prompt", "await_retry"):
        return []
    try:
        packet = store.read("proposal_packet") or {}
        pb_name = str(packet.get("playbook") or "").strip()
        if not pb_name:
            return []
        from montage.playbooks import get_playbook

        pb = get_playbook(pb_name)
        pattern = str((pb or {}).get("master_pattern") or "").strip()
        if not pattern:
            return []
        from lib.kling_master import CAPABILITY_GUARDRAILS

        rows = CAPABILITY_GUARDRAILS.get(pattern) or []
        return [
            {
                "field": f"kling_master.{pattern}",
                "message": f"[能力边界] {row.get('instruction')}：{row.get('risk')} → {row.get('mitigation')}",
            }
            for row in rows
            if row.get("instruction")
        ]
    except (OSError, TypeError, ValueError, KeyError):
        return []


_FORBIDDEN_PATH = (
    "clip_path", "pipeline_type", "human_approved", "review_card",
    "produce_progress", "edit_decisions", "REVIEW.md",
)
_SKIP_TRUE = {"skip", "跳过", "true", "1", "yes", "本角色跳过四视图"}
_SKIP_FALSE = {"generate", "出四视图", "false", "0", "no", "出四视图（默认）"}
_INDEXED_RE = re.compile(
    r"^(bible|scene_plan)\.(characters|locations|props|scenes|shots)\[([^\]]+)\]\.(.+)$"
)
_SHOT_SIZE_IDS = {str(x["id"]) for x in SHOT_SIZE_CHOICES}
_CAMERA_IDS = {str(x["id"]) for x in CAMERA_MOVE_CHOICES}


def _card_paths(card: dict[str, Any] | None) -> set[str]:
    out: set[str] = set()
    for field in (card or {}).get("fields") or []:
        if isinstance(field, dict) and field.get("path"):
            out.add(str(field["path"]))
    return out


def _coerce_skip_turnaround(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {x.lower() for x in _SKIP_TRUE}:
        return True
    if text in {x.lower() for x in _SKIP_FALSE}:
        return False
    return False


def _split_lines(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(x) for x in value if _text(x)]
    return [ln.strip() for ln in str(value or "").splitlines() if ln.strip()]


def _parse_indexed(path: str) -> tuple[str, str, str, str] | None:
    match = _INDEXED_RE.fullmatch(path)
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3), match.group(4)


def _find_row(rows: Any, item_id: str, *, id_key: str = "id") -> dict[str, Any] | None:
    if not isinstance(rows, list):
        return None
    for row in rows:
        if isinstance(row, dict) and str(row.get(id_key) or "") == item_id:
            return row
    return None


def _find_shot(plan: dict[str, Any], shot_id: str) -> dict[str, Any] | None:
    for scene in plan.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        hit = _find_row(scene.get("shots"), shot_id, id_key="shot_id")
        if hit is not None:
            return hit
    return None


def _set_dotted(doc: dict[str, Any], dotted: str, value: Any) -> None:
    parts = [p for p in dotted.split(".") if p]
    cur: Any = doc
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    key = parts[-1]
    if key in {"target_duration_seconds", "start_seconds", "end_seconds", "duration_seconds"}:
        try:
            number = float(value)
            cur[key] = int(number) if number.is_integer() else number
            return
        except (TypeError, ValueError):
            pass
    cur[key] = value


def _map_shot_enum(rest: str, value: Any) -> tuple[Any, str]:
    """返回 (mapped, error)。error 非空则 skip。"""
    raw = _text(value)
    if rest == "shot_language.shot_size":
        if raw not in _SHOT_SIZE_IDS:
            return value, f"非法景别 {raw}（要用 wide/medium/…）"
        return raw, ""
    if rest == "shot_language.camera_movement":
        if raw not in _CAMERA_IDS:
            return value, f"非法运镜 {raw}（要用 dolly_in/static/…）"
        return raw, ""
    if rest == "shot_budget_class" and raw and raw not in {"hero", "talk", "establishing"}:
        return value, f"非法 shot_budget_class {raw}"
    if rest == "cut" and raw and raw not in {"bridge", "hard"}:
        return value, f"非法 cut {raw}"
    if rest == "rework_mode":
        mode = raw.strip().lower()
        if mode and mode not in {"edit", "extend", "splice", "regenerate", "feature"}:
            return value, f"非法 rework_mode {raw}"
        return mode, ""
    return value, ""


def _write_shot_leaf(shot: dict[str, Any], rest: str, value: Any) -> str:
    """写镜头叶子。返回错误信息，空串表示成功。"""
    mapped, err = _map_shot_enum(rest, value)
    if err:
        return err
    if rest == "picture":
        vd = shot.get("visual_details")
        if not isinstance(vd, dict):
            vd = {}
            shot["visual_details"] = vd
        env = vd.get("environment")
        if isinstance(env, dict):
            subjects = vd.get("subjects")
            if isinstance(subjects, list) and subjects and isinstance(subjects[0], dict):
                act = subjects[0].get("action")
                if not isinstance(act, dict):
                    act = {}
                    subjects[0]["action"] = act
                act["verb"] = _text(mapped)
                return ""
            return "environment 是对象，请直接改 scene_plan JSON"
        vd["environment"] = _text(mapped)
        return ""
    if rest == "dialogue":
        ap = shot.get("audio_prompt")
        if not isinstance(ap, dict):
            ap = {}
            shot["audio_prompt"] = ap
        text = _text(mapped)
        lines = list(ap.get("dialogue") or [])
        if text:
            if lines and isinstance(lines[0], dict):
                lines[0]["dialogue_text"] = text
            else:
                lines = [{"dialogue_text": text}]
        else:
            lines = []
        ap["dialogue"] = lines
        return ""
    _set_dotted(shot, rest, mapped)
    return ""


def apply_review_fields(
    project_dir: str | Path,
    patches: list[dict[str, Any]] | None,
    *,
    card: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把确认卡上的叶子字段写进 bible / proposal / scene_plan。不跑 produce。"""
    from montage.engine.bible import write_bible

    root = Path(project_dir)
    store = ArtifactStore(root)
    if card is None:
        raw = store.read("review_card")
        card = raw if isinstance(raw, dict) else {}
    allowed = _card_paths(card)
    bible = dict(_bible(store))
    proposal = dict(_proposal(store))
    scene_plan = dict(_scene_plan(store))
    applied: list[str] = []
    skipped: list[dict[str, str]] = []
    bible_dirty = proposal_dirty = plan_dirty = False

    for item in patches or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        value = item.get("value")
        if not path:
            continue
        if path.startswith("retry:") or "[]" in path or any(tok in path for tok in _FORBIDDEN_PATH):
            skipped.append({"path": path, "reason": "禁止写入（retry / 通配 / 禁改字段）"})
            continue
        if path not in allowed:
            skipped.append({"path": path, "reason": "不在当前确认卡 fields"})
            continue
        indexed = _parse_indexed(path)
        if indexed:
            root_name, collection, item_id, rest = indexed
            if collection == "shots":
                row = _find_shot(scene_plan, item_id)
                if row is None:
                    skipped.append({"path": path, "reason": f"找不到镜头 {item_id}"})
                    continue
                err = _write_shot_leaf(row, rest, value)
                if err:
                    skipped.append({"path": path, "reason": err})
                    continue
                plan_dirty = True
                applied.append(path)
                continue
            if root_name == "bible":
                id_key = "id"
                bag = bible.setdefault(collection, [])
                if not isinstance(bag, list):
                    bag = []
                    bible[collection] = bag
                row = _find_row(bag, item_id, id_key=id_key)
                if row is None and collection == "props":
                    row = _find_row(bag, item_id, id_key="name")
                if row is None:
                    skipped.append({"path": path, "reason": f"找不到 {collection} {item_id}"})
                    continue
                if rest == "skip_turnaround":
                    row[rest] = _coerce_skip_turnaround(value)
                else:
                    _set_dotted(row, rest, value)
                bible_dirty = True
                applied.append(path)
                continue
            if root_name == "scene_plan" and collection == "scenes":
                bag = scene_plan.get("scenes")
                row = _find_row(bag, item_id)
                if row is None:
                    skipped.append({"path": path, "reason": f"找不到幕 {item_id}"})
                    continue
                _set_dotted(row, rest, value)
                plan_dirty = True
                applied.append(path)
                continue
            skipped.append({"path": path, "reason": "未知索引文档"})
            continue
        if path == "bible.gold_lines":
            bible["gold_lines"] = _split_lines(value)[:6]
            bible_dirty = True
            applied.append(path)
            continue
        if path.startswith("bible."):
            _set_dotted(bible, path[len("bible."):], value)
            bible_dirty = True
            applied.append(path)
            continue
        if path.startswith("proposal_packet."):
            _set_dotted(proposal, path[len("proposal_packet."):], value)
            proposal_dirty = True
            applied.append(path)
            continue
        if path.startswith("scene_plan."):
            skipped.append({"path": path, "reason": "scene_plan 只接受带 id 的叶子 path"})
            continue
        skipped.append({"path": path, "reason": "未知根文档"})

    if bible_dirty:
        write_bible(root, bible)
    if proposal_dirty:
        store.write("proposal_packet", proposal)
    if plan_dirty:
        store.write("scene_plan", scene_plan)

    status = str((card or {}).get("status") or "")
    refreshed = card or {}
    if status and (bible_dirty or proposal_dirty or plan_dirty):
        refreshed = write_director_review(root, status, findings=list(card.get("findings") or []))
    return {"applied": applied, "skipped": skipped, "card": refreshed}
