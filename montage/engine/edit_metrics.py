"""edit_metrics — 剪辑导演的客观量规（v8.2 P0-4；纯函数、零 LLM、零光流/显著性）。

素材库是「分别独立生成的镜」拼起来的，不存在混剪 match-cut 才有的物理约束。
所以量规只采纳 DIRECT 中在长片自动生成场景下**真能算**的四条 + 一条身份维度：

| 指标 | 含义 | 阈值 |
|---|---|---|
| ``m1`` | prompt 相关性：本镜提示词是否带齐身份/场景/道具锚（每锚组至少一个原子词命中）；有 VLM 结果时并计入其 ok 判定 | 1.0 |
| ``m2`` | 语义连贯：相邻镜必须有叙事锚（同场/同章/共享角色/共享道具），否则是「断裂硬切」 | 0.9 |
| ``m5`` | beat-cut 同步：切点落在 BGM 拍网格容差内的比例 | 0.7 |
| ``m6`` | 能量-视觉对应：切点密度与音频能量正相关（Pearson r） | 0.5 |
| ``drift`` | 身份漂移：``identity_memory`` 里漂移/已触发重拍定妆的角色数 | 0 |

**刻意不做 ``m3``（运动连续/光流）与 ``m4``（构图一致/显著性）**：那是混剪
match-cut 专属指标，要求相邻两镜的光流场与主体位置对得上；本管线的镜是分别
独立生成的，不存在这个约束。硬算 m3/m4 等于为一个不存在的目标烧光流 + 显著
性检测的算力（30fps × 2h 的光流非常慢），且与 ``edit_advisor`` 的「叙事角色
驱动转场」架构冲突。``DELETED_METRICS`` 显式登记，测试锁死不回流。

每条 finding 都带 ``metric`` / ``value`` / ``threshold``——4 轮 review 有据可依，
review_log 里不再是主观感受。

``skipped=True`` 的指标 ``value`` 记 0.0（没测到就不冒充满分），判定仍看 ``pass``
（跳过不算不达标，只降证据强度）。

**P0-5 自我满足（circular）标注**：P0-5 ``energy_wave`` 用拍网格 + 能量波**生成**切点后，
``m5``（切点吸拍）与 ``m6``（密度∝能量）就成了「自己出的题自己答对」——DP 的约束本就是
吸拍 + 密度跟随能量。此时两条指标仍照算照显示，但打 ``circular=true``：
**不能当质量证据，也不能拿它给 REVISE/PASS 加分**。外部供给的拍网格（曲库 bpm）不算
circular。``metric_summary`` 里 circular 指标带 ``c`` 标（``m5c1.0``）。
"""

from __future__ import annotations

import math
import re
from typing import Any

# 指标登记表：id → 元信息。阈值是「下限」语义（越大越好），drift 例外（越小越好）。
METRIC_SPECS: dict[str, dict[str, Any]] = {
    "m1": {
        "name": "prompt 相关性",
        "threshold": 1.0,
        "unit": "ratio",
        "direction": "higher",
        "source": "shot_prompts.video_prompt + character_registry + vlm_review",
    },
    "m2": {
        "name": "语义连贯",
        "threshold": 0.9,
        "unit": "ratio",
        "direction": "higher",
        "source": "scene_plan + chapters",
    },
    "m5": {
        "name": "beat-cut 同步",
        "threshold": 0.7,
        "unit": "ratio",
        "direction": "higher",
        "source": "soundtrack.events[].bpm + 切点帧号",
    },
    "m6": {
        "name": "能量-视觉对应",
        "threshold": 0.5,
        "unit": "pearson_r",
        "direction": "higher",
        "source": "soundtrack 事件能量（或 rms 轮廓）× 切点密度",
    },
    "drift": {
        "name": "身份漂移",
        "threshold": 0.0,
        "unit": "count",
        "direction": "lower",
        "source": "identity_memory.json",
    },
}

# m3（运动连续/光流）、m4（构图一致/显著性）：混剪 match-cut 专属，本管线不采。
# 显式登记以防后续「顺手补上」——见模块 docstring。
DELETED_METRICS: tuple[str, ...] = ("m3", "m4")

# 切点容差（帧）：|Δ| ≤ 该值算落在拍上。120bpm@30fps 一拍 15 帧，2 帧 ≈ 1/7 拍。
DEFAULT_BEAT_TOLERANCE_FRAMES = 2

# 小于该镜数的片子不做相关性/连贯统计（样本太小，噪声淹没信号）。
MIN_SHOTS_FOR_CORRELATION = 4

# 单指标最多吐多少条 finding（导演审读要的是最差的几条，不是全量刷屏）。
MAX_FINDINGS_PER_METRIC = 8

