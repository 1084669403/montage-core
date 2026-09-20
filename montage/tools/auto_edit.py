"""auto_edit — AutoEditor 编排器（快速路径，不进 7 阶段管线）。

纯逻辑层（无 ffmpeg）：build_plan / replan / rebuild_edits / 路径校验 / 指纹。
副作用层：probe / analyze / preview / render 调现有 ffmpeg 函数。
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from montage.compose.ffmpeg_engine import check_ffmpeg
from montage.compose.ffmpeg_engine import probe as ffprobe
from montage.style_packs import apply_overrides, get_style_pack, validate_overrides
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus
from montage.tools.edit_advisor import suggest_transitions

SCHEMA_VERSION = "1.0"
RENDER_STEPS = ("workspace", "effects", "stitch", "grade", "profile", "final")
# plan_history（P0-3）：版本链放 tmp_autoedit/（与 progress.json 同域，不进交付包），
# 可追溯性靠项目根的 decisions.jsonl（那份是导出的）。
PLAN_HISTORY_SUBDIR = "plan_history"
PLAN_HISTORY_NAME = "plan_history.json"
PLAN_HISTORY_VERSION = "1"
DEFAULT_MAX_VERSIONS = 50
# P0-5：能量波/拍网格产物。落 tmp_autoedit/（与 progress.json 同域，不进交付包），
# 量规层（review_logger → edit_metrics）按约定路径读它，判定 m5/m6 的 circular 标注。
BEAT_MAP_NAME = "beat_map.json"
BEAT_MAP_VERSION = "1.0"


def ensure_dirs(project_dir: str | Path) -> tuple[Path, Path]:
    """幂等创建 auto_edit/ 与 tmp_autoedit/。不改 init_project。"""
    root = Path(project_dir)
    auto = root / "auto_edit"
    tmp = root / "tmp_autoedit"
    auto.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    return auto, tmp


def assert_output_inside(project_dir: str | Path, path: str | Path) -> Path:
    """输出必须落在 auto_edit/ 或 tmp_autoedit/ 内。"""
    root = Path(project_dir).resolve()
    resolved = Path(path).resolve()
    for base in (root / "auto_edit", root / "tmp_autoedit"):
        try:
            resolved.relative_to(base.resolve())
            return resolved
        except ValueError:
            continue
    raise ValueError(f"输出路径必须在 auto_edit/ 或 tmp_autoedit/ 内: {resolved}")


def hash_file(path: str | Path) -> str:
    """分块 sha256，避免大文件一次性进内存。"""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        while True:
            chunk = fh.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_integrity(files: list[dict[str, Any]]) -> dict[str, Any]:
    details = []
    unchanged = True
    for item in files:
        path = Path(item["path"])
        current = hash_file(path) if path.is_file() else ""
        match = current == item.get("sha256")
        if not match:
            unchanged = False
        details.append({
            "path": str(path), "expected": item.get("sha256"),
            "actual": current, "match": match,
        })
    return {"unchanged": unchanged, "files": details}


def new_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_project_meta(project_dir: str | Path) -> Path:
    """缺 project.json 时原地写最小元数据（不调用 init_project）。"""
    root = Path(project_dir)
    root.mkdir(parents=True, exist_ok=True)
    meta_path = root / "project.json"
    if meta_path.exists():
        return root
    meta = {
        "version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "project_id": root.name,
        "title": root.name,
        "pipeline_type": "cinematic",
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def _unique_points(duration: float, scene_changes: list[float]) -> list[float]:
    points = [0.0]
    for t in scene_changes:
        if 0.05 < t < duration - 0.05:
            points.append(float(t))
    points.append(float(duration))
    points = sorted({round(p, 3) for p in points})
    if points[0] != 0.0:
        points.insert(0, 0.0)
    last = round(duration, 3)
    if points[-1] != last:
        points.append(last)
    return points


def shots_from_cuts(
    duration: float,
    scene_changes: list[float],
    *,
    min_hold: float,
    max_hold: float,
    segment_id: str,
) -> list[dict[str, Any]]:
    """scene_changes → shots；过短合并、过长按 max_hold 切开。"""
    points = _unique_points(duration, scene_changes)
    merged = [points[0]]
    for p in points[1:-1]:
        if p - merged[-1] >= min_hold:
            merged.append(p)
    merged.append(points[-1])
    if len(merged) > 2 and merged[-1] - merged[-2] < min_hold:
        merged.pop(-2)

    shots: list[dict[str, Any]] = []
    idx = 0
    for i in range(len(merged) - 1):
        start, end = merged[i], merged[i + 1]
        span = end - start
        if max_hold > 0 and span > max_hold + 0.05:
            n = max(2, round(span / max_hold))
            step = span / n
            for k in range(n):
                s = start + k * step
                e = end if k == n - 1 else start + (k + 1) * step
                shots.append({
                    "shot_id": f"{segment_id}_shot_{idx}",
                    "start": round(s, 3), "end": round(e, 3), "speed": 1.0,
                })
                idx += 1
        else:
            shots.append({
                "shot_id": f"{segment_id}_shot_{idx}",
                "start": round(start, 3), "end": round(end, 3), "speed": 1.0,
            })
            idx += 1
    return shots


def _effective_pack(plan: dict[str, Any], pack: dict[str, Any] | None = None) -> dict[str, Any]:
    """replan 时优先用 plan.params 里已生效的转场/时长，避免丢掉首次 pack 覆写。"""
    base = dict(pack or get_style_pack(plan.get("style_pack") or "") or {})
    params = plan.get("params") or {}
    if params.get("transitions"):
        base["transitions"] = list(params["transitions"])
    if "transition_duration" in params:
        pacing = dict(base.get("pacing") or {})
        pacing["transition_duration"] = params["transition_duration"]
        base["pacing"] = pacing
    if "lut" in params:
        base["lut"] = params.get("lut") or ""
    return base


def rebuild_edits(plan: dict[str, Any], pack: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """只对相邻 keep 段之镜头生成转场；调 suggest_transitions 后用 StylePack 覆写。"""
    pack = _effective_pack(plan, pack)
    kept: list[tuple[str, dict[str, Any]]] = []
    for seg in plan.get("segments") or []:
        if seg.get("action", "keep") != "keep":
            continue
        for shot in seg.get("shots") or []:
            kept.append((seg["segment_id"], shot))
    if len(kept) < 2:
        return []

    fake_scenes = [{"id": shot["shot_id"]} for _, shot in kept]
    advisor_style = "documentary" if plan.get("style_pack") == "documentary" else "cinematic"
    suggestions = suggest_transitions(fake_scenes, advisor_style)

    default_t = (pack.get("transitions") or ["cut"])[0]
    tdur = float((pack.get("pacing") or {}).get("transition_duration") or 0.0)
    if default_t == "cut":
        tdur = 0.0

    edits: list[dict[str, Any]] = []
    for i, _sug in enumerate(suggestions):
        from_seg, from_shot = kept[i]
        to_seg, to_shot = kept[i + 1]
        edits.append({
            "from": from_shot["shot_id"],
            "to": to_shot["shot_id"],
            "from_segment": from_seg,
            "to_segment": to_seg,
            "transition": default_t,
            "transition_duration": tdur,
        })
    return edits


def build_plan(
    *,
    source_decl: dict[str, Any],
    style_pack_id: str,
    scene_changes_by_source: list[list[float]],
    session_id: str | None = None,
    target_duration: float | None = None,
    pack_overrides: dict[str, Any] | None = None,
    bpm: float | None = None,
) -> dict[str, Any]:
    pack = get_style_pack(style_pack_id)
    if pack is None:
        raise ValueError(f"未知风格包: {style_pack_id}")
    pack = apply_overrides(pack, pack_overrides)
    pacing = pack.get("pacing") or {}
    min_hold = float(pacing.get("min_hold") or 1.0)
    max_hold = float(pacing.get("max_hold") or 8.0)
    is_speech = bool(source_decl.get("is_speech"))

    files = list(source_decl.get("files") or [])
    segments: list[dict[str, Any]] = []
    for i, item in enumerate(files):
        duration = float(item.get("duration") or 0.0)
        sid = f"seg_{i}"
        changes = scene_changes_by_source[i] if i < len(scene_changes_by_source) else []
        shots = shots_from_cuts(
            duration, changes, min_hold=min_hold, max_hold=max_hold, segment_id=sid,
        )
        if is_speech:
            for shot in shots:
                shot["speed"] = 1.0
        segments.append({
            "segment_id": sid,
            "source_index": i,
            "src_start": 0.0,
            "src_end": duration,
            "action": "keep",
            "shots": shots,
        })

    plan: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id or new_session_id(),
        "stage": "plan",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source_decl": source_decl,
        "style_pack": style_pack_id,
        "bind_playbook": pack.get("bind_playbook"),
        "output_profile": pack.get("output_profile") or "youtube_landscape",
        "params": {
            "target_duration": target_duration,
            "bpm": (round(float(bpm), 2) if bpm else None),
            "min_hold": min_hold,
            "max_hold": max_hold,
            "lut": pack.get("lut") or "",
            "transitions": list(pack.get("transitions") or ["cut"]),
            "transition_duration": float(pacing.get("transition_duration") or 0.0),
        },
        "segments": segments,
        "edits": [],
    }
    plan["edits"] = rebuild_edits(plan, pack)
    if target_duration:
        plan = _fit_target_duration(plan, float(target_duration))
        plan["edits"] = rebuild_edits(plan, pack)
    return plan


def plan_duration(plan: dict[str, Any]) -> float:
    """整片时长（kept 段的镜长÷速率）。`_fit_target_duration` 与 history diff 共用，
    防两处算法漂移。"""
    acc = 0.0
    for seg in plan.get("segments") or []:
        if seg.get("action") != "keep":
            continue
        for shot in seg.get("shots") or []:
            acc += (float(shot["end"]) - float(shot["start"])) / max(float(shot.get("speed") or 1.0), 0.01)
    return acc


def _fit_target_duration(plan: dict[str, Any], target: float) -> dict[str, Any]:
    def _total() -> float:
        return plan_duration(plan)

    while _total() > target + 0.05:
        dropped = False
        for seg in reversed(plan["segments"]):
            if seg.get("action") != "keep" or not seg["shots"]:
                continue
            seg["shots"].pop()
            dropped = True
            break
        if not dropped:
            break
    return plan


def replan(plan: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    """幂等应用 overrides。"""
    if not overrides:
        return plan
    result = json.loads(json.dumps(plan))
    is_speech = bool((result.get("source_decl") or {}).get("is_speech"))
    shots_by_id: dict[str, dict[str, Any]] = {}
    segs_by_id: dict[str, dict[str, Any]] = {}
    for seg in result["segments"]:
        segs_by_id[seg["segment_id"]] = seg
        for shot in seg["shots"]:
            shots_by_id[shot["shot_id"]] = shot

    if overrides.get("target_duration") is not None:
        result.setdefault("params", {})["target_duration"] = float(overrides["target_duration"])

    for key, val in overrides.items():
        if key == "target_duration":
            continue
        if not isinstance(val, dict):
            continue
        if key in segs_by_id and "action" in val:
            action = val["action"]
            if action not in ("keep", "drop"):
                raise ValueError(f"未知 action: {action}")
            segs_by_id[key]["action"] = action
        elif key in shots_by_id:
            shot = shots_by_id[key]
            if "start" in val:
                shot["start"] = float(val["start"])
            if "end" in val:
                shot["end"] = float(val["end"])
            if "speed" in val:
                speed = float(val["speed"])
                shot["speed"] = 1.0 if is_speech else max(0.8, min(1.2, speed))
            # P0-8 后续小刀：vfx 作者面——空列表=清除；非空=sanitize 后写入。
            # 进 segments → plan_core 天然覆盖 → plan_fingerprint 自动失效下游步骤。
            if "vfx" in val:
                cleaned = sanitize_shot_vfx(val.get("vfx"))
                if cleaned:
                    shot["vfx"] = cleaned
                else:
                    shot.pop("vfx", None)
            if shot["end"] <= shot["start"]:
                raise ValueError(f"{key} 的 end 必须大于 start")

    if result.get("params", {}).get("target_duration"):
        result = _fit_target_duration(result, float(result["params"]["target_duration"]))
    result["edits"] = rebuild_edits(result)
    result["stage"] = "plan"
    result["updated_at"] = datetime.now(timezone.utc).isoformat()
    return result


def plan_core(plan: dict[str, Any]) -> str:
    """plan 的「实质内容」指纹串：segments + edits + params（不含时间戳/session）。"""
    slim = {"segments": plan.get("segments"), "edits": plan.get("edits"), "params": plan.get("params")}
    return json.dumps(slim, sort_keys=True, ensure_ascii=False)


def plan_fingerprint(plan: dict[str, Any]) -> str:
    """``plan_core`` 的短哈希：既做版本等价判定，也做渲染中间产物的缓存键。

    P0-8：``segments[].shots[].vfx`` 已在 plan_core 内——改特效 → sha 变 →
    stitch/grade/profile 断点自动失效（无需单独 vfx 摘要键；作者面走 replan）。
    """
    return hashlib.sha256(plan_core(plan).encode("utf-8")).hexdigest()[:16]


def clip_cache_name(
    index: int,
    shot: dict[str, Any],
    src_path: str | Path,
    *,
    is_speech: bool = False,
) -> str:
    """单镜中间产物文件名：**按镜内容寻址**，不按序号。

    按序号命名（旧行为 ``shot_0000.mp4``）在 plan 改动后会静默复用上一版的裁剪结果——
    「改了 in/out 重渲还是旧片」就是从这里来的。
    """
    raw = "|".join(str(x) for x in (
        src_path, shot.get("start"), shot.get("end"), shot.get("speed") or 1.0, bool(is_speech),
    ))
    tag = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"shot_{index:04d}_{tag}.mp4"


def plan_differs(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return plan_core(a) != plan_core(b)


def _shots_by_id(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for seg in plan.get("segments") or []:
        for shot in seg.get("shots") or []:
            sid = str(shot.get("shot_id") or "")
            if sid:
                out[sid] = shot
    return out


def plan_diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """两版 plan 的**字段级**差异（P0-3：只报「变了」没有可操作性）。

    按 ``shot_id`` 对齐，把「挪了时间」与「改了速率」分开报；段级 action、params、
    转场各自成键。返回的所有列表都排序，便于稳定断言与人工比对。
    """
    before, after = _shots_by_id(a), _shots_by_id(b)
    kept = set(before) & set(after)
    retimed: list[str] = []
    resped: list[str] = []
    vfxed: list[str] = []
    for sid in sorted(kept):
        x, y = before[sid], after[sid]
        if abs(float(x.get("start", 0.0)) - float(y.get("start", 0.0))) > 0.001 or \
                abs(float(x.get("end", 0.0)) - float(y.get("end", 0.0))) > 0.001:
            retimed.append(sid)
        if abs(float(x.get("speed") or 1.0) - float(y.get("speed") or 1.0)) > 0.001:
            resped.append(sid)
        if sanitize_shot_vfx(x.get("vfx")) != sanitize_shot_vfx(y.get("vfx")):
            vfxed.append(sid)

    def _seg_actions(plan: dict[str, Any]) -> dict[str, Any]:
        return {
            str(seg.get("segment_id") or ""): seg.get("action")
            for seg in plan.get("segments") or []
            if seg.get("segment_id")
        }

    sa, sb = _seg_actions(a), _seg_actions(b)
    action_changed = {
        sid: {"before": sa.get(sid), "after": sb.get(sid)}
        for sid in sorted(set(sa) | set(sb))
        if sa.get(sid) != sb.get(sid)
    }

    pa, pb = a.get("params") or {}, b.get("params") or {}
    params_changed = {
        key: {"before": pa.get(key), "after": pb.get(key)}
        for key in sorted(set(pa) | set(pb))
        if pa.get(key) != pb.get(key)
    }

    ea = [json.dumps(e, sort_keys=True, ensure_ascii=False) for e in (a.get("edits") or [])]
    eb = [json.dumps(e, sort_keys=True, ensure_ascii=False) for e in (b.get("edits") or [])]
    edit_count = sum(1 for x, y in zip(ea, eb) if x != y) + abs(len(ea) - len(eb))

    dur_a, dur_b = plan_duration(a), plan_duration(b)
    unchanged = not (retimed or resped or vfxed or action_changed or params_changed or edit_count
                     or set(before) != set(after))
    return {
        "unchanged": unchanged,
        "shots_kept": sorted(kept),
        "shots_added": sorted(set(after) - set(before)),
        "shots_removed": sorted(set(before) - set(after)),
        "shots_retimed": retimed,
        "shots_resped": resped,
        "shots_vfxed": vfxed,
        "segments_action_changed": action_changed,
        "params_changed": params_changed,
        "edits_changed": edit_count,
        "duration_before": round(dur_a, 3),
        "duration_after": round(dur_b, 3),
        "duration_delta": round(dur_b - dur_a, 3),
        "counts": {
            "kept": len(kept),
            "added": len(set(after) - set(before)),
            "removed": len(set(before) - set(after)),
            "retimed": len(retimed),
            "resped": len(resped),
            "vfxed": len(vfxed),
        },
    }


def plan_diff_summary(diff: dict[str, Any]) -> str:
    """diff → 一行中文摘要（写进 decisions.jsonl / CLI 回显）。"""
    if diff.get("unchanged"):
        return "无变化"
    parts: list[str] = []
    counts = diff.get("counts") or {}
    for key, label in (("added", "增"), ("removed", "删"), ("retimed", "重定时"), ("resped", "改速率"), ("vfxed", "改特效")):
        if counts.get(key):
            parts.append(f"{label}{counts[key]}镜")
    for sid, chg in (diff.get("segments_action_changed") or {}).items():
        parts.append(f"{sid}:{chg.get('before')}→{chg.get('after')}")
    if diff.get("params_changed"):
        parts.append("参数改 " + ",".join(sorted(diff["params_changed"])))
    if diff.get("edits_changed"):
        parts.append(f"转场改{diff['edits_changed']}")
    delta = diff.get("duration_delta") or 0.0
    if abs(delta) > 0.001:
        parts.append(f"时长{delta:+.2f}s")
    return "；".join(parts) or "无变化"


# ---------------------------------------------------------------------------
# P0-5：能量波切点（beat cuts）
# ---------------------------------------------------------------------------


def beat_map_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / "tmp_autoedit" / BEAT_MAP_NAME


def load_beat_map(project_dir: str | Path) -> dict[str, Any] | None:
    """读 P0-5 能量波产物。缺文件/坏 JSON → None（量规层据此不标 circular）。"""
    path = beat_map_path(project_dir)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def clear_beat_map(project_dir: str | Path) -> bool:
    """删掉上一次的能量波产物（返回是否真的删了）。

    **必须在下闸（``--no-beat-cuts`` / 无 BGM / 对白片）时调用**：产物留着就是
    「这次切点被能量波接管」的假证据，量规层会照它把 m5/m6 标成 circular。
    """
    path = beat_map_path(project_dir)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def save_beat_map(project_dir: str | Path, payload: dict[str, Any]) -> Path:
    """落盘能量波产物（**非致命** schema 校验：错误进 ``schema_errors`` 字段）。

    真跑时踩过「顶层漏 grid/bars/cuts、量规层静默退回 soundtrack」的坑——校验就是防它
    再犯：缺字段会在这里留下可查的错误列表，而不是让 circular 标不上。
    """
    from montage.engine.artifacts import ArtifactStore
    from montage.schemas import get_schema

    path = beat_map_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = get_schema("beat_map")
    errors: list[str] = []
    if schema:
        errors = ArtifactStore.validate(payload, schema)
    data = payload if not errors else {**payload, "schema_errors": errors}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def beat_map_contour(beat_map: dict[str, Any] | None) -> list[dict[str, Any]]:
    """bar 能量 → m6 的 ``energy_contour``。

    键名沿用 m6 既有约定（``rms_db``），实际量是 ebur128 瞬时响度 M 的 bar 均值
    ——m6 只做**相对**比较（相关），不关心绝对刻度。
    """
    rows: list[dict[str, Any]] = []
    if not isinstance(beat_map, dict):
        return rows
    bars = beat_map.get("bars")
    if not bars:
        # 老/少见的 payload 只把 bar 明细放在 sources[] 里 → 取首个有数据的源
        for src in beat_map.get("sources") or []:
            if isinstance(src, dict) and src.get("bars"):
                bars = src["bars"]
                break
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        db = bar.get("mean_db")
        if db is None:
            continue
        rows.append({
            "start_seconds": float(bar.get("start_seconds") or 0.0),
            "end_seconds": float(bar.get("end_seconds") or 0.0),
            "rms_db": float(db),
        })
    return rows


def beat_map_summary(beat_map: dict[str, Any] | None) -> dict[str, Any]:
    """给回报/日志用的一行摘要（不带 windows 那种大数组）。"""
    if not beat_map:
        return {"used": False, "reason": "未跑能量波分析"}
    note = beat_map.get("cuts_by_source") or {}
    by_index = {
        int(r.get("index")): r
        for r in (note.get("rows") or [])
        if isinstance(r, dict) and r.get("index") is not None
    }
    return {
        "used": bool(note.get("used")),
        "bpm": beat_map.get("bpm"),
        "bpm_source": beat_map.get("bpm_source"),
        "energy_source": beat_map.get("energy_source"),
        "duration": beat_map.get("duration"),
        "cuts": list(beat_map.get("cuts") or []),
        "per_source": [
            {
                "path": row.get("path"),
                "feasible": bool(row.get("feasible")),
                "cuts": len(row.get("cuts") or []),
                "mode": (by_index.get(i) or {}).get("mode"),
                "scene_cuts_before": (by_index.get(i) or {}).get("before"),
                "replaced": (by_index.get(i) or {}).get("after")
                if (by_index.get(i) or {}).get("mode") == "replaced" else None,
            }
            for i, row in enumerate(beat_map.get("sources") or [])
            if isinstance(row, dict)
        ],
        "reasons": list(note.get("reasons") or []),
        "warnings": list(beat_map.get("warnings") or []),
    }


def plan_beat_cuts_for_sources(
    paths: list[Path],
    *,
    bpm: float = 0.0,
    fps: float = 30.0,
    beats_per_bar: int = 4,
    min_hold: float = 1.0,
    max_hold: float = 8.0,
) -> dict[str, Any]:
    """多源逐个跑 ``energy_wave.build_beat_map``，汇总成一份可落盘的产物。

    只依赖 ffmpeg；任一路测不出就**只跳过那一路**，不牵连其它源（也绝不猜拍）。
    """
    from montage.engine.energy_wave import DEFAULT_BEATS_PER_BAR, build_beat_map

    per_source: list[dict[str, Any]] = []
    warnings: list[str] = []
    for path in paths:
        row = build_beat_map(
            path, bpm=bpm, fps=fps,
            beats_per_bar=beats_per_bar or DEFAULT_BEATS_PER_BAR,
            min_hold=min_hold, max_hold=max_hold,
        )
        row["path"] = str(path)
        per_source.append(row)
        for w in row.get("warnings") or []:
            warnings.append(f"{Path(path).name}: {w}")
    resolved_bpm = max((float(r.get("bpm") or 0.0) for r in per_source), default=0.0)
    bpm_source = next((str(r.get("bpm_source") or "") for r in per_source if r.get("bpm_source")), "")
    return {
        "version": BEAT_MAP_VERSION,
        "operation": "beat_map",
        # 顶层 feasible 的语义 = 「切点真的被能量波接管了吗」（apply_beat_cuts 会再校正一次）。
        # 量规层按它决定 m5/m6 是否标 circular，缺了就等于白跑一趟。
        "feasible": any(bool(r.get("feasible")) and bool(r.get("cuts")) for r in per_source),
        "bpm": round(resolved_bpm, 2),
        "bpm_source": bpm_source,
        "beats_per_bar": int(beats_per_bar or DEFAULT_BEATS_PER_BAR),
        "min_hold": min_hold,
        "max_hold": max_hold,
        "energy_source": next((str(r.get("energy_source") or "") for r in per_source if r.get("energy_source")), ""),
        "duration": max((float(r.get("duration") or 0.0) for r in per_source), default=0.0),
        # 顶层 grid/cuts/bars 只是**单源视角的便利字段**（取源 0 或首个可用源）：多源各自
        # 的切点/bar 明细在 sources[]。apply_beat_cuts 会把 cuts 校正成真正落进 plan 的源。
        # 量规层（edit_metrics.beat_grid → m5）只认顶层 grid，漏了它 m5 会静默退回
        # soundtrack 事件、circular 也标不上——真跑时踩过这个坑。
        "grid": next((dict(r["grid"]) for r in per_source if isinstance(r.get("grid"), dict)), None),
        "cuts": next((list(r.get("cuts") or []) for r in per_source if r.get("cuts")), []),
        "bars": next((list(r.get("bars") or []) for r in per_source if r.get("bars")), []),
        "cuts_source_index": None,
        "sources": per_source,
        "warnings": warnings,
    }


def apply_beat_cuts(
    changes: list[list[float]],
    beat_map: dict[str, Any],
) -> tuple[list[list[float]], dict[str, Any]]:
    """把可行源的切点**替换**该源的 scene-change 切点；不可行源保持原样。

    替换而非并集：P0-5 的全部意义是「密度跟随能量」——并上 scene-change 会把安静段
    又切碎，等于两种策略互相打架。回报里逐源给 ``replaced/kept``，混合结果不藏着。
    """
    merged = [list(c) for c in changes]
    rows: list[dict[str, Any]] = []
    reasons: list[str] = []
    for i, src in enumerate(beat_map.get("sources") or []):
        if not isinstance(src, dict) or i >= len(merged):
            continue
        cuts = sorted(float(c) for c in (src.get("cuts") or []))
        feasible = bool(src.get("feasible")) and bool(cuts)
        if feasible:
            rows.append({
                "index": i, "path": src.get("path"), "mode": "replaced",
                "before": len(merged[i]), "after": len(cuts),
            })
            merged[i] = cuts
        else:
            reason = str(src.get("reason") or "；".join(src.get("warnings") or []) or "不可行")
            rows.append({
                "index": i, "path": src.get("path"), "mode": "kept_scene_cuts",
                "before": len(merged[i]), "after": len(merged[i]), "reason": reason,
            })
            reasons.append(f"源 {i}：{reason}")
    used = any(r["mode"] == "replaced" for r in rows)
    return merged, {
        "used": used,
        "reasons": reasons,
        "rows": rows,
        "n_cuts": sum(len(c) for c in merged),
    }


# ---------------------------------------------------------------------------
# plan_history：版本链（P0-3）
# ---------------------------------------------------------------------------

def history_dir(project_dir: str | Path) -> Path:
    return Path(project_dir) / "tmp_autoedit" / PLAN_HISTORY_SUBDIR


def history_path(project_dir: str | Path) -> Path:
    return history_dir(project_dir) / PLAN_HISTORY_NAME


def empty_history() -> dict[str, Any]:
    return {"version": PLAN_HISTORY_VERSION, "current": 0, "entries": [], "pruned": 0,
            "max_versions": DEFAULT_MAX_VERSIONS}


def load_history(project_dir: str | Path) -> dict[str, Any]:
    """读版本链索引。坏 JSON / 缺文件 → 空索引（老项目零迁移，不抛不删）。"""
    path = history_path(project_dir)
    if not path.is_file():
        return empty_history()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_history()
    if not isinstance(data, dict):
        return empty_history()
    return {
        "version": str(data.get("version") or PLAN_HISTORY_VERSION),
        "current": int(data.get("current") or 0),
        "entries": [e for e in data.get("entries") or [] if isinstance(e, dict)],
        "pruned": int(data.get("pruned") or 0),
        "max_versions": data.get("max_versions"),
    }


def _read_snapshot(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """按路径读快照。**不走索引**——补录历史起点时索引还没落盘，查索引会扑空。"""
    if not entry:
        return None
    path = Path(str(entry.get("path") or ""))
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def read_version(project_dir: str | Path, rev: int) -> dict[str, Any] | None:
    """读某一版全量快照；缺失/坏 JSON → None。"""
    for entry in load_history(project_dir).get("entries") or []:
        if int(entry.get("rev") or 0) == int(rev):
            return _read_snapshot(entry)
    return None


def _effective_max_versions(index: dict[str, Any], requested: int | None) -> int:
    """版本上限是**项目级策略**：显式传入的说了算，否则沿用上次，最后回落默认。

    否则会出现「plan 时设 5，replan 不传就悄悄变回 50」这种不一致。
    """
    if requested is not None:
        return int(requested)
    if index.get("max_versions") is not None:
        return int(index["max_versions"])
    return DEFAULT_MAX_VERSIONS


def _prune_history(dirpath: Path, index: dict[str, Any], max_versions: int) -> int:
    """只保留最近 ``max_versions`` 版（0=不限）；删掉的快照计入 ``pruned``。"""
    if max_versions <= 0:
        return 0
    entries = index.get("entries") or []
    excess = len(entries) - int(max_versions)
    if excess <= 0:
        return 0
    for entry in entries[:excess]:
        try:
            Path(str(entry.get("path") or "")).unlink(missing_ok=True)
        except OSError:
            pass
    index["entries"] = entries[excess:]
    index["pruned"] = int(index.get("pruned") or 0) + excess
    return excess


def append_plan_version(
    project_dir: str | Path,
    plan: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    reason: str = "",
    overrides: dict[str, Any] | None = None,
    max_versions: int | None = None,
) -> dict[str, Any]:
    """把 ``plan`` 记入版本链；与上一版实质无差异则不新增。

    ``previous`` 只在索引为空时用于补「历史起点」（老项目已有 plan.json 但没版本链）：
    先按 ``previous`` 写 rev=1，再把 ``plan`` 写 rev=2。索引非空则忽略。
    ``max_versions=None`` 表示沿用项目已存策略（见 ``_effective_max_versions``）。
    """
    index = load_history(project_dir)
    dirpath = history_dir(project_dir)
    dirpath.mkdir(parents=True, exist_ok=True)

    if not index["entries"] and previous is not None:
        seed_rev = 1
        seed_path = dirpath / f"v{seed_rev:04d}.json"
        seed_path.write_text(json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8")
        index["entries"].append({
            "rev": seed_rev,
            "path": str(seed_path),
            "at": str(previous.get("updated_at") or ""),
            "reason": "历史起点（补录）",
            "session_id": str(previous.get("session_id") or ""),
            "plan_sha": plan_fingerprint(previous),
            "overrides": {},
            "diff_from_prev": None,
        })
        index["current"] = seed_rev

    last = index["entries"][-1] if index["entries"] else None
    last_plan = _read_snapshot(last)
    if last_plan is not None and not plan_differs(last_plan, plan):
        return {"changed": False, "rev": int(last.get("rev") or 0), "index": index,
                "diff": {"unchanged": True}, "pruned": 0}

    rev = int(index["current"] or 0) + 1
    snapshot = dirpath / f"v{rev:04d}.json"
    snapshot.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    diff = plan_diff(last_plan or {}, plan) if last is not None else None
    index["entries"].append({
        "rev": rev,
        "path": str(snapshot),
        "at": str(plan.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        "reason": reason or ("初始草稿" if last is None else "未注明"),
        "session_id": str(plan.get("session_id") or ""),
        "plan_sha": plan_fingerprint(plan),
        "overrides": dict(overrides or {}),
        "diff_from_prev": diff,
    })
    index["current"] = rev
    index["max_versions"] = _effective_max_versions(index, max_versions)
    pruned = _prune_history(dirpath, index, index["max_versions"])
    errors: list[str] = []
    try:
        from montage.engine.artifacts import ArtifactStore
        from montage.schemas import get_schema

        schema = get_schema("plan_history")
        if schema:
            errors = ArtifactStore.validate(index, schema)
    except (ImportError, RuntimeError):
        errors = []
    history_path(project_dir).write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return {"changed": True, "rev": rev, "index": index, "diff": diff or {"unchanged": True},
            "pruned": pruned, "schema_errors": errors}


def revert_plan(
    project_dir: str | Path,
    rev: int,
    *,
    max_versions: int | None = None,
) -> dict[str, Any]:
    """把 ``plan.json`` 换回 v``rev``；**不删任何快照**，回滚自身记一条新版本。

    调用方负责重渲：``_fit_target_duration`` 等会让 plan 与已渲染产物脱钩。
    """
    auto_dir, _tmp = ensure_dirs(project_dir)
    plan_path = auto_dir / "plan.json"
    target = read_version(project_dir, int(rev))
    if target is None:
        return {"ok": False, "error": f"找不到版本 v{int(rev)}"}

    previous = None
    if plan_path.is_file():
        try:
            previous = json.loads(plan_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = None

    restored = json.loads(json.dumps(target))
    restored["updated_at"] = datetime.now(timezone.utc).isoformat()
    plan_path.write_text(json.dumps(restored, ensure_ascii=False, indent=2), encoding="utf-8")
    recorded = append_plan_version(
        project_dir, restored, previous=previous,
        reason=f"revert to v{int(rev)}", max_versions=max_versions,
    )
    return {"ok": True, "plan": restored, "rev": recorded.get("rev"),
            "reverted_to": int(rev), "changed": recorded.get("changed"),
            "note": "plan.json 已改，需重新 render（产物与旧 plan 已脱钩）"}


def plan_history_summary(project_dir: str | Path) -> dict[str, Any]:
    """版本链概览（CLI/回报用）：不含快照正文。"""
    index = load_history(project_dir)
    entries = index.get("entries") or []
    return {
        "current": int(index.get("current") or 0),
        "count": len(entries),
        "pruned": int(index.get("pruned") or 0),
        "entries": [
            {
                "rev": int(e.get("rev") or 0),
                "at": str(e.get("at") or ""),
                "reason": str(e.get("reason") or ""),
                "plan_sha": str(e.get("plan_sha") or ""),
                "summary": plan_diff_summary(e["diff_from_prev"]) if isinstance(e.get("diff_from_prev"), dict) else "",
            }
            for e in entries
        ],
    }


def progress_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / "tmp_autoedit" / "progress.json"


def load_progress(project_dir: str | Path) -> dict[str, Any]:
    path = progress_path(project_dir)
    if not path.exists():
        return {"steps": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"steps": {}}


def mark_step(project_dir: str | Path, name: str, output: str | Path,
              *, plan_sha: str = "") -> None:
    """记一步完成。``plan_sha`` 把产物绑到具体 plan 版本上（P0-3）。"""
    ensure_dirs(project_dir)
    data = load_progress(project_dir)
    out = Path(output)
    sha = hash_file(out) if out.is_file() else ""
    entry: dict[str, Any] = {"output": str(out), "sha256": sha, "done": True}
    if plan_sha:
        entry["plan_sha"] = plan_sha
    data.setdefault("steps", {})[name] = entry
    progress_path(project_dir).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def step_complete(project_dir: str | Path, name: str, *, plan_sha: str = "") -> Path | None:
    """该步产物是否可复用。**认 plan 版本**：plan 变了不复用（否则改完 plan 重渲出旧片）。

    ``plan_sha`` 留空时退回旧行为（只看文件存在与 sha256），仅为兼容老调用点。
    """
    info = (load_progress(project_dir).get("steps") or {}).get(name) or {}
    if not info.get("done"):
        return None
    if plan_sha and str(info.get("plan_sha") or "") != plan_sha:
        return None
    out = Path(info.get("output") or "")
    if not out.is_file() or hash_file(out) != info.get("sha256"):
        return None
    return out


def probe_sources(
    paths: list[Path],
    *,
    kind: str = "video",
    has_bgm: bool = False,
    is_speech: bool = False,
    language: str = "zh",
) -> dict[str, Any]:
    files = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"输入不存在或不可读: {path}")
        info = ffprobe(path)
        duration = float((info.get("format") or {}).get("duration") or 0.0)
        files.append({
            "path": str(path.resolve()),
            "sha256": hash_file(path),
            "duration": duration,
        })
    return {
        "kind": kind, "has_bgm": has_bgm, "is_speech": is_speech,
        "language": language, "files": files,
    }


def analyze_scene_changes(path: Path, *, threshold: float = 0.3) -> list[float]:
    from montage.tools.video_probe import SceneDetect

    result = SceneDetect().execute({"path": str(path), "threshold": threshold})
    if not result.success:
        raise RuntimeError(result.error or "scene_detect 失败")
    return list(result.data.get("scene_changes") or [])


def resolve_lut_path(lut_id: str) -> Path | None:
    from montage.compose.ffmpeg_engine import resolve_lut_file

    return resolve_lut_file(lut_id)


def _log_decision(project_dir: Path, session_id: str, choice: str, detail: str = "") -> None:
    """写决策日志 + 记一条 $0 本地账本。``detail`` 存进 rejected_because（审计补充）。"""
    from montage.engine.budget import BudgetLedger
    from montage.engine.decisions import DecisionLog

    meta_id = project_dir.name
    meta_path = project_dir / "project.json"
    if meta_path.exists():
        try:
            meta_id = json.loads(meta_path.read_text(encoding="utf-8")).get("project_id") or meta_id
        except json.JSONDecodeError:
            pass
    DecisionLog(project_dir / "decisions.jsonl").log(
        "auto_edit",
        f"auto_edit/{meta_id}/{session_id}",
        choice,
        options_considered=[choice],
        rejected_because=detail,
    )
    ledger = BudgetLedger(project_dir / "cost.jsonl")
    eid = ledger.estimate("auto_edit", f"auto_edit/{meta_id}/{session_id}/local", "auto_edit", 0.0)
    ledger.settle(eid, 0.0)


def _black_video_with_audio(src_path: Path, duration: float, dest: Path) -> Path:
    """audio_only：黑场画面 + 原音频，时长取音频时长。"""
    ff = check_ffmpeg()
    if not ff:
        raise RuntimeError("缺少 ffmpeg")
    from montage.compose.ffmpeg_engine import _run

    dur = max(float(duration), 0.1)
    _run([
        ff, "-y",
        "-f", "lavfi", "-i", f"color=c=black:s=1920x1080:d={dur:.3f}:r=30",
        "-i", str(src_path),
        "-shortest",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-movflags", "+faststart",
        str(dest),
    ])
    return dest


def _kept_shots(plan: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    out: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    files = (plan.get("source_decl") or {}).get("files") or []
    for seg in plan.get("segments") or []:
        if seg.get("action", "keep") != "keep":
            continue
        src = files[int(seg["source_index"])]
        for shot in seg.get("shots") or []:
            out.append((seg, shot, src))
    return out


# ---------------------------------------------------------------------------
# P0-8 vfx 作者面（后续小刀）：plan 内 vfx 数据 + onset 解析 + 渲染指纹
# ---------------------------------------------------------------------------

#: plan 单镜 vfx 上限：防 overrides 把单镜塞成特效串烧（提示词层密度红线同款纪律）。
MAX_VFX_PER_SHOT = 4


def sanitize_shot_vfx(raw: Any) -> list[dict[str, Any]]:
    """plan 镜的 vfx 清洗：只收 dict、layer 收敛 prompt/post、字段类型归一、限 ≤4 条。

    与 bible 侧 ``_overlay_vfx`` 语义一致但更宽松（plan 是机器产物，脏数据丢弃
    即可，不出 findings——审查发生在 compile 自审与 replan 回显）。
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if len(out) >= MAX_VFX_PER_SHOT:
            break
        if not isinstance(item, dict):
            continue
        layer = str(item.get("layer") or "").strip()
        kind = str(item.get("kind") or "").strip()
        if layer not in ("prompt", "post") or not kind:
            continue
        row: dict[str, Any] = {"layer": layer, "kind": kind}
        skip = False
        for key in ("onset", "duration", "intensity"):
            if item.get(key) is not None:
                try:
                    row[key] = float(item[key])
                except (TypeError, ValueError):
                    skip = True
                    break
        if skip:
            continue
        note = str(item.get("note") or "").strip()
        if note:
            row["note"] = note
        out.append(row)
    return out


