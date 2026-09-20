"""energy_wave — 能量波 / 拍网格 / Bar-DP 切点（P0-5）。

纯确定性计算（无 LLM、无密钥）。三件事：

1. **能量包络**：ffmpeg ``ebur128`` 的瞬时响度 M（EBU R128，K 加权 + 门限，比裸 RMS
   更贴人耳），每 100ms 一个采样；ebur128 元数据不可用时自动退回「解码 PCM 算每窗
   RMS」（两条路都只依赖 ffmpeg，`source` 字段回报实际用了哪条）。
2. **拍网格**：曲库/显式 bpm 优先，缺失时对能量包络做自相关估拍（``source="estimated"``），
   再把拍位锚到帧（``frames_per_beat``）。
3. **Bar-DP**：在拍网格上按 bar 选切点，密度跟随能量——安静段少切、高潮段密集；
   风格包现有 ``min_hold`` / ``max_hold`` 直接当硬约束，**不新增旋钮**。

与 P0-4 的契约（``montage/engine/edit_metrics.py`` 的 ``beat_grid`` 形状）：
本模块产出的网格保持
``{bpm, fps, offset_seconds, start_seconds, frames_per_beat, source}``，
只**多**加 ``beats_per_bar`` / ``bars`` 两个键；m5 的指标定义不动。
"""

from __future__ import annotations

import array
import math
import re
import subprocess
from pathlib import Path
from typing import Any

BEAT_MAP_VERSION = "1.0"

#: ebur128 瞬时响度 M 的更新步长（EBU R128：400ms 窗、100ms 步进）。
ENERGY_WINDOW_SECONDS = 0.1
DEFAULT_BEATS_PER_BAR = 4
DEFAULT_FPS = 30.0
MIN_BPM = 60.0
MAX_BPM = 180.0

#: 每 bar 的目标切点数下限/上限（能量最低/最高时）。实际会被 min_hold/max_hold 夹紧。
MIN_DENSITY = 0.3
MAX_DENSITY = 2.0

#: 低于此响度（LUFS）视作静音，归一到 0。
SILENCE_FLOOR_LUFS = -60.0

_RX_M = re.compile(r"lavfi\.r128\.M=(-?[\d.]+|-?inf|nan)")
_RX_PTS = re.compile(r"pts_time:([\d.]+)")
_RX_LOG_PREFIX = re.compile(r"^\[[^\]]*\]\s*")


def _db_to_linear(db: float) -> float:
    return 10.0 ** (db / 20.0)


# ---------------------------------------------------------------------------
# 1. 能量包络
# ---------------------------------------------------------------------------


