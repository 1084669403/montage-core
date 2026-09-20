"""film_health — 成片发布前整片探测（P5 核心 + P0-7 长片指标）。

两层，**都只 warning，critical 仍只报确定性硬伤**（否则 VLM/量测会把 export 挡死）：

- 核心（P5）：看成片的确定性硬伤（存在/可读/视频流/时长>0/分辨率），缺 ffprobe 只
  warning。缺 ffmpeg 不挡导出，不要对成片再跑 asset_quality_gate 的 blurdetect。
- P0-7 长片指标：①音轨段间响度一致性（复用 P0-5 的能量包络测量）②时长偏差长片口径
  收紧到 10% ③镜连续性抽检（VLM 分段抽帧，**默认关**，显式给点数才烧配额）。

VLM 抽检结果永不进 critical：VLM 有不确定性又要配额，让它一票挡住 export 与既有
「缺密钥不挡成片」纪律直接冲突；它的价值是把「哪一段该重抽」指给人看。
"""

from __future__ import annotations

import os
import statistics
from collections.abc import Callable
from pathlib import Path
from typing import Any

from montage.compose.ffmpeg_engine import (
    check_ffprobe,
    detect_freezes,
    detect_pts_gaps,
    probe,
)
from montage.engine.artifacts import ArtifactStore
from montage.toolbase import BaseTool, ToolResult, ToolRuntime, ToolStatus

ProbeFn = Callable[[Path], dict[str, Any]]
MeasureFn = Callable[..., dict[str, Any]]
ReviewFn = Callable[..., dict[str, Any]]

#: 长片口径：目标时长 ≥ 此值（或 bible 带 chapters）时，时长偏差容差收紧到 10%。
LONGFORM_MIN_SECONDS = 300.0
#: 段间响度一致性的分块粒度（秒）。
LOUDNESS_BLOCK_SECONDS = 60.0
#: 段间落差 / 单段偏离中位的告警阈值（ebur128 下单位是 LU，PCM 兜底是相对 dB）。
LOUDNESS_SPREAD_WARN = 6.0
LOUDNESS_OUTLIER_WARN = 4.0
#: 时长时间偏差默认容差（短片）/ 长片容差。
DEFAULT_DURATION_TOLERANCE = 0.2
LONGFORM_DURATION_TOLERANCE = 0.1
#: 镜连续性抽检上限：再多也只是烧配额，长片抽 12 点足够暴露成片级漂移。
MAX_CONTINUITY_SAMPLES = 12
#: 偏离中位的逐段告警最多列几条（其余给一条汇总，别刷屏）。
MAX_OUTLIER_WARNINGS = 3
#: 一次 VLM 抽检的估算成本（与 vlm_reviewer.estimate_cost 对齐）。
CONTINUITY_COST_USD = 0.01


def _r_frame_rate(value: Any) -> float | None:
    if not value:
        return None
    text = str(value)
    if "/" in text:
        num, den = text.split("/", 1)
        try:
            d = float(den)
            return float(num) / d if d else None
        except (TypeError, ValueError):
            return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def duration_check(
    duration: float,
    expected_duration: float | None,
    *,
    tolerance: float | None = None,
    longform: bool | None = None,
) -> dict[str, Any]:
    """时长偏差判定（P0-7）。``tolerance=0`` = 只报数不判。

    ``longform=None`` 时按目标时长自动判（≥300s 算长片）；bible 带 chapters 的片由
    调用方显式传 ``longform=True``（章节不等于 300s 硬阈值）。
    """
    report: dict[str, Any] = {
        "expected": None,
        "delta": None,
        "tolerance": None,
        "longform": bool(longform),
        "over": False,
    }
    if not expected_duration or expected_duration <= 0 or duration <= 0:
        return report
    if longform is None:
        longform = expected_duration >= LONGFORM_MIN_SECONDS
    if tolerance is None:
        tolerance = LONGFORM_DURATION_TOLERANCE if longform else DEFAULT_DURATION_TOLERANCE
    tolerance = max(float(tolerance), 0.0)
    delta = abs(duration - float(expected_duration)) / float(expected_duration)
    report.update({
        "expected": round(float(expected_duration), 3),
        "delta": round(delta, 4),
        "tolerance": tolerance,
        "longform": bool(longform),
        "over": tolerance > 0 and delta > tolerance,
    })
    return report