_TOKEN_SPLIT_RE = re.compile(r"[，,、；;。.！!？?：:·\s/|（）()\[\]【】“”\"'’]+")


def metric_specs() -> dict[str, dict[str, Any]]:
    """指标登记表副本（防调用方改坏常量）。"""
    return {key: dict(val) for key, val in METRIC_SPECS.items()}


def metric_ids() -> tuple[str, ...]:
    return tuple(METRIC_SPECS)


def spec_of(metric: str) -> dict[str, Any]:
    return dict(METRIC_SPECS.get(str(metric or ""), {}))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _text(value: Any) -> str:
    return str(value or "").strip()


def anchor_tokens(text: Any, *, min_len: int = 2) -> list[str]:
    """把中文锚点串切成原子词（去标点/空白），过滤过短碎片。

    「黑发齐耳，左眉一道旧疤」→ ["黑发齐耳", "左眉一道旧疤"]；整串子串匹配太
    脆（提示词不可能逐字复述），按原子词命中才稳。
    """
    out: list[str] = []
    for raw in _TOKEN_SPLIT_RE.split(_text(text)):
        token = raw.strip()
        if len(token) >= min_len and token not in out:
            out.append(token)
    return out


def _hit_any(tokens: list[str], haystack: str) -> bool:
    return any(token in haystack for token in tokens)


# ---------------------------------------------------------------------------
# m1：prompt 相关性
# ---------------------------------------------------------------------------