def _measure_ebur128(path: Path) -> list[tuple[float, float]] | None:
    """ffmpeg ebur128 → [(t, M_dB)]。元数据行走 ffmpeg 日志（stderr）。

    注意：`ametadata` 的 `file=` 在 Windows 上会被驱动器的 `:` 截断，所以不给
    `file`，让它按默认走日志，再按 `lavfi.r128.M=` 键抓取——这个键不会误伤别的日志行。
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
        "-af", "ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M",
        "-f", "null", "NUL" if _is_windows() else "-",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None

    rows: list[tuple[float, float]] = []
    pending_t: float | None = None
    for raw in proc.stderr.splitlines():
        line = _RX_LOG_PREFIX.sub("", raw)
        pts = _RX_PTS.search(line)
        if pts:
            pending_t = float(pts.group(1))
            continue
        hit = _RX_M.search(line)
        if not hit:
            continue
        token = hit.group(1)
        value = float("-inf") if token.lstrip("-") == "inf" else float(token)
        if math.isnan(value):
            value = float("-inf")
        t = pending_t if pending_t is not None else len(rows) * ENERGY_WINDOW_SECONDS
        rows.append((float(t), value))
        pending_t = None
    return rows or None


def _is_windows() -> bool:
    import os

    return os.name == "nt"


def _measure_pcm_rms(path: Path, *, window_seconds: float, sample_rate: int = 8000) -> list[tuple[float, float]] | None:
    """兜底：ffmpeg 解码成单声道 s16 PCM（stdout 裸字节）→ 每窗 RMS。

    比 ebur128 糙（无 K 加权/门限），但零解析风险，且不吃内存（流式读 stdout）。
    """
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
        "-f", "s16le", "-ac", "1", "-ar", str(sample_rate), "-",
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None

    per_window = max(1, round(sample_rate * window_seconds))
    want = per_window * 2          # s16 = 2 字节/样点
    buf = bytearray()
    rows: list[tuple[float, float]] = []
    index = 0
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(want * 64)
        if not chunk:
            break
        buf.extend(chunk)
        while len(buf) >= want:
            block = bytes(buf[:want])
            del buf[:want]
            samples = array.array("h")
            samples.frombytes(block)
            acc = 0.0
            for x in samples:
                acc += float(x) * float(x)
            rms = math.sqrt(acc / len(samples)) if samples else 0.0
            rows.append((index * window_seconds, rms))
            index += 1
    proc.stdout.close()
    proc.wait(timeout=30)
    if not rows:
        return None
    peak = max(r for _t, r in rows) or 1.0
    # 归一到 dB 尺度，与 ebur128 分支同域（后续统一做相对归一）
    return [(t, 20.0 * math.log10(max(r / peak, 1e-6))) for t, r in rows]


def measure_energy_envelope(path: str | Path, *, window_seconds: float = ENERGY_WINDOW_SECONDS) -> dict[str, Any]:
    """测能量包络。返回 ``{source, window_seconds, duration, windows:[{t, db}]}``。

    两条路都失败 → ``source=""``、``windows=[]``（调用方据此走降级阶梯，不抛错）。
    """
    src = Path(path)
    if not src.is_file():
        return {"source": "", "window_seconds": float(window_seconds), "duration": 0.0,
                "windows": [], "reason": "音频文件不存在"}
    if not _has_ffmpeg():
        return {"source": "", "window_seconds": float(window_seconds), "duration": 0.0,
                "windows": [], "reason": "缺少 ffmpeg"}

    rows = _measure_ebur128(src)
    source = "ebur128"
    if not rows:
        rows = _measure_pcm_rms(src, window_seconds=window_seconds)
        source = "pcm_rms"
    if not rows:
        return {"source": "", "window_seconds": float(window_seconds), "duration": 0.0,
                "windows": [], "reason": "ebur128 与 PCM 兜底都没测出数据"}
    duration = float(rows[-1][0]) + float(window_seconds)
    return {
        "source": source,
        "window_seconds": float(window_seconds),
        "duration": round(duration, 3),
        "windows": [{"t": round(t, 3), "db": (None if db == float("-inf") else round(db, 2))}
                    for t, db in rows],
    }


def _has_ffmpeg() -> bool:
    from montage.compose.ffmpeg_engine import check_ffmpeg

    return bool(check_ffmpeg())


# ---------------------------------------------------------------------------
# 2. 拍网格 + tempo 兜底
# ---------------------------------------------------------------------------


def estimate_tempo(envelope: dict[str, Any], *, min_bpm: float = MIN_BPM, max_bpm: float = MAX_BPM) -> dict[str, Any]:
    """对能量包络做自相关估 bpm。返回 ``{bpm, confidence, source}``（失败 ``bpm=0``）。

    **这是兜底，不是曲库数据**：调用方必须把 ``source="estimated"`` 透出并带 warning。
    """
    windows = envelope.get("windows") or []
    step = float(envelope.get("window_seconds") or ENERGY_WINDOW_SECONDS) or ENERGY_WINDOW_SECONDS
    if len(windows) < 8:
        return {"bpm": 0.0, "confidence": 0.0, "source": "", "reason": "能量包络太短"}
    series = [
        (float(w["db"]) if w.get("db") is not None else SILENCE_FLOOR_LUFS)
        for w in windows
    ]
    mean = sum(series) / len(series)
    centered = [x - mean for x in series]
    energy = sum(x * x for x in centered)
    if energy <= 1e-9:
        return {"bpm": 0.0, "confidence": 0.0, "source": "", "reason": "能量无起伏（静音或恒定）"}

    lag_min = max(1, round(60.0 / max_bpm / step))
    lag_max = min(len(centered) // 2, round(60.0 / min_bpm / step))
    if lag_max <= lag_min:
        return {"bpm": 0.0, "confidence": 0.0, "source": "", "reason": "窗口太短，估不出拍"}

    best_lag, best_score = 0, 0.0
    for lag in range(lag_min, lag_max + 1):
        acc = 0.0
        for i in range(len(centered) - lag):
            acc += centered[i] * centered[i + lag]
        score = acc / energy
        if score > best_score:
            best_lag, best_score = lag, score
    if best_lag <= 0 or best_score <= 0.05:
        return {"bpm": 0.0, "confidence": round(max(best_score, 0.0), 3), "source": "",
                "reason": "自相关峰不显著"}
    bpm = 60.0 / (best_lag * step)
    # 把倍频折回可读区间（60–180）
    while bpm < min_bpm:
        bpm *= 2.0
    while bpm > max_bpm:
        bpm /= 2.0
    return {"bpm": round(bpm, 2), "confidence": round(best_score, 3), "source": "estimated"}


def beat_grid(
    *,
    bpm: float,
    fps: float = DEFAULT_FPS,
    offset_seconds: float = 0.0,
    beats_per_bar: int = DEFAULT_BEATS_PER_BAR,
    start_seconds: float = 0.0,
    source: str = "energy_wave",
) -> dict[str, Any] | None:
    """拍网格（P0-4 ``beat_grid`` 形状 + ``beats_per_bar``）。bpm 非法 → None。"""
    if not bpm or bpm <= 0 or fps <= 0:
        return None
    beat_seconds = 60.0 / float(bpm)
    return {
        "bpm": float(bpm),
        "fps": float(fps),
        "offset_seconds": float(offset_seconds),
        "start_seconds": float(start_seconds),
        "frames_per_beat": float(fps) * beat_seconds,
        "beats_per_bar": int(beats_per_bar),
        "bar_seconds": round(beat_seconds * int(beats_per_bar), 4),
        "source": source,
    }


def bar_energies(envelope: dict[str, Any], grid: dict[str, Any]) -> list[dict[str, Any]]:
    """把能量包络按 bar 聚合成能量波，并归一到 ``energy ∈ [0,1]``。

    归一用**极差分位**（去掉 5% 尾噪声）而非纯 min-max，避免单个尖峰把整片压平。
    """
    bar_seconds = float(grid.get("bar_seconds") or 0.0)
    start = float(grid.get("start_seconds") or 0.0)
    if bar_seconds <= 0:
        return []
    windows = envelope.get("windows") or []
    duration = float(envelope.get("duration") or 0.0)
    if not windows or duration <= 0:
        return []

    n_bars = max(1, math.ceil((duration - start) / bar_seconds))
    buckets: list[list[float]] = [[] for _ in range(n_bars)]
    for w in windows:
        db = w.get("db")
        value = SILENCE_FLOOR_LUFS if db is None else float(db)
        value = max(value, SILENCE_FLOOR_LUFS)
        idx = int((float(w["t"]) - start) // bar_seconds)
        if 0 <= idx < n_bars:
            buckets[idx].append(value)

    means = [sum(b) / len(b) if b else SILENCE_FLOOR_LUFS for b in buckets]
    ordered = sorted(means)
    lo = ordered[int(len(ordered) * 0.05)]
    hi = ordered[max(0, int(len(ordered) * 0.95) - 1)]
    span = hi - lo
    rows: list[dict[str, Any]] = []
    for i, mean in enumerate(means):
        norm = 0.0 if span <= 1e-6 else (mean - lo) / span
        rows.append({
            "bar": i,
            "start_seconds": round(start + i * bar_seconds, 3),
            "end_seconds": round(start + (i + 1) * bar_seconds, 3),
            "mean_db": round(mean, 2),
            "energy": round(min(max(norm, 0.0), 1.0), 4),
        })
    return rows


# ---------------------------------------------------------------------------
# 3. Bar-DP：按能量决定切点密度
# ---------------------------------------------------------------------------


def _bar_cut_options(
    beats_per_bar: int,
    *,
    since: int,
    min_gap: int,
    max_gap: int,
    is_last: bool,
) -> list[tuple[int, tuple[int, ...]]]:
    """本 bar 可行的落刀组合：``(新 since, 组合内切点的 beat 偏移)``。

    偏移 ``o ∈ 1..beats_per_bar``，第 ``o`` 个 beat 在 ``bar_start + (o-1)*beat``。

    拍数记账（``since`` = 上一个切点距本 bar 起点几个 beat）：
    - 本 bar 首个切点 o 与上一个切点相距 ``since + (o-1)`` 拍；
    - 同 bar 内相邻切点相距 ``o2 - o1`` 拍；
    - 落刀后 ``new_since = beats_per_bar - o + 1``（末刀到下一个 bar 起点的拍数）。

    双重过滤：间隔必须 ∈ ``[min_gap, max_gap]``；且除末 bar 外 ``new_since`` 不得
    超过 ``max_gap``（否则下一 bar 无论怎么切都来不及，是个死状态）。
    """
    offsets = list(range(1, beats_per_bar + 1))
    found: list[tuple[int, tuple[int, ...]]] = []
    for mask in range(1 << len(offsets)):
        picked = tuple(offsets[i] for i in range(len(offsets)) if mask >> i & 1)
        prev_offset: int | None = None
        ok = True
        for o in picked:
            gap = (since + (o - 1)) if prev_offset is None else (o - prev_offset)
            if gap < min_gap or gap > max_gap:
                ok = False
                break
            prev_offset = o
        if not ok:
            continue
        if not picked:
            if since + beats_per_bar > max_gap:   # 不落刀就超 max_hold → 必须落刀
                continue
            found.append((since + beats_per_bar, ()))
            continue
        new_since = beats_per_bar - picked[-1] + 1
        if not is_last and new_since > max_gap:   # 死状态：下一 bar 来不及
            continue
        found.append((new_since, picked))
    return found


def plan_beat_cuts(
    bars: list[dict[str, Any]],
    grid: dict[str, Any],
    *,
    min_hold: float,
    max_hold: float,
    duration: float = 0.0,
    min_density: float = MIN_DENSITY,
    max_density: float = MAX_DENSITY,
) -> dict[str, Any]:
    """Bar-DP：在拍网格上选切点，使每 bar 的切点数贴近能量波给出的目标密度。

    代价 = ``Σ|本 bar 实际刀数 − 目标刀数|``，硬约束是 ``min_hold``/``max_hold``
    （风格包现值，不新增旋钮）。切点只会落在拍位上，所以 m5 的「落在拍上」天然成立
    ——**这也是它在该路径上不能当质量证据的原因**（见 ``circular`` 标注）。
    """
    bpm = float(grid.get("bpm") or 0.0)
    beats_per_bar = int(grid.get("beats_per_bar") or DEFAULT_BEATS_PER_BAR)
    if bpm <= 0 or not bars or beats_per_bar <= 0:
        return {"cuts": [], "bars": bars, "feasible": False, "reason": "无有效拍网格/能量"}

    beat_seconds = 60.0 / bpm
    bar_seconds = beat_seconds * beats_per_bar
    start = float(grid.get("start_seconds") or 0.0)
    min_gap = max(1, math.ceil(min_hold / beat_seconds - 1e-9))
    raw_max = math.floor(max_hold / beat_seconds + 1e-9) if max_hold and max_hold > 0 else 0
    max_gap = max(min_gap, raw_max) if raw_max > 0 else max(min_gap, beats_per_bar * 8)

    targets: list[int] = []
    clamped = False
    for row in bars:
        want = min_density + (max_density - min_density) * float(row.get("energy") or 0.0)
        want = min(max(want, 0.0), float(beats_per_bar))
        # 风格包 hold 约束下的可达上限：每 bar 至多 beats_per_bar//min_gap 刀
        reachable = max(1.0, math.floor(beats_per_bar / max(min_gap, 1)))
        if want > reachable:
            want = reachable
            clamped = True
        targets.append(round(want))

    # DP：state(since) → (累计代价, 回指针)
    states: dict[int, tuple[float, int, tuple[int, ...], int]] = {}
    # t=0 视作一个真实切点（since=0）→ 首镜长度也受 min_gap 约束，不会一上来就切碎
    states[0] = (0.0, -1, (), 0)

    layers: list[dict[int, tuple[float, int, tuple[int, ...], int]]] = []
    last_bar = len(bars) - 1
    for bar_idx, _row in enumerate(bars):
        layer: dict[int, tuple[float, int, tuple[int, ...], int]] = {}
        for since, (cost, _pbar, _picked, _psince) in states.items():
            for new_since, picked in _bar_cut_options(
                beats_per_bar, since=since, min_gap=min_gap, max_gap=max_gap,
                is_last=(bar_idx == last_bar),
            ):
                new_cost = cost + abs(len(picked) - targets[bar_idx])
                cur = layer.get(new_since)
                if cur is None or new_cost < cur[0]:
                    layer[new_since] = (new_cost, bar_idx, picked, since)
        if not layer:
            return {"cuts": [], "bars": bars, "feasible": False,
                    "reason": (
                        f"第 {bar_idx} 个 bar 无可行落刀组合：min_hold={min_hold}s → "
                        f"至少 {min_gap} 拍一刀，max_hold={max_hold}s → 至多 {max_gap} 拍一刀，"
                        f"而每 bar 只有 {beats_per_bar} 拍（{bar_seconds:.2f}s）"
                    )}
        layers.append(layer)
        states = layer

    best_since = min(states, key=lambda s: (states[s][0], s))
    total_cost = states[best_since][0]
    cuts: list[float] = []
    for bar_idx in range(len(bars) - 1, -1, -1):
        _cost, _pbar, picked, prev_since = layers[bar_idx][best_since]
        for o in picked:
            cuts.append(round(start + bar_idx * bar_seconds + (o - 1) * beat_seconds, 3))
        best_since = prev_since

    cuts.sort()
    # 去掉 t<=0 的伪切点（片头不是切点）
    cuts = [c for c in cuts if c > 1e-6]
    # 尾巴收敛：DP 不知道片尾落在拍格哪里，末镜可能过短或过长
    tail_notes: list[str] = []
    if duration and duration > 0 and beat_seconds > 0:
        before = len(cuts)
        guard = 0
        while cuts and duration - cuts[-1] < min_hold - 1e-6 and guard < 1000:
            cuts.pop()
            guard += 1
        if len(cuts) != before:
            tail_notes.append("末镜过短，已吃掉最后一刀")
        anchor = cuts[-1] if cuts else 0.0
        guard = 0
        while duration - anchor > max_hold + 1e-6 and guard < 1000:
            nxt = round(anchor + max_gap * beat_seconds, 3)
            if duration - nxt < min_hold - 1e-6:
                tail_notes.append(
                    f"末镜 {duration - anchor:.2f}s 略超 max_hold（再补一刀会切出 "
                    f"{duration - nxt:.2f}s 的过短末镜，宁长不碎）"
                )
                break
            cuts.append(nxt)
            anchor = nxt
            guard += 1
        cuts.sort()
    by_bar = []
    for row, target in zip(bars, targets):
        n = sum(1 for c in cuts if row["start_seconds"] <= c < row["end_seconds"])
        by_bar.append({**row, "target_cuts": target, "cuts": n})
    return {
        "cuts": cuts,
        "bars": by_bar,
        "feasible": True,
        "reason": "；".join(tail_notes),
        "density_clamped": clamped,
        "min_gap_beats": min_gap,
        "max_gap_beats": max_gap,
        "total_cost": round(total_cost, 3),
    }


# ---------------------------------------------------------------------------
# 4. 组装
# ---------------------------------------------------------------------------


def build_beat_map(
    audio_path: str | Path,
    *,
    bpm: float = 0.0,
    fps: float = DEFAULT_FPS,
    beats_per_bar: int = DEFAULT_BEATS_PER_BAR,
    offset_seconds: float = 0.0,
    min_hold: float = 0.0,
    max_hold: float = 0.0,
    min_density: float = MIN_DENSITY,
    max_density: float = MAX_DENSITY,
    envelope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """一段音频 → 能量波 + 拍网格 + Bar-DP 切点。

    ``bpm`` 传 0 时走 tempo 兜底。任一步测不出 → 对应字段留空 + ``warnings``，
    调用方据此降级（绝不抛错、绝不猜）。
    """
    warnings: list[str] = []
    env = envelope or measure_energy_envelope(audio_path)
    if not env.get("source"):
        return {
            "path": str(audio_path), "bpm": 0.0, "bpm_source": "", "grid": None,
            "cuts": [], "bars": [], "energy_source": "", "duration": 0.0,
            "feasible": False, "warnings": [str(env.get("reason") or "能量不可测")],
        }

    bpm_source = ""
    resolved = float(bpm or 0.0)
    if resolved > 0:
        bpm_source = "explicit"
    else:
        est = estimate_tempo(env)
        if est.get("bpm"):
            resolved = float(est["bpm"])
            bpm_source = "estimated"
            warnings.append(
                f"bpm 缺失，用能量包络自相关估值 {resolved:g}（置信 {est.get('confidence')}）"
                "——非曲库数据，请人工确认"
            )
        else:
            warnings.append(f"bpm 缺失且估不出拍（{est.get('reason')}），退等密度吸拍")

    grid = beat_grid(bpm=resolved, fps=fps, offset_seconds=offset_seconds,
                     beats_per_bar=beats_per_bar,
                     source="energy_wave" if bpm_source != "estimated" else "estimated")
    if grid is None:
        return {
            "path": str(audio_path), "bpm": 0.0, "bpm_source": "", "grid": None,
            "cuts": [], "bars": [], "energy_source": env.get("source") or "",
            "duration": float(env.get("duration") or 0.0), "feasible": False,
            "warnings": warnings + ["无 bpm 网格，P0-5 不接管切点"],
        }

    bars = bar_energies(env, grid)
    planned = plan_beat_cuts(bars, grid, min_hold=min_hold, max_hold=max_hold,
                             duration=float(env.get("duration") or 0.0),
                             min_density=min_density, max_density=max_density)
    if not planned.get("feasible"):
        warnings.append(str(planned.get("reason") or "Bar-DP 不可行"))
    elif planned.get("reason"):
        warnings.append(str(planned["reason"]))
    if planned.get("density_clamped"):
        warnings.append(
            f"风格包 min_hold={min_hold}s 夹紧了目标密度（每 bar 至多 "
            f"{planned.get('max_gap_beats')} 拍一刀），高潮段切不碎是预期行为"
        )
    return {
        "path": str(audio_path),
        "bpm": round(resolved, 2),
        "bpm_source": bpm_source,
        "grid": grid,
        "bars": planned.get("bars") or bars,
        "cuts": planned.get("cuts") or [],
        "energy_source": env.get("source") or "",
        "duration": float(env.get("duration") or 0.0),
        "feasible": bool(planned.get("feasible")),
        "density_clamped": bool(planned.get("density_clamped")),
        "min_gap_beats": planned.get("min_gap_beats"),
        "max_gap_beats": planned.get("max_gap_beats"),
        "warnings": warnings,
    }