def resolve_shot_vfx(
    shot: dict[str, Any],
    beat_map: dict[str, Any] | None,
    *,
    source_index: int = 0,
) -> list[dict[str, Any]]:
    """P0-8 第 2.5 刀：post 层 vfx 的 onset 解析（生成前定值，不进滤镜表达式）。

    - **显式 onset 永远优先**（作者面拍板的是硬数据，吸附只是缺省兜底）；
    - onset 缺省 → 吸附该镜时间窗内**能量最高**的 bar 起点（冲击落高潮点，
      而非最近 bar——闪白打在重拍上才有意义）；跨源镜（source_index）取对应
      源的 bars；beat_map 缺失/无 bars/镜不在任何 bar 内 → 回落 0；
    - prompt 层条目原样带过（assemble 无意义，留给审计完整性）。
    """
    rows = sanitize_shot_vfx(shot.get("vfx"))
    if not rows:
        return []
    need_anchor = [r for r in rows if r["layer"] == "post" and "onset" not in r]
    anchor: float | None = None
    if need_anchor:
        anchor = _beat_anchor_for_shot(shot, beat_map, source_index=source_index)
    out: list[dict[str, Any]] = []
    for row in rows:
        if row["layer"] == "post" and "onset" not in row:
            out.append({**row, "onset": round(anchor or 0.0, 3)})
        else:
            out.append(row)
    return out


