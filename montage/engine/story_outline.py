"""story_outline — 叙事结构模型（v8.2 P0-0；纯函数，无 LLM）。

长片主锚：bible.chapters[] 显式分章（id/title/作用/钩子/起始场景/目标时长段），
四拍（hook/escalation/reveal/landing）只在**章内**起作用——短篇（无 chapters）
保持旧全局四拍行为不变，长篇场景数多时全局四拍会把"每 15% 一拍"机械撒在
几十场上，失去叙事意义。

音乐锚降章节内辅助：chapter.bgm_id 提供章节级默认曲，显式 scene.bgm_id 仍
最高优先（compile overlay 现有行为），章节默认只兜底无显式曲的场。
"""

from __future__ import annotations

from typing import Any

# 章节四拍：章内每章独立走 hook→escalation→reveal→landing。
CHAPTER_BEATS = ("hook", "escalation", "reveal", "landing")

# 章节四拍全局配比（与 converter 旧 _narrative_role 同构，作用域从全片收到章内）
_BEAT_FRACS = ((0.15, "hook"), (0.65, "escalation"), (0.80, "reveal"), (1.01, "landing"))

# 自动分章阈值：≥ 该场数且 bible 未声明 chapters 时，按场景分组启发式分章。
# 长片 2h@10s/场 ≈ 700 场，短篇（<12 场）分章是伪结构，不触发。
AUTO_CHAPTER_SCENE_MIN = 12

# 自动分章目标章长（场数）：2h/10min-章 ≈ 60 场/章；取 40 场/章平衡审核粒度。
AUTO_CHAPTER_TARGET_SCENES = 40

VALID_CHAPTER_BEATS = set(CHAPTER_BEATS)


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_chapters(raw: Any) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """规范化 bible.chapters[] → 合法章列表 + findings。

    每章：id（必填，缺则 chNN 自动）、title、role（章节作用，自由文本）、
    hook（章钩子）、start_scene（章首场景 id）、bgm_id（章节默认曲）、
    target_duration_seconds（章节目标时长段）。非法条目丢进 findings 不静默。
    """
    if not isinstance(raw, list):
        if raw:
            return [], [{
                "severity": "warning",
                "stage": "bible",
                "field": "chapters",
                "message": "chapters 不是数组，已忽略",
                "proposed_fix": "chapters 写成 [{id, title, start_scene, ...}]",
            }]
        return [], []
    chapters: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            findings.append({
                "severity": "warning",
                "stage": "bible",
                "field": f"chapters[{i}]",
                "message": "章节条目不是对象，已丢弃",
                "proposed_fix": "每章写对象 {id, title, start_scene}",
            })
            continue
        cid = _text(entry.get("id"))
        start = _text(entry.get("start_scene"))
        if not cid:
            cid = f"ch{i + 1:02d}"
        if cid in seen:
            findings.append({
                "severity": "warning",
                "stage": "bible",
                "field": f"chapters[{i}].id",
                "message": f"章节 id {cid} 重复，已丢弃后者",
                "proposed_fix": "章节 id 全局唯一",
            })
            continue
        seen.add(cid)
        row: dict[str, Any] = {"id": cid}
        if _text(entry.get("title")):
            row["title"] = _text(entry.get("title"))
        if _text(entry.get("role")):
            row["role"] = _text(entry.get("role"))
        if _text(entry.get("hook")):
            row["hook"] = _text(entry.get("hook"))
        if start:
            row["start_scene"] = start
        if _text(entry.get("bgm_id")):
            row["bgm_id"] = _text(entry.get("bgm_id"))
        try:
            target = float(entry.get("target_duration_seconds") or 0)
        except (TypeError, ValueError):
            target = 0.0
        if target > 0:
            row["target_duration_seconds"] = target
        chapters.append(row)
    return chapters, findings


