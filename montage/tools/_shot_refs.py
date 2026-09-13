"""shot_runner 引用解析 + 就绪判定 + 定妆收集 纯函数。

从 shot_runner.py 抽出，原文件保留显式重导出 shim。
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

from montage.toolbase import ToolResult

from montage.tools._shot_constants import (
    _AGNES_FLASH_IMAGE_RANK,
    _AGNES_FLASH_MAX_IMAGES,
    _CAST_EST_IMAGE_WARN,
    _MAX_FORMS,
    _MAX_PROPS,
    _REF_ROLES,
    _REFINE_HINT,
)
from montage.tools._shot_route import _agnes_is_v25, collect_shots


def character_forms(char: dict[str, Any] | None) -> list[dict[str, Any]]:
    """角色的形态列表；无显式 forms[] 时返回单隐式形态（id=""）。

    隐式形态 == 旧行为：用角色自身的 appearance/outfit 出一张定妆。
    """
    forms = char.get("forms") if isinstance(char, dict) else None
    out: list[dict[str, Any]] = []
    if isinstance(forms, list):
        for raw in forms:
            if not isinstance(raw, dict):
                continue
            if not str(raw.get("id") or "").strip():
                continue
            out.append(raw)
    if out:
        return out
    return [{"id": ""}]


def form_id_of(form: dict[str, Any] | None) -> str:
    return str((form or {}).get("id") or "").strip()


def default_form_id(char: dict[str, Any] | None) -> str:
    """镜头未声明形态时的默认形态：forms[].default=true，否则首个，否则空。"""
    forms = character_forms(char)
    for form in forms:
        if form.get("default") is True:
            return form_id_of(form)
    return form_id_of(forms[0])


def blend_character_form(
    char: dict[str, Any] | None,
    form: dict[str, Any] | None,
) -> dict[str, Any]:
    """用 form 覆盖角色外观字段（name/appearance/outfit_anchor），空值回落角色。

    不合并则每个形态都会用基础形象生成，forms 形同虚设。
    """
    merged = dict(char or {})
    if not isinstance(form, dict):
        return merged
    fid = form_id_of(form)
    if fid:
        merged["form_id"] = fid
    name = str(form.get("name") or "").strip()
    if name:
        merged["name"] = name
    appearance = str(form.get("appearance") or "").strip()
    if appearance:
        merged["appearance"] = appearance
    outfit = str(form.get("outfit_anchor") or form.get("outfit") or "").strip()
    if outfit:
        merged["outfit_anchor"] = outfit
    return merged


def form_ref_id(kind: str, cid: str, form_id: str = "") -> str:
    """manifest reference_assets[].id：隐式 ``portrait_{cid}``；显式 ``portrait_{cid}_{form}``。"""
    fid = str(form_id or "").strip()
    return f"{kind}_{cid}_{fid}" if fid else f"{kind}_{cid}"


def form_subject(kind: str, cid: str, form_id: str = "") -> str:
    """人机接口 subject / --retry token：隐式 ``portrait/<cid>``；显式 ``portrait/<cid>:<form>``。"""
    fid = str(form_id or "").strip()
    return f"{kind}/{cid}:{fid}" if fid else f"{kind}/{cid}"


def parse_form_subject(token: str) -> tuple[str, str]:
    """解析 ``<kind>/<cid>[:<form>]`` → (cid, form_id)；非法返回 ("", "")。

    form_id 为空表示「无 form 语义」（命中该角色全部形态）。
    """
    text = str(token or "").strip()
    if "/" not in text:
        return "", ""
    rest = text.split("/", 1)[1].strip()
    if not rest:
        return "", ""
    if ":" in rest:
        cid, fid = rest.split(":", 1)
        return cid.strip(), fid.strip()
    return rest, ""


def effective_skip_turnaround(char: dict[str, Any] | None, form: dict[str, Any] | None) -> bool:
    """form.skip_turnaround 有值时用它，否则回落 char.skip_turnaround。"""
    if isinstance(form, dict) and "skip_turnaround" in form:
        return bool(form.get("skip_turnaround"))
    return bool((char or {}).get("skip_turnaround"))


def _http_video_urls(shot: dict[str, Any], refs: list[dict[str, Any]]) -> list[str]:
    urls: list[str] = []
    for key in ("video_url", "reference_video_url"):
        raw = str(shot.get(key) or "").strip()
        if raw.startswith("http") and raw not in urls:
            urls.append(raw)
    for ref in refs:
        if str(ref.get("kind") or "") != "video":
            continue
        raw = str(ref.get("url") or "").strip()
        if raw.startswith("http") and raw not in urls:
            urls.append(raw)
    return urls


def _http_still_urls(refs: list[dict[str, Any]]) -> list[str]:
    kinds = {"portrait", "turnaround", "scene_ref", "style_anchor", "prop"}
    urls: list[str] = []
    for ref in refs:
        if str(ref.get("kind") or "") not in kinds:
            continue
        raw = str(ref.get("url") or "").strip()
        if raw.startswith("http") and raw not in urls:
            urls.append(raw)
    return urls


def missing_identity_refs(
    shot: dict[str, Any],
    refs: list[dict[str, Any]],
    scene_plan: dict[str, Any] | None,
    *,
    project_dir: str = "",
    cast_ref_kind: str = "portrait",
    video_loop: str = "",
    require_url: bool = False,
) -> list[str]:
    """本镜每个声明的 ``(cid, form_id)`` 缺选中身份图时返回标识列表。

    发送侧与 I2V 护栏共用这一判定；缺该形态 URL 才拦，不因同角色另一形态
    已生成而误放行/误拦。
    """
    from montage.engine.policy import resolve_cast_ref_kind

    registry = _registry_map(scene_plan)
    portraits_by_form = _portrait_refs_by_form({"reference_assets": refs})
    turnarounds_by_form = _turnaround_refs_by_form({"reference_assets": refs})
    missing: list[str] = []
    for cid, fid, _has_forms in _shot_character_forms(shot, scene_plan):
        if cid not in registry:
            continue
        char = registry.get(cid) or {}
        form = next((f for f in character_forms(char) if form_id_of(f) == fid), None)
        kind = resolve_cast_ref_kind(cast_ref_kind, char, form, video_loop=video_loop)
        by_form = turnarounds_by_form if kind == "turnaround" else portraits_by_form
        hit = by_form.get((cid, fid)) or by_form.get((cid, ""))
        if not _ref_ready(project_dir, hit, require_url=require_url):
            missing.append(f"{cid}:{fid}" if fid else cid)
    return missing


def _identity_http_refs(
    shot: dict[str, Any],
    refs: list[dict[str, Any]],
    script: dict[str, Any] | None,
    scene_plan: dict[str, Any] | None,
    *,
    skip_urls: set[str] | frozenset[str] | None = None,
    cast_ref_kind: str = "portrait",
    video_loop: str = "",
) -> list[dict[str, Any]]:
    """本镜身份图 + 场景/道具的 URL / 可灵工牌，供 Omni subject / Agnes extra_body。

    **每个出场形态只发一张身份图**（默认 portrait；turnaround 由 form/character/
    packet 的 ``cast_ref_kind`` 显式开启）。未声明 ``form_id`` 的镜头只发该角色
    默认形态，不发全部形态；未选中的 kind 不进候选、不占名额。选中 kind 仅有
    element_id（可灵工牌，无 URL）时照发；两者皆无则回落另一身份 kind 的可用图。
    """
    from montage.engine.policy import resolve_cast_ref_kind

    skip = {str(u).strip() for u in (skip_urls or []) if str(u).strip()}
    shot = dict(shot)
    shot["_include_turnaround_refs"] = True  # 需要时能取到四视图候选
    all_refs = resolve_shot_refs(shot, {"reference_assets": refs}, script, scene_plan)

    registry = _registry_map(scene_plan)
    wanted: dict[tuple[str, str], str] = {}
    for cid, fid, _has_forms in _shot_character_forms(shot, scene_plan):
        char = registry.get(cid) or {"id": cid}
        form = next((f for f in character_forms(char) if form_id_of(f) == fid), None)
        wanted[(cid, fid)] = resolve_cast_ref_kind(
            cast_ref_kind, char, form, video_loop=video_loop,
        )

    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for ref in all_refs:
        by_key[(
            str(ref.get("kind") or ""),
            str(ref.get("character_id") or ""),
            str(ref.get("form_id") or ""),
        )] = ref

    out: list[dict[str, Any]] = []
    seen: set[str] = set(skip)

    def _usable(ref: dict[str, Any] | None) -> bool:
        """可发：公网 URL，或已有可灵工牌 element_id。"""
        if not isinstance(ref, dict):
            return False
        if str(ref.get("element_id") or "").strip():
            return True
        return str(ref.get("url") or "").strip().startswith("http")

    def _push(ref: dict[str, Any], *, identity: bool) -> None:
        url = str(ref.get("url") or "").strip()
        eid = str(ref.get("element_id") or "").strip()
        # 可灵工牌走 element_id，无 URL 也要发；其余必须公网 http(s)。
        key = url if url.startswith("http") else (f"element:{eid}" if eid else "")
        if not key or key in seen:
            return
        seen.add(key)
        row: dict[str, Any] = {"url": url, "type": "subject", "kind": ref.get("kind")}
        if eid:
            row["element_id"] = eid
        if identity:
            row["identity"] = True
        # 透传身份/归属，供 _agnes_flash_image_plan 排序、供编号对账。
        for key in ("id", "name", "character_id", "form_id", "location_id", "prop_id"):
            val = ref.get(key)
            if val:
                row[key] = val
        # 多形态镜的图例用「角色·形态」名，锁到该形态的 <Picture N>。
        cid = str(ref.get("character_id") or "")
        fid = str(ref.get("form_id") or "")
        if cid and fid:
            char = registry.get(cid) or {}
            form = next((f for f in character_forms(char) if form_id_of(f) == fid), None)
            label = str((form or {}).get("name") or "").strip() or fid
            base = str(ref.get("name") or cid).strip() or cid
            row["name"] = f"{base}·{label}"
            row["form_id"] = fid
        out.append(row)

    for (cid, fid), kind in wanted.items():
        # 选中 kind 无可用参考时回落另一身份 kind（可灵四视图常只有 element_id、
        # 无 URL；缺 element 的测试/降级场景则回落定妆 URL），避免整镜丢身份。
        for cand in (kind, *(k for k in ("portrait", "turnaround") if k != kind)):
            hit = by_key.get((cand, cid, fid)) or by_key.get((cand, cid, ""))
            if _usable(hit):
                _push(hit, identity=True)
                break
    for ref in all_refs:
        if str(ref.get("kind") or "") in ("portrait", "turnaround"):
            continue
        _push(ref, identity=False)
    return out


_AGNES_DROP_FINDING_KINDS = ("scene_ref", "prop")


def _agnes_flash_image_plan(
    identity: list[dict[str, Any]],
    *,
    max_images: int = _AGNES_FLASH_MAX_IMAGES,
) -> dict[str, Any]:
    """Flash 参考图的最终有序表 + 丢弃清单（唯一排序/截断入口）。

    返回 ``{"urls": [...], "entries": [...], "dropped": [...]}``：

    - ``urls`` 与 Agnes ``images[]`` 一一对应，下标即 ``<Picture N>`` 的 N；
    - ``entries[i]`` = ``{"index": i+1, "url", "kind", ...}``，是截断/过滤后**重算**的
      引用编号，提示词图例必须照它生成（见 picture-sot）；
    - ``dropped`` 记录每个被丢弃的参考及其原因（非公网 URL / 超过上限），
      供 ``agnes_ref_findings`` 出 finding。

    排序：``first_frame``（rank -1）仍最前；``identity=True`` 的条目（每形态选中
    的身份图，portrait 或 turnaround）统一 rank 0，保证不被 scene_ref/prop 截断；
    其余按 ``_AGNES_FLASH_IMAGE_RANK``。身份图由发送侧按 ``cast_ref_kind`` 选型，
    多角色镜不再无条件硬丢四视图。
    """
    entries: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _rank(ref: dict[str, Any]) -> int:
        kind = str(ref.get("kind") or "")
        if kind == "first_frame":
            return -1
        if ref.get("identity"):
            return 0
        return _AGNES_FLASH_IMAGE_RANK.get(kind, 9)

    for ref in sorted(
        (r for r in identity if isinstance(r, dict)),
        key=_rank,
    ):
        kind = str(ref.get("kind") or "")
        url = str(ref.get("url") or "").strip()
        if not url.startswith("http"):
            dropped.append({"kind": kind, "url": url, "reason": "非公网 http(s) URL"})
            continue
        if url in seen:
            continue
        seen.add(url)
        if len(entries) >= max_images:
            dropped.append({"kind": kind, "url": url, "reason": f"超过 {max_images} 张上限"})
            continue
        row: dict[str, Any] = {"index": len(entries) + 1, "url": url, "kind": kind}
        if ref.get("identity"):
            row["identity"] = True
        for key in ("id", "name", "character_id", "form_id", "location_id", "prop_id"):
            val = ref.get(key)
            if val:
                row[key] = val
        entries.append(row)
    return {
        "urls": [entry["url"] for entry in entries],
        "entries": entries,
        "dropped": dropped,
    }


def _agnes_flash_images(identity: list[dict[str, Any]]) -> list[str]:
    """Flash reference 的最终有序 URL（≤5 张 https），切段与首段必须同一套。"""
    return _agnes_flash_image_plan(identity)["urls"]


def split_segment_seconds(
    total: float,
    count: int,
    *,
    min_seconds: float = 4.0,
    max_seconds: float = 12.0,
) -> list[float] | None:
    """把 ``total`` 秒整数均分到 ``count`` 段，每段落在 [min,max]；不可行返回 None。

    各段之和 == round(total)，保证 concat 后时长不变。仅服务秒级 duration_policy。
    """
    n = int(count or 0)
    total = float(total or 0)
    if n <= 0:
        return None
    if n == 1:
        return [total]
    lo = int(math.ceil(float(min_seconds) - 1e-6))
    hi = int(math.floor(float(max_seconds) + 1e-6))
    if lo > hi:
        return None
    target = int(round(total))
    if target < n * lo or target > n * hi:
        return None
    base = target // n
    extra = target - base * n
    vals = [base + (1 if i < extra else 0) for i in range(n)]
    return [float(v) for v in vals]


def _segment_single(rows: list[dict[str, Any]], reason: str = "") -> dict[str, Any]:
    return {"mode": "single", "reason": reason, "segments": [], "dropped": []}


def plan_reference_segments(
    identity: list[dict[str, Any]],
    *,
    max_images: int = _AGNES_FLASH_MAX_IMAGES,
    wanted_seconds: float = 0.0,
    min_seconds: float = 4.0,
    max_seconds: float = 12.0,
    reserve_first: bool = False,
    max_segments: int = 4,
) -> dict[str, Any]:
    """参考溢出时的镜内分段规划（joint 时间轴切片，同 shot_id）。

    返回 ``{"mode": "single"|"segment", "reason", "segments", "dropped"}``：

    - ``single``：沿用旧行为（由调用方跑 ``_agnes_flash_image_plan`` 截断 + finding）；
    - ``segment``：``segments[i] = {"refs", "seconds", "bridge"}``，第 2 段起
      ``bridge=True``（需上一段尾帧合成新首帧，占 1 个视频名额）。

    切段模型：段是时间轴上的顺序切片，``n = max(参考组所需段数, 时长所需段数, 1)``，
    各段秒数之和 ≈ ``wanted``。``first_has_bridge`` 由 ``reserve_first`` 表示
    （``frames_mode=reference_first`` 时第 1 段也已被首帧占位）。

    分配：身份图每段必带；``scene_ref`` 每段都带；``prop`` 依次填满每段剩余名额。
    可行性不足（身份+场景超名额 / 时长切不动 / 超段数上限）一律回退 ``single``+reason，
    绝不开跑白烧钱。
    """
    rows = [r for r in identity if isinstance(r, dict)]
    budget = int(max_images or 0)
    if budget <= 0 or not rows:
        return _segment_single(rows)

    # 首帧（reference_first 的本镜首帧）由调用方声明 reserve_first，占位不参与分配。
    first_frames = [r for r in rows if str(r.get("kind") or "") == "first_frame"]
    base = [r for r in rows if str(r.get("kind") or "") != "first_frame"]
    identity_refs = [
        r for r in base if str(r.get("kind") or "") in ("portrait", "turnaround")
    ]
    support = [
        r for r in base if str(r.get("kind") or "") not in ("portrait", "turnaround")
    ]
    scene_refs = [r for r in support if str(r.get("kind") or "") == "scene_ref"]
    others = [r for r in support if str(r.get("kind") or "") != "scene_ref"]
    reserve1 = 1 if reserve_first else 0

    # 未溢出：不触发续拍。
    if len(base) + reserve1 <= budget:
        return _segment_single(rows)

    # 第 2 段起必有桥接位；取最紧容量（保守，避免某段塞爆）。
    cap_first = budget - reserve1 - len(identity_refs)
    cap_later = budget - 1 - len(identity_refs)
    cap = min(cap_first, cap_later)
    if cap < len(scene_refs):
        return _segment_single(rows, "身份+场景参考已超每段名额，分段不可行")
    remaining = cap - len(scene_refs)
    if others and remaining <= 0:
        return _segment_single(rows, "每段名额被身份+场景占满，道具无法分担，分段不可行")

    n_by_refs = max(1, math.ceil(len(others) / remaining)) if others else 1
    n_by_duration = max(1, math.ceil(float(wanted_seconds) / float(max_seconds))) if wanted_seconds else 1
    n = max(n_by_refs, n_by_duration, 1)
    if n <= 1:
        return _segment_single(rows)
    if n > int(max_segments or 0):
        return _segment_single(rows, f"分段数 {n} 超过上限 {max_segments}")

    secs = split_segment_seconds(
        wanted_seconds, n, min_seconds=min_seconds, max_seconds=max_seconds,
    )
    if secs is None:
        return _segment_single(
            rows,
            f"时长 {float(wanted_seconds or 0):.0f}s 不足以切成 {n} 段"
            f"（每段需 {min_seconds:.0f}-{max_seconds:.0f}s）",
        )

    buckets: list[list[dict[str, Any]]] = [[] for _ in range(n)]
    for ref in others:
        for s in range(n):
            if len(buckets[s]) < remaining:
                buckets[s].append(ref)
                break

    segments: list[dict[str, Any]] = []
    for s in range(n):
        refs = [*identity_refs, *scene_refs, *buckets[s]]
        if s == 0:
            # 第 1 段保留本镜设计首帧（reference_first）；否则无桥接。
            refs = [*first_frames, *refs]
        segments.append({
            "refs": refs,
            "seconds": secs[s],
            "bridge": s > 0,
        })
    return {"mode": "segment", "reason": "", "segments": segments, "dropped": []}


def agnes_ref_findings(plan: dict[str, Any], subject: str) -> list[dict[str, Any]]:
    """scene_ref/prop 被截断或 URL 过滤丢弃时出 finding；其余 kind 静默。"""
    findings: list[dict[str, Any]] = []
    for drop in plan.get("dropped") or []:
        kind = str(drop.get("kind") or "")
        if kind not in _AGNES_DROP_FINDING_KINDS:
            continue
        zh = "场景参考图" if kind == "scene_ref" else "道具参考图"
        url = str(drop.get("url") or "") or "无 URL"
        findings.append({
            "severity": "warning",
            "field": subject,
            "message": f"{zh}未随本镜发送：{drop.get('reason')}（{url}）",
            "proposed_fix": "参考图压到 ≤5 张，或把该场景/道具写进提示词正文",
        })
    return findings


def agnes_plan_adapter_refs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """把最终有序表翻成 adapter refs：顺序即 ``<Picture N>``，图例与占位符共用此源。

    这是 Agnes 2.5 参考编号的唯一生产者；``_adapter_refs`` 只管非 Agnes 路线。
    """
    out: list[dict[str, Any]] = []
    for entry in plan.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or "")
        row: dict[str, Any] = {
            "role": _REF_ROLES.get(kind, "参考"),
            "url": entry.get("url"),
            "kind": kind,
        }
        if kind in ("portrait", "turnaround"):
            row["type"] = "element"
            if entry.get("id") is not None:
                row["element_id"] = entry.get("id")
        elif kind in ("scene_ref", "prop", "first_frame"):
            row["type"] = "refer_image"
        if entry.get("name"):
            row["name"] = entry["name"]
        out.append(row)
    return out


def _vlm_expected(
    shot: dict[str, Any],
    registry: dict[str, dict[str, Any]],
    scene_plan: dict[str, Any] | None,
    portraits: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cids = _character_ids(shot, scene_plan)
    appearances: list[str] = []
    outfits: list[str] = []
    portrait_path = ""
    for cid in cids:
        char = registry.get(cid) or {}
        app = str(char.get("appearance") or "").strip()
        if app:
            appearances.append(app)
        outfit = str(char.get("outfit_anchor") or char.get("outfit") or "").strip()
        if outfit:
            outfits.append(outfit)
        if not portrait_path and portraits:
            hit = portraits.get(cid) or {}
            portrait_path = str(hit.get("path") or "")
    return {
        "expected": {
            "appearance": "；".join(appearances),
            "outfit": "；".join(outfits),
            "location": str(shot.get("location_id") or ""),
            "props": collect_prop_ids([shot], None, cap=8),
        },
        "portrait_path": portrait_path,
    }


def _needs_agnes_refine(report: dict[str, Any] | None, expected: float) -> bool:
    data = report if isinstance(report, dict) else {}
    if _critical_fail(data):
        return True
    try:
        duration = float(data.get("duration_seconds"))
    except (TypeError, ValueError):
        return False
    return expected > 0 and duration < expected * 0.8


def _media_item(
    *,
    item_id: str,
    kind: str,
    path: str,
    scene_id: str,
    shot_id: str,
    provider: str,
    url: str = "",
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    row = {
        "id": item_id,
        "kind": kind,
        "path": path,
        "scene_id": scene_id,
        "shot_id": shot_id,
        "provider": provider,
    }
    if url:
        row["url"] = url
    # 实测时长：供应商不保证按请求秒数返回（Agnes 2.5 请求 6s 实回 6.59s），
    # compose_planner 用它重算时间轴，避免字幕/转场按计划时长错位。
    if duration_seconds and float(duration_seconds) > 0:
        row["duration_seconds"] = round(float(duration_seconds), 3)
    # 实测分辨率：Agnes 视频 720P 实为 1280x704（非 1280x720），图片 2K 也
    # 不是 1920x1080；落盘供 compose/report 按实际像素处理，不靠猜。
    size = probe_size(path)
    if size:
        row["width"], row["height"] = size
    return row


def probe_media(path: str | Path) -> dict[str, Any]:
    """探测媒体时长/画面尺寸；失败返回全 0（不回落任何假设值）。"""
    out = {"seconds": 0.0, "width": 0, "height": 0}
    if not path or not Path(path).exists():
        return out
    try:
        from montage.compose.ffmpeg_engine import probe

        info = probe(Path(path))
    except Exception:  # noqa: BLE001
        return out
    try:
        out["seconds"] = float((info.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        out["seconds"] = 0.0
    for stream in info.get("streams") or []:
        if stream.get("codec_type") != "video":
            continue
        try:
            out["width"] = int(stream.get("width") or 0)
            out["height"] = int(stream.get("height") or 0)
        except (TypeError, ValueError):
            out["width"] = out["height"] = 0
        break
    return out


def probe_size(path: str | Path) -> tuple[int, int] | None:
    """探测画面尺寸；未知返回 None（调用方须自行决定兜底，禁止默认 1080p）。"""
    media = probe_media(path)
    if media["width"] > 0 and media["height"] > 0:
        return (media["width"], media["height"])
    return None


def probe_seconds(path: str | Path) -> float:
    """探测媒体时长（秒）；失败返回 0.0。"""
    return float(probe_media(path)["seconds"])


def _character_ids(shot: dict[str, Any], scene_plan: dict[str, Any] | None) -> list[str]:
    ids: list[str] = []
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    for sub in vd.get("subjects") or []:
        if isinstance(sub, dict) and sub.get("id") and sub["id"] not in ids:
            ids.append(str(sub["id"]))
    if ids:
        return ids
    scene_id = str(shot.get("scene_id") or "")
    for scene in (scene_plan or {}).get("scenes") or []:
        if str(scene.get("id") or "") == scene_id:
            for cid in scene.get("character_ids") or []:
                if cid and cid not in ids:
                    ids.append(str(cid))
            break
    return ids


def _declared_forms(shot: dict[str, Any]) -> dict[str, str]:
    """本镜 subjects[].form_id → {cid: form_id}（只取显式声明）。"""
    out: dict[str, str] = {}
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    for sub in vd.get("subjects") or []:
        if not isinstance(sub, dict):
            continue
        sid = str(sub.get("id") or "").strip()
        fid = str(sub.get("form_id") or "").strip()
        if sid and fid and sid not in out:
            out[sid] = fid
    return out


def _shot_character_forms(
    shot: dict[str, Any],
    scene_plan: dict[str, Any] | None,
) -> list[tuple[str, str, bool]]:
    """本镜出场 ``(character_id, form_id, has_forms)``。

    - 显式 ``subjects[].form_id`` 优先；未声明用默认形态（``default_form_id``）。
    - ``has_forms=False`` 表示 registry 未声明 ``forms[]``（旧数据/单隐式形态），
      参考解析走 cid-keyed 兼容查找。
    """
    registry = _registry_map(scene_plan)
    declared = _declared_forms(shot)
    out: list[tuple[str, str, bool]] = []
    for cid in _character_ids(shot, scene_plan):
        char = registry.get(cid) or {"id": cid}
        forms = character_forms(char)
        has_forms = bool(form_id_of(forms[0]))
        fid = declared.get(cid) or default_form_id(char)
        out.append((cid, fid, has_forms))
    return out


def _form_ref(
    by_form: dict[tuple[str, str], dict[str, Any]],
    by_cid: dict[str, dict[str, Any]],
    cid: str,
    fid: str,
    has_forms: bool,
) -> dict[str, Any] | None:
    """按声明形态取参考图。

    - 声明了 forms[]：严格按 ``(cid, form_id)``；显式形态缺失时容忍隐式
      ``(cid, "")``（可灵环忽略 forms、历史数据）。
    - 旧数据（无 forms[]）：沿用 cid-keyed「最后写入」。
    """
    if has_forms:
        hit = by_form.get((cid, fid))
        if hit is None and fid:
            hit = by_form.get((cid, ""))
        return hit
    return by_cid.get(cid) or by_form.get((cid, fid))


def _registry_map(scene_plan: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for char in (scene_plan or {}).get("character_registry") or []:
        if isinstance(char, dict) and char.get("id"):
            out[str(char["id"])] = char
    return out


def _media_exists(project_dir: str, raw: str) -> bool:
    text = str(raw or "").strip()
    if not text:
        return False
    path = Path(text)
    candidates = [path] if path.is_absolute() else ([Path(project_dir) / path, path] if project_dir else [path])
    return any(cand.is_file() for cand in candidates)


def _item_ready(project_dir: str, item: dict[str, Any] | None) -> bool:
    if not isinstance(item, dict):
        return False
    if _media_exists(project_dir, str(item.get("path") or "")):
        return True
    return str(item.get("url") or "").startswith("http")


def _ref_ready(project_dir: str, item: dict[str, Any] | None, *, require_url: bool = False) -> bool:
    """Agnes 2.5 参考图必须是 https；可灵认 https 或可装箱本地文件。"""
    if require_url:
        return isinstance(item, dict) and str(item.get("url") or "").startswith("http")
    return _item_ready(project_dir, item)


def _agnes_cast_needs_url(policy: dict[str, Any] | None = None) -> bool:
    if not _agnes_is_v25():
        return False
    loop = str((policy or {}).get("video_loop") or "").strip().lower()
    return loop in ("", "agnes")


def _cast_needs_url(policy: dict[str, Any] | None = None) -> bool:
    loop = str((policy or {}).get("video_loop") or "").strip().lower()
    if loop == "kling":
        return False
    return _agnes_cast_needs_url(policy)


def _items_of(
    manifest: dict[str, Any] | None,
    *,
    kind: str,
    shot_id: str = "",
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in (manifest or {}).get("items") or []:
        if not isinstance(item, dict) or str(item.get("kind") or "") != kind:
            continue
        if shot_id and str(item.get("shot_id") or "") != shot_id:
            continue
        if kind == "image" and "portrait" in str(item.get("id") or ""):
            continue
        out.append(item)
    return out


def shot_final_ready(
    shot: dict[str, Any],
    manifest: dict[str, Any] | None,
    project_dir: str = "",
) -> bool:
    """能否进 W0：视频镜要有 video，或非 first_frame 的静图 clip（Ken Burns）。"""
    sid = str(shot.get("shot_id") or "")
    if not sid:
        return False
    if str(shot.get("shot_kind") or "video") == "image":
        return any(_item_ready(project_dir, item) for item in _items_of(manifest, kind="image", shot_id=sid))
    if any(_item_ready(project_dir, item) for item in _items_of(manifest, kind="video", shot_id=sid)):
        return True
    for item in _items_of(manifest, kind="image", shot_id=sid):
        if "_first" in str(item.get("id") or ""):
            continue
        if _item_ready(project_dir, item):
            return True
    return False


def clips_compose_ready(
    scene_plan: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    project_dir: str = "",
) -> bool:
    shots = collect_shots(scene_plan, None)
    if not shots:
        return False
    return all(shot_final_ready(shot, manifest, project_dir) for shot in shots)


def _retry_covers(
    retry_ids: set[str],
    *,
    shot_id: str = "",
    portrait_id: str = "",
    prop_id: str = "",
    turnaround_id: str = "",
    location_id: str = "",
    form_id: str = "",
) -> bool:
    if not retry_ids:
        return False
    if shot_id and shot_id in retry_ids:
        return True
    fid = str(form_id or "").strip()
    if portrait_id and (portrait_id in retry_ids or f"portrait/{portrait_id}" in retry_ids):
        return True
    if portrait_id and fid and f"portrait/{portrait_id}:{fid}" in retry_ids:
        return True
    if turnaround_id and (
        turnaround_id in retry_ids or f"turnaround/{turnaround_id}" in retry_ids
    ):
        return True
    if turnaround_id and fid and f"turnaround/{turnaround_id}:{fid}" in retry_ids:
        return True
    if location_id and (
        location_id in retry_ids
        or f"scene_ref/{location_id}" in retry_ids
        or f"location/{location_id}" in retry_ids
    ):
        return True
    if prop_id and (prop_id in retry_ids or f"prop/{prop_id}" in retry_ids):
        return True
    return False


def _job_already_done(
    job: dict[str, Any],
    *,
    manifest: dict[str, Any] | None,
    project_dir: str,
    retry_ids: set[str],
    portraits: dict[str, dict[str, Any]],
    props: dict[str, dict[str, Any]],
    turnarounds: dict[str, dict[str, Any]] | None = None,
    scene_refs: dict[str, dict[str, Any]] | None = None,
    frames_only: bool = False,
    require_url: bool = False,
    force_ids: set[str] | None = None,
) -> bool:
    kind = str(job.get("kind") or "")
    if kind == "portrait":
        cid = str(job.get("character_id") or "")
        fid = str(job.get("form_id") or "")
        if _retry_covers(retry_ids, portrait_id=cid, form_id=fid):
            return False
        return _ref_ready(project_dir, portraits.get((cid, fid)), require_url=require_url)
    if kind == "turnaround":
        cid = str(job.get("character_id") or "")
        fid = str(job.get("form_id") or "")
        if _retry_covers(retry_ids, turnaround_id=cid, form_id=fid):
            return False
        return _ref_ready(project_dir, (turnarounds or {}).get((cid, fid)), require_url=require_url)
    if kind == "scene_ref":
        lid = str(job.get("location_id") or "")
        if _retry_covers(retry_ids, location_id=lid):
            return False
        return _ref_ready(project_dir, (scene_refs or {}).get(lid), require_url=require_url)
    if kind == "prop":
        pid = str(job.get("prop_id") or "")
        if _retry_covers(retry_ids, prop_id=pid):
            return False
        return _ref_ready(project_dir, props.get(pid), require_url=require_url)
    sid = str(job.get("shot_id") or "")
    force = {str(x) for x in (force_ids or []) if x}
    if kind == "video":
        if _retry_covers(retry_ids, shot_id=sid) or sid in force:
            return False
        return any(_item_ready(project_dir, item) for item in _items_of(manifest, kind="video", shot_id=sid))
    if kind == "first_frame":
        if (frames_only and _retry_covers(retry_ids, shot_id=sid)) or sid in force:
            return False
        return any(_item_ready(project_dir, item) for item in _items_of(manifest, kind="image", shot_id=sid))
    if _retry_covers(retry_ids, shot_id=sid) or sid in force:
        return False
    return False


def _spoken_skip_portraits(
    scene_plan: dict[str, Any] | None,
    script: dict[str, Any] | None,
    format_card: dict[str, Any] | None,
    bible: dict[str, Any] | None = None,
) -> bool:
    from montage.tools.script_validator import is_spoken_mode

    doc = dict(bible or script or {})
    if not doc.get("characters"):
        doc["characters"] = list((scene_plan or {}).get("character_registry") or [])
    if not doc.get("playbook"):
        doc["playbook"] = str(
            (bible or {}).get("playbook")
            or (scene_plan or {}).get("playbook")
            or ""
        )
    return is_spoken_mode(bible=doc, format_card=format_card)


def _ref_index(
    manifest: dict[str, Any] | None,
    kind: str,
    id_key: str,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for ref in (manifest or {}).get("reference_assets") or []:
        if not isinstance(ref, dict) or str(ref.get("kind") or "") != kind:
            continue
        kid = str(ref.get(id_key) or "")
        if kid and (ref.get("path") or ref.get("url")):
            out[kid] = ref
    return out


def _portrait_index(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return _ref_index(manifest, "portrait", "character_id")


def _turnaround_index(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return _ref_index(manifest, "turnaround", "character_id")


def _ref_index_by_form(
    manifest: dict[str, Any] | None,
    kind: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    """按 (character_id, form_id) 索引某 kind 的参考图；form_id 缺省为 ""。"""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for ref in (manifest or {}).get("reference_assets") or []:
        if not isinstance(ref, dict) or str(ref.get("kind") or "") != kind:
            continue
        cid = str(ref.get("character_id") or "")
        if not cid or not (ref.get("path") or ref.get("url")):
            continue
        out[(cid, str(ref.get("form_id") or ""))] = ref
    return out


def _portrait_refs_by_form(
    manifest: dict[str, Any] | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    return _ref_index_by_form(manifest, "portrait")


def _turnaround_refs_by_form(
    manifest: dict[str, Any] | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    return _ref_index_by_form(manifest, "turnaround")


def _scene_ref_index(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    by_loc = _ref_index(manifest, "scene_ref", "location_id")
    if by_loc:
        return by_loc
    return _ref_index(manifest, "scene_ref", "scene_id")


def _prop_index(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return _ref_index(manifest, "prop", "prop_id")


def _char_for_prompt(char: dict[str, Any]) -> dict[str, Any]:
    row = dict(char)
    if not str(row.get("outfit_anchor") or "").strip():
        row["outfit_anchor"] = str(row.get("outfit") or "")
    return row


def _append_note(prompt: str, note: str) -> str:
    text = str(note or "").strip()
    if not text or not prompt:
        return prompt
    return f"{prompt}。修正：{text}"


def _location_scene(loc: dict[str, Any]) -> dict[str, Any]:
    lid = str(loc.get("id") or loc.get("name") or "").strip()
    desc = str(
        loc.get("sensory") or loc.get("appearance") or loc.get("name") or lid
    ).strip()
    note = str(loc.get("cast_note") or "").strip()
    parts = [p for p in (desc, "无人空镜，不锁机位", note) if p]
    return {"id": lid, "description": "，".join(parts)}


def collect_cast_jobs(
    bible: dict[str, Any] | None,
    *,
    scene_plan: dict[str, Any] | None = None,
    video_loop: str = "",
    sample_shot_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """根据 bible 列出定妆作业：全身照、四视图、空镜、道具白底。

    可灵环：忽略 skip_turnaround（一张拼板含四视图），有 scene_plan 时按出场过滤。
    """
    loop = str(video_loop or "").strip().lower()
    kling = loop == "kling"
    allow_chars: set[str] | None = None
    allow_locs: set[str] | None = None
    allow_props: set[str] | None = None
    if kling and isinstance(scene_plan, dict):
        shots = collect_shots(scene_plan, None)
        if shots:
            wanted: set[str] | None = None
            if sample_shot_ids:
                origins = [str(x) for x in sample_shot_ids if x]
                wanted = set(origins)
                ordered = [str(s.get("shot_id") or "") for s in shots]
                # 只从最早点名镜并入下一镜；调用方已传入窗口时不再扩到第三镜。
                for sid in origins[:1]:
                    if sid in ordered:
                        idx = ordered.index(sid)
                        if idx + 1 < len(ordered) and ordered[idx + 1]:
                            wanted.add(ordered[idx + 1])
                shots = [s for s in shots if str(s.get("shot_id") or "") in wanted]
            allow_chars = set()
            allow_locs = set()
            allow_props = set(collect_prop_ids(shots, bible, cap=_MAX_PROPS))
            for shot in shots:
                for cid in _character_ids(shot, scene_plan):
                    allow_chars.add(cid)
                speaker = str(shot.get("speaker_id") or "").strip()
                if speaker:
                    allow_chars.add(speaker)
                lid = str(shot.get("location_id") or "").strip()
                if lid:
                    allow_locs.add(lid)
    jobs: list[dict[str, Any]] = []
    for char in (bible or {}).get("characters") or []:
        if not isinstance(char, dict):
            continue
        cid = str(char.get("id") or "").strip()
        if not cid:
            continue
        if allow_chars is not None and cid not in allow_chars:
            continue
        if kling:
            # 可灵环忽略 forms[]：单隐式形态出一张 look_sheet（一张拼板已含四视图）。
            jobs.append({
                "kind": "portrait",
                "subject": f"portrait/{cid}",
                "character_id": cid,
                "form_id": "",
                "form": {"id": ""},
                "category": "image_generation",
            })
            continue
        for form in character_forms(char):
            fid = form_id_of(form)
            jobs.append({
                "kind": "portrait",
                "subject": form_subject("portrait", cid, fid),
                "character_id": cid,
                "form_id": fid,
                "form": form,
                "category": "image_generation",
            })
            if effective_skip_turnaround(char, form):
                continue
            jobs.append({
                "kind": "turnaround",
                "subject": form_subject("turnaround", cid, fid),
                "character_id": cid,
                "form_id": fid,
                "form": form,
                "category": "image_generation",
            })
    for loc in (bible or {}).get("locations") or []:
        if not isinstance(loc, dict):
            continue
        lid = str(loc.get("id") or loc.get("name") or "").strip()
        if not lid:
            continue
        if allow_locs is not None and lid not in allow_locs:
            continue
        jobs.append({
            "kind": "scene_ref",
            "subject": f"scene_ref/{lid}",
            "location_id": lid,
            "category": "image_generation",
        })
    n_props = 0
    for prop in (bible or {}).get("props") or []:
        pid = ""
        if isinstance(prop, dict):
            pid = str(prop.get("id") or prop.get("name") or "").strip()
        elif isinstance(prop, str):
            pid = prop.strip()
        if not pid:
            continue
        if allow_props is not None and pid not in allow_props:
            continue
        jobs.append({
            "kind": "prop",
            "subject": f"prop/{pid}",
            "prop_id": pid,
            "category": "image_generation",
        })
        n_props += 1
        if n_props >= _MAX_PROPS:
            break
    return jobs


def _cast_ref_for_job(job: dict[str, Any], manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    kind = str(job.get("kind") or "")
    fid = str(job.get("form_id") or "")
    if kind == "portrait":
        key = (str(job.get("character_id") or ""), fid)
        return _portrait_refs_by_form(manifest).get(key)
    if kind == "turnaround":
        key = (str(job.get("character_id") or ""), fid)
        return _turnaround_refs_by_form(manifest).get(key)
    if kind == "scene_ref":
        return _scene_ref_index(manifest).get(str(job.get("location_id") or ""))
    if kind == "prop":
        return _prop_index(manifest).get(str(job.get("prop_id") or ""))
    return None


def first_frame_item(
    shot: dict[str, Any],
    manifest: dict[str, Any] | None,
    project_dir: str = "",
) -> dict[str, Any] | None:
    """该镜已落盘的首帧静图（任意 kind=image + shot_id，含 *_first）。"""
    sid = str(shot.get("shot_id") or "")
    if not sid:
        return None
    for item in reversed(_items_of(manifest, kind="image", shot_id=sid)):
        if _item_ready(project_dir, item):
            return item
    return None


def frames_missing(
    scene_plan: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    project_dir: str,
    retry_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """尚未就绪（或被 retry 点名）的首帧。"""
    retry_ids = retry_ids or set()
    missing: list[dict[str, Any]] = []
    for shot in collect_shots(scene_plan, None):
        sid = str(shot.get("shot_id") or "")
        if not sid:
            continue
        if _retry_covers(retry_ids, shot_id=sid) or not first_frame_item(shot, manifest, project_dir):
            missing.append(shot)
    return missing


def cast_missing(
    bible: dict[str, Any] | None,
    manifest: dict[str, Any] | None,
    project_dir: str,
    retry_ids: set[str] | None = None,
    *,
    require_url: bool = False,
    scene_plan: dict[str, Any] | None = None,
    video_loop: str = "",
    sample_shot_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """尚未就绪（或被 retry 点名）的定妆作业。"""
    retry_ids = retry_ids or set()
    missing: list[dict[str, Any]] = []
    for job in collect_cast_jobs(
        bible,
        scene_plan=scene_plan,
        video_loop=video_loop,
        sample_shot_ids=sample_shot_ids,
    ):
        kind = str(job.get("kind") or "")
        fid = str(job.get("form_id") or "")
        force = False
        if kind == "portrait":
            force = _retry_covers(
                retry_ids, portrait_id=str(job.get("character_id") or ""), form_id=fid,
            )
        elif kind == "turnaround":
            force = _retry_covers(
                retry_ids, turnaround_id=str(job.get("character_id") or ""), form_id=fid,
            )
        elif kind == "scene_ref":
            force = _retry_covers(retry_ids, location_id=str(job.get("location_id") or ""))
        elif kind == "prop":
            force = _retry_covers(retry_ids, prop_id=str(job.get("prop_id") or ""))
        if force or not _ref_ready(
            project_dir, _cast_ref_for_job(job, manifest), require_url=require_url,
        ):
            missing.append(job)
    return missing


def _upsert_ref(
    refs: list[dict[str, Any]],
    new_ref: dict[str, Any],
    *,
    kind: str,
    id_key: str,
    id_val: str,
) -> None:
    """写入 reference_assets：有 id 时只按 id 匹配（form 感知），否则按 kind+id_key 回落。"""
    rid = str(new_ref.get("id") or "")
    for idx, ref in enumerate(refs):
        if not isinstance(ref, dict):
            continue
        if rid:
            if str(ref.get("id") or "") == rid:
                refs[idx] = new_ref
                return
            continue
        if str(ref.get("kind") or "") == kind and str(ref.get(id_key) or "") == id_val:
            refs[idx] = new_ref
            return
    refs.append(new_ref)


def _upsert_item(items: list[dict[str, Any]], row: dict[str, Any]) -> None:
    """写入媒体行：删除同 id 全部旧行后追加到末尾（last-wins）。

    消费方一律按末尾取最新（compose_planner 用 ``videos[-1]``，first_frame_item
    用 ``reversed(items)``），故重抽后的新行必须落在末尾，否则会被历史重复行反噬。
    """
    rid = str(row.get("id") or "")
    if rid:
        items[:] = [
            item
            for item in items
            if not (isinstance(item, dict) and str(item.get("id") or "") == rid)
        ]
    items.append(row)


def dedupe_items_by_id(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 id 去重：保留最后一次出现的行（最新），顺序按首次出现。

    仅处理 id 非空的 dict；无 id 或非 dict 的行原样保留。用于旧项目 manifest 自愈。
    """
    out: list[dict[str, Any]] = []
    first_pos: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        rid = str(item.get("id") or "")
        if not rid:
            out.append(item)
            continue
        if rid in first_pos:
            out[first_pos[rid]] = item
        else:
            first_pos[rid] = len(out)
            out.append(item)
    return out