def _beat_anchor_for_shot(
    shot: dict[str, Any],
    beat_map: dict[str, Any] | None,
    *,
    source_index: int,
) -> float | None:
    """镜时间窗 [start, end) 内能量最高的 bar → **镜内相对** onset（秒）。

    找不到 → None（调用方回落 0）。返回值是相对秒（0=镜头起点），可直接喂给
    ``apply_post_vfx``——bar 起点是源时间轴绝对秒，必须减掉 ``shot.start``。

    单源用顶层 ``bars``；多源优先 ``sources[i].bars``（``plan_beat_cuts_for_sources``
    落盘形状）。切点 ``cuts`` 不参与吸附——bar 起点才是节拍重音位。
    """
    if not isinstance(beat_map, dict):
        return None
    sources = beat_map.get("sources") or []
    bars: Any = None
    if 0 <= source_index < len(sources) and isinstance(sources[source_index], dict):
        bars = sources[source_index].get("bars")
    if not isinstance(bars, list) or not bars:
        bars = beat_map.get("bars")
    if not isinstance(bars, list) or not bars:
        return None
    try:
        start = float(shot.get("start") or 0.0)
        end = float(shot.get("end") or 0.0)
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    best: tuple[float, float] | None = None  # (energy, bar_start_abs)
    for bar in bars:
        if not isinstance(bar, dict):
            continue
        try:
            bar_start = float(bar.get("start_seconds") or 0.0)
            bar_end = float(bar.get("end_seconds") or 0.0)
            energy = float(bar.get("energy") or 0.0)
        except (TypeError, ValueError):
            continue
        # bar 与镜时间窗有重叠即候选，取能量最高者
        if bar_end > start and bar_start < end and (best is None or energy > best[0]):
            best = (energy, bar_start)
    if best is None:
        return None
    # 绝对 → 镜内相对；bar 起点早于镜起点时落在 0
    rel = max(0.0, best[1] - start)
    return round(min(rel, end - start), 3)


