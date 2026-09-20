"""scene_pipeline — 场景语义聚合层（v8.2 P0-2；通路 B 剪「现有素材」的语义前置）。

问题：`scene_detect` 的切点是**视觉碎切**（一个抽烟动作能切出 8 个点），直接喂
`auto_edit.build_plan` 得到的草稿对齐的是像素抖动，不是叙事。本层只补**缺的那层
语义**，低层组件全部复用不重建：

    复用                        本层新增
    ----                        --------
    ``scene_detect`` 切点   →   CutClaw 式相邻镜判定：视觉换场 + 台词连读
    ffmpeg 抽帧（seek 定位）→   8x8 RGB 指纹（零依赖 rawvideo）+ VLM 窗口代表帧
    ``dashscope_asr`` 转写  →   转写按时间重叠落到镜（台词即单元语义）
    -----                       ``scene_index.json`` + 通路 B 草稿叙事提示

抽帧不用 ``frame_sampler``（它是「按 fps 均匀扫一遍落 png」，帧号≠时间且给不出像素
字节）；指纹要 rawvideo、VLM 要「第 X 秒的帧」，所以走 seek 定位单帧（``extract_frame_at``）。

**全局硬规则：LLM 只回索引，不回时间戳**（clips-studio 教训）。聚合模型只被允许
回答「第几号镜开始新情节单元」；所有 ``start_seconds``/``end_seconds`` 一律由
确定性切点推导（``_timestamps_from_shots``）。模型若吐时间码，解析层直接丢弃并
记 warning —— 长片里让模型报时间是漂移之源。

零密钥路径完整可用：VLM 分组失败/未配置 → 回落确定性分块（视觉指纹 + 台词连读
+ 时间间隔），只在 ``warnings`` 里留痕，不挡流程。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from montage.compose.ffmpeg_engine import check_ffmpeg, probe
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus
from montage.tools.auto_edit import shots_from_cuts

SCENE_INDEX_ARTIFACT = "scene_index"

# 视觉指纹相似度低于该值 → 判「视觉换场」（RGB 均值绝对差/255，同场不同机位≈0.7+）。
DEFAULT_VISUAL_CUT = 0.55
# 台词 Jaccard 高于该值 → 判「台词连读」，压得住轻微视觉变化（同场反复的同一句）。
DEFAULT_TEXT_HOLD = 0.5
# 相邻镜时间间隔超过该秒数 → 直接判边界（静默/转场后必换单元）。
DEFAULT_TIME_GAP_SECONDS = 3.0
# VLM 分组窗口（一次最多看这么多镜的代表帧，长片靠窗口滑动覆盖）。
VLM_WINDOW_SHOTS = 24
# 指纹边长（8x8 RGB = 192 字节/镜，几百镜也只有几十 KB）。
FINGERPRINT_SIZE = 8
FINGERPRINT_CHANNELS = 3
# 默认最多给多少镜算视觉指纹（超过则只靠间隔，留 warning）。
DEFAULT_MAX_FINGERPRINT_SHOTS = 300

# 四拍叙事角色（与 series_bible.structure 同词表），按单元位置确定性指派。
NARRATIVE_ROLES = ("hook", "escalation", "reveal", "landing")

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_WORD_RE = re.compile(r"[a-z0-9]{2,}")
# 任何形态的时间码：HH:MM(:SS) 或 数字+时间单位。
_TIMEISH_RE = re.compile(r"\d{1,3}:\d{2}(?::\d{2})?|\d+(?:\.\d+)?\s*(?:秒|毫秒|s|ms|sec|second)")
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def extract_frame_at(path: str | Path, at_seconds: float, output: str | Path) -> str | None:
    """抽指定时刻的单帧到 ``output``；失败返回 None（VLM 窗口分组用）。"""
    ffmpeg = check_ffmpeg()
    if ffmpeg is None:
        return None
    dest = Path(output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-v", "error", "-ss", f"{max(0.0, float(at_seconds)):.3f}",
        "-i", str(path), "-frames:v", "1", "-q:v", "3", str(dest),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)  # noqa: S603
    except (subprocess.TimeoutExpired, OSError):
        return None
    return str(dest) if getattr(proc, "returncode", 1) == 0 and dest.is_file() else None


# ---------------------------------------------------------------------------
# 转写
# ---------------------------------------------------------------------------


def normalize_transcript(raw: Any) -> list[dict[str, Any]]:
    """各种转写产物 → ``[{text, start_seconds, end_seconds, words?}]``（时间升序）。"""
    sentences: list[Any] = []
    if isinstance(raw, dict):
        if isinstance(raw.get("sentences"), list):
            sentences = raw["sentences"]
        elif isinstance(raw.get("data"), dict) and isinstance(raw["data"].get("sentences"), list):
            sentences = raw["data"]["sentences"]
    elif isinstance(raw, list):
        sentences = raw
    out: list[dict[str, Any]] = []
    for item in sentences:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        start = _num(item.get("start_seconds", item.get("start")))
        end = max(start, _num(item.get("end_seconds", item.get("end"))))
        if not text and not item.get("words"):
            continue
        row: dict[str, Any] = {"text": text, "start_seconds": start, "end_seconds": end}
        words = item.get("words")
        if isinstance(words, list) and words:
            row["words"] = [
                {
                    "text": str(w.get("text") or ""),
                    "start_seconds": _num(w.get("start_seconds")),
                    "end_seconds": _num(w.get("end_seconds")),
                }
                for w in words
                if isinstance(w, dict)
            ]
        out.append(row)
    out.sort(key=lambda r: (r["start_seconds"], r["end_seconds"]))
    return out


def _overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def attach_transcript(shots: list[dict[str, Any]], sentences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """转写按**时间重叠最大**落到唯一一镜（单位秒；同镜内按时间序拼接）。

    一句跨切点时只归给它重叠最多的那镜——否则同一句会同时进两个单元的文本，
    把文本相似度虚高、边界判不出来。
    """
    if not sentences or not shots:
        return [dict(s) for s in shots]
    spans = [(_num(s.get("start")), _num(s.get("end"), _num(s.get("start")))) for s in shots]
    buckets: list[list[tuple[float, str]]] = [[] for _ in shots]
    for sent in sentences:
        text = str(sent.get("text") or "").strip()
        if not text:
            continue
        start, end = _num(sent.get("start_seconds")), _num(sent.get("end_seconds"))
        best_idx, best_span = -1, 0.0
        for idx, (shot_start, shot_end) in enumerate(spans):
            span = _overlap(shot_start, shot_end, start, end)
            if span > best_span:
                best_idx, best_span = idx, span
        if best_idx >= 0:
            buckets[best_idx].append((start, text))
    out: list[dict[str, Any]] = []
    for shot, bucket in zip(shots, buckets):
        row = dict(shot)
        row["text"] = "".join(text for _, text in sorted(bucket))
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# 相似度（CutClaw Sim(s_i, s_{i+1}) 的确定性实现）
# ---------------------------------------------------------------------------


def content_tokens(text: Any) -> set[str]:
    """中文按 2-gram、拉丁/数字按词切；零依赖、无分词库（对齐提示词检索惯例）。"""
    raw = str(text or "").lower()
    tokens: set[str] = set(_ASCII_WORD_RE.findall(raw))
    for run in _CJK_RE.findall(raw):
        if len(run) == 1:
            tokens.add(run)
            continue
        for i in range(len(run) - 1):
            tokens.add(run[i:i + 2])
    return tokens


def text_similarity(a: Any, b: Any) -> float:
    """台词 Jaccard（0..1）。**只用于「连读」判定**：普通连续台词 2-gram 重叠≈0，
    所以「低」不是边界证据，见 ``adjacency_similarity`` 的非对称用法。"""
    ta, tb = content_tokens(a), content_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def fingerprint_bytes(path: str | Path, *, at_seconds: float = 0.0, size: int = FINGERPRINT_SIZE,
                      runner: Callable[..., Any] | None = None) -> bytes | None:
    """抽一帧缩到 ``size×size`` RGB 原始字节（零依赖视觉指纹）。失败返回 None。

    用 RGB 而非灰度：灰度丢色相，红场 vs 蓝场的相似度高达 0.82，视觉换场全被漏判。
    """
    if check_ffmpeg() is None:
        return None
    cmd = [
        check_ffmpeg(), "-v", "error", "-ss", f"{max(0.0, float(at_seconds)):.3f}",
        "-i", str(path), "-frames:v", "1",
        "-vf", f"scale={int(size)}:{int(size)}:flags=area,format=rgb24",
        "-f", "rawvideo", "-",
    ]
    try:
        if runner is not None:
            proc = runner(cmd)
        else:
            proc = subprocess.run(cmd, capture_output=True, timeout=120)  # noqa: S603
    except (subprocess.TimeoutExpired, OSError):
        return None
    if getattr(proc, "returncode", 1) != 0:
        return None
    raw = proc.stdout or b""
    want = int(size) * int(size) * FINGERPRINT_CHANNELS
    return raw[:want] if len(raw) >= want else None


def fingerprint_hex(raw: bytes | None) -> str:
    return raw.hex() if raw else ""


def fingerprint_similarity(hex_a: str, hex_b: str) -> float | None:
    """指纹相似度：1 - 平均绝对灰度差/255。任一缺失返回 None（不计入加权）。"""
    if not hex_a or not hex_b or len(hex_a) != len(hex_b):
        return None
    try:
        a = bytes.fromhex(hex_a)
        b = bytes.fromhex(hex_b)
    except ValueError:
        return None
    if not a:
        return None
    diff = sum(abs(x - y) for x, y in zip(a, b)) / (255.0 * len(a))
    return max(0.0, 1.0 - diff)


def adjacency_similarity(
    prev: dict[str, Any],
    cur: dict[str, Any],
    *,
    visual_cut: float = DEFAULT_VISUAL_CUT,
    text_hold: float = DEFAULT_TEXT_HOLD,
    time_gap_seconds: float = DEFAULT_TIME_GAP_SECONDS,
) -> dict[str, Any]:
    """相邻镜是否换情节单元：``视觉换场 AND NOT 台词连读``，或大间隔硬断。

    非对称用法是刻意的：连续台词的 2-gram 重叠本来就≈0（「你来了」vs「我等你很久了」），
    所以「台词不像」**不是**换场证据，只有「视觉变脸」才是；反过来，台词高度重合
    （同一句反复/一段话跨镜）能压住轻微视觉变化，把同场戏黏住。

    没有视觉指纹时不猜（无证据不切），只认硬间隔；这类相邻镜在审计里留
    ``visual=None``，由 VLM 分组或人工补。
    """
    parts: dict[str, float] = {}
    warnings: list[str] = []

    text_sim = text_similarity(prev.get("text"), cur.get("text"))
    parts["text"] = round(text_sim, 4)
    held = text_sim >= float(text_hold)

    vis = fingerprint_similarity(str(prev.get("fingerprint") or ""), str(cur.get("fingerprint") or ""))
    visual_change = vis is not None and vis < float(visual_cut)
    if vis is not None:
        parts["visual"] = round(vis, 4)
    else:
        warnings.append("缺视觉指纹（无证据不切）")

    gap = max(0.0, _num(cur.get("start"), 0.0) - _num(prev.get("end"), 0.0))
    hard_gap = bool(time_gap_seconds) and gap >= float(time_gap_seconds)
    parts["gap_seconds"] = round(gap, 3)

    boundary = bool(hard_gap or (visual_change and not held))
    return {
        "boundary": boundary,
        "hard_boundary": hard_gap,
        "visual_change": visual_change,
        "text_hold": held,
        "parts": parts,
        "warnings": warnings,
    }


def deterministic_groups(
    shots: list[dict[str, Any]],
    *,
    visual_cut: float = DEFAULT_VISUAL_CUT,
    text_hold: float = DEFAULT_TEXT_HOLD,
    time_gap_seconds: float = DEFAULT_TIME_GAP_SECONDS,
) -> dict[str, Any]:
    """按 ``adjacency_similarity`` 切开；返回 ``{"groups": [[i...]], "similarities": [...]}``。"""
    if not shots:
        return {"groups": [], "similarities": []}
    groups: list[list[int]] = [[0]]
    similarities: list[dict[str, Any]] = []
    for idx in range(1, len(shots)):
        row = adjacency_similarity(shots[idx - 1], shots[idx], visual_cut=visual_cut,
                                   text_hold=text_hold, time_gap_seconds=time_gap_seconds)
        similarities.append({"after_shot_index": idx - 1, **row})
        if row["boundary"]:
            groups.append([idx])
        else:
            groups[-1].append(idx)
    return {"groups": groups, "similarities": similarities}


# ---------------------------------------------------------------------------
# LLM 只回索引（时间戳一律丢）
# ---------------------------------------------------------------------------


def _payload_from_text(text: Any) -> dict[str, Any] | None:
    raw = str(text or "")
    match = _FENCE_RE.search(raw)
    if match:
        raw = match.group(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        obj = _JSON_OBJ_RE.search(raw)
        if not obj:
            return None
        try:
            data = json.loads(obj.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _model_text(raw: Any) -> str:
    if isinstance(raw, dict):
        choices = raw.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            msg = choices[0].get("message")
            if isinstance(msg, dict) and msg.get("content"):
                return str(msg["content"])
        if isinstance(raw.get("output"), (dict, str)):
            out = raw["output"]
            return json.dumps(out, ensure_ascii=False) if isinstance(out, dict) else str(out)
        if "boundaries" in raw or "groups" in raw:
            return json.dumps(raw, ensure_ascii=False)
        return json.dumps(raw, ensure_ascii=False)
    return str(raw or "")


def _index_list(value: Any) -> list[int]:
    out: list[int] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, bool):
            continue
        if isinstance(item, int):
            out.append(item)
        elif isinstance(item, str) and item.strip().lstrip("-").isdigit():
            out.append(int(item.strip()))
        elif isinstance(item, float) and float(item).is_integer():
            out.append(int(item))
    return out


def _timestamp_leaks(payload: dict[str, Any]) -> list[str]:
    """找出模型越界上报的时间码（审计用；值一律不采用）。"""
    leaks: list[str] = []
    for key in ("boundaries_seconds", "times", "timestamps", "boundaries_time", "durations"):
        if key in payload:
            leaks.append(key)
    for item in payload.get("units") or []:
        if isinstance(item, dict) and ({"start", "end", "start_seconds", "end_seconds"} & set(item)):
            leaks.append("units[].start/end")
    # 文本里出现的 HH:MM 或「3.5 秒」也算越界（长片里让模型报时间=漂移之源）
    for hit in _TIMEISH_RE.findall(_model_text(payload)):
        leaks.append(f"text:{hit}")
    return sorted(set(leaks))


def parse_boundaries(raw: Any, *, shot_count: int, offset: int = 0) -> dict[str, Any]:
    """解析窗口内「新单元起始镜号」（0-based，窗口内偏移）。

    只读索引字段；``summaries``/``characters`` 的键也是索引。任何时间码丢弃并
    记进 ``time_leaks``。
    """
    payload = _payload_from_text(_model_text(raw))
    if payload is None:
        return {"boundaries": [], "summaries": {}, "characters": {}, "warnings": ["VLM 返回非 JSON，已忽略"]}
    warnings = _timestamp_leaks(payload)
    raw_boundaries = payload.get("boundaries")
    if raw_boundaries is None:
        raw_boundaries = payload.get("groups")
    boundaries: list[int] = []
    for local in _index_list(raw_boundaries):
        if local <= 0:  # 首镜必是单元开头，不用报
            continue
        if local >= shot_count:
            warnings.append(f"边界索引越界丢弃: {local}")
            continue
        boundaries.append(offset + local)
    summaries: dict[int, str] = {}
    characters: dict[int, list[str]] = {}
    for key, target in (("summaries", summaries), ("characters", characters)):
        block = payload.get(key)
        if not isinstance(block, dict):
            continue
        for raw_idx, val in block.items():
            idxs = _index_list([raw_idx])
            if not idxs:
                continue
            idx = offset + idxs[0]
            if key == "summaries":
                target[idx] = str(val or "").strip()
            else:
                target[idx] = [str(x).strip() for x in (val or []) if str(x).strip()]
    return {
        "boundaries": sorted(set(boundaries)),
        "summaries": summaries,
        "characters": characters,
        "warnings": warnings,
        "time_leaks": _timestamp_leaks(payload),
    }


# ---------------------------------------------------------------------------
# 索引构建（时间戳只从确定性切点推导）
# ---------------------------------------------------------------------------


def narrative_roles(count: int) -> list[str]:
    """按位置指派四拍：首=hook、末=landing、其余按比例分 escalation/reveal。"""
    if count <= 0:
        return []
    if count == 1:
        return ["hook"]
    if count == 2:
        return ["hook", "landing"]
    roles = ["escalation"] * count
    roles[0] = "hook"
    roles[-1] = "landing"
    reveal_at = max(1, round((count - 1) * 0.7))
    if reveal_at >= count - 1:
        reveal_at = count - 2
    roles[reveal_at] = "reveal"
    return roles


def _timestamps_from_shots(shots: list[dict[str, Any]], indices: list[int]) -> tuple[float, float]:
    """单元起止**只**由镜区间推导——不接受任何模型给出的时间。"""
    picked = [shots[i] for i in indices if 0 <= i < len(shots)]
    if not picked:
        return 0.0, 0.0
    return _num(picked[0].get("start")), _num(picked[-1].get("end"))


def build_scene_index(
    *,
    shots: list[dict[str, Any]],
    groups: list[list[int]],
    grouping: str,
    source: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    summaries: dict[int, str] | None = None,
    characters: dict[int, list[str]] | None = None,
    similarities: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """shots + 索引分组 → ``scene_index``（单元时间戳全部来自 shots）。"""
    summaries = summaries or {}
    characters = characters or {}
    roles = narrative_roles(len(groups))
    units: list[dict[str, Any]] = []
    shot_rows: list[dict[str, Any]] = []
    for unit_no, indices in enumerate(groups):
        start, end = _timestamps_from_shots(shots, indices)
        uid = f"u{unit_no + 1:02d}"
        text = "".join(str(shots[i].get("text") or "") for i in indices if 0 <= i < len(shots))
        unit = {
            "unit_id": uid,
            "index": unit_no,
            "narrative_role": roles[unit_no] if unit_no < len(roles) else "escalation",
            "start_seconds": round(start, 3),
            "end_seconds": round(end, 3),
            "shot_indices": list(indices),
            "shot_ids": [str(shots[i].get("shot_id") or "") for i in indices if 0 <= i < len(shots)],
            "text": text,
            "summary": str(summaries.get(indices[0] if indices else -1) or ""),
            "characters": list(characters.get(indices[0] if indices else -1) or []),
        }
        units.append(unit)
        for i in indices:
            if not (0 <= i < len(shots)):
                continue
            shot_rows.append({
                "shot_id": str(shots[i].get("shot_id") or ""),
                "index": i,
                "start_seconds": round(_num(shots[i].get("start")), 3),
                "end_seconds": round(_num(shots[i].get("end")), 3),
                "unit_id": uid,
                "characters": list(unit["characters"]),
                "text": str(shots[i].get("text") or ""),
            })
    shot_rows.sort(key=lambda r: r["index"])
    return {
        "version": "1",
        "grouping": grouping,
        "source": dict(source or {}),
        "params": dict(params or {}),
        "shots": shot_rows,
        "units": units,
        "unit_boundaries_seconds": [unit["start_seconds"] for unit in units if unit["index"] > 0],
        "preferred_cuts": [
            {"at_seconds": unit["start_seconds"], "shot_index": (unit["shot_indices"] or [0])[0],
             "reason": "情节单元边界", "unit_id": unit["unit_id"]}
            for unit in units
            if unit["index"] > 0
        ],
        "similarities": list(similarities or []),
        "warnings": list(warnings or []),
    }


def load_scene_index(project_dir: str | Path | None = None, path: str | Path | None = None) -> dict[str, Any] | None:
    """读 ``scene_index``：显式 ``path`` 优先，否则用 ``<project>/artifacts/scene_index.json``。"""
    target: Path | None = None
    if path:
        target = Path(str(path))
    elif project_dir:
        target = Path(str(project_dir)) / "artifacts" / "scene_index.json"
    if target is None or not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def narrative_cuts_for_source(index: dict[str, Any] | None, source_path: str | Path) -> list[float]:
    """该源视频的情节单元边界秒数；索引记录的源不是这个文件则返回 ``[]``。

    只认 ``source.path`` 解析后同一文件——多素材项目里张冠李戴的切点比不切更糟。
    """
    src = (index or {}).get("source") or {}
    recorded = str(src.get("path") or "")
    if not recorded or not source_path:
        return []
    try:
        same = Path(recorded).resolve() == Path(str(source_path)).resolve()
    except OSError:
        same = False
    if not same:
        return []
    out: list[float] = []
    for cut in (index or {}).get("preferred_cuts") or []:
        if not isinstance(cut, dict):
            continue
        value = _num(cut.get("at_seconds"), -1.0)
        if value > 0:
            out.append(round(value, 3))
    return sorted(set(out))


def scene_cuts_by_source(index: dict[str, Any] | None, paths: list[str | Path]) -> list[list[float]]:
    """逐源给出叙事切点（未匹配到的源给空表），与 ``paths`` 等长对齐。"""
    return [narrative_cuts_for_source(index, p) for p in paths]


def shot_hints(index: dict[str, Any] | None) -> dict[str, Any]:
    """通路 B 草稿对齐叙事的机器提示：每镜所属单元 + 必须保留的叙事切点。"""
    rows = (index or {}).get("shots") or []
    units = {str(u.get("unit_id") or ""): u for u in ((index or {}).get("units") or []) if isinstance(u, dict)}
    hints: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        sid = str(row.get("shot_id") or "")
        unit = units.get(str(row.get("unit_id") or "")) or {}
        if not sid:
            continue
        hints[sid] = {
            "unit_id": str(row.get("unit_id") or ""),
            "narrative_role": str(unit.get("narrative_role") or ""),
            "unit_start": bool(unit.get("shot_ids") and str(unit["shot_ids"][0]) == sid),
            "unit_index": int(_num(unit.get("index"))),
            "characters": list(row.get("characters") or []),
        }
    return {
        "unit_count": len(units),
        "shot_hints": hints,
        "preferred_cuts": list((index or {}).get("preferred_cuts") or []),
        "note": "草稿沿单元边界断镜；unit_start=true 的镜是该情节单元的开场锚，优先保留",
    }


# ---------------------------------------------------------------------------
# VLM 分组（窗口滑动；无密钥/失败 → 回落确定性）
# ---------------------------------------------------------------------------


def vlm_boundary_windows(
    shots: list[dict[str, Any]],
    *,
    media_path: str = "",
    window: int = VLM_WINDOW_SHOTS,
    sender: Callable[..., Any] | None = None,
    encode_fn: Callable[[str], str] | None = None,
    extract_fn: Callable[..., Any] | None = None,
    frame_dir: str = "",
) -> dict[str, Any]:
    """窗口化请求 VLM 只回单元边界索引，合并成全片 boundaries。

    每窗口只发代表帧 + 该窗口台词，要求回答窗口内 0-based 边界序号。越界/非 JSON
    的窗口降级为「该窗口无边界」（由确定性相似度兜底补上），不整体失败。
    """
    key = os.environ.get("DASHSCOPE_API_KEY")
    if not key or not media_path:
        return {"boundaries": [], "summaries": {}, "characters": {}, "skipped": True,
                "warnings": ["VLM 分组跳过（缺 DASHSCOPE_API_KEY 或 media_path）"]}
    from montage.providers.http import HttpError, post_json
    from montage.tools.vlm_reviewer import encode_image_data_url

    encode = encode_fn or encode_image_data_url
    out_root = Path(frame_dir) if frame_dir else Path(media_path).with_suffix("").parent / "_scene_frames"
    extract = extract_fn or (lambda src, at: extract_frame_at(src, at, out_root / f"f_{int(at * 1000):09d}.jpg"))
    send = sender or post_json
    base = str(os.environ.get("DASHSCOPE_API_BASE") or "https://dashscope.aliyuncs.com").rstrip("/")
    model = str(os.environ.get("QWEN_VL_MODEL") or "qwen-vl-plus").strip() or "qwen-vl-plus"

    boundaries: list[int] = []
    summaries: dict[int, str] = {}
    characters: dict[int, list[str]] = {}
    warnings: list[str] = []
    size = max(2, int(window))
    for offset in range(0, len(shots), size):
        chunk = shots[offset:offset + size]
        if len(chunk) < 2:
            continue
        content: list[dict[str, Any]] = []
        lines: list[str] = []
        for local, shot in enumerate(chunk):
            frame = extract(media_path, (_num(shot.get("start")) + _num(shot.get("end"))) / 2.0)
            if frame:
                try:
                    content.append({"type": "image_url", "image_url": {"url": encode(frame)}})
                except OSError:
                    pass
            lines.append(f"{local}. {str(shot.get('text') or '').strip() or '（无台词）'}")
        content.append({"type": "text", "text": _vlm_prompt(len(chunk), lines)})
        try:
            raw = send(f"{base}/compatible-mode/v1/chat/completions",
                       {"model": model, "messages": [{"role": "user", "content": content}]},
                       headers={"Authorization": f"Bearer {key}"}, timeout=180)
        except HttpError as exc:
            warnings.append(f"VLM 分组窗口 {offset} 失败: {exc}")
            continue
        parsed = parse_boundaries(raw, shot_count=len(chunk), offset=offset)
        boundaries.extend(parsed["boundaries"])
        summaries.update(parsed["summaries"])
        characters.update(parsed["characters"])
        warnings.extend(parsed["warnings"])
        if parsed.get("time_leaks"):
            warnings.append(f"VLM 越界上报时间码（已丢弃）: {parsed['time_leaks']}")
    return {
        "boundaries": sorted(set(boundaries)),
        "summaries": summaries,
        "characters": characters,
        "skipped": False,
        "warnings": warnings,
    }


def _vlm_prompt(count: int, lines: list[str]) -> str:
    return (
        f"你是粗剪助手。下面是连续 {count} 个镜头（图片顺序 = 镜头 0..{count - 1}，附各自台词）。"
        "判断哪些镜头是「情节单元」的开头（人物/地点/事件明显转换）。"
        "只输出 JSON，禁止输出任何时间码（秒/毫秒/时间戳一律不要）："
        '{"boundaries":[3,7],"summaries":{"0":"开场夜巷","3":"天台争执"},'
        '"characters":{"0":["c1"],"3":["c1","c2"]}} '
        "boundaries = 窗口内 0-based 镜头序号（不含 0）；summaries/characters 的键 = 单元起始序号。"
        "不要输出 start/end/seconds 字段。\n"
        + "\n".join(lines)
    )


def groups_from_boundaries(boundaries: list[int], shot_count: int) -> list[list[int]]:
    """边界索引 → 连续分组（边界本身即新单元首镜）。"""
    marks = sorted({int(b) for b in boundaries if 0 < int(b) < int(shot_count)})
    groups: list[list[int]] = []
    cursor = 0
    for mark in marks:
        groups.append(list(range(cursor, mark)))
        cursor = mark
    groups.append(list(range(cursor, int(shot_count))))
    return [g for g in groups if g]


def merge_boundaries(det: list[int], vlm: list[int], shot_count: int) -> list[int]:
    """VLM 边界与确定性边界取并集：宁可多切一段让导演合并，不可漏切叙事转折。"""
    return sorted({int(b) for b in [*det, *vlm] if 0 < int(b) < int(shot_count)})


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out


class ScenePipeline(BaseTool):
    """场景语义聚合：切点 → 镜 → 台词/指纹 → 情节单元 ``scene_index.json``。"""

    name = "scene_pipeline"
    version = "0.1.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    env_keys = ("DASHSCOPE_API_KEY",)
    input_schema = {
        "type": "object",
        "required": ["path"],
        "properties": {
            "path": {"type": "string", "description": "源视频（现有素材）"},
            "project_dir": {"type": "string", "description": "给了则写 artifacts/scene_index.json"},
            "cuts": {
                "type": "array",
                "items": {"type": "number"},
                "description": "预computed 切点秒数（复用 scene_detect 结果，免二次检测）",
            },
            "threshold": {"type": "number", "default": 0.3, "description": "scene 滤镜敏感度"},
            "min_hold": {"type": "number", "default": 1.0},
            "max_hold": {"type": "number", "default": 8.0},
            "visual_cut": {
                "type": "number", "default": DEFAULT_VISUAL_CUT,
                "description": "视觉指纹相似度低于此值判换场（RGB 均值差；同场不同机位≈0.7+）",
            },
            "text_hold": {
                "type": "number", "default": DEFAULT_TEXT_HOLD,
                "description": "台词 Jaccard 高于此值判连读，可压住轻微视觉变化",
            },
            "time_gap_seconds": {"type": "number", "default": DEFAULT_TIME_GAP_SECONDS},
            "transcript": {"description": "转写：{sentences:[...]} 或 sentences 列表"},
            "transcript_path": {"type": "string"},
            "asr_file_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选：调 dashscope_asr（需公网可访问音频 URL）",
            },
            "grouping": {"type": "string", "enum": ["deterministic", "vlm"], "default": "deterministic"},
            "fingerprints": {"type": "object", "description": "预computed {shot_id: hex}"},
            "skip_fingerprints": {"type": "boolean", "default": False},
            "max_fingerprint_shots": {"type": "integer", "default": DEFAULT_MAX_FINGERPRINT_SHOTS},
            "vlm_window": {"type": "integer", "default": VLM_WINDOW_SHOTS},
            "write": {"type": "boolean", "default": True},
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if check_ffmpeg() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # VLM 分组每窗口一次 qwen-vl；实际窗口数取决于镜数（执行后 settle 校正），
        # 这里给下限（对齐 dashscope_asr 的「按时长计费，此处给下限」惯例）。
        if str(inputs.get("grouping") or "deterministic") != "vlm":
            return 0.0
        return 0.01

    # -- 输入准备 ---------------------------------------------------------

    def _cuts(self, inputs: dict[str, Any], path: Path) -> tuple[list[float], list[str]]:
        warnings: list[str] = []
        cuts = inputs.get("cuts")
        if isinstance(cuts, list) and cuts:
            return [float(c) for c in cuts], warnings
        from montage.tools.video_probe import SceneDetect

        result = SceneDetect().execute({"path": str(path), "threshold": float(inputs.get("threshold", 0.3) or 0.3)})
        if not result.success:
            warnings.append(f"scene_detect 失败，按单镜处理: {result.error}")
            return [], warnings
        return [float(t) for t in (result.data or {}).get("scene_changes") or []], warnings

    def _transcript(self, inputs: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        warnings: list[str] = []
        raw = inputs.get("transcript")
        if raw is None and inputs.get("transcript_path"):
            path = Path(str(inputs["transcript_path"]))
            if path.is_file():
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    warnings.append(f"transcript_path 读取失败: {exc}")
            else:
                warnings.append(f"transcript_path 不存在: {path}")
        urls = inputs.get("asr_file_urls")
        if raw is None and isinstance(urls, list) and urls:
            from montage.providers.dashscope import DashscopeAsr

            result = DashscopeAsr().execute({"file_urls": urls})
            if result.success:
                raw = result.data
            else:
                warnings.append(f"ASR 失败（需公网可访问音频 URL）: {result.error}")
        return normalize_transcript(raw), warnings

    def _fingerprints(
        self,
        shots: list[dict[str, Any]],
        inputs: dict[str, Any],
        *,
        path: Path,
        warnings: list[str],
    ) -> None:
        provided = inputs.get("fingerprints")
        if isinstance(provided, dict):
            for shot in shots:
                shot["fingerprint"] = str(provided.get(str(shot.get("shot_id")) or "") or "")
            return
        if inputs.get("skip_fingerprints"):
            return
        cap = int(inputs.get("max_fingerprint_shots") or DEFAULT_MAX_FINGERPRINT_SHOTS)
        if len(shots) > cap:
            warnings.append(
                f"镜数 {len(shots)} 超过指纹上限 {cap}：仅前 {cap} 镜判视觉换场，"
                "其余只认硬间隔（无视觉证据不切；要更细请调 max_fingerprint_shots 或 grouping=vlm）"
            )
        sized = shots[:cap]
        missing = 0
        for shot in sized:
            mid = (_num(shot.get("start")) + _num(shot.get("end"))) / 2.0
            raw = fingerprint_bytes(path, at_seconds=mid)
            if raw is None:
                missing += 1
                continue
            shot["fingerprint"] = fingerprint_hex(raw)
        if missing and sized:
            warnings.append(f"{missing}/{len(sized)} 镜抽指纹失败（这些相邻镜无视觉证据，只认硬间隔）")

    # -- 执行 -------------------------------------------------------------

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        path = Path(str(inputs.get("path") or ""))
        if not path.is_file():
            return ToolResult(success=False, error=f"输入不存在: {path}")
        if check_ffmpeg() is None:
            return ToolResult(success=False, error="缺少 ffmpeg")

        info = {}
        try:
            info = probe(path)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"无法解析: {exc}")
        duration = _num((info.get("format") or {}).get("duration"))
        if duration <= 0:
            return ToolResult(success=False, error="无法取得时长（ffprobe）")

        warnings: list[str] = []
        cuts, cut_warnings = self._cuts(inputs, path)
        warnings.extend(cut_warnings)
        shots = shots_from_cuts(
            duration, cuts,
            min_hold=float(inputs.get("min_hold", 1.0) or 1.0),
            max_hold=float(inputs.get("max_hold", 8.0) or 8.0),
            segment_id="src",
        )
        sentences, asr_warnings = self._transcript(inputs)
        warnings.extend(asr_warnings)
        shots = attach_transcript(shots, sentences)
        self._fingerprints(shots, inputs, path=path, warnings=warnings)

        visual_cut = float(inputs.get("visual_cut", DEFAULT_VISUAL_CUT) or DEFAULT_VISUAL_CUT)
        text_hold = float(inputs.get("text_hold", DEFAULT_TEXT_HOLD) or DEFAULT_TEXT_HOLD)
        gap = float(inputs.get("time_gap_seconds", DEFAULT_TIME_GAP_SECONDS) or DEFAULT_TIME_GAP_SECONDS)
        det = deterministic_groups(shots, visual_cut=visual_cut, text_hold=text_hold,
                                   time_gap_seconds=gap)
        det_boundaries = [g[0] for g in det["groups"] if g and g[0] > 0]
        for row in det["similarities"]:
            warnings.extend(row.get("warnings") or [])

        grouping = str(inputs.get("grouping") or "deterministic")
        summaries: dict[int, str] = {}
        characters: dict[int, list[str]] = {}
        boundaries = det_boundaries
        if grouping == "vlm":
            vlm = vlm_boundary_windows(shots, media_path=str(path), window=int(inputs.get("vlm_window") or VLM_WINDOW_SHOTS))
            warnings.extend(vlm.get("warnings") or [])
            if vlm.get("skipped"):
                grouping = "deterministic"
            else:
                summaries = dict(vlm.get("summaries") or {})
                characters = dict(vlm.get("characters") or {})
                boundaries = merge_boundaries(det_boundaries, vlm.get("boundaries") or [], len(shots))
        groups = groups_from_boundaries(boundaries, len(shots))

        index = build_scene_index(
            shots=shots,
            groups=groups,
            grouping=grouping,
            source={
                "kind": "cuts",
                "path": str(path),
                "duration_seconds": round(duration, 3),
                "cut_count": len(cuts),
                "transcript_sentences": len(sentences),
            },
            params={
                "visual_cut": visual_cut,
                "text_hold": text_hold,
                "time_gap_seconds": gap,
                "min_hold": float(inputs.get("min_hold", 1.0) or 1.0),
                "max_hold": float(inputs.get("max_hold", 8.0) or 8.0),
            },
            summaries=summaries,
            characters=characters,
            similarities=det["similarities"],
            warnings=warnings,
        )

        project_dir = str(inputs.get("project_dir") or "")
        written = ""
        if project_dir and inputs.get("write", True):
            from montage.engine.artifacts import ArtifactStore
            from montage.schemas import get_schema

            written = str(ArtifactStore(project_dir).write(
                SCENE_INDEX_ARTIFACT, index, schema=get_schema(SCENE_INDEX_ARTIFACT),
            ))
        return ToolResult(
            success=True,
            data={
                "scene_index": index,
                "hints": shot_hints(index),
                "unit_count": len(index["units"]),
                "shot_count": len(index["shots"]),
                "path": written,
                "warnings": index["warnings"] or None,
                "usage": "草稿按 preferred_cuts 断镜；unit_start 的镜优先保留（通路 B 对齐叙事）",
            },
            meta={"grouping": grouping},
        )