def shot_anchor_groups(
    shot: dict[str, Any],
    *,
    registry: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """本镜「必须在提示词里出现」的锚组：每个角色/场景/道具一组。"""
    groups: list[dict[str, Any]] = []
    reg = registry or {}
    # scene_plan.character_registry 是 **list[dict]**，而调用方（prompt_contract /
    # shot_runner._prompt_inputs）两种形状都会传进来。此前只按 dict 处理，一旦
    # 走真机生成路径就会 AttributeError: 'list' object has no attribute 'get'，
    # 表现为 shot_runner 报"提示词失败"→ 该镜进 retryable → produce 报"有坏镜未通过"。
    if isinstance(reg, list):
        reg = {
            str(row.get("id")): row
            for row in reg
            if isinstance(row, dict) and row.get("id")
        }
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    for sub in vd.get("subjects") or []:
        if not isinstance(sub, dict):
            continue
        cid = _text(sub.get("id") or sub.get("character_id"))
        if not cid:
            continue
        char = reg.get(cid) or {}
        appearance = _text(sub.get("appearance_anchor")) or _text(char.get("appearance"))
        outfit = _text(char.get("outfit_anchor")) or _text(char.get("outfit"))
        for label, raw in (("appearance", appearance), ("outfit", outfit)):
            tokens = anchor_tokens(raw)
            if tokens:
                groups.append({"kind": f"character_{label}", "id": cid, "tokens": tokens})
    env = vd.get("environment")
    env_text = _text(env.get("description") if isinstance(env, dict) else env)
    if not env_text:
        env_text = _text(shot.get("location_id"))
    if env_text:
        groups.append({"kind": "location", "id": _text(shot.get("location_id")), "tokens": anchor_tokens(env_text)})
    # B2.5 修正：overlay 会把**全片**道具塞进每一镜的 objects，直接当锚点会要求
    # 每镜都出现曲谱/玉簪等根本不在场的道具（m1 被误判）。有 presence.props 时
    # 只按本镜在场清单取道具。
    presence = shot.get("presence") if isinstance(shot.get("presence"), dict) else {}
    declared_props = {
        _text(row.get("id"))
        for row in (presence.get("props") or [])
        if isinstance(row, dict) and _text(row.get("id"))
    }
    for obj in vd.get("objects") or []:
        raw = obj.get("appearance") if isinstance(obj, dict) else obj
        pid = _text(obj.get("id") or obj.get("prop_id") or obj.get("name")) if isinstance(obj, dict) else _text(obj)
        if declared_props and pid and pid not in declared_props:
            continue
        tokens = anchor_tokens(raw) or anchor_tokens(pid)
        if tokens:
            groups.append({"kind": "prop", "id": pid, "tokens": tokens})
    for pid in shot.get("prop_ids") or []:
        tokens = anchor_tokens(pid)
        if tokens:
            groups.append({"kind": "prop", "id": _text(pid), "tokens": tokens})
    return groups


def _vlm_row(vlm: dict[str, Any] | None, shot_id: str) -> dict[str, Any]:
    for row in (vlm or {}).get("shots") or []:
        if isinstance(row, dict) and _text(row.get("shot_id")) == shot_id:
            return row
    return {}


def m1_prompt_relevance(
    shots: list[dict[str, Any]],
    *,
    prompts: dict[str, str] | None = None,
    registry: dict[str, dict[str, Any]] | None = None,
    vlm: dict[str, Any] | None = None,
    image_bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prompt relevance with separated text, reference, and VLM evidence."""
    from montage.engine.prompt_contract import evaluate_prompt_contract

    prompt_map = prompts or {}
    binding_map = (image_bindings or {}).get("shots") or {}
    # 没有任何绑定产物 = 参考证据通道不存在（degraded/本地跑法）：标 skipped，
    # 不算 m1 不达标。有产物则逐镜照报缺参考（B1 禁静默降级）。
    reference_evidence = image_bindings is not None and bool(binding_map)
    total_groups = 0
    hit_groups = 0
    reference_required = 0
    reference_covered = 0
    reference_sent = 0
    findings: list[dict[str, Any]] = []
    detail: list[dict[str, Any]] = []
    vlm_checked = 0
    vlm_failed: list[str] = []
    vlm_skipped = True
    compensated_text_misses: list[dict[str, str]] = []
    for shot in shots:
        sid = _text(shot.get("shot_id") or shot.get("scene_id"))
        prompt = _text(prompt_map.get(sid))
        evidence = evaluate_prompt_contract(
            shot,
            prompt=prompt,
            registry=registry,
            image_binding=binding_map.get(sid) if isinstance(binding_map, dict) else None,
            vlm_row=_vlm_row(vlm, sid),
            reference_evidence_available=reference_evidence,
        )
        text = evidence["text"]
        reference = evidence["reference"]
        visual = evidence["visual_verification"]
        missing = text["missing"]
        total_groups += text["groups"]
        hit_groups += text["covered"]
        reference_required += reference["required"]
        reference_covered += reference["covered"]
        reference_sent += reference["sent"]
        row = _vlm_row(vlm, sid)
        row_skipped = bool(row.get("skipped")) if row else True
        if row and not row_skipped:
            vlm_skipped = False
            vlm_checked += 1
            if not row.get("ok", True):
                vlm_failed.append(sid)
        problems: list[str] = []
        if not prompt and text["groups"]:
            problems.append("缺 video_prompt")
        if missing:
            missing_text = "、".join(missing[:4])
            if reference["sent"] < reference["required"]:
                problems.append(f"锚组未进提示词：{missing_text}")
            else:
                compensated_text_misses.append({sid: missing_text})
        if reference["missing"] and not reference.get("skipped"):
            shown = reference["missing"][:4]
            problems.append(
                "参考缺失：" + "、".join(f"{r['kind']}/{r['id']}" for r in shown)
            )
        if evidence["forbidden_text_hits"]:
            problems.append(
                "forbidden text 进入 prompt：" + "、".join(evidence["forbidden_text_hits"][:4])
            )
        if row and not row_skipped and not row.get("ok", True):
            problems.append("VLM 未过检")
        if problems:
            findings.append({
                "severity": "warning",
                "field": f"m1/{sid}",
                "metric": "m1",
                "value": text["coverage"],
                "threshold": 1.0,
                "message": "prompt 相关性不达标：" + "；".join(problems),
                "proposed_fix": "补 character_registry/visual_details 锚点或重编提示词（改 bible 后先重编译）",
            })
        detail.append({
            "shot_id": sid,
            "groups": text["groups"],
            "missing": missing,
            "prompt_hash": evidence["prompt_hash"],
            "reference": reference,
            "visual_verification": visual,
        })
    value = (hit_groups / total_groups) if total_groups else 1.0
    reference_first = image_bindings is not None and reference_required > 0
    if reference_first and reference_sent == reference_required:
        # In reference-first mode, complete sent/recomputed references can
        # carry identity when prompt text omits an anchor.  This is explicit,
        # while a missing required reference still remains a finding.
        value = 1.0
    if vlm_failed:
        value = 0.0
    return {
        "value": round(value, 3),
        "skipped": False,
        "detail": {
            "groups": total_groups,
            "covered": hit_groups,
            "reference": {
                "required": reference_required,
                "covered": reference_covered,
                "sent": reference_sent,
                "coverage": (
                    round(reference_covered / reference_required, 3)
                    if reference_required else 1.0
                ),
                "sent_coverage": (
                    round(reference_sent / reference_required, 3)
                    if reference_required else 1.0
                ),
                "text_misses_compensated_by_refs": compensated_text_misses,
            },
            "vlm_checked": vlm_checked,
            "vlm_failed": vlm_failed,
            "vlm_skipped": vlm_skipped,
            "shots": detail[:MAX_FINDINGS_PER_METRIC * 4],
        },
        "findings": findings[:MAX_FINDINGS_PER_METRIC],
    }


# ---------------------------------------------------------------------------
# m2：语义连贯
# ---------------------------------------------------------------------------


def _scene_index(scene_plan: dict[str, Any] | None) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    scenes: dict[str, dict[str, Any]] = {}
    chapter_of: dict[str, str] = {}
    for scene in (scene_plan or {}).get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        sid = _text(scene.get("id"))
        if not sid:
            continue
        scenes[sid] = scene
        chapter_of[sid] = _text(scene.get("chapter_id"))
    return scenes, chapter_of


def _shot_character_ids(shot: dict[str, Any], scene: dict[str, Any] | None) -> set[str]:
    ids: set[str] = set()
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    for sub in vd.get("subjects") or []:
        if isinstance(sub, dict):
            token = _text(sub.get("id") or sub.get("character_id"))
        else:
            token = _text(sub)
        if token:
            ids.add(token)
    for cid in shot.get("character_ids") or []:
        if _text(cid):
            ids.add(_text(cid))
    if not ids and scene:
        for cid in scene.get("character_ids") or []:
            if _text(cid):
                ids.add(_text(cid))
    return ids


def _shot_prop_ids(shot: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    for obj in vd.get("objects") or []:
        token = _text(obj.get("id") or obj.get("prop_id") or obj.get("name")) if isinstance(obj, dict) else _text(obj)
        if token:
            ids.add(token)
    for pid in shot.get("prop_ids") or []:
        if _text(pid):
            ids.add(_text(pid))
    return ids


def _transition_contract_boundaries(
    projection: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    """Index the read-only transition projection by the incoming shot id."""
    rows = ((projection or {}).get("boundaries") or [])
    return {
        str(row.get("to_shot_id") or ""): row
        for row in rows
        if isinstance(row, dict) and str(row.get("to_shot_id") or "")
    }


def m2_semantic_coherence(
    shots: list[dict[str, Any]],
    *,
    scene_plan: dict[str, Any] | None = None,
    transition_contract_projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """语义连贯：相邻镜必须有叙事锚（同场/同章/共享角色/共享道具）。

    同场和同章是「结构锚」——换场换章本身是导演的叙事指令，允许不共享角色。
    跨场**且**跨章且不共享角色/道具 = 断裂硬切（观众不知道为什么切）。
    """
    scenes, chapter_of = _scene_index(scene_plan)
    contract_by_to_shot = _transition_contract_boundaries(
        transition_contract_projection
    )
    total = 0
    ok = 0
    findings: list[dict[str, Any]] = []
    detail: list[dict[str, Any]] = []
    for i in range(len(shots) - 1):
        cur, nxt = shots[i], shots[i + 1]
        cur_scene = _text(cur.get("scene_id"))
        nxt_scene = _text(nxt.get("scene_id"))
        same_scene = bool(cur_scene) and cur_scene == nxt_scene
        cur_chapter = chapter_of.get(cur_scene, "") or _text(cur.get("chapter_id"))
        nxt_chapter = chapter_of.get(nxt_scene, "") or _text(nxt.get("chapter_id"))
        same_chapter = bool(cur_chapter) and cur_chapter == nxt_chapter
        shared_chars = _shot_character_ids(cur, scenes.get(cur_scene)) & _shot_character_ids(nxt, scenes.get(nxt_scene))
        shared_props = _shot_prop_ids(cur) & _shot_prop_ids(nxt)
        anchors: list[str] = []
        if same_scene:
            anchors.append("同场")
        if same_chapter:
            anchors.append("同章")
        if shared_chars:
            anchors.append("同角色")
        if shared_props:
            anchors.append("同道具")
        contract_boundary = contract_by_to_shot.get(_text(nxt.get("shot_id")))
        contract_status = (
            str(contract_boundary.get("status"))
            if contract_boundary else "not_cross_scene"
        )
        contract = (contract_boundary or {}).get("contract")
        contract_decision = str((contract or {}).get("decision") or "")
        if contract_status in {"contracted", "accepted_hard_cut"}:
            anchors.append(f"transition_contract:{contract_decision}")
        total += 1
        junction = f"{_text(cur.get('shot_id'))}→{_text(nxt.get('shot_id'))}"
        if anchors:
            ok += 1
        else:
            if contract_status == "invalid_contract":
                message = (
                    f"语义断裂未解除：{junction} 的 transition contract 非法 "
                    f"（errors={','.join((contract or {}).get('errors') or [])}）"
                )
                proposed_fix = "修正 transition contract 的 decision/reason"
            elif contract_status == "mismatched_transition":
                message = (
                    f"语义断裂未解除：{junction} 声明 transition contract "
                    f"{contract_decision}，但实际执行为 {contract_boundary.get('current_transition')}"
                )
                proposed_fix = "按 transition contract 修正 compose transition，或更新 contract 决策"
            else:
                message = (
                    f"语义断裂：{cur_scene or '?'}/{junction.split('→')[0]} → "
                    f"{nxt_scene or '?'}/{junction.split('→')[-1]} 既不同场不同章，也无共享角色/道具"
                )
                proposed_fix = (
                    "添加显式 transition contract（fade/xfade/match_cut/audio_bridge/"
                    "shared_element/establishing_shot/user_accepted_hard_cut）"
                )
            findings.append({
                "severity": "warning",
                "field": f"m2/{junction}",
                "metric": "m2",
                "value": 0.0,
                "threshold": 1.0,
                "message": message,
                "contract_status": contract_status,
                "proposed_fix": proposed_fix,
            })
        detail.append({
            "junction": junction,
            "anchors": anchors,
            "contract_status": contract_status,
        })
    value = (ok / total) if total else 1.0
    return {
        "value": round(value, 3),
        "skipped": total == 0,
        "detail": {"junctions": total, "anchored": ok, "rows": detail},
        "findings": findings[:MAX_FINDINGS_PER_METRIC],
    }


# ---------------------------------------------------------------------------
# m5：beat-cut 同步
# ---------------------------------------------------------------------------


def _beat_map_grid(beat_map: dict[str, Any] | None) -> dict[str, Any] | None:
    """P0-5 ``build_beat_map`` 产物 → 拍网格（含 ``circular=True``）。不可用 → None。"""
    if not isinstance(beat_map, dict) or not beat_map.get("feasible"):
        return None
    grid = beat_map.get("grid")
    if not isinstance(grid, dict) or _num(grid.get("bpm")) <= 0:
        # 老/少见的 payload 只把 grid 放在 sources[] 里 → 取首个可用源
        grid = next(
            (s["grid"] for s in (beat_map.get("sources") or [])
             if isinstance(s, dict) and isinstance(s.get("grid"), dict) and _num(s["grid"].get("bpm")) > 0),
            None,
        )
    if not isinstance(grid, dict):
        return None
    out = dict(grid)
    out.setdefault("fps", 30.0)
    out["source"] = str(grid.get("source") or "energy_wave")
    out["circular"] = True
    out["bpm_source"] = str(beat_map.get("bpm_source") or "")
    return out


def beat_grid(
    soundtrack: dict[str, Any] | None,
    *,
    fps: float = 30.0,
    beat_map: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """拍网格：P0-5 能量波网格优先，否则从 soundtrack 的 bgm 事件取；都没有 → None。

    bpm 来自曲库元数据（``soundtrack_planner`` 的 ``hit.bpm``），无需 librosa。
    两条来路都只是**供给**：指标定义（``beat_alignment``）不变。
    """
    energy_grid = _beat_map_grid(beat_map)
    if energy_grid is not None:
        return energy_grid
    best: dict[str, Any] | None = None
    for ev in (soundtrack or {}).get("events") or []:
        if not isinstance(ev, dict) or _text(ev.get("kind")) != "bgm":
            continue
        bpm = _num(ev.get("bpm"))
        if bpm <= 0:
            continue
        candidate = {
            "bpm": bpm,
            "fps": float(fps),
            "offset_seconds": _num(ev.get("offset_seconds")),
            "start_seconds": _num(ev.get("start_seconds")),
            "frames_per_beat": (float(fps) * 60.0 / bpm),
            "asset_id": _text(ev.get("asset_id")),
            "source": "soundtrack",
        }
        if best is None or candidate["start_seconds"] < best["start_seconds"]:
            best = candidate
    return best


def beat_alignment(
    shots: list[dict[str, Any]],
    grid: dict[str, Any] | None,
    *,
    tolerance_frames: int = DEFAULT_BEAT_TOLERANCE_FRAMES,
    circular: bool = False,
) -> dict[str, Any]:
    """每个切点（镜起点，除首镜）到最近拍格的偏差（帧）。

    ``circular=True``：切点本身就是按这张网格生成的（P0-5 Bar-DP），m5 必然是满分，
    只作**回归哨兵**（防网格/帧率换算出 bug），不作剪辑质量证据。
    """
    is_circular = bool(circular or (isinstance(grid, dict) and grid.get("circular")))
    if not grid:
        return {"value": 0.0, "skipped": True, "circular": False,
                "reason": "无 bpm（soundtrack 事件未提供）", "findings": [], "detail": {}}
    fps = _num(grid.get("fps"), 30.0) or 30.0
    fpb = _num(grid.get("frames_per_beat"))
    if fpb <= 0:
        return {"value": 0.0, "skipped": True, "circular": False,
                "reason": "frames_per_beat 非法", "findings": [], "detail": {}}
    offset_frames = _num(grid.get("offset_seconds")) * fps
    total = 0
    aligned = 0
    worst: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for shot in shots[1:]:
        sid = _text(shot.get("shot_id") or shot.get("scene_id"))
        start_frames = _num(shot.get("start_seconds")) * fps
        beat_pos = (start_frames - offset_frames) / fpb
        delta_frames = abs(beat_pos - round(beat_pos)) * fpb
        total += 1
        is_ok = delta_frames <= tolerance_frames
        if is_ok:
            aligned += 1
        worst.append({"shot_id": sid, "delta_frames": round(delta_frames, 2), "aligned": is_ok})
        if not is_ok:
            findings.append({
                "severity": "warning",
                "field": f"m5/{sid}",
                "metric": "m5",
                "value": round(delta_frames, 2),
                "threshold": float(tolerance_frames),
                "message": (
                    f"切点没落在拍上：{sid} @{_num(shot.get('start_seconds')):.2f}s "
                    f"偏 {delta_frames:.1f} 帧（容差 {tolerance_frames} 帧，"
                    f"{_num(grid.get('bpm')):g}bpm）"
                ),
                "proposed_fix": "微调该镜时长或前序镜长使切点吸拍；若切点本就是 Bar-DP 生成的，说明网格/帧率换算有 bug，先查 grids",
            })
    value = (aligned / total) if total else 1.0
    return {
        "value": round(value, 3),
        "skipped": total == 0,
        "circular": is_circular,
        "reason": "" if total else "无切点可测（镜数 < 2）",
        "detail": {
            "bpm": grid.get("bpm"),
            "frames_per_beat": round(fpb, 3),
            "tolerance_frames": tolerance_frames,
            "cuts": total,
            "aligned": aligned,
            "grid_source": _text(grid.get("source")),
            "circular": is_circular,
            "note": (
                "切点由拍网格生成（P0-5 Bar-DP），本指标自我满足：只当回归哨兵，"
                "不作质量证据"
            ) if is_circular else "",
            "rows": worst[:MAX_FINDINGS_PER_METRIC * 4],
        },
        "findings": findings[:MAX_FINDINGS_PER_METRIC],
    }


# ---------------------------------------------------------------------------
# m6：能量-视觉对应
# ---------------------------------------------------------------------------


def _shot_audio_energy(
    shot: dict[str, Any],
    soundtrack: dict[str, Any] | None,
    contour: list[dict[str, Any]] | None,
) -> tuple[float, str]:
    """本镜音频能量：优先实测 rms 轮廓，否则用音频编排的能量代理。"""
    start = _num(shot.get("start_seconds"))
    end = _num(shot.get("end_seconds"), start + _num(shot.get("duration_seconds")))
    if contour:
        windows = [
            _num(w.get("rms_db"))
            for w in contour
            if isinstance(w, dict)
            and _num(w.get("end_seconds")) > start
            and _num(w.get("start_seconds")) < max(end, start + 0.001)
        ]
        if windows:
            return sum(windows) / len(windows), "rms_contour"
    sid = _text(shot.get("shot_id"))
    scene_id = _text(shot.get("scene_id"))
    energy = 0.0
    for ev in (soundtrack or {}).get("events") or []:
        if not isinstance(ev, dict):
            continue
        ev_shot = _text(ev.get("shot_id"))
        same = ev_shot == sid if ev_shot else (
            _text(ev.get("kind")) == "bgm" and _text(ev.get("scene_id")) == scene_id
        )
        if same:
            energy += _num(ev.get("volume"))
    ap = shot.get("audio_prompt") if isinstance(shot.get("audio_prompt"), dict) else {}
    # 对白/sfx 是额外的听觉能量源（说话镜头不该配空镜式的慢切）。
    energy += 0.2 * len(ap.get("dialogue") or [])
    energy += 0.15 * len(ap.get("sfx") or [])
    return energy, "audio_plan"


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx <= 1e-9 or dy <= 1e-9:
        return None
    return num / (dx * dy)


def m6_energy_visual(
    shots: list[dict[str, Any]],
    *,
    soundtrack: dict[str, Any] | None = None,
    contour: list[dict[str, Any]] | None = None,
    circular: bool = False,
) -> dict[str, Any]:
    """切点密度 vs 音频能量：应当正相关（响处快切、静处长拍）。

    ``circular=True``：切点密度是**按能量目标**求解出来的（P0-5 Bar-DP 的代价函数就是
    Σ|实际刀数−目标刀数|，目标 ∝ 能量），m6 自然成立，只当回归哨兵。
    """
    if len(shots) < MIN_SHOTS_FOR_CORRELATION:
        return {
            "value": 0.0,
            "skipped": True,
            "circular": False,
            "reason": f"镜数 {len(shots)} < {MIN_SHOTS_FOR_CORRELATION}，不足以做相关统计",
            "findings": [],
            "detail": {},
        }
    densities: list[float] = []
    energies: list[float] = []
    sources: set[str] = set()
    rows: list[dict[str, Any]] = []
    for shot in shots:
        dur = _num(shot.get("duration_seconds"))
        if dur <= 0:
            continue
        density = 1.0 / dur
        energy, src = _shot_audio_energy(shot, soundtrack, contour)
        sources.add(src)
        densities.append(density)
        energies.append(energy)
        rows.append({
            "shot_id": _text(shot.get("shot_id")),
            "duration_seconds": round(dur, 3),
            "cut_density": round(density, 4),
            "audio_energy": round(energy, 4),
        })
    corr = _pearson(densities, energies)
    if corr is None:
        return {
            "value": 0.0,
            "skipped": True,
            "circular": False,          # 跳过 = 没证据，谈不上「自证」
            "reason": "能量或密度无方差（时长/音量全同），相关未定义",
            "findings": [],
            "detail": {"rows": rows[:MAX_FINDINGS_PER_METRIC * 4]},
        }
    findings: list[dict[str, Any]] = []
    if corr < float(METRIC_SPECS["m6"]["threshold"]):
        findings.append({
            "severity": "warning",
            "field": "m6/timeline",
            "metric": "m6",
            "value": round(corr, 3),
            "threshold": float(METRIC_SPECS["m6"]["threshold"]),
            "message": (
                f"切点密度与音频能量相关性 r={corr:.2f} < {METRIC_SPECS['m6']['threshold']:g}："
                "响段没有快切、或静段切得太碎，节奏与音乐脱节"
            ),
            "proposed_fix": "按能量波调整该段镜长（P0-5 的 Bar-DP 已能接管切点）或整段换曲",
        })
    return {
        "value": round(corr, 3),
        "skipped": False,
        "circular": bool(circular),
        "reason": "",
        "detail": {
            "shots": len(densities),
            "energy_source": "+".join(sorted(sources)) or "audio_plan",
            "circular": bool(circular),
            "note": (
                "切点密度按能量目标求解（P0-5 Bar-DP），本指标自我满足："
                "只当回归哨兵，不作质量证据"
            ) if circular else "",
            "rows": rows[:MAX_FINDINGS_PER_METRIC * 4],
        },
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# drift：身份漂移
# ---------------------------------------------------------------------------


def drift_metric(identity_memory: dict[str, Any] | None) -> dict[str, Any]:
    """身份漂移：把 identity_memory 的漂移计数升成量规维度（越小越好）。"""
    chars = (identity_memory or {}).get("characters") or {}
    findings: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    dirty = 0
    for key in sorted(chars):
        entry = chars.get(key)
        if not isinstance(entry, dict):
            continue
        count = int(_num(entry.get("drift_count")))
        retake = bool(entry.get("retake"))
        if not count and not retake:
            continue
        dirty += 1
        rows.append({"key": key, "drift_count": count, "retake": retake})
        findings.append({
            "severity": "warning" if retake else "info",
            "field": f"drift/{key}",
            "metric": "drift",
            "value": float(count),
            "threshold": 0.0,
            "message": (
                f"角色 {key} 身份漂移 {count} 次"
                + ("（已触发重拍定妆）" if retake else "（未达重拍阈值）")
                + (f"；最近坏镜 {entry.get('last_drift_shot')}" if entry.get("last_drift_shot") else "")
            ),
            "proposed_fix": f"重跑定妆 --retry portrait/{key}；或该角色改显式 agnes_mode=keyframe（首帧即身份锚）",
        })
    return {
        "value": float(dirty),
        "skipped": False,
        "detail": {"characters_with_drift": dirty, "rows": rows},
        "findings": findings[:MAX_FINDINGS_PER_METRIC],
    }


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def compute_edit_metrics(
    *,
    shots: list[dict[str, Any]],
    scene_plan: dict[str, Any] | None = None,
    transition_contract_projection: dict[str, Any] | None = None,
    registry: dict[str, dict[str, Any]] | None = None,
    prompts: dict[str, str] | None = None,
    image_bindings: dict[str, Any] | None = None,
    vlm: dict[str, Any] | None = None,
    soundtrack: dict[str, Any] | None = None,
    identity_memory: dict[str, Any] | None = None,
    energy_contour: list[dict[str, Any]] | None = None,
    beat_map: dict[str, Any] | None = None,
    fps: float = 30.0,
    beat_tolerance_frames: int = DEFAULT_BEAT_TOLERANCE_FRAMES,
) -> dict[str, Any]:
    """算齐 DIRECT 四条 + 身份漂移，产出 assemble 前人审的量规报告。

    ``beat_map`` 传 P0-5 ``build_beat_map`` 的产物（``tmp_autoedit/beat_map.json``）：
    网格以它为准，且 ``m5``/``m6`` 会打 ``circular``（切点由该网格+能量生成，自证）。
    """
    grid = beat_grid(soundtrack, fps=fps, beat_map=beat_map)
    circular = bool(grid and grid.get("circular"))
    computed: dict[str, dict[str, Any]] = {
        "m1": m1_prompt_relevance(
            shots, prompts=prompts, registry=registry, vlm=vlm,
            image_bindings=image_bindings,
        ),
        "m2": m2_semantic_coherence(
            shots,
            scene_plan=scene_plan,
            transition_contract_projection=transition_contract_projection,
        ),
        "m5": beat_alignment(
            shots, grid, tolerance_frames=beat_tolerance_frames, circular=circular,
        ),
        "m6": m6_energy_visual(
            shots, soundtrack=soundtrack, contour=energy_contour, circular=circular,
        ),
        "drift": drift_metric(identity_memory),
    }
    metrics: dict[str, Any] = {}
    findings: list[dict[str, Any]] = []
    for metric, spec in METRIC_SPECS.items():
        result = computed.get(metric) or {"value": 0.0, "skipped": True, "reason": "未计算", "findings": []}
        value = _num(result.get("value"), 0.0)
        threshold = float(spec["threshold"])
        skipped = bool(result.get("skipped"))
        # 跳过 = 没证据，不算 circular（circular 的语义是「值自我满足、不能当证据」）
        circular_metric = bool(result.get("circular")) and not skipped
        if skipped:
            passed = True
        elif spec["direction"] == "lower":
            passed = value <= threshold
        else:
            passed = value >= threshold
        metrics[metric] = {
            "metric": metric,
            "name": spec["name"],
            "value": value,
            "threshold": threshold,
            "unit": spec["unit"],
            "direction": spec["direction"],
            "source": spec["source"],
            "pass": passed,
            "skipped": skipped,
            "circular": circular_metric,
            "reason": _text(result.get("reason")),
            "detail": result.get("detail") or {},
            "findings": list(result.get("findings") or []),
        }
        findings.extend(metrics[metric]["findings"])
    circular_metrics = [m for m, row in metrics.items() if row.get("circular")]
    return {
        "version": "1",
        "fps": float(fps),
        "beat_tolerance_frames": int(beat_tolerance_frames),
        "beat_grid_source": _text((grid or {}).get("source")),
        "beat_grid_bpm": _num((grid or {}).get("bpm")) or None,
        "beat_grid_bpm_source": _text((grid or {}).get("bpm_source")),
        "circular_metrics": circular_metrics,
        "circular_note": (
            "切点由 P0-5 能量波/拍网格生成，以下指标自我满足、不作质量证据："
            + "/".join(circular_metrics)
        ) if circular_metrics else "",
        "deleted_metrics": list(DELETED_METRICS),
        "metrics": metrics,
        "pass": all(row["pass"] for row in metrics.values()),
        "findings": findings,
    }


def metric_findings(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """报告 → findings 列表（每条都带 metric/value/threshold）。"""
    rows = (report or {}).get("findings")
    if isinstance(rows, list):
        return [dict(r) for r in rows if isinstance(r, dict)]
    out: list[dict[str, Any]] = []
    for row in ((report or {}).get("metrics") or {}).values():
        if isinstance(row, dict):
            out.extend(dict(f) for f in (row.get("findings") or []) if isinstance(f, dict))
    return out


def overall_decision(report: dict[str, Any] | None) -> str:
    """量规 → 剪辑导演决策：全过 PASS，否则 REVISE。"""
    return "PASS" if (report or {}).get("pass") else "REVISE"


def metric_summary(report: dict[str, Any] | None) -> str:
    """一行摘要，供停点卡 / review_log message 复用。

    标记只用 ASCII（``!`` 未过 / ``~`` 跳过 / ``c`` 自我满足）——Windows GBK 控制台
    不认 ``✓`` 这类符号，写进日志还会污染人工审读。
    """
    rows = []
    for metric, row in ((report or {}).get("metrics") or {}).items():
        if not isinstance(row, dict):
            continue
        if row.get("skipped"):
            mark = "~"
        elif row.get("circular"):
            mark = "c"
        else:
            mark = "" if row.get("pass") else "!"
        rows.append(f"{metric}{mark}{row.get('value')}")
    return " ".join(rows) or "无量规结果"