def vfx_summary(vfx_rows: list[dict[str, Any]]) -> str:
    """vfx 列表的稳定短摘要（渲染断点指纹成分；不含 prompt 层——它不进渲染）。"""
    post = sorted(
        (r for r in vfx_rows if r.get("layer") == "post"),
        key=lambda r: (float(r.get("onset") or 0), str(r.get("kind"))),
    )
    if not post:
        return ""
    return hashlib.sha256(
        json.dumps(post, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]


def vfx_enabled() -> bool:
    """MONTAGE_NO_VFX=1 时 auto_edit 渲染路径整体跳过 vfx（与 assemble 同一逃生门）。"""
    return os.environ.get("MONTAGE_NO_VFX", "").strip() != "1"


def render_plan(
    project_dir: str | Path,
    plan: dict[str, Any],
    *,
    preview: bool = False,
    resume: bool = True,
) -> dict[str, Any]:
    from montage.compose import effects
    from montage.compose import ffmpeg_engine as fe
    from montage.tools.system_probe import build_hardware_profile

    root = Path(project_dir)
    auto_dir, tmp = ensure_dirs(root)
    report: dict[str, Any] = {"output_path": "", "render_log": []}
    plan_sha = plan_fingerprint(plan)
    report["plan_sha"] = plan_sha

    def _step(name: str, status: str, detail: str = "") -> None:
        """六步断点的可见面：跑了 / 跳了（resume 命中）/ 没条件跑。"""
        row: dict[str, Any] = {"step": name, "status": status}
        if detail:
            row["detail"] = detail
        report["render_log"].append(row)

    def _fail(step: str, err: str, partial: dict[str, Any]) -> dict[str, Any]:
        report.update({"failed_step": step, "error": err, "partial_outputs": partial})
        (auto_dir / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        return report

    files = (plan.get("source_decl") or {}).get("files") or []
    try:
        integrity = verify_integrity(files)
    except OSError as exc:
        return _fail("workspace", str(exc), {})
    report["source_integrity"] = integrity
    _step("workspace", "done", f"校验 {len(files)} 个源文件 sha256")

    hw = build_hardware_profile()
    report["hardware_profile"] = hw
    report["hardware_crf"] = int((hw.get("encode") or {}).get("crf") or 18)

    pack = get_style_pack(plan.get("style_pack") or "") or {}
    profile_name = plan.get("output_profile") or pack.get("output_profile") or "youtube_landscape"
    kept = _kept_shots(plan)
    if not kept:
        return _fail("effects", "没有可保留的镜头", {})

    kind = (plan.get("source_decl") or {}).get("kind") or "video"
    is_speech = bool((plan.get("source_decl") or {}).get("is_speech"))
    audio_cache: dict[int, Path] = {}
    clip_paths: list[Path] = []
    # P0-8：beat_map 只在有 post 层缺 onset 的 vfx 需要吸附时读。
    need_beat = vfx_enabled() and any(
        any(v.get("layer") == "post" and "onset" not in v for v in sanitize_shot_vfx(shot.get("vfx")))
        for _seg, shot, _src in kept
    )
    beat_map = load_beat_map(root) if need_beat else None
    vfx_clips = 0
    for i, (seg, shot, src) in enumerate(kept):
        src_path = Path(src["path"])
        base_clip = tmp / clip_cache_name(i, shot, src_path, is_speech=is_speech)
        assert_output_inside(root, base_clip)
        if not (resume and base_clip.is_file()):
            work = base_clip
            if kind == "audio_only":
                idx = int(seg["source_index"])
                if idx not in audio_cache:
                    placeholder = tmp / f"audio_src_{idx:02d}.mp4"
                    assert_output_inside(root, placeholder)
                    _black_video_with_audio(src_path, float(src.get("duration") or 0), placeholder)
                    audio_cache[idx] = placeholder
                src_path = audio_cache[idx]
            start = float(shot["start"])
            dur = float(shot["end"]) - start
            fe.trim_clip(src_path, work, start, dur)
            if is_speech:
                silenced = work.with_name(f"{work.stem}_sil.mp4")
                assert_output_inside(root, silenced)
                effects.cut_silence(work, silenced, threshold_db=-35.0)
                work = silenced
            speed = float(shot.get("speed") or 1.0)
            if abs(speed - 1.0) > 0.01:
                sped = tmp / f"{work.stem}_speed.mp4"
                assert_output_inside(root, sped)
                effects.change_speed(work, sped, factor=speed)
                work = sped
            # 基础裁剪产物统一落到 clip_cache_name；中间 sil/speed 用完后拷回。
            if work.resolve() != base_clip.resolve():
                base_clip.write_bytes(work.read_bytes())

        # P0-8：post 层 vfx 在 trim/speed 之后、join 之前（先 vfx 后 LUT）。
        # 裁剪缓存不含 vfx——改特效不重 trim；特效产物按内容寻址。
        clip = base_clip
        if vfx_enabled():
            resolved = resolve_shot_vfx(
                shot, beat_map, source_index=int(seg.get("source_index") or 0),
            )
            post_rows = [v for v in resolved if v.get("layer") == "post"]
            if post_rows:
                tag = vfx_summary(post_rows)
                vfx_out = tmp / f"{base_clip.stem}_vfx_{tag}.mp4"
                assert_output_inside(root, vfx_out)
                if not (resume and vfx_out.is_file()):
                    effects.apply_post_vfx(base_clip, vfx_out, post_rows, work_dir=tmp)
                clip = vfx_out
                vfx_clips += 1
        clip_paths.append(clip)

    if clip_paths:
        mark_step(root, "effects", clip_paths[-1], plan_sha=plan_sha)
        detail = f"{len(clip_paths)} 镜（trim/cut_silence/change_speed）"
        if vfx_clips:
            detail += f" + {vfx_clips} 镜特效"
        _step("effects", "done", detail)
    report["vfx_clips"] = vfx_clips

    joined = tmp / "joined.mp4"
    assert_output_inside(root, joined)
    skipped = resume and step_complete(root, "stitch", plan_sha=plan_sha)
    if skipped:
        joined = skipped
        _step("stitch", "skipped", "resume 命中同版产物（sha256 校验通过）")
    elif len(clip_paths) == 1:
        fe.concat_videos(clip_paths, joined)
        mark_step(root, "stitch", joined, plan_sha=plan_sha)
        _step("stitch", "done", "单镜直接落盘")
    else:
        transitions = []
        for edit in plan.get("edits") or []:
            transitions.append({
                "transition_in": edit.get("transition") or "cut",
                "transition_duration": float(edit.get("transition_duration") or 0.0),
            })
        while len(transitions) < len(clip_paths) - 1:
            transitions.append({"transition_in": "cut", "transition_duration": 0.0})
        fe.stitch_with_transitions(clip_paths, transitions[: len(clip_paths) - 1], joined)
        mark_step(root, "stitch", joined, plan_sha=plan_sha)
        _step("stitch", "done", f"{len(clip_paths)} 镜拼接")

    current = joined
    lut_id = (plan.get("params") or {}).get("lut") or pack.get("lut") or ""
    lut_path = resolve_lut_path(str(lut_id))
    if lut_path is not None:
        graded = tmp / "graded.mp4"
        assert_output_inside(root, graded)
        skipped = resume and step_complete(root, "grade", plan_sha=plan_sha)
        if skipped:
            current = skipped
            _step("grade", "skipped", "resume 命中同版产物")
        else:
            fe.apply_lut(current, lut_path, graded)
            mark_step(root, "grade", graded, plan_sha=plan_sha)
            current = graded
            _step("grade", "done", f"LUT {lut_id}")
        report["note"] = ""
    elif lut_id:
        report["note"] = f"LUT {lut_id} 文件不可用，已跳过调色"
        _step("grade", "skipped", f"LUT {lut_id} 文件不可用")
    else:
        _step("grade", "skipped", "风格包未指定 LUT")

    if preview:
        preview_path = tmp / "preview.mp4"
        assert_output_inside(root, preview_path)
        ff = check_ffmpeg()
        if not ff:
            return _fail("preview", "缺少 ffmpeg", {"current": str(current)})
        from montage.compose.ffmpeg_engine import _run

        _run([
            ff, "-y", "-i", str(current),
            "-vf", "scale=-2:480",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "28",
            "-c:a", "aac", "-movflags", "+faststart", str(preview_path),
        ])
        report["output_path"] = str(preview_path)
        report["encoding"] = "h264-preview-480p"
        _step("profile", "skipped", "preview 模式不出成片 profile")
        _step("final", "skipped", "preview 模式写 tmp_autoedit/preview.mp4")
        return report

    profiled = tmp / "profiled.mp4"
    assert_output_inside(root, profiled)
    skipped = resume and step_complete(root, "profile", plan_sha=plan_sha)
    if skipped:
        current = skipped
        _step("profile", "skipped", "resume 命中同版产物")
    else:
        fe.apply_profile(current, profile_name, profiled)
        mark_step(root, "profile", profiled, plan_sha=plan_sha)
        current = profiled
        _step("profile", "done", profile_name)

    final = auto_dir / "final.mp4"
    assert_output_inside(root, final)
    if current.resolve() != final.resolve():
        final.write_bytes(current.read_bytes())
    mark_step(root, "final", final, plan_sha=plan_sha)
    _step("final", "done", str(final))
    report["output_path"] = str(final)
    report["encoding"] = "h264"
    try:
        info = ffprobe(final)
        report["duration_seconds"] = float((info.get("format") or {}).get("duration") or 0)
    except Exception:  # noqa: BLE001
        report["duration_seconds"] = 0
    report["source_integrity"] = verify_integrity(files)
    (auto_dir / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (auto_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return report


class AutoEdit(BaseTool):
    name = "auto_edit"
    version = "0.1.0"
    capability = "auto_edit"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "required": ["operation", "project_dir"],
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["probe", "analyze", "plan", "preview", "replan", "render", "history", "revert"],
            },
            "project_dir": {"type": "string"},
            "video": {"type": "string"},
            "clips": {"type": "array", "items": {"type": "string"}},
            "audio_only": {"type": "string"},
            "style": {"type": "string", "default": "documentary"},
            "profile": {"type": "string"},
            "overrides": {
                "type": "object",
                "description": (
                    "StylePack 覆写和/或 replan（段 action / 镜 start·end·speed·vfx）。"
                    "P0-8：镜级 vfx[] 写 {\"seg_0_shot_0\": {\"vfx\": [{\"layer\":\"post\",\"kind\":\"impact_flash\"}]}}"
                ),
            },
            "has_bgm": {"type": "boolean", "default": False},
            "is_speech": {"type": "boolean", "default": False},
            "language": {"type": "string", "default": "zh"},
            "bpm": {"type": "number", "default": 0, "description": "P0-5 曲库 bpm；0=自相关估拍（estimated，带 warning）"},
            "beats_per_bar": {"type": "integer", "default": 4, "description": "P0-5 每 bar 拍数"},
            "beat_cuts": {
                "type": "boolean", "default": True,
                "description": "P0-5：has_bgm 时用能量波/拍网格接管切点（false=纯 scene-change）",
            },
            "target_duration": {"type": "number"},
            "resume": {"type": "boolean", "default": True},
            "scene_index": {"type": "object", "description": "P0-2 情节单元索引（省略则自动读 artifacts/scene_index.json）"},
            "scene_index_path": {"type": "string", "description": "显式 scene_index.json 路径"},
            "use_scene_index": {"type": "boolean", "default": True},
            "reason": {"type": "string", "description": "本版改动说明（写入 plan_history + decisions.jsonl）"},
            "rev": {"type": "integer", "description": "history/revert 的目标版本号"},
            "max_versions": {"type": "integer",
                             "description": "版本链保留上限（0=不限）。不传则沿用项目已存策略，首次默认 50"},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if check_ffmpeg() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        return 0.0

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        op = inputs.get("operation")
        project_dir = Path(inputs.get("project_dir") or "")
        if not str(project_dir):
            return ToolResult(success=False, error="缺少 project_dir")
        try:
            ensure_project_meta(project_dir)
            auto_dir, _tmp = ensure_dirs(project_dir)
            if op == "probe":
                return self._probe(inputs)
            if op == "analyze":
                return self._analyze(inputs)
            if op == "plan":
                return self._plan(inputs, project_dir, auto_dir)
            if op == "replan":
                return self._replan(inputs, project_dir, auto_dir)
            if op == "history":
                return self._history(inputs, project_dir)
            if op == "revert":
                return self._revert(inputs, project_dir)
            if op == "preview":
                return self._render(inputs, project_dir, auto_dir, preview=True)
            if op == "render":
                return self._render(inputs, project_dir, auto_dir, preview=False)
            return ToolResult(success=False, error=f"未知 operation: {op}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=str(exc))

    def _collect_paths(self, inputs: dict[str, Any]) -> tuple[list[Path], str]:
        video = inputs.get("video")
        clips = inputs.get("clips") or []
        audio_only = inputs.get("audio_only")
        present = sum(bool(x) for x in (video, clips, audio_only))
        if present != 1:
            raise ValueError("video / clips / audio_only 必须三选一")
        if video:
            return [Path(video)], "video"
        if audio_only:
            return [Path(audio_only)], "audio_only"
        return [Path(p) for p in clips], "video"

    def _probe(self, inputs: dict[str, Any]) -> ToolResult:
        paths, kind = self._collect_paths(inputs)
        decl = probe_sources(
            paths, kind=kind,
            has_bgm=bool(inputs.get("has_bgm")),
            is_speech=bool(inputs.get("is_speech")),
            language=str(inputs.get("language") or "zh"),
        )
        return ToolResult(success=True, data={"source_decl": decl})

    def _analyze(self, inputs: dict[str, Any]) -> ToolResult:
        paths, kind = self._collect_paths(inputs)
        style = inputs.get("style") or "documentary"
        threshold = 0.15 if style == "beat" else 0.3
        if kind == "audio_only":
            changes = [[] for _ in paths]
        else:
            changes = [analyze_scene_changes(p, threshold=threshold) for p in paths]
        return ToolResult(success=True, data={"scene_changes_by_source": changes, "threshold": threshold})

    @staticmethod
    def _profile_fps(style: str, pack_over: dict[str, Any] | None) -> float:
        """渲染帧率（拍网格锚帧用）：风格包的 output_profile 优先，未知回 30。"""
        try:
            from montage.compose.profiles import get_profile

            pack = apply_overrides(get_style_pack(style) or {}, pack_over)
            profile = get_profile(str(pack.get("output_profile") or ""))
            return float(getattr(profile, "fps", 30.0) or 30.0)
        except Exception:  # noqa: BLE001
            return 30.0

    def _beat_cuts(
        self,
        inputs: dict[str, Any],
        paths: list[Path],
        changes: list[list[float]],
        *,
        style: str,
        pack_over: dict[str, Any] | None,
    ) -> tuple[list[list[float]], dict[str, Any], dict[str, Any] | None, float | None]:
        """P0-5：有 BGM 时用能量波/拍网格生成切点，**替换**该源的 scene-change 切点。

        上闸条件（任一不满足 → 原样放行 + 回报原因，绝不悄悄改剪辑）：
        - ``beat_cuts`` 显式为 false（用户主动关）；
        - ``has_bgm`` 为 false（没有音乐能量可跟，切点无处可依）；
        - ``is_speech`` 为 true（对白片切在拍上会切断句子，P0-5 不接管语音节奏）。

        ``min_hold``/``max_hold`` 直接取风格包（含 overrides.pacing）——P0-5 不新增旋钮。
        """
        holds = self._pack_holds(style, pack_over)
        bpm_in = float(inputs.get("bpm") or 0.0)
        if inputs.get("beat_cuts") is False:
            return changes, {"used": False, "reason": "beat_cuts=false（显式关闭）"}, None, None
        if not inputs.get("has_bgm"):
            return changes, {"used": False, "reason": "has_bgm=false（无音乐能量可跟）"}, None, None
        if inputs.get("is_speech"):
            return changes, {"used": False, "reason": "is_speech=true：对白片不按拍切（会切断句子）"}, None, None
        if not paths:
            return changes, {"used": False, "reason": "无源文件"}, None, None

        beats_per_bar = int(inputs.get("beats_per_bar") or 4)
        fps = self._profile_fps(style, pack_over)
        beat_map = plan_beat_cuts_for_sources(
            paths, bpm=bpm_in, fps=fps, beats_per_bar=beats_per_bar,
            min_hold=holds[0], max_hold=holds[1],
        )
        merged, note = apply_beat_cuts(changes, beat_map)
        beat_map["cuts_by_source"] = note
        beat_map["note"] = note
        replaced = [r for r in note.get("rows") or [] if r.get("mode") == "replaced"]
        if replaced:
            # 顶层 cuts/bars 校正为**真正接管了 plan 的那个源**（多源看 sources[]）
            idx = int(replaced[0]["index"])
            if 0 <= idx < len(merged):
                beat_map["cuts"] = list(merged[idx])
                beat_map["cuts_source_index"] = idx
            src_row = (beat_map.get("sources") or [{}])[idx] if idx < len(beat_map.get("sources") or []) else {}
            if isinstance(src_row, dict) and src_row.get("bars"):
                beat_map["bars"] = list(src_row["bars"])
        warnings = list(beat_map.get("warnings") or [])
        if inputs.get("target_duration") and note.get("used"):
            warnings.append(
                "同时给了 target_duration：_fit_target_duration 会整体缩放镜长，"
                "切点会离开拍位——要严格吸拍请去掉 target_duration"
            )
        beat_map["warnings"] = warnings
        beat_map["feasible"] = bool(note.get("used"))
        return merged, note, beat_map, (float(beat_map.get("bpm") or 0.0) or None)

    @staticmethod
    def _pack_holds(style: str, pack_over: dict[str, Any] | None) -> tuple[float, float]:
        """风格包（含 overrides.pacing）的 min_hold/max_hold——与 build_plan 同源。"""
        pack = apply_overrides(get_style_pack(style) or {}, pack_over)
        pacing = pack.get("pacing") or {}
        return float(pacing.get("min_hold") or 1.0), float(pacing.get("max_hold") or 8.0)

    def _plan(self, inputs: dict[str, Any], project_dir: Path, auto_dir: Path) -> ToolResult:
        paths, kind = self._collect_paths(inputs)
        style = str(inputs.get("style") or "documentary")
        if get_style_pack(style) is None:
            return ToolResult(success=False, error=f"未知风格包: {style}")
        overrides = inputs.get("overrides") or {}
        pack_over = {
            k: overrides[k]
            for k in ("lut", "transitions", "pacing", "output_profile", "bind_playbook")
            if k in overrides
        }
        if inputs.get("profile"):
            pack_over["output_profile"] = inputs["profile"]
        if pack_over:
            errs = validate_overrides(pack_over)
            if errs:
                return ToolResult(success=False, error="；".join(errs))
        decl = probe_sources(
            paths, kind=kind,
            has_bgm=bool(inputs.get("has_bgm")),
            is_speech=bool(inputs.get("is_speech")),
            language=str(inputs.get("language") or "zh"),
        )
        threshold = 0.15 if style == "beat" else 0.3
        if kind == "audio_only":
            changes = [[] for _ in decl["files"]]
        else:
            changes = [analyze_scene_changes(Path(f["path"]), threshold=threshold) for f in decl["files"]]
        changes, scene_note = self._merge_scene_cuts(inputs, project_dir, paths, changes)
        changes, beat_note, beat_map, plan_bpm = self._beat_cuts(
            inputs, paths, changes, style=style, pack_over=pack_over or None,
        )
        if beat_map is not None:
            save_beat_map(project_dir, beat_map)
        else:
            # 下闸时必须清掉旧产物：留着会让量规层拿上一次的网格把 m5/m6 误标 circular
            clear_beat_map(project_dir)
        previous = self._load_plan(auto_dir)
        max_versions = inputs.get("max_versions")
        plan = build_plan(
            source_decl=decl, style_pack_id=style,
            scene_changes_by_source=changes,
            target_duration=inputs.get("target_duration"),
            pack_overrides=pack_over or None,
            bpm=plan_bpm,
        )
        self._count_applied_cuts(plan, scene_note)
        if beat_note.get("used"):
            self._count_applied_cuts(plan, {
                "used": True,
                "cut_times": [list(s.get("cuts") or []) for s in (beat_map or {}).get("sources") or []],
            })
        (auto_dir / "plan.json").write_text(
            json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        reason = str(inputs.get("reason") or "").strip()
        recorded = append_plan_version(
            project_dir, plan, previous=previous,
            reason=reason or f"plan（style={style}）", max_versions=max_versions,
        )
        detail = plan_diff_summary(recorded.get("diff") or {"unchanged": True})
        if beat_note.get("used"):
            detail = (detail + "；" if detail else "") + (
                f"P0-5 能量波切点接管：bpm={beat_map.get('bpm')}"
                f"（{beat_map.get('bpm_source')}）× {plan.get('params', {}).get('min_hold')}–"
                f"{plan.get('params', {}).get('max_hold')}s 硬约束"
            )
        _log_decision(project_dir, plan["session_id"],
                      f"rev {recorded.get('rev')}: {reason or '初始草稿'}（style={style}）",
                      detail=detail)
        return ToolResult(
            success=True,
            data={
                "plan": plan,
                "path": str(auto_dir / "plan.json"),
                "scene_index": scene_note,
                "beat_map": beat_map_summary(beat_map) if beat_map else beat_note,
                "beat_cuts": beat_note,
                "history": plan_history_summary(project_dir),
                "rev": recorded.get("rev"),
            },
        )

    @staticmethod
    def _load_plan(auto_dir: Path) -> dict[str, Any] | None:
        """读现有 plan.json（版本链接力用）；缺失/坏 JSON → None。"""
        path = auto_dir / "plan.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _merge_scene_cuts(
        inputs: dict[str, Any],
        project_dir: Path,
        paths: list[Path],
        changes: list[list[float]],
    ) -> tuple[list[list[float]], dict[str, Any]]:
        """把 ``scene_index`` 的情节单元边界并进切点（P0-2）：草稿沿叙事单元断镜。

        未提供/未匹配到的源不加切点，只回报 ``matched``，绝不张冠李戴。
        """
        from montage.tools.scene_pipeline import load_scene_index, scene_cuts_by_source

        if inputs.get("use_scene_index") is False:
            return changes, {"used": False, "reason": "已显式关闭"}
        index = inputs.get("scene_index")
        if not isinstance(index, dict):
            index = load_scene_index(project_dir, inputs.get("scene_index_path"))
        if not index:
            return changes, {"used": False, "reason": "无 scene_index（可先跑 scene_pipeline）"}
        per_source = scene_cuts_by_source(index, paths)
        merged = [
            sorted(set(changes[i]) | set(per_source[i])) if i < len(per_source) else changes[i]
            for i in range(len(changes))
        ]
        return merged, {
            "used": any(per_source),
            "unit_count": len(index.get("units") or []),
            "narrative_cuts": [len(c) for c in per_source],
            "matched": [bool(c) for c in per_source],
            "grouping": index.get("grouping"),
            "cut_times": per_source,
        }

    @staticmethod
    def _count_applied_cuts(plan: dict[str, Any], note: dict[str, Any]) -> None:
        """回报叙事切点有多少真的落成断镜点——被风格包 min_hold 吃掉时别装作成功。"""
        cut_times = note.pop("cut_times", None) or []
        if not note.get("used") or not cut_times:
            return
        starts_by_source: dict[int, list[float]] = {}
        for seg in plan.get("segments") or []:
            idx = int(seg.get("source_index") or 0)
            starts_by_source.setdefault(idx, []).extend(float(s["start"]) for s in seg.get("shots") or [])
        applied: list[int] = []
        for idx, cuts in enumerate(cut_times):
            starts = starts_by_source.get(idx, [])
            applied.append(sum(1 for c in cuts if any(abs(s - c) < 0.01 for s in starts)))
        note["applied"] = applied
        dropped = sum(len(c) for c in cut_times) - sum(applied)
        if dropped > 0:
            note["dropped"] = dropped
            note["note"] = (
                f"{dropped} 个叙事切点被风格包 min_hold={plan.get('params', {}).get('min_hold')} 吃掉"
                "（要保留请换更碎的风格包或调 overrides.pacing.min_hold）"
            )

    def _replan(self, inputs: dict[str, Any], project_dir: Path, auto_dir: Path) -> ToolResult:
        plan_path = auto_dir / "plan.json"
        if not plan_path.exists():
            return ToolResult(success=False, error="缺少 plan.json，请先 plan")
        first = json.loads(plan_path.read_text(encoding="utf-8"))
        nxt = replan(first, inputs.get("overrides") or {})
        plan_path.write_text(json.dumps(nxt, ensure_ascii=False, indent=2), encoding="utf-8")
        reason = str(inputs.get("reason") or "").strip()
        recorded = append_plan_version(
            project_dir, nxt, previous=first,
            reason=reason or "replan",
            overrides=inputs.get("overrides") or {},
            max_versions=inputs.get("max_versions"),
        )
        diff = recorded.get("diff") if isinstance(recorded.get("diff"), dict) else {}
        _log_decision(project_dir, nxt.get("session_id") or "",
                      f"rev {recorded.get('rev')}: {reason or 'replan'}",
                      detail=plan_diff_summary(diff))
        return ToolResult(
            success=True,
            data={
                "plan": nxt,
                "changed": plan_differs(first, nxt),
                "path": str(plan_path),
                "rev": recorded.get("rev"),
                "recorded": bool(recorded.get("changed")),
                "diff": diff,
                "diff_summary": plan_diff_summary(diff),
                "history": plan_history_summary(project_dir),
            },
        )

    def _history(self, inputs: dict[str, Any], project_dir: Path) -> ToolResult:
        summary = plan_history_summary(project_dir)
        data: dict[str, Any] = {"history": summary}
        rev = inputs.get("rev")
        if rev is not None:
            plan = read_version(project_dir, int(rev))
            if plan is None:
                return ToolResult(success=False, error=f"找不到版本 v{int(rev)}")
            data["plan"] = plan
        return ToolResult(success=True, data=data)

    def _revert(self, inputs: dict[str, Any], project_dir: Path) -> ToolResult:
        rev = inputs.get("rev")
        if rev is None:
            return ToolResult(success=False, error="revert 需要 'rev'")
        out = revert_plan(project_dir, int(rev),
                          max_versions=inputs.get("max_versions"))
        if not out.get("ok"):
            return ToolResult(success=False, error=str(out.get("error") or "回滚失败"))
        _log_decision(project_dir, str((out.get("plan") or {}).get("session_id") or ""),
                      f"rev {out.get('rev')}: revert to v{int(rev)}",
                      detail=f"回滚到 v{int(rev)}；plan.json 已改，需重新 render")
        return ToolResult(success=True, data={
            "plan": out.get("plan"),
            "reverted_to": out.get("reverted_to"),
            "rev": out.get("rev"),
            "note": out.get("note"),
            "history": plan_history_summary(project_dir),
        })

    def _render(self, inputs: dict[str, Any], project_dir: Path, auto_dir: Path, *, preview: bool) -> ToolResult:
        plan_path = auto_dir / "plan.json"
        if not plan_path.exists():
            return ToolResult(success=False, error="缺少 plan.json，请先 plan")
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        report = render_plan(
            project_dir, plan, preview=preview, resume=bool(inputs.get("resume", True)),
        )
        if report.get("failed_step"):
            return ToolResult(success=False, error=report.get("error") or "render 失败", data=report)
        return ToolResult(success=True, data=report)