def audio_blocks(
    envelope: dict[str, Any],
    *,
    block_seconds: float = LOUDNESS_BLOCK_SECONDS,
) -> dict[str, Any]:
    """能量包络 → 分块响度统计（纯函数，不碰 ffmpeg）。

    返回 ``{source, unit, block_seconds, blocks[], median_db, spread, reason}``。
    ``unit``：ebur128 是绝对响度 LU，PCM 兜底是峰值相对 dB（同片内可比、跨片不可比）。
    """
    source = str(envelope.get("source") or "")
    block_seconds = max(float(block_seconds or LOUDNESS_BLOCK_SECONDS), 1.0)
    report: dict[str, Any] = {
        "source": source,
        "unit": "LU" if source == "ebur128" else "dB",
        "block_seconds": round(block_seconds, 3),
        "blocks": [],
        "median_db": None,
        "spread": None,
    }
    windows = envelope.get("windows") or []
    if not windows:
        report["reason"] = str(envelope.get("reason") or "没有能量采样")
        return report

    buckets: dict[int, list[float | None]] = {}
    for window in windows:
        if not isinstance(window, dict):
            continue
        try:
            t = float(window.get("t") or 0.0)
        except (TypeError, ValueError):
            continue
        raw = window.get("db")
        try:
            db = None if raw is None else float(raw)
        except (TypeError, ValueError):
            db = None
        buckets.setdefault(int(t // block_seconds), []).append(db)
    if not buckets:
        report["reason"] = "能量采样无法分块"
        return report

    blocks: list[dict[str, Any]] = []
    for index in sorted(buckets):
        values = [v for v in buckets[index] if v is not None]
        blocks.append({
            "start": round(index * block_seconds, 3),
            "end": round((index + 1) * block_seconds, 3),
            "mean_db": round(sum(values) / len(values), 2) if values else None,
            "silent": not values,
        })
    audible = [b["mean_db"] for b in blocks if b["mean_db"] is not None]
    report["blocks"] = blocks
    report["median_db"] = round(statistics.median(audible), 2) if audible else None
    report["spread"] = round(max(audible) - min(audible), 2) if len(audible) >= 2 else None
    return report


def loudness_warnings(report: dict[str, Any]) -> list[dict[str, str]]:
    """段间响度一致性 → warning 列表（永不 critical）。"""
    blocks = [b for b in (report.get("blocks") or []) if isinstance(b, dict)]
    unit = str(report.get("unit") or "dB")
    block_seconds = float(report.get("block_seconds") or LOUDNESS_BLOCK_SECONDS)
    if not blocks:
        return [{
            "field": "audio_consistency",
            "message": f"段间响度未测到（{report.get('reason') or '无采样'}）",
        }]
    audible = [b for b in blocks if b.get("mean_db") is not None]
    if not audible:
        return [{"field": "audio_consistency", "message": "整片没有有效响度（全静音）"}]

    out: list[dict[str, str]] = []
    spread = report.get("spread")
    if isinstance(spread, (int, float)) and spread > LOUDNESS_SPREAD_WARN:
        loud = max(audible, key=lambda b: float(b["mean_db"]))
        quiet = min(audible, key=lambda b: float(b["mean_db"]))
        out.append({
            "field": "audio_loudness_spread",
            "message": (
                f"段间响度落差 {spread}{unit}（最响 {loud['mean_db']}{unit}@{loud['start']:.0f}s / "
                f"最静 {quiet['mean_db']}{unit}@{quiet['start']:.0f}s）超过 {LOUDNESS_SPREAD_WARN}{unit}"
            ),
        })
    median = report.get("median_db")
    if isinstance(median, (int, float)):
        outliers = sorted(
            ((abs(float(b["mean_db"]) - median), b) for b in audible),
            key=lambda pair: -pair[0],
        )
        outliers = [pair for pair in outliers if pair[0] > LOUDNESS_OUTLIER_WARN]
        for delta, block in outliers[:MAX_OUTLIER_WARNINGS]:
            out.append({
                "field": "audio_loudness_outlier",
                "message": (
                    f"{block['start']:.0f}–{block['end']:.0f}s 段平均 {block['mean_db']}{unit}，"
                    f"偏离中位 {median}{unit} 达 {round(delta, 2)}{unit}"
                ),
            })
        if len(outliers) > MAX_OUTLIER_WARNINGS:
            out.append({
                "field": "audio_loudness_outlier",
                "message": f"另有 {len(outliers) - MAX_OUTLIER_WARNINGS} 段同样偏离中位，未逐条列出",
            })
    silent = [b for b in blocks if b.get("silent")]
    if silent and audible:
        out.append({
            "field": "audio_silence",
            "message": (
                f"{len(silent)}/{len(blocks)} 段（每段约 {block_seconds:.0f}s）没有有效响度，"
                "可能是留白/空镜，请人工确认"
            ),
        })
    return out


def _default_measure(path: Path, *, window_seconds: float = 1.0) -> dict[str, Any]:
    """默认响度测量 = P0-5 的能量包络（ebur128 瞬时 M，不可用退 PCM RMS）。"""
    from montage.engine.energy_wave import measure_energy_envelope

    return measure_energy_envelope(path, window_seconds=window_seconds)


def probe_loudness(
    film: str | Path,
    *,
    measure_fn: MeasureFn | None = None,
    block_seconds: float = LOUDNESS_BLOCK_SECONDS,
) -> dict[str, Any]:
    """测整片段间响度。测不出只记 reason（调用方转成 warning），不抛。"""
    target = Path(film)
    measure = measure_fn or _default_measure
    try:
        envelope = measure(target, window_seconds=1.0)
    except Exception as exc:  # noqa: BLE001
        return {
            "source": "",
            "unit": "dB",
            "block_seconds": round(float(block_seconds), 3),
            "blocks": [],
            "median_db": None,
            "spread": None,
            "reason": f"响度测量失败: {exc}",
        }
    if not isinstance(envelope, dict):
        envelope = {}
    return audio_blocks(envelope, block_seconds=block_seconds)


def continuity_anchors(
    *,
    duration: float,
    scene_index: dict[str, Any] | None = None,
    samples: int,
) -> list[dict[str, Any]]:
    """抽检锚点：优先用 P0-2 的 ``scene_index.units[].start_seconds``，缺则等距。

    抽检要**铺满整片**（成片级漂移最爱藏在后半段），所以锚点多于 ``samples`` 时按
    等距下标挑，首尾必取。
    """
    limit = max(0, min(int(samples or 0), MAX_CONTINUITY_SAMPLES))
    if limit <= 0:
        return []
    points: list[dict[str, Any]] = []
    units = scene_index.get("units") if isinstance(scene_index, dict) else None
    for unit in units or []:
        if not isinstance(unit, dict):
            continue
        try:
            t = float(unit.get("start_seconds"))
        except (TypeError, ValueError):
            continue
        if duration > 0 and t >= duration:
            continue
        points.append({"t": round(t, 3), "unit_id": str(unit.get("unit_id") or "")})
    if not points:
        from montage.tools.vlm_reviewer import sample_timestamps

        return [{"t": t, "unit_id": ""} for t in sample_timestamps(duration, limit)]
    points.sort(key=lambda p: p["t"])
    unique: list[dict[str, Any]] = []
    for point in points:
        if unique and abs(float(unique[-1]["t"]) - float(point["t"])) < 0.5:
            continue
        unique.append(point)
    if len(unique) <= limit:
        return unique
    if limit == 1:
        return [unique[0]]
    step = (len(unique) - 1) / (limit - 1)
    picked: list[dict[str, Any]] = []
    seen: set[int] = set()
    for i in range(limit):
        index = round(i * step)
        if index in seen:
            continue
        seen.add(index)
        picked.append(unique[index])
    return picked


def _expected_reference(project_dir: str | Path) -> dict[str, Any]:
    """整片抽检的「定妆/场记」参考（bible → scene_plan → continuity 逐级补）。"""
    store = ArtifactStore(project_dir)
    bible = store.read("series_bible") or {}
    scene_plan = store.read("scene_plan") or {}
    continuity = store.read("continuity") or {}

    sources: list[str] = []
    appearance: list[str] = []
    outfit: list[str] = []

    def _add(name: Any, app: Any, out: Any, tag: str) -> None:
        label = str(name or "").strip()
        a = str(app or "").strip()
        o = str(out or "").strip()
        if not a and not o:
            return
        sources.append(tag)
        if a:
            appearance.append(f"{label}：{a}" if label else a)
        if o:
            outfit.append(f"{label}：{o}" if label else o)

    for char in bible.get("characters") or []:
        if isinstance(char, dict):
            _add(char.get("name") or char.get("id"), char.get("appearance"), char.get("outfit"), "series_bible")
    for char in scene_plan.get("character_registry") or []:
        if isinstance(char, dict):
            _add(
                char.get("name") or char.get("id"),
                char.get("appearance"),
                char.get("outfit_anchor") or char.get("outfit"),
                "scene_plan",
            )
    for char in continuity.get("characters") or []:
        if isinstance(char, dict):
            _add(char.get("id"), "", char.get("outfit"), "continuity")

    locations = [
        str(loc.get("name") or loc.get("id") or "")
        for loc in (bible.get("locations") or [])
        if isinstance(loc, dict)
    ]
    props = [
        str(prop.get("name") or prop.get("id") or "")
        for prop in (bible.get("props") or [])
        if isinstance(prop, dict)
    ]
    return {
        "appearance": "；".join(dict.fromkeys(appearance)),
        "outfit": "；".join(dict.fromkeys(outfit)),
        "location": "、".join(dict.fromkeys(x for x in locations if x)),
        "props": [x for x in dict.fromkeys(props) if x],
        "source": "+".join(dict.fromkeys(sources)),
    }


def probe_continuity(
    film: str | Path,
    *,
    anchors: list[dict[str, Any]],
    expected: dict[str, Any] | None = None,
    review_fn: ReviewFn | None = None,
) -> dict[str, Any]:
    """镜连续性抽检：每个锚点抽一帧走 VLM 对照定妆。缺密钥即停（不逐点失败）。"""
    anchors = list(anchors or [])
    if not anchors:
        return {
            "requested": 0, "sampled": 0, "failed": 0, "drift": 0, "critical": 0,
            "skipped": True, "reason": "没有采样锚点", "samples": [],
        }
    if review_fn is None:
        from montage.tools.vlm_reviewer import review_media

        review_fn = review_media

    samples: list[dict[str, Any]] = []
    drift = 0
    critical = 0
    failed = 0
    skipped_reason = ""
    for anchor in anchors:
        t = float(anchor.get("t") or 0.0)
        report = review_fn(
            media_path=str(film),
            expected=expected or {},
            mode="video_clip",
            timestamps=[t],
        )
        if report.get("skipped"):
            skipped_reason = "缺少 DASHSCOPE_API_KEY（VLM 未配置）"
            break
        issues = [i for i in (report.get("issues") or []) if isinstance(i, dict)]
        frames = ((report.get("sampled") or {}).get("sampled")) or []
        if not frames:
            failed += 1
        critical += sum(1 for i in issues if str(i.get("severity")) == "critical")
        drift += int(any(
            str(i.get("kind")) == "人物不一致" and str(i.get("severity")) == "critical"
            for i in issues
        ))
        samples.append({
            "t": round(t, 3),
            "unit_id": str(anchor.get("unit_id") or ""),
            "ok": bool(report.get("ok")),
            "score": report.get("score"),
            "issues": [
                {
                    "severity": str(i.get("severity") or ""),
                    "kind": str(i.get("kind") or ""),
                    "message": str(i.get("message") or ""),
                }
                for i in issues
            ],
        })
    return {
        "requested": len(anchors),
        "sampled": len(samples) - failed,
        "failed": failed,
        "drift": drift,
        "critical": critical,
        "skipped": bool(skipped_reason),
        "reason": skipped_reason,
        "samples": samples,
    }


def continuity_warnings(report: dict[str, Any]) -> list[dict[str, str]]:
    """抽检结论 → warning（永不 critical）。``requested=0``（没开抽检）不产生噪声。"""
    if not report or not report.get("requested"):
        return []
    if report.get("skipped"):
        return [{
            "field": "continuity_drift",
            "message": f"镜连续性抽检未执行：{report.get('reason') or '已跳过'}",
        }]
    out: list[dict[str, str]] = []
    requested = int(report.get("requested") or 0)
    if report.get("failed"):
        out.append({
            "field": "continuity_sample",
            "message": f"{report['failed']}/{requested} 个抽检点没抽出帧，这些时间点没被抽检到",
        })
    drift = int(report.get("drift") or 0)
    if drift:
        out.append({
            "field": "continuity_drift",
            "message": (
                f"{drift}/{requested} 个抽检点报「人物不一致」critical（VLM 抽检，不挡 export）"
            ),
            "proposed_fix": "整镜重抽或首尾帧重锚（shot_runner retry_ids）",
        })
    elif report.get("critical"):
        out.append({
            "field": "continuity_drift",
            "message": f"{report['critical']} 条抽检 critical（非人物不一致），详见 continuity.samples",
        })
    return out


def filter_title_card_freezes(
    report: dict[str, Any],
    title_seconds: float,
) -> dict[str, Any]:
    """剔除片头卡片区间的冻结帧（设计上的静止画面，不是缺陷）。"""
    if title_seconds <= 0 or not isinstance(report, dict):
        return report
    segments = list(report.get("segments") or [])
    kept = [
        seg for seg in segments
        if float(seg.get("start") or 0) >= title_seconds - 0.05
    ]
    if len(kept) != len(segments):
        report["segments"] = kept
        report["total_seconds"] = round(
            sum(float(seg.get("duration") or 0) for seg in kept), 3,
        )
        report["ignored_title_card_seconds"] = title_seconds
    return report


def inspect_film(
    path: str | Path,
    *,
    expected_duration: float | None = None,
    probe_fn: ProbeFn | None = None,
    duration_tolerance: float | None = None,
    longform: bool | None = None,
    gap_fn: Callable[[Path], dict[str, Any]] | None = None,
    freeze_fn: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """返回 {pass, critical, warnings, probe, duration_check, path}。缺文件即 critical。

    ``gap_fn`` / ``freeze_fn`` 是**可选**的整片深检（A2 兜底）：PTS 断档与
    冻结帧都只记 warning——它们是「哪一段该重跑」的定位信息，不是出口门禁。
    不传则完全跳过（单测不需要解码整片）。
    """
    target = Path(path)
    critical: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    report: dict[str, Any] = {"path": str(target), "probe": {}}
    if not target.is_file():
        critical.append({"field": "path", "message": f"成片不存在: {target}"})
        report["pass"] = False
        report["critical"] = critical
        report["warnings"] = warnings
        report["duration_check"] = duration_check(0.0, expected_duration, tolerance=duration_tolerance, longform=longform)
        return report

    if probe_fn is None and check_ffprobe() is None:
        warnings.append({"field": "ffprobe", "message": "缺少 ffprobe，跳过整片探测"})
        report["pass"] = True
        report["critical"] = critical
        report["warnings"] = warnings
        report["duration_check"] = duration_check(
            0.0, expected_duration, tolerance=duration_tolerance, longform=longform,
        )
        return report

    reader = probe_fn or probe
    try:
        info = reader(target)
    except Exception as exc:  # noqa: BLE001
        critical.append({"field": "probe", "message": f"ffprobe 失败: {exc}"})
        report["pass"] = False
        report["critical"] = critical
        report["warnings"] = warnings
        report["duration_check"] = duration_check(
            0.0, expected_duration, tolerance=duration_tolerance, longform=longform,
        )
        return report

    streams = info.get("streams") or []
    video = next((s for s in streams if isinstance(s, dict) and s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if isinstance(s, dict) and s.get("codec_type") == "audio"), {})
    fmt = info.get("format") if isinstance(info.get("format"), dict) else {}
    duration = float(fmt.get("duration") or 0)
    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    fps = _r_frame_rate(video.get("r_frame_rate"))
    report["probe"] = {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "video_codec": video.get("codec_name") or "",
        "audio_codec": audio.get("codec_name") or "",
        "has_audio": bool(audio),
        "size_bytes": int(fmt.get("size") or 0),
    }
    if not video:
        critical.append({"field": "video", "message": "成片没有视频流"})
    if duration <= 0:
        critical.append({"field": "duration", "message": "成片时长为 0"})
    if video and (width <= 0 or height <= 0):
        critical.append({"field": "resolution", "message": "成片分辨率无效"})
    if not audio:
        warnings.append({"field": "audio", "message": "成片没有音轨"})
    if fps is not None and (fps < 1 or fps > 120):
        warnings.append({"field": "fps", "message": f"帧率异常: {fps}"})

    check = duration_check(
        duration, expected_duration, tolerance=duration_tolerance, longform=longform,
    )
    report["duration_check"] = check
    if check["over"]:
        scale = "长片" if check["longform"] else "短片"
        warnings.append({
            "field": "duration",
            "message": (
                f"时长 {duration:.1f}s 相对目标 {check['expected']:.1f}s 偏差 "
                f"{check['delta'] * 100:.1f}% 超过 {check['tolerance'] * 100:.0f}%（{scale}口径）"
            ),
        })

    # A2 兜底：PTS 断档 / 冻结帧整片深检（都只 warning）。
    for field, fn, kind in (
        ("pts_gaps", gap_fn, "PTS 断档"),
        ("freezes", freeze_fn, "冻结帧"),
    ):
        if fn is None or not video:
            continue
        try:
            detail = fn(target)
        except Exception as exc:  # noqa: BLE001 — 深检失败只记 warning
            detail = {"checked": False, "error": str(exc)}
        report[field] = detail if isinstance(detail, dict) else {}
        if not detail or not detail.get("checked"):
            continue
        if field == "pts_gaps":
            hits = detail.get("gaps") or []
            total = float(detail.get("gap_seconds") or 0)
            if hits:
                first = hits[0]
                warnings.append({
                    "field": field,
                    "message": (
                        f"{len(hits)} 处 PTS 断档（合计 {total:.2f}s；首处 "
                        f"{first.get('start')}s 持续 {first.get('duration')}s）——"
                        "播放器会表现为定格"
                    ),
                })
        else:
            hits = detail.get("segments") or []
            total = float(detail.get("total_seconds") or 0)
            if hits:
                warnings.append({
                    "field": field,
                    "message": f"{len(hits)} 处冻结帧（合计 {total:.2f}s）——{kind}检查",
                })
    report["pass"] = not critical
    report["critical"] = critical
    report["warnings"] = warnings
    return report


def _expected_duration(project_dir: Path) -> float | None:
    store = ArtifactStore(project_dir)
    bible = store.read("series_bible") or {}
    try:
        target = float(bible.get("target_duration_seconds") or 0)
    except (TypeError, ValueError):
        target = 0.0
    if target > 0:
        return target
    plan = store.read("compose_plan") or {}
    total = 0.0
    for shot in plan.get("shots") or []:
        if not isinstance(shot, dict):
            continue
        try:
            total += float(shot.get("duration_seconds") or 0)
        except (TypeError, ValueError):
            continue
    return total if total > 0 else None


def _resolve_film(root: Path, raw: str) -> tuple[Path, str]:
    """成片路径：显式 > renders/final.mp4 > auto_edit/final.mp4。

    回落到 auto_edit 是给「剪辑现有视频」通路用的：那条路不进 7 阶段，成片落在
    ``auto_edit/final.mp4``，但它同样需要长片指标与抽检。
    """
    if raw:
        return Path(raw), "explicit"
    primary = root / "renders" / "final.mp4"
    if primary.is_file():
        return primary, "renders"
    fallback = root / "auto_edit" / "final.mp4"
    if fallback.is_file():
        return fallback, "auto_edit"
    return primary, "renders"


class FilmHealth(BaseTool):
    """发布前整片健康检查（核心硬伤 + P0-7 长片指标）。"""

    name = "film_health"
    version = "0.2.0"
    capability = "analysis"
    provider = "openmontage"
    runtime = ToolRuntime.LOCAL
    input_schema = {
        "type": "object",
        "properties": {
            "project_dir": {"type": "string"},
            "path": {"type": "string", "description": "默认 renders/final.mp4，缺则 auto_edit/final.mp4"},
            "expected_duration": {"type": "number"},
            "duration_tolerance": {
                "type": "number",
                "description": "P0-7：时长偏差容差（0=只报数）；缺省按长片 10% / 短片 20%",
            },
            "longform": {"type": "boolean", "description": "P0-7：强制长片口径（bible 带 chapters 时用）"},
            "continuity_samples": {
                "type": "integer",
                "default": 0,
                "description": f"P0-7：镜连续性抽检点数（0=不抽检，上限 {MAX_CONTINUITY_SAMPLES}；需 DASHSCOPE_API_KEY）",
            },
            "scene_index": {"type": "object", "description": "P0-7：抽检锚点来源（缺省读 artifacts/scene_index.json）"},
            "deep_check": {
                "type": "boolean",
                "default": True,
                "description": "整片深检：PTS 断档 + 冻结帧（各一次解码/探测，均只 warning）",
            },
            "title_seconds": {
                "type": "number",
                "default": 0,
                "description": "片头卡片时长：该区间内的静止画面是设计，不计入冻结帧告警",
            },
        },
    }

    def get_status(self) -> ToolStatus:
        return ToolStatus.AVAILABLE if check_ffprobe() else ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        if not os.environ.get("DASHSCOPE_API_KEY"):
            return 0.0
        try:
            samples = int(inputs.get("continuity_samples") or 0)
        except (TypeError, ValueError):
            samples = 0
        return CONTINUITY_COST_USD * max(0, min(samples, MAX_CONTINUITY_SAMPLES))

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        root = Path(str(inputs.get("project_dir") or "")).resolve() if inputs.get("project_dir") else None
        raw = str(inputs.get("path") or "").strip()
        deep = True
        if inputs.get("deep_check") is not None:
            deep = str(inputs.get("deep_check")).strip().lower() not in {"0", "false", "no", "off"}
        if os.environ.get("MONTAGE_NO_DEEP_HEALTH"):
            deep = False
        try:
            title_seconds = float(inputs.get("title_seconds") or 0.0)
        except (TypeError, ValueError):
            title_seconds = 0.0

        def _freezes_without_title_card(target: Path) -> dict[str, Any]:
            """片头卡片是设计上的静止画面，不算冻结帧缺陷。"""
            return filter_title_card_freezes(detect_freezes(target), title_seconds)

        if root is not None:
            path, path_source = _resolve_film(root, raw)
        elif raw:
            path, path_source = Path(raw), "explicit"
        else:
            return ToolResult(success=False, error="需要 path 或 project_dir")
        expected = inputs.get("expected_duration")
        try:
            expected_f = float(expected) if expected not in (None, "") else None
        except (TypeError, ValueError):
            expected_f = None
        tolerance = inputs.get("duration_tolerance")
        try:
            tolerance_f = float(tolerance) if tolerance not in (None, "") else None
        except (TypeError, ValueError):
            tolerance_f = None
        longform = inputs.get("longform")
        longform_f = bool(longform) if longform is not None else None

        store = ArtifactStore(root) if root else None
        if store is not None:
            if expected_f is None:
                expected_f = _expected_duration(root)
            if longform_f is None:
                bible = store.read("series_bible") or {}
                if bible.get("chapters"):
                    longform_f = True

        data = inspect_film(
            path,
            expected_duration=expected_f,
            duration_tolerance=tolerance_f,
            longform=longform_f,
            gap_fn=detect_pts_gaps if deep else None,
            freeze_fn=_freezes_without_title_card if deep else None,
        )
        data["path_source"] = path_source
        warnings = list(data.get("warnings") or [])

        # P0-7 长片指标：只在真探到片子（有 probe）时跑，缺 ffprobe/缺文件不硬撑。
        probe_info = data.get("probe") if isinstance(data.get("probe"), dict) else {}
        if probe_info.get("has_audio"):
            loudness = probe_loudness(path)
            data["audio_consistency"] = loudness
            warnings.extend(loudness_warnings(loudness))

        try:
            samples = int(inputs.get("continuity_samples") or 0)
        except (TypeError, ValueError):
            samples = 0
        if samples > MAX_CONTINUITY_SAMPLES:
            warnings.append({
                "field": "continuity_sample",
                "message": f"抽检点数 {samples} 超过上限 {MAX_CONTINUITY_SAMPLES}，已截断",
            })
        if samples > 0 and probe_info:
            scene_index = inputs.get("scene_index")
            if not isinstance(scene_index, dict) and store is not None:
                scene_index = store.read("scene_index")
            duration = float(probe_info.get("duration_seconds") or 0)
            anchors = continuity_anchors(
                duration=duration,
                scene_index=scene_index if isinstance(scene_index, dict) else None,
                samples=samples,
            )
            expected_ref = _expected_reference(root) if root else {}
            continuity = probe_continuity(path, anchors=anchors, expected=expected_ref)
            continuity["expected_source"] = str(expected_ref.get("source") or "")
            data["continuity"] = continuity
            warnings.extend(continuity_warnings(continuity))

        data["warnings"] = warnings
        if store is not None:
            store.write("film_health", data, schema=None)
            data["artifact"] = str(root / "artifacts" / "film_health.json")
        return ToolResult(success=True, data=data)