def _script_prop_map(script: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for prop in (script or {}).get("props") or []:
        if isinstance(prop, dict) and (prop.get("id") or prop.get("name")):
            out[str(prop.get("id") or prop.get("name"))] = prop
        elif isinstance(prop, str) and prop.strip():
            out[prop.strip()] = {"id": prop.strip(), "name": prop.strip(), "appearance": prop.strip()}
    return out


def collect_prop_ids(
    shots: list[dict[str, Any]],
    script: dict[str, Any] | None = None,
    *,
    cap: int = _MAX_PROPS,
) -> list[str]:
    """镜头 objects / audio_prompt 引用到的 prop_id，去重后全片上限 cap。"""
    known = _script_prop_map(script)
    ids: list[str] = []

    def add(raw: Any) -> None:
        pid = str(raw or "").strip()
        if not pid or pid in ids:
            return
        if known and pid not in known:
            return
        ids.append(pid)

    for shot in shots:
        vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
        for obj in vd.get("objects") or []:
            if isinstance(obj, dict):
                add(obj.get("id") or obj.get("prop_id") or obj.get("name"))
            else:
                add(obj)
        ap = shot.get("audio_prompt") if isinstance(shot.get("audio_prompt"), dict) else {}
        for key in ("prop_ids", "props"):
            for item in ap.get(key) or []:
                if isinstance(item, dict):
                    add(item.get("id") or item.get("prop_id"))
                else:
                    add(item)
        for extra in shot.get("prop_ids") or []:
            add(extra)
    return ids[:cap]


def resolve_shot_refs(
    shot: dict[str, Any],
    manifest: dict[str, Any] | None,
    script: dict[str, Any] | None = None,
    scene_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按 character_ids / scene_id / prop 引用收集 reference_assets。

    turnaround（四视图网格）不进参考：网格布局会被图生图/参考生图复制，
    首帧出现重复人物；人物身份已由 portrait 承载。仅 _identity_http_refs
    （视频 identity 参考，_agnes_flash_images 按 rank 自行去网格化）经
    use_turnaround=True 取回。
    """
    refs: list[dict[str, Any]] = []
    include_turnaround = bool(shot.get("_include_turnaround_refs"))
    portraits = _portrait_index(manifest)
    turnarounds = _turnaround_index(manifest)
    portraits_by_form = _portrait_refs_by_form(manifest)
    turnarounds_by_form = _turnaround_refs_by_form(manifest)
    props = _prop_index(manifest)
    seen: set[str] = set()
    for cid, fid, has_forms in _shot_character_forms(shot, scene_plan):
        hit_t = (
            _form_ref(turnarounds_by_form, turnarounds, cid, fid, has_forms)
            if include_turnaround
            else None
        )
        hit_p = _form_ref(portraits_by_form, portraits, cid, fid, has_forms)
        for hit in (hit_t, hit_p):
            if hit and hit.get("id") not in seen:
                refs.append(hit)
                seen.add(str(hit.get("id") or cid))
    scene_id = str(shot.get("scene_id") or "")
    location_id = str(shot.get("location_id") or "")
    if not location_id and scene_plan:
        for scene in scene_plan.get("scenes") or []:
            if isinstance(scene, dict) and str(scene.get("id") or "") == scene_id:
                location_id = str(scene.get("location_id") or "").strip()
                break
    for ref in (manifest or {}).get("reference_assets") or []:
        if not isinstance(ref, dict) or ref.get("kind") != "scene_ref":
            continue
        rid = str(ref.get("id") or "")
        if rid in seen:
            continue
        if location_id and str(ref.get("location_id") or "") == location_id:
            refs.append(ref)
            seen.add(rid)
            continue
        if scene_id and str(ref.get("scene_id") or "") == scene_id:
            refs.append(ref)
            seen.add(rid)
    for pid in collect_prop_ids([shot], script, cap=_MAX_PROPS):
        hit = props.get(pid)
        if hit and str(hit.get("id") or pid) not in seen:
            refs.append(hit)
            seen.add(str(hit.get("id") or pid))
    return refs


def _binding_ref_row(
    *,
    ref_id: str = "",
    kind: str = "",
    role: str = "",
    url: str = "",
    path: str = "",
    form_id: str = "",
    character_id: str = "",
    location_id: str = "",
    prop_id: str = "",
    picture_index: int | None = None,
    source: str = "sent_plan",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": ref_id,
        "kind": kind,
        "role": role,
        "picture_index": picture_index,
        "source": source,
    }
    for key, val in (
        ("form_id", form_id),
        ("character_id", character_id),
        ("location_id", location_id),
        ("prop_id", prop_id),
        ("path", path),
        ("url", url),
    ):
        if val:
            row[key] = val
    return row


def _binding_refs_from_plan(
    plan: dict[str, Any] | None,
    by_id: dict[str, dict[str, Any]],
    *,
    source: str = "sent_plan",
) -> list[dict[str, Any]]:
    """把实发有序表转成绑定行；``picture_index`` 取实发 ``index``（不重算）。"""
    out: list[dict[str, Any]] = []
    for entry in (plan or {}).get("entries") or []:
        if not isinstance(entry, dict):
            continue
        rid = str(entry.get("id") or "")
        kind = str(entry.get("kind") or "")
        base = by_id.get(rid) or {}
        if kind == "first_frame":
            role = "first_frame"
        elif entry.get("identity"):
            role = "identity"
        else:
            role = "support"
        try:
            idx: int | None = int(entry.get("index"))
        except (TypeError, ValueError):
            idx = None
        out.append(_binding_ref_row(
            ref_id=rid or str(entry.get("url") or ""),
            kind=kind,
            role=role,
            url=str(entry.get("url") or base.get("url") or ""),
            path=str(base.get("path") or ""),
            form_id=str(entry.get("form_id") or base.get("form_id") or ""),
            character_id=str(entry.get("character_id") or base.get("character_id") or ""),
            location_id=str(entry.get("location_id") or base.get("location_id") or ""),
            prop_id=str(entry.get("prop_id") or base.get("prop_id") or ""),
            picture_index=idx,
            source=source,
        ))
    return out


def _cast_binding_map(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """定妆/场景/道具参考的归属快照（按 ref id 整条替换）。"""
    out: dict[str, dict[str, Any]] = {}
    for ref in (manifest or {}).get("reference_assets") or []:
        if not isinstance(ref, dict):
            continue
        kind = str(ref.get("kind") or "")
        if kind not in ("portrait", "turnaround", "scene_ref", "prop"):
            continue
        rid = str(ref.get("id") or "")
        if not rid:
            continue
        out[rid] = _binding_ref_row(
            ref_id=rid,
            kind=kind,
            role="identity" if kind in ("portrait", "turnaround") else "support",
            url=str(ref.get("url") or ""),
            path=str(ref.get("path") or ""),
            form_id=str(ref.get("form_id") or ""),
            character_id=str(ref.get("character_id") or ""),
            location_id=str(ref.get("location_id") or ""),
            prop_id=str(ref.get("prop_id") or ""),
            picture_index=None,
            source="manifest",
        )
    return out


def build_image_bindings(
    shots: list[dict[str, Any]] | None,
    manifest: dict[str, Any] | None,
    scene_plan: dict[str, Any] | None = None,
    script: dict[str, Any] | None = None,
    *,
    project_dir: str = "",
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """逐镜冻结 图↔场景文字↔``<Picture N>``。

    有实发 plan（``shot["_agnes_ref_plan"]`` / ``["_agnes_segment_plan"]``，仅
    ``_execute_jobs`` 内存里有）时 ``picture_index`` 取实发 ``index``；
    无 plan（非 Agnes / 另一次调用的回填）时回落 ``resolve_shot_refs`` 顺序，
    ``picture_index=None`` + ``source="recomputed"``——绝不用重算序号冒充真序号。
    ``cast`` 段始终按 manifest 全量快照刷新，供 cast-only 调用写。
    """
    existing = existing if isinstance(existing, dict) else {}
    by_id = {
        str(r.get("id") or ""): r
        for r in (manifest or {}).get("reference_assets") or []
        if isinstance(r, dict) and r.get("id")
    }
    scene_loc: dict[str, str] = {}
    scene_shot_loc: dict[str, str] = {}
    for scene in (scene_plan or {}).get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        sid_key = str(scene.get("id") or "")
        if not sid_key:
            continue
        scene_loc[sid_key] = str(scene.get("location_id") or "")
        for row in scene.get("shots") or []:
            if isinstance(row, dict) and row.get("shot_id"):
                scene_shot_loc[str(row["shot_id"])] = str(row.get("location_id") or "")

    prev_shots = existing.get("shots") if isinstance(existing.get("shots"), dict) else {}
    shots_out: dict[str, Any] = dict(prev_shots)
    for shot in shots or []:
        if not isinstance(shot, dict):
            continue
        sid = str(shot.get("shot_id") or "")
        if not sid:
            continue
        vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
        seg_plan = (
            shot.get("_agnes_segment_plan")
            if isinstance(shot.get("_agnes_segment_plan"), dict)
            else {}
        )
        ref_plan = (
            shot.get("_agnes_ref_plan")
            if isinstance(shot.get("_agnes_ref_plan"), dict)
            else {}
        )
        seg_rows = [
            seg for seg in (seg_plan.get("segments") or []) if isinstance(seg, dict)
        ]

        if ref_plan or seg_rows:
            rows = _binding_refs_from_plan(ref_plan, by_id)
            segments = [
                {
                    "index": i + 1,
                    "seconds": float(seg.get("seconds") or 0),
                    "bridge": bool(seg.get("bridge")),
                    "refs": _binding_refs_from_plan(seg.get("_plan"), by_id),
                }
                for i, seg in enumerate(seg_rows)
            ]
        else:
            rows = []
            for ref in resolve_shot_refs(shot, manifest, script, scene_plan):
                if not isinstance(ref, dict):
                    continue
                kind = str(ref.get("kind") or "")
                rows.append(_binding_ref_row(
                    ref_id=str(ref.get("id") or ""),
                    kind=kind,
                    role="identity" if kind in ("portrait", "turnaround") else "support",
                    url=str(ref.get("url") or ""),
                    path=str(ref.get("path") or ""),
                    form_id=str(ref.get("form_id") or ""),
                    character_id=str(ref.get("character_id") or ""),
                    location_id=str(ref.get("location_id") or ""),
                    prop_id=str(ref.get("prop_id") or ""),
                    picture_index=None,
                    source="recomputed",
                ))
            segments = []

        first = first_frame_item(shot, manifest, project_dir) if project_dir else None
        video: dict[str, Any] | None = None
        for item in reversed(_items_of(manifest, kind="video", shot_id=sid)):
            if _item_ready(project_dir, item):
                video = {"path": str(item.get("path") or ""), "url": str(item.get("url") or "")}
                break

        scene_id = str(shot.get("scene_id") or "")
        shots_out[sid] = {
            "scene_id": scene_id or None,
            "location_id": (
                scene_shot_loc.get(sid)
                or str(shot.get("location_id") or "")
                or scene_loc.get(scene_id)
                or None
            ),
            "location_sensory": str(shot.get("location_sensory") or ""),
            "environment": str(vd.get("environment") or ""),
            "character_forms": [
                {"character_id": cid, "form_id": fid}
                for cid, fid, _has_forms in _shot_character_forms(shot, scene_plan)
            ],
            "prop_ids": collect_prop_ids([shot], script),
            "refs": rows,
            "segments": segments,
            "first_frame": (
                {
                    "path": str(first.get("path") or ""),
                    "url": str(first.get("url") or ""),
                    "source": "manifest",
                }
                if first
                else None
            ),
            "video": video,
        }
    return {"version": 1, "shots": shots_out, "cast": _cast_binding_map(manifest)}


def merge_image_bindings(
    base: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """顶层 ``shots`` / ``cast`` 逐键合并；同一 key **整条替换**（不做列表深合并）。"""
    out: dict[str, Any] = dict(base) if isinstance(base, dict) else {}
    out["version"] = int(
        (incoming or {}).get("version") or out.get("version") or 1
    )
    for key in ("shots", "cast"):
        cur = out.get(key)
        cur = dict(cur) if isinstance(cur, dict) else {}
        new = (incoming or {}).get(key) if isinstance(incoming, dict) else None
        if isinstance(new, dict):
            for k, v in new.items():
                cur[str(k)] = v
        out[key] = cur
    return out


def reconcile_image_bindings(
    existing: dict[str, Any] | None,
    *,
    shots: list[dict[str, Any]] | None,
    manifest: dict[str, Any] | None,
    scene_plan: dict[str, Any] | None = None,
    script: dict[str, Any] | None = None,
    project_dir: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """回填缺失镜 + 对账集合漂移；**保留已有 ``picture_index``**（按 id 对齐）。

    回填（无内存 plan）算出的序号不可信，只用旧绑定里的真序号补回；
    ``source="sent_plan"`` 的行是真值，永不被覆盖。集合变化只出 finding，不改序号。
    """
    existing = existing if isinstance(existing, dict) else {}
    prev_shots = existing.get("shots") if isinstance(existing.get("shots"), dict) else {}
    fresh = build_image_bindings(
        shots, manifest, scene_plan, script,
        project_dir=project_dir, existing=existing,
    )
    findings: list[dict[str, Any]] = []
    for sid, row in (fresh.get("shots") or {}).items():
        if not isinstance(row, dict):
            continue
        prev = prev_shots.get(sid)
        if not isinstance(prev, dict):
            continue
        prev_idx = {
            str(r.get("id") or ""): r.get("picture_index")
            for r in prev.get("refs") or []
            if isinstance(r, dict) and r.get("picture_index") is not None
        }
        for ref in row.get("refs") or []:
            if not isinstance(ref, dict) or ref.get("source") != "recomputed":
                continue
            keep = prev_idx.get(str(ref.get("id") or ""))
            if keep is not None:
                ref["picture_index"] = keep
        prev_ids = sorted(
            str(r.get("id") or "") for r in prev.get("refs") or [] if isinstance(r, dict)
        )
        new_ids = sorted(
            str(r.get("id") or "") for r in row.get("refs") or [] if isinstance(r, dict)
        )
        if prev_ids and prev_ids != new_ids:
            findings.append({
                "severity": "warning",
                "field": f"image_bindings/{sid}",
                "message": (
                    f"{sid} 绑定集合与当前参考不一致"
                    f"（旧 {len(prev_ids)} → 新 {len(new_ids)}）"
                ),
                "proposed_fix": "已刷新绑定；如需重出图再 --retry <shot_id> --resume",
            })
    merged = merge_image_bindings(existing, fresh)
    return merged, findings


def _prop_prompt(prop: dict[str, Any]) -> str:
    name = str(prop.get("name") or prop.get("id") or "道具")
    appearance = str(prop.get("appearance") or name)
    return (
        f"道具白底静物照：{name}，{appearance}；纯白背景，物体孤立居中，"
        "静物题材，影棚均匀布光，画面无人物无手部，非手持拍摄，细节清晰"
    )


def _media_path(result: ToolResult, fallback: str) -> str:
    data = result.data if isinstance(result.data, dict) else {}
    for key in ("output", "local_path", "path", "cached_path"):
        val = data.get(key)
        if val:
            return str(val)
    if fallback and Path(fallback).exists():
        return fallback
    return fallback


def _media_url(result: ToolResult) -> str:
    data = result.data if isinstance(result.data, dict) else {}
    for key in ("url", "image_url", "video_url"):
        val = data.get(key)
        if val:
            return str(val)
    urls = data.get("urls")
    if isinstance(urls, list) and urls:
        return str(urls[0])
    return ""


def _skip_pacing() -> bool:
    if os.environ.get("MONTAGE_SKIP_PACING") == "1":
        return True
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _critical_fail(report: dict[str, Any]) -> bool:
    if report.get("skipped") or report.get("ok"):
        return False
    return any(i.get("severity") == "critical" for i in (report.get("issues") or []))
