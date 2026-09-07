"""可灵定妆拼板：比例裁切 + 每格 QC（第 2 波原语，不接 shot_runner）。

16:9 白底格线（禁止 VLM 找人）：
- 左半：正面全身
- 右 2×2：小正脸（工牌不用）、侧、背、3/4
失败策略见 look_sheet_next_action：先重打一张拼板，第二次才 series。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

# x, y, w, h 均为相对整图的比例（左上原点）。
LOOK_SHEET_CELLS: dict[str, dict[str, float]] = {
    "portrait": {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0},
    "face": {"x": 0.5, "y": 0.0, "w": 0.25, "h": 0.5},
    "side": {"x": 0.75, "y": 0.0, "w": 0.25, "h": 0.5},
    "back": {"x": 0.5, "y": 0.5, "w": 0.25, "h": 0.5},
    "tq": {"x": 0.75, "y": 0.5, "w": 0.25, "h": 0.5},
}
LOOK_SHEET_ELEMENT_KEYS = ("portrait", "side", "back", "tq")
LOOK_SHEET_FILENAMES = {
    "portrait": "portrait_{cid}.png",
    "face": "look_face_{cid}.png",
    "side": "turnaround_{cid}_side.png",
    "back": "turnaround_{cid}_back.png",
    "tq": "turnaround_{cid}_tq.png",
}

_MIN_SIDE = 300
_MIN_BYTES = 2000
_MIN_NONWHITE = 0.04
_MAX_NONWHITE = 0.96
_WHITE = 240
_EDGE_FRAC = 0.05


def look_sheet_next_action(*, qc_ok: bool, attempt: int) -> str:
    """拼板质检下一步。attempt 从 1 起：第一次失败重打，第二次 series。"""
    if qc_ok:
        return "ok"
    if int(attempt) < 2:
        return "retry_sheet"
    return "series"


def element_slots_from_crops(crops: dict[str, Any]) -> dict[str, Any]:
    """工牌只用左大格 + 侧/背/3/4；丢弃小正脸。"""
    frontal = str(crops.get("portrait") or "").strip()
    refer = [str(crops[k]) for k in ("side", "back", "tq") if str(crops.get(k) or "").strip()]
    return {
        "frontal": frontal,
        "refer": refer[:3],
        "discarded": ["face"],
    }


def _crop_vf(cell: dict[str, float]) -> str:
    return (
        f"crop=iw*{cell['w']}:ih*{cell['h']}:iw*{cell['x']}:ih*{cell['y']}"
    )


def _ffmpeg_crop_cell(src: Path, dest: Path, cell: dict[str, float]) -> bool:
    from montage.compose.ffmpeg_engine import check_ffmpeg

    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(src), "-frames:v", "1", "-vf", _crop_vf(cell), str(dest),
        ],
        capture_output=True, timeout=60, check=False,
    )
    return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0


def _stream_size(info: dict[str, Any]) -> tuple[int, int]:
    for stream in info.get("streams") or []:
        if not isinstance(stream, dict):
            continue
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        if width > 0 and height > 0:
            return width, height
    return 0, 0


def _analyze_cell_pixels(path: Path) -> dict[str, Any]:
    """非白占比 + 边带是否有主体（连通域贴边的近似）。单测可 monkeypatch。"""
    from montage.compose.ffmpeg_engine import ComposError, check_ffmpeg, probe

    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        return {"ok": False, "error": "缺少 ffmpeg"}
    try:
        info = probe(path)
    except ComposError as exc:
        return {"ok": False, "error": str(exc)}
    width, height = _stream_size(info)
    if width <= 0 or height <= 0:
        return {"ok": False, "error": "无法读宽高"}
    proc = subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ],
        capture_output=True, timeout=60, check=False,
    )
    raw = proc.stdout or b""
    need = width * height * 3
    if proc.returncode != 0 or len(raw) < need:
        return {"ok": False, "error": "无法读像素", "width": width, "height": height}
    total = width * height
    nonwhite = 0
    band = max(2, int(min(width, height) * _EDGE_FRAC))
    edge_hits = {"top": 0, "bottom": 0, "left": 0, "right": 0}
    for y in range(height):
        row = y * width * 3
        for x in range(width):
            i = row + x * 3
            r, g, b = raw[i], raw[i + 1], raw[i + 2]
            if r >= _WHITE and g >= _WHITE and b >= _WHITE:
                continue
            nonwhite += 1
            if y < band:
                edge_hits["top"] += 1
            if y >= height - band:
                edge_hits["bottom"] += 1
            if x < band:
                edge_hits["left"] += 1
            if x >= width - band:
                edge_hits["right"] += 1
    return {
        "ok": True,
        "width": width,
        "height": height,
        "nonwhite_ratio": nonwhite / total if total else 0.0,
        "touches_edge": {k: v > 0 for k, v in edge_hits.items()},
    }


def qc_look_sheet_cell(path: str | Path) -> dict[str, Any]:
    dest = Path(path)
    notes: list[str] = []
    size = dest.stat().st_size if dest.is_file() else 0
    if not dest.is_file():
        return {"ok": False, "notes": ["裁切文件不存在"], "bytes": 0}
    if size < _MIN_BYTES:
        notes.append("文件过小")
    pixels = _analyze_cell_pixels(dest)
    width = int(pixels.get("width") or 0)
    height = int(pixels.get("height") or 0)
    if not pixels.get("ok"):
        notes.append(str(pixels.get("error") or "像素分析失败"))
        return {
            "ok": False,
            "notes": notes,
            "bytes": size,
            "width": width,
            "height": height,
        }
    if min(width, height) < _MIN_SIDE:
        notes.append(f"边长不足 {_MIN_SIDE}px")
    ratio = float(pixels.get("nonwhite_ratio") or 0.0)
    if ratio < _MIN_NONWHITE:
        notes.append("过空/过白")
    if ratio > _MAX_NONWHITE:
        notes.append("非白占比过高（不像白底格）")
    touches = pixels.get("touches_edge") or {}
    if ratio < 0.20 and not any(touches.values()):
        notes.append("主体未贴边（可能裁空）")
    return {
        "ok": not notes,
        "notes": notes,
        "bytes": size,
        "width": width,
        "height": height,
        "nonwhite_ratio": ratio,
        "touches_edge": touches,
    }


def crop_look_sheet(src: str | Path, dest_dir: str | Path, cid: str) -> dict[str, Any]:
    """按固定比例裁五格。工牌 QC 只看 portrait/side/back/tq。"""
    source = Path(src)
    out_dir = Path(dest_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cells: dict[str, str] = {}
    qc: dict[str, dict[str, Any]] = {}
    notes: list[str] = []
    if not source.is_file():
        return {
            "ok": False,
            "cells": cells,
            "qc": qc,
            "notes": [f"拼板不存在: {source}"],
            "discarded_for_element": ["face"],
        }
    for key, box in LOOK_SHEET_CELLS.items():
        dest = out_dir / LOOK_SHEET_FILENAMES[key].format(cid=cid)
        if not _ffmpeg_crop_cell(source, dest, box):
            notes.append(f"{key} 裁切失败")
            qc[key] = {"ok": False, "notes": ["裁切失败"]}
            continue
        cells[key] = str(dest)
        qc[key] = qc_look_sheet_cell(dest)
        notes.extend(f"{key}: {n}" for n in (qc[key].get("notes") or []))
    qc_ok = all((qc.get(k) or {}).get("ok") for k in LOOK_SHEET_ELEMENT_KEYS)
    return {
        "ok": qc_ok,
        "cells": cells,
        "qc": qc,
        "notes": notes,
        "discarded_for_element": ["face"],
    }
