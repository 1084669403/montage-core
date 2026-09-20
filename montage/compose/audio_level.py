"""audio_level — 逐镜音轨配平（A6）。

问题：44 镜原生音轨 mean_volume 散布 42dB（-12.6 … -55.2dB），其中数镜等同
无声；播放时表现为"声音忽大忽小 / 某几镜没声"。本片走 Agnes 原生音轨路径，
``policy.keep_embedded_audio`` 返回 True → ``place_audio`` 整体早退，配平写在
它之前或之内都不生效，所以落点是 **assemble 之前的独立步骤**。

做法：

1. 逐镜 ``volumedetect`` 测 mean_volume；
2. 取**中位数**为目标响度（限制在 ``[TARGET_DB_MIN, TARGET_DB_MAX]``）；
3. 逐镜增益 = 目标 − 实测，钳制在 ``[max_cut, max_boost]``；
4. 只重编码音轨（``-c:v copy``），产物写独立目录 ``assets/leveled/``，
   **不原地覆盖** ``assets/videos/``（避免污染源素材与合成就绪判定）。

超过钳制上限仍达不到目标的镜（如 -55dB 的准静音镜）会在报告里标
``residual_db``：那是模型侧没生成有效环境声，不是配平算法能补的。
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_engine import (
    ComposError,
    check_ffmpeg,
    probe,
    run_ffmpeg,
)

#: 目标响度钳制区间（mean_volume dB）。
TARGET_DB_MIN = -34.0
TARGET_DB_MAX = -14.0
#: 单镜最大提升 / 衰减。
MAX_BOOST_DB = 30.0
MAX_CUT_DB = -12.0
#: 小于此绝对增益就不做处理，直接复用源文件。
MIN_GAIN_DB = 0.05
#: 实测 mean_volume 低于此值视为"模型没给有效环境声"，报告里标注。
NEAR_SILENCE_DB = -45.0

_MEAN_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def _has_audio(path: Path) -> bool:
    try:
        info = probe(path)
    except ComposError:
        return False
    return any(
        isinstance(s, dict) and s.get("codec_type") == "audio"
        for s in (info.get("streams") or [])
    )


def measure_mean_volume(path: str | Path, *, timeout: int = 600) -> float | None:
    """``volumedetect`` 测片段平均响度（dB）；无音轨/探测失败返回 None。"""
    target = Path(path)
    if not target.is_file():
        return None
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    proc = run_ffmpeg(
        [
            ffmpeg, "-hide_banner", "-nostdin", "-i", str(target),
            "-map", "0:a:0?", "-af", "volumedetect", "-f", "null", "-",
        ],
        timeout=timeout, check=False,
    )
    hit = _MEAN_RE.search(proc.stderr or "")
    if not hit:
        return None
    try:
        return float(hit.group(1))
    except ValueError:
        return None


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def plan_gains(
    means: dict[str, float | None],
    *,
    target_db: float | None = None,
    max_boost: float = MAX_BOOST_DB,
    max_cut: float = MAX_CUT_DB,
) -> dict[str, Any]:
    """纯函数：中位数目标 + 逐镜增益钳制；不碰 ffmpeg，便于单测。"""
    usable = [v for v in means.values() if v is not None]
    if target_db is None:
        if usable:
            target_db = max(min(_median(usable), TARGET_DB_MAX), TARGET_DB_MIN)
        else:
            target_db = -20.0
    gains: dict[str, dict[str, Any]] = {}
    for key, mean in means.items():
        if mean is None:
            gains[key] = {
                "mean_db": None, "gain_db": 0.0,
                "reason": "无音轨或无法测量（保持原样）",
            }
            continue
        raw = float(target_db) - float(mean)
        gain = max(min(raw, float(max_boost)), float(max_cut))
        entry: dict[str, Any] = {
            "mean_db": round(float(mean), 2),
            "gain_db": round(gain, 2),
            "projected_db": round(float(mean) + gain, 2),
        }
        if abs(gain - raw) > 0.01:
            entry["clamped"] = round(raw, 2)
        if float(mean) <= NEAR_SILENCE_DB:
            entry["note"] = "实测接近静音：模型未产出有效环境声"
        residual = round(float(target_db) - (float(mean) + gain), 2)
        if abs(residual) > 0.5:
            entry["residual_db"] = residual
        gains[key] = entry
    return {"target_db": round(float(target_db), 2), "gains": gains}


def apply_gain(
    input_path: Path,
    output: Path,
    gain_db: float,
    *,
    timeout: int = 900,
) -> Path:
    """只重编码音轨（视频流拷贝），输出 AAC 48kHz 立体声。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    if not _has_audio(input_path):
        shutil.copy2(input_path, output)
        return output
    ffmpeg = check_ffmpeg()
    if not ffmpeg:
        raise ComposError("缺少 ffmpeg")
    run_ffmpeg(
        [
            ffmpeg, "-y", "-nostdin", "-i", str(input_path),
            "-c:v", "copy",
            "-af", f"volume={float(gain_db):.2f}dB",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(output),
        ],
        timeout=timeout,
        error_prefix="逐镜配平失败",
    )
    return output