def auto_chapter_plan(
    scene_ids: list[str],
) -> list[dict[str, Any]]:
    """无显式 chapters 且场数达标时的自动分章（等距切，40 场/章）。

    纯启发式：只保证章粒度可供"按日拆批次=章节粒度"使用，不猜叙事内容
    （章 title/role 留空给导演审核补写）。
    """
    n = len(scene_ids)
    if n < AUTO_CHAPTER_SCENE_MIN:
        return []
    n_ch = max(1, (n + AUTO_CHAPTER_TARGET_SCENES - 1) // AUTO_CHAPTER_TARGET_SCENES)
    per = (n + n_ch - 1) // n_ch
    out: list[dict[str, Any]] = []
    for i in range(0, n, per):
        out.append({
            "id": f"ch{(i // per) + 1:02d}",
            "start_scene": str(scene_ids[i]),
        })
    return out


def chapter_beat_role(
    index_in_chapter: int,
    chapter_size: int,
) -> str:
    """章内四拍：单章按位置归 hook/escalation/reveal/landing（≥1 场即可归拍）。

    长片主锚下，四拍从"全片一次"改为"每章一次"：单章也走完整四拍弧线。
    """
    if chapter_size <= 1:
        return "hook"
    frac = (index_in_chapter + 0.5) / chapter_size
    for bound, beat in _BEAT_FRACS:
        if frac < bound:
            return beat
    return "landing"


def chapter_bgm_fallback(
    chapters: list[dict[str, Any]],
    scene_chapter: dict[str, str],
) -> dict[str, str]:
    """音乐锚降章节内辅助：chapter.bgm_id → 无显式 bgm_id 场的章节默认曲。

    返回 {scene_id: bgm_id}；compile 侧只对**没有**显式 bgm_id 的场兜底，
    显式 scene.bgm_id 仍最高优先（overlay_visuals 现有行为不变）。
    """
    bgm_by_ch = {
        str(ch.get("id") or ""): _text(ch.get("bgm_id"))
        for ch in chapters
    }
    out: dict[str, str] = {}
    for scene_id, cid in scene_chapter.items():
        bgm = bgm_by_ch.get(cid, "")
        if bgm:
            out[scene_id] = bgm
    return out


def build_chapter_plan(
    bible: dict[str, Any],
    scene_ids: list[str],
    *,
    auto: bool = True,
) -> dict[str, Any]:
    """chapters 主入口：bible + 场次序 → {chapters, scene_chapter, findings}。

    - 显式 chapters：normalize 后按 start_scene 区间归属场次；
    - 未声明且场数 ≥ AUTO_CHAPTER_SCENE_MIN 且 auto=True：启发式等距分章；
    - auto=False（converter 独立转换路径）：不做自动分章，短篇/未声明都
      返回空 → 四拍回落旧全局行为，独立转换结果与历史版本逐字节一致。
    """
    chapters, findings = normalize_chapters(bible.get("chapters"))
    scene_chapter: dict[str, str] = {}
    if chapters:
        if not any(_text(ch.get("start_scene")) for ch in chapters):
            findings.append({
                "severity": "warning",
                "stage": "bible",
                "field": "chapters",
                "message": "chapters 无任何 start_scene，章节归属失效，已忽略",
                "proposed_fix": "每章写 start_scene=章首场景 id",
            })
            chapters = []
        else:
            ordered = [sid for sid in scene_ids if _text(sid)]
            # start_scene 在场次序中未出现 → 该章区间为空，记 finding 不静默
            known = set(ordered)
            for ch in chapters:
                start = _text(ch.get("start_scene"))
                if start and start not in known:
                    findings.append({
                        "severity": "warning",
                        "stage": "bible",
                        "field": f"chapters[{ch.get('id')}].start_scene",
                        "message": f"start_scene {start} 不在 bible.scenes 中",
                        "proposed_fix": "修正 start_scene 或删除该章",
                    })
            boundaries = [
                (ordered.index(_text(ch["start_scene"])), str(ch.get("id") or ""))
                for ch in chapters
                if _text(ch.get("start_scene")) in known
            ]
            boundaries.sort()
            # 有 start_scene 的章存在时，缺 start_scene 的章不参与归属——显式提示
            if any(bpos >= 0 for bpos, _ in boundaries):
                for ch in chapters:
                    if not _text(ch.get("start_scene")):
                        findings.append({
                            "severity": "warning",
                            "stage": "bible",
                            "field": f"chapters[{ch.get('id')}].start_scene",
                            "message": f"章节 {ch.get('id')} 缺 start_scene，无法归属场次",
                            "proposed_fix": "补 start_scene=章首场景 id",
                        })
            for pos, sid in enumerate(ordered):
                cid = ""
                for bpos, bcid in boundaries:
                    if bpos <= pos:
                        cid = bcid
                    else:
                        break
                if cid:
                    scene_chapter[sid] = cid
    if not chapters and auto and len(scene_ids) >= AUTO_CHAPTER_SCENE_MIN:
        chapters = auto_chapter_plan(scene_ids)
        findings.append({
            "severity": "info",
            "stage": "bible",
            "field": "chapters",
            "message": (
                f"未声明 chapters 且场数 {len(scene_ids)} ≥ {AUTO_CHAPTER_SCENE_MIN}，"
                f"已自动等距分 {len(chapters)} 章（章节粒度供按日拆批次）"
            ),
            "proposed_fix": "导演审核后可显式声明 chapters[] 覆盖",
        })
        # 自动章也做区间归属：start_scene 即自动章锚点（等距切点必在场次序中）
        if chapters:
            start_to_ch = {
                str(ch.get("start_scene") or ""): str(ch.get("id") or "")
                for ch in chapters
            }
            current = ""
            for sid in scene_ids:
                if sid in start_to_ch:
                    current = start_to_ch[sid]
                if current:
                    scene_chapter[sid] = current
    return {"chapters": chapters, "scene_chapter": scene_chapter, "findings": findings}


def chapter_index_map(
    scene_ids: list[str],
    scene_chapter: dict[str, str],
) -> tuple[dict[str, int], dict[str, int]]:
    """章内位置索引：{scene_id: 章内序} 与 {scene_id: 章大小}。

    供 converter 章内四拍（chapter_beat_role）消费；未归属章的场返回 (-1, 0)
    → 上层回落全局四拍。
    """
    order: dict[str, int] = {}
    size: dict[str, int] = {}
    # 保序分组：按 scene_ids 顺序（=剧本顺序）聚章
    grouped: dict[str, list[int]] = {}
    for pos, sid in enumerate(scene_ids):
        cid = scene_chapter.get(sid, "")
        if not cid:
            continue
        grouped.setdefault(cid, []).append(pos)
    for cid, positions in grouped.items():
        for in_chapter, pos in enumerate(positions):
            order[scene_ids[pos]] = in_chapter
            size[scene_ids[pos]] = len(positions)
    for sid in scene_ids:
        if sid not in order:
            order[sid] = -1
            size[sid] = 0
    return order, size


# ---- 分层合成（v8.2 P0-6）---------------------------------------------
# 长片合成三层：short（单集/单短片，existing final.mp4）→ segment（章节段
# 成片）→ longform（整片 = 段拼）。章节是分层锚：chapters[] 决定段边界，
# BGM 所有权沿层声明（每段知道自己的曲，拼接层只做 concat 不再混音）。


def chapter_layers(
    scene_plan: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """scene_plan.chapters → 段层清单（保序）。

    每段：``{chapter_id, title, role, hook, bgm_id, scene_ids[]}``。
    无 chapters 的 scene_plan（短篇/未编译）返回 []——分层是长片泛化，
    short 层不需要段。scene_ids 按 scene_plan.scenes 顺序过滤归属。
    """
    plan = scene_plan if isinstance(scene_plan, dict) else {}
    chapters = [c for c in (plan.get("chapters") or []) if isinstance(c, dict)]
    if not chapters:
        return []
    scene_ids = [
        str(s.get("id") or "") for s in (plan.get("scenes") or [])
        if isinstance(s, dict) and str(s.get("id") or "").strip()
    ]
    scene_chapter = {
        str(s.get("id") or ""): str(s.get("chapter_id") or "").strip()
        for s in (plan.get("scenes") or []) if isinstance(s, dict)
    }
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    for sid in scene_ids:
        cid = scene_chapter.get(sid, "")
        if not cid:
            continue
        if cid not in grouped:
            grouped[cid] = []
            order.append(cid)
        grouped[cid].append(sid)
    out: list[dict[str, Any]] = []
    for ch in chapters:
        cid = str(ch.get("id") or "").strip()
        if not cid or cid not in grouped:
            continue
        out.append({
            "chapter_id": cid,
            "title": str(ch.get("title") or ""),
            "role": str(ch.get("role") or ""),
            "hook": str(ch.get("hook") or ""),
            "bgm_id": str(ch.get("bgm_id") or "").strip(),
            "scene_ids": grouped[cid],
        })
    # 场次归属了但 chapters 没声明的孤儿段：附录在末尾（保序），不静默丢
    known = {row["chapter_id"] for row in out}
    for cid in order:
        if cid in known:
            continue
        out.append({
            "chapter_id": cid,
            "title": "",
            "role": "",
            "hook": "",
            "bgm_id": "",
            "scene_ids": grouped[cid],
        })
    return out


def layer_bgm_ownership(
    layers: list[dict[str, Any]],
    scene_bgm: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """每层 BGM 所有权：段内显式 scene bgm_id 集合 + 段默认曲。

    规则（与 compile 的 chapter_bgm_fallback 同向）：段默认 = chapter.bgm_id；
    场显式 bgm_id 优先且记进 ``scene_bgm_ids`` 供审。无任何曲的段
    ``bgm_id=""``——上游（soundtrack_planner）回退整片一首，不静默。
    """
    bgm_by_scene = scene_bgm or {}
    rows: list[dict[str, Any]] = []
    for layer in layers:
        sids = [str(s) for s in (layer.get("scene_ids") or [])]
        explicit = []
        for sid in sids:
            bgm = str(bgm_by_scene.get(sid) or "").strip()
            if bgm:
                explicit.append({"scene_id": sid, "bgm_id": bgm})
        rows.append({
            "chapter_id": str(layer.get("chapter_id") or ""),
            "bgm_id": str(layer.get("bgm_id") or "").strip(),
            "scene_bgm_ids": explicit,
            "has_music": bool(layer.get("bgm_id")) or bool(explicit),
        })
    return rows


def episode_segment_clips(
    ep_dir: str | Path,
) -> dict[str, Any]:
    """单集 → 章节段成片清单（P0-6 segment 层；纯读，不拼不写）。

    读集内 scene_plan：有 chapters 时按 ``chapter_layers`` 把该集全部
    clip 分组到段；无 chapters 返回空（短篇集不需要段层）。
    返回 ``{layers: [{chapter_id, bgm_id, clips[], scene_ids[]}], missing[]}``。
    供 ``maybe_concat_season`` 的 segment→longform 泛化消费。
    """
    from pathlib import Path as _P

    root = _P(ep_dir)
    from montage.engine.artifacts import ArtifactStore

    store = ArtifactStore(root)
    scene_plan = store.read("scene_plan")
    manifest = store.read("asset_manifest")
    if not isinstance(scene_plan, dict):
        return {"layers": [], "missing": ["scene_plan"]}
    layers = chapter_layers(scene_plan)
    if not layers:
        return {"layers": [], "missing": []}
    if not isinstance(manifest, dict):
        return {"layers": layers, "missing": ["asset_manifest"]}
    from montage.tools.compose_planner import clip_path_for_shot

    shots_by_scene: dict[str, list[str]] = {}
    missing: list[str] = []
    for shot in scene_plan.get("scenes") or []:
        if not isinstance(shot, dict):
            continue
        sid = str(shot.get("id") or "")
        rows: list[str] = []
        for nested in shot.get("shots") or []:
            if not isinstance(nested, dict):
                continue
            shot_id = str(nested.get("shot_id") or "")
            scene_id = str(nested.get("scene_id") or "") or sid
            raw = clip_path_for_shot(shot_id, scene_id, manifest)
            if not raw:
                missing.append(f"{shot_id}: manifest 无 clip_path")
                continue
            path = _P(raw)
            if not path.is_absolute():
                path = root / path
            if not path.is_file():
                missing.append(f"{shot_id}: 文件不存在 {raw}")
                continue
            rows.append(str(path))
        if rows:
            shots_by_scene[sid] = rows
    out_layers: list[dict[str, Any]] = []
    for layer in layers:
        clips: list[str] = []
        for sid in layer["scene_ids"]:
            clips.extend(shots_by_scene.get(sid, []))
        if not clips:
            continue
        out_layers.append({
            "chapter_id": layer["chapter_id"],
            "title": layer.get("title") or "",
            "bgm_id": layer.get("bgm_id") or "",
            "scene_ids": layer["scene_ids"],
            "clips": clips,
        })
    return {"layers": out_layers, "missing": missing}
