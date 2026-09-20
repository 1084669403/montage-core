"""Material contract: read-only checks for source clips and compose usage.

The first implementation deliberately does not block produce.  It gives the
pipeline a machine-readable answer to the question that caused the mixed-media
incident: did every shot actually get an AI video, and is the compose plan
using that material instead of a still-image fallback?
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Callable


MaterialProbeFn = Callable[[Path], dict[str, Any]]
PathExistsFn = Callable[[Path], bool]


def _finding(
    severity: str,
    field: str,
    message: str,
    proposed_fix: str = "",
) -> dict[str, str]:
    row = {
        "severity": severity,
        "field": field,
        "message": message,
    }
    if proposed_fix:
        row["proposed_fix"] = proposed_fix
    return row


def _flatten_shots(scene_plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    shots: list[dict[str, Any]] = []
    for scene in (scene_plan or {}).get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for shot in scene.get("shots") or []:
            if isinstance(shot, dict) and shot.get("shot_id"):
                shots.append(shot)
    return shots


def _display_path(project_root: Path, raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        path = project_root / path
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _probe_video(path: Path) -> dict[str, Any]:
    from montage.compose.ffmpeg_engine import probe

    return probe(path)


def _probe_row(
    path: Path,
    *,
    probe_fn: MaterialProbeFn | None,
    strict: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    fn = probe_fn or _probe_video
    try:
        data = fn(path)
    except Exception as exc:  # noqa: BLE001 - contract records local media errors
        return None, {
            # 与模块内其它 finding 同一口径：只有 --all-video（strict）时挡出口；
            # 历史图模式/占位素材项目下只作 warning，不静默降级也不误伤拼片。
            "severity": "critical" if strict else "warning",
            "field": str(path),
            "message": f"ffprobe 失败: {exc}",
            "proposed_fix": "重抽该镜头或修复本地文件",
        }

    streams = data.get("streams") if isinstance(data.get("streams"), list) else []
    video = next(
        (row for row in streams if isinstance(row, dict) and row.get("codec_type") == "video"),
        {},
    )
    try:
        duration = float((data.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    try:
        width = int(video.get("width"))
        height = int(video.get("height"))
    except (TypeError, ValueError):
        width = height = None
    return {
        "duration_seconds": duration,
        "width": width,
        "height": height,
        "video_codec": str(video.get("codec_name") or ""),
        "r_frame_rate": str(video.get("r_frame_rate") or ""),
    }, None


def _default_exists(path: Path) -> bool:
    return path.is_file()


def _safe_duration(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def build_material_contract(
    *,
    scene_plan: dict[str, Any] | None,
    asset_manifest: dict[str, Any] | None,
    compose_plan: dict[str, Any] | None = None,
    project_dir: str | Path = ".",
    all_ai_video: bool = True,
    probe_fn: MaterialProbeFn | None = None,
    probe_sources: bool = True,
    exists_fn: PathExistsFn | None = None,
) -> dict[str, Any]:
    """Build a read-only source/compose material contract.

    ``probe_fn`` and ``exists_fn`` are injectable for tests.  Production
    callers should omit them so real filesystem checks and the shared ffprobe
    wrapper are used.  No artifact is written here.
    """
    root = Path(project_dir).resolve()
    path_exists = exists_fn or _default_exists
    shots = _flatten_shots(scene_plan)
    shot_ids = [str(row.get("shot_id") or "") for row in shots]
    duplicate_scene_shot_ids = sorted(
        shot_id for shot_id, count in Counter(shot_ids).items() if shot_id and count > 1
    )

    manifest_items = [
        row
        for row in (asset_manifest or {}).get("items") or []
        if isinstance(row, dict)
    ]
    items_by_shot: dict[str, list[dict[str, Any]]] = {}
    for item in manifest_items:
        shot_id = str(item.get("shot_id") or "")
        if shot_id:
            items_by_shot.setdefault(shot_id, []).append(item)

    compose_shots = [
        row
        for row in (compose_plan or {}).get("shots") or []
        if isinstance(row, dict) and row.get("shot_id")
    ]
    compose_by_shot: dict[str, list[dict[str, Any]]] = {}
    for row in compose_shots:
        compose_by_shot.setdefault(str(row.get("shot_id")), []).append(row)

    source_items: list[dict[str, Any]] = []
    source_findings: list[dict[str, str]] = []
    compose_findings: list[dict[str, str]] = []
    fallbacks: list[dict[str, str]] = []

    for shot in shots:
        shot_id = str(shot.get("shot_id") or "")
        expected_kind = "video" if all_ai_video else str(shot.get("shot_kind") or "video")
        if expected_kind not in {"video", "image"}:
            expected_kind = "video"

        shot_items = items_by_shot.get(shot_id, [])
        video_items = [row for row in shot_items if str(row.get("kind") or "") == "video"]
        image_items = [row for row in shot_items if str(row.get("kind") or "") == "image"]
        latest_video = video_items[-1] if video_items else None
        selected_item = latest_video or (image_items[-1] if image_items else None)
        actual_kind = (
            "video" if video_items else
            "image" if image_items else
            "missing"
        )
        raw_path = str((selected_item or {}).get("path") or "")
        path = Path(raw_path)
        if raw_path and not path.is_absolute():
            path = root / path
        exists = bool(raw_path) and path_exists(path)
        relative_path = _display_path(root, raw_path) if raw_path else ""

        probe_row: dict[str, Any] | None = None
        if expected_kind == "video" and probe_sources and exists:
            probe_row, probe_error = _probe_row(
                path, probe_fn=probe_fn, strict=all_ai_video,
            )
            if probe_error:
                source_findings.append(probe_error)

        if shot_id in duplicate_scene_shot_ids:
            source_findings.append(_finding(
                "critical", shot_id, "scene_plan 中 shot_id 重复", "修正 scene_plan 后重编译"
            ))

        if actual_kind != expected_kind:
            severity = "critical" if all_ai_video else "warning"
            source_findings.append(_finding(
                severity,
                shot_id,
                f"expected {expected_kind}, got {actual_kind}",
                "重抽该镜头或修正 shot_kind 契约",
            ))
            fallbacks.append({
                "shot_id": shot_id,
                "expected_kind": expected_kind,
                "actual_kind": actual_kind,
            })

        if len(video_items) > 1:
            # 历史重复行（如重抽后 id 变了：sh01_clip → sh01_video）不改变成片：
            # 下游一律按**最后一条**取片（compose_planner 的 videos[-1]、_upsert_item
            # 的 last-wins）。所以只要最后一条确实可用，这是历史痕迹 → warning；
            # 最后一条不可用才是 critical（真的没有可用成片）。
            source_findings.append(_finding(
                "warning" if exists else "critical",
                shot_id,
                f"asset_manifest has {len(video_items)} video items",
                "清理重复 manifest 条目并保留唯一成功生成（当前按最后一条为准）",
            ))

        if raw_path and raw_path == str(path):
            source_findings.append(_finding(
                "warning",
                shot_id,
                "manifest uses an absolute project path",
                "后续写入统一使用项目相对路径",
            ))
        if raw_path and not exists:
            source_findings.append(_finding(
                "critical",
                shot_id,
                f"manifest file does not exist: {relative_path}",
                "重抽该镜头或修复本地素材路径",
            ))

        manifest_duration = _safe_duration((selected_item or {}).get("duration_seconds"))
        probe_duration = _safe_duration((probe_row or {}).get("duration_seconds"))
        duration_match = (
            manifest_duration is None
            or probe_duration is None
            or abs(manifest_duration - probe_duration) <= 0.25
        )
        if not duration_match:
            source_findings.append(_finding(
                "warning",
                shot_id,
                (
                    f"manifest duration {manifest_duration:.3f}s differs from "
                    f"ffprobe {probe_duration:.3f}s"
                ),
                "以 ffprobe 实测为准并更新 manifest",
            ))

        source_items.append({
            "shot_id": shot_id,
            "scene_id": str(shot.get("scene_id") or ""),
            "expected_kind": expected_kind,
            "actual_kind": actual_kind,
            "path": relative_path,
            "exists": exists,
            "manifest_duration_seconds": manifest_duration,
            "probe": probe_row,
            "video_item_count": len(video_items),
            "pass": (
                actual_kind == expected_kind
                and len(video_items) <= 1
                and exists
                and (probe_duration is not None if expected_kind == "video" else True)
            ),
        })

    expected_ids = set(shot_ids)
    compose_ids = set(compose_by_shot)
    missing_in_compose = sorted(expected_ids - compose_ids)
    extra_in_compose = sorted(compose_ids - expected_ids)
    if missing_in_compose:
        compose_findings.append(_finding(
            "critical",
            "compose_plan",
            f"missing compose shots: {', '.join(missing_in_compose)}",
            "重编 compose_plan",
        ))
    if extra_in_compose:
        compose_findings.append(_finding(
            "critical",
            "compose_plan",
            f"unknown compose shots: {', '.join(extra_in_compose)}",
            "重编 compose_plan 或移除未知镜头",
        ))

    kenburns_count = 0
    vfx_total_count = 0
    vfx_overlay_count = 0
    compose_items: list[dict[str, Any]] = []
    source_by_id = {row["shot_id"]: row for row in source_items}
    for shot_id in sorted(expected_ids | compose_ids):
        rows = compose_by_shot.get(shot_id, [])
        if len(rows) > 1:
            compose_findings.append(_finding(
                "critical", shot_id, "compose_plan has duplicate shot entries", "重编 compose_plan"
            ))
        row = rows[-1] if rows else {}
        effects = row.get("effects") if isinstance(row.get("effects"), list) else []
        is_kenburns = any(
            isinstance(effect, dict) and str(effect.get("operation") or "") == "ken_burns"
            for effect in effects
        )
        if is_kenburns:
            kenburns_count += 1
        vfx = row.get("vfx") if isinstance(row.get("vfx"), list) else []
        post_vfx = [
            item for item in vfx if str(item.get("layer") or "") == "post"
        ]
        vfx_total_count += len(vfx)
        vfx_overlay_count += len(post_vfx)

        source = source_by_id.get(shot_id)
        actual_kind = str((source or {}).get("actual_kind") or "missing")
        render_kind = str(row.get("render_kind") or "")
        if row and (
            (actual_kind == "video" and render_kind not in {"", "ai_clip"})
            or (actual_kind == "image" and render_kind == "ai_clip")
        ):
            compose_findings.append(_finding(
                "critical",
                shot_id,
                f"render_kind={render_kind or 'missing'} does not match {actual_kind} source",
                "重编 compose_plan",
            ))
        if all_ai_video and (is_kenburns or actual_kind == "image"):
            compose_findings.append(_finding(
                "critical", shot_id, "KenBurns fallback while all_ai_video is required",
                "重抽该镜头为 AI 视频",
            ))
            if not any(item["shot_id"] == shot_id for item in fallbacks):
                fallbacks.append({
                    "shot_id": shot_id,
                    "expected_kind": "video",
                    "actual_kind": actual_kind,
                })

        compose_items.append({
            "shot_id": shot_id,
            "present": bool(rows),
            "render_kind": render_kind,
            "is_kenburns": is_kenburns,
            "vfx_total_count": len(vfx),
            "vfx_post_count": len(post_vfx),
        })

    missing_source_ids = sorted(
        row["shot_id"] for row in source_items if row["actual_kind"] == "missing"
    )
    duplicate_manifest_ids = sorted(
        row["shot_id"] for row in source_items if row["video_item_count"] > 1
    )
    actual_ai_video_count = sum(row["actual_kind"] == "video" for row in source_items)
    source_pass = (
        not duplicate_scene_shot_ids
        and not missing_source_ids
        and not duplicate_manifest_ids
        and all(row["pass"] for row in source_items)
        and not any(row["severity"] == "critical" for row in source_findings)
    )
    compose_provided = compose_plan is not None
    compose_pass = compose_provided and not any(
        row["severity"] == "critical" for row in compose_findings
    )

    return {
        "schema_version": 1,
        "intent": {
            "all_ai_video": bool(all_ai_video),
        },
        "source_contract": {
            "required_shots": len(shots),
            "actual_ai_video_count": actual_ai_video_count,
            "missing_shot_ids": missing_source_ids,
            "duplicate_manifest_shot_ids": duplicate_manifest_ids,
            "duplicate_scene_shot_ids": duplicate_scene_shot_ids,
            "items": source_items,
            "pass": source_pass,
            "findings": source_findings,
        },
        "compose_contract": {
            "provided": compose_provided,
            "shot_count": len(compose_shots),
            "missing_shot_ids": missing_in_compose,
            "extra_shot_ids": extra_in_compose,
            "render_kind_mismatch_count": sum(
                row["severity"] == "critical" and "render_kind" in row["message"]
                for row in compose_findings
            ),
            "items": compose_items,
            "pass": compose_pass,
            "findings": compose_findings,
        },
        "summary": {
            "required_shots": len(shots),
            "actual_ai_video_count": actual_ai_video_count,
            "kenburns_count": kenburns_count,
            "vfx_total_count": vfx_total_count,
            "vfx_overlay_count": vfx_overlay_count,
            "fallback_count": len(fallbacks),
        },
        "fallbacks": fallbacks,
        "pass": source_pass and (compose_pass if compose_provided else False),
        "findings": [*source_findings, *compose_findings],
    }