def level_clips(
    clips: list[Path],
    out_dir: Path,
    *,
    force: bool = False,
    target_db: float | None = None,
    measure_after: bool = True,
    timeout: int = 900,
) -> dict[str, Any]:
    """逐镜配平到同一目标响度；产物按源文件 stem 命名落 ``out_dir``。

    已存在且不旧于源文件的产物直接复用（幂等，避免每次 produce 重跑 44 次
    编码）。返回报告供 render_report 与验收脚本使用。
    """
    if os.environ.get("MONTAGE_NO_AUDIO_LEVEL"):
        return {
            "disabled": True, "reason": "MONTAGE_NO_AUDIO_LEVEL",
            "target_db": None, "clips": [], "dir": str(out_dir),
            "spread_before": None, "spread_after": None,
        }
    out_dir.mkdir(parents=True, exist_ok=True)
    before: dict[str, float | None] = {}
    for clip in clips:
        before[clip.stem] = measure_mean_volume(clip, timeout=timeout)
    plan = plan_gains(before, target_db=target_db)

    rows: list[dict[str, Any]] = []
    leveled_paths: list[Path] = []
    for clip in clips:
        key = clip.stem
        entry = plan["gains"].get(key, {})
        gain = float(entry.get("gain_db") or 0.0)
        dest = out_dir / f"{key}.mp4"
        cached = (
            not force
            and dest.is_file()
            and dest.stat().st_mtime >= clip.stat().st_mtime
            and dest.stat().st_size > 0
        )
        if cached:
            pass
        elif abs(gain) < MIN_GAIN_DB and _has_audio(clip):
            # 已在目标电平：不重编码，直接用源文件（避免无谓降质）。
            dest = clip
        else:
            apply_gain(clip, dest, gain, timeout=timeout)
        leveled_paths.append(dest)
        row: dict[str, Any] = {
            "name": key,
            "source": str(clip),
            "leveled": str(dest),
            "mean_before_db": entry.get("mean_db"),
            "gain_db": gain,
            "cached": bool(cached),
        }
        if entry.get("note"):
            row["note"] = entry["note"]
        if entry.get("residual_db") is not None:
            row["residual_db"] = entry["residual_db"]
        if measure_after and dest != clip:
            after = measure_mean_volume(dest, timeout=timeout)
            if after is not None:
                row["mean_after_db"] = round(after, 2)
        elif measure_after:
            row["mean_after_db"] = entry.get("mean_db")
        rows.append(row)

    def _spread(key: str) -> float | None:
        values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return round(max(values) - min(values), 2) if len(values) >= 2 else None

    report: dict[str, Any] = {
        "disabled": False,
        "dir": str(out_dir),
        "target_db": plan["target_db"],
        "clips": rows,
        "spread_before": _spread("mean_before_db"),
        "spread_after": _spread("mean_after_db"),
        "paths": [str(p) for p in leveled_paths],
    }
    report["outliers"] = [
        r["name"] for r in rows
        if isinstance(r.get("residual_db"), (int, float)) and abs(r["residual_db"]) > 6.0
    ]
    return report
