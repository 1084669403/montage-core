"""shot_presence — 逐镜「在场清单」与「承接表」（原创）。

**要解决的问题**：每一镜的生成提示词是独立送给模型的，模型看不到上一镜。
如果分镜只写"这一镜发生什么"，就很容易漏掉"上一镜已经在场、这一镜还应
在场"的人物 / 道具 / 场景方位——上一镜手里抱着的琴，这一镜就没了。

**两段式解法**（本模块是唯一实现处）：

1. **在场清单 presence**（每镜一份，编剧写 / 缺省由现有字段派生）
   - ``location``：地点 id + 画面方位 zone + 时段 time_of_day + 光位 light；
   - ``characters[]``：id + zone + state（姿态/动作状态）+ costume_state +
     ``enters`` / ``exits``（进出场标记，承接判定的依据）；
   - ``props[]``：id + holder（谁拿着）+ zone；
   - 空镜必须给 ``empty_reason``，否则视为缺项。

2. **承接表 continuity**（**编译器生成，不交给模型猜**）
   编译器逐镜比对相邻 presence，产出 ``must_keep`` / ``changed`` / ``missing``：
   上一镜在场、既没有 exits 标记、本镜又没写进来的实体 = ``missing``，
   就是"模型会漏掉"的那一类，直接进 findings 与提示词。

所有函数都是纯函数（不碰网络、不碰 ffmpeg），便于单测与在编译器里复用。
"""

from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "yes", "y", "是"}


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, dict)]


def normalize_presence(raw: Any) -> dict[str, Any]:
    """把任意来源的 presence 规范成稳定结构（缺字段给空值，不抛错）。"""
    row = raw if isinstance(raw, dict) else {}
    loc_raw = row.get("location") if isinstance(row.get("location"), dict) else {}
    location = {
        "id": _text(loc_raw.get("id") or loc_raw.get("location_id")),
        "zone": _text(loc_raw.get("zone") or loc_raw.get("position")),
        "time_of_day": _text(loc_raw.get("time_of_day") or loc_raw.get("time")),
        "light": _text(loc_raw.get("light") or loc_raw.get("lighting")),
    }
    characters: list[dict[str, Any]] = []
    for item in _dict_list(row.get("characters")):
        cid = _text(item.get("id") or item.get("character_id") or item.get("name"))
        if not cid:
            continue
        characters.append({
            "id": cid,
            "zone": _text(item.get("zone") or item.get("position") or item.get("blocking")),
            "state": _text(item.get("state") or item.get("action")),
            "costume_state": _text(item.get("costume_state") or item.get("outfit_state")),
            "enters": _as_bool(item.get("enters")),
            "exits": _as_bool(item.get("exits")),
        })
    props: list[dict[str, Any]] = []
    for item in _dict_list(row.get("props")):
        pid = _text(item.get("id") or item.get("prop_id") or item.get("name"))
        if not pid:
            continue
        props.append({
            "id": pid,
            "holder": _text(item.get("holder") or item.get("owner")),
            "zone": _text(item.get("zone") or item.get("position")),
            # 道具也允许退场（留在原地/被带走/不再出现），否则承接表会把
            # "上镜有、本镜没有"的道具一律判成缺失。
            "exits": _as_bool(item.get("exits")),
        })
    off_frame: list[str] = []
    raw_off = row.get("off_frame")
    if isinstance(raw_off, list):
        off_frame = [_text(v) for v in raw_off if _text(v)]
    return {
        "location": location,
        "characters": characters,
        "props": props,
        "off_frame": off_frame,
        "empty_reason": _text(row.get("empty_reason") or row.get("empty_note")),
    }


def derive_presence(
    shot: dict[str, Any],
    *,
    scene: dict[str, Any] | None = None,
    registry: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """bible 未写 presence 时，从现有字段派生一份（尽量不丢信息）。"""
    vd = shot.get("visual_details") if isinstance(shot.get("visual_details"), dict) else {}
    env = vd.get("environment")
    env_text = _text(env.get("description") if isinstance(env, dict) else env)
    if not env_text:
        env_text = _text(shot.get("environment") or (scene or {}).get("description"))
    location_id = _text(shot.get("location_id") or (scene or {}).get("location_id"))
    characters: list[dict[str, Any]] = []
    for subj in _dict_list(vd.get("subjects")):
        cid = _text(subj.get("id") or subj.get("character_id"))
        if not cid:
            continue
        zhang = subj.get("blocking") if isinstance(subj.get("blocking"), dict) else {}
        zone = "".join(
            _text(zhang.get(key)) for key in ("x", "y", "z")
        ) or _text(subj.get("position"))
        action = subj.get("action") if isinstance(subj.get("action"), dict) else {}
        outfit = ""
        if registry:
            char = registry.get(cid) if isinstance(registry, dict) else None
            outfit = _text((char or {}).get("outfit_state"))
        characters.append({
            "id": cid,
            "zone": zone,
            "state": _text(action.get("verb") or subj.get("action")),
            "costume_state": outfit,
            "enters": False,
            "exits": False,
        })
    props: list[dict[str, Any]] = []
    for obj in _dict_list(vd.get("objects")):
        pid = _text(obj.get("id") or obj.get("prop_id") or obj.get("name"))
        if not pid:
            continue
        props.append({
            "id": pid,
            "holder": _text(obj.get("holder") or obj.get("owner")),
            "zone": _text(obj.get("position") or obj.get("zone")),
        })
    return normalize_presence({
        "location": {
            "id": location_id,
            "zone": _text(shot.get("location_zone")),
            "time_of_day": _text(shot.get("time_of_day")),
            "light": env_text,
        },
        "characters": characters,
        "props": props,
        "empty_reason": _text(shot.get("empty_reason")),
    })


def presence_of(
    shot: dict[str, Any],
    *,
    scene: dict[str, Any] | None = None,
    registry: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], bool]:
    """返回 (presence, derived)。显式写了的用显式的，否则派生。"""
    explicit = shot.get("presence")
    if isinstance(explicit, dict) and explicit:
        return normalize_presence(explicit), False
    return derive_presence(shot, scene=scene, registry=registry), True


def _entities(presence: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in presence.get("characters") or []:
        rows.append({"kind": "character", "id": item["id"], "row": item})
    for item in presence.get("props") or []:
        rows.append({"kind": "prop", "id": item["id"], "row": item})
    for ident in presence.get("off_frame") or []:
        rows.append({"kind": "character", "id": ident, "row": {"exits": False, "off_frame": True}})
    return rows


def continuity_for(
    prev: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    prev_shot_id: str = "",
) -> dict[str, Any]:
    """相邻两镜的承接判定：上镜在场、未标退场、本镜缺失 → missing。"""
    ledger: dict[str, Any] = {
        "from_shot": _text(prev_shot_id),
        "must_keep": [],
        "changed": [],
        "missing": [],
    }
    if not prev:
        return ledger
    prev_ids = {f"{row['kind']}:{row['id']}": row for row in _entities(prev)}
    cur_ids = {f"{row['kind']}:{row['id']}": row for row in _entities(current)}
    for key, row in prev_ids.items():
        kind, ident = key.split(":", 1)
        if key in cur_ids:
            off = bool(cur_ids[key]["row"].get("off_frame"))
            ledger["must_keep"].append({
                "kind": kind, "id": ident,
                "why": (
                    "上镜在场，本镜画外（不入画但仍在场）" if off
                    else "上镜在场且本镜仍在场（承接保持）"
                ),
                "off_frame": off,
            })
            continue
        if _as_bool(row["row"].get("exits")):
            ledger["changed"].append({
                "kind": kind, "id": ident, "why": "上镜已标记退场",
            })
            continue
        ledger["missing"].append({
            "kind": kind, "id": ident,
            "why": "上镜在场、未标退场，本镜却未出现（模型容易漏）",
        })
    for key, row in cur_ids.items():
        if key in prev_ids:
            continue
        kind, ident = key.split(":", 1)
        if _as_bool(row["row"].get("enters")) or kind == "prop":
            ledger["changed"].append({
                "kind": kind, "id": ident,
                "why": "本镜新入场/新增" if kind == "character" else "本镜新增道具",
            })
    return ledger


def build_ledger(
    shots: list[dict[str, Any]],
    *,
    scenes_by_id: dict[str, dict[str, Any]] | None = None,
    registry: dict[str, dict[str, Any]] | None = None,
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """给一串按叙事顺序排列的镜头生成 presence + continuity。

    返回 ``{shots: {shot_id: {presence, continuity, derived}}, findings: [...]}``。
    调用方负责把结果写回 scene_plan 并把 findings 交给评审。
    """
    scene_map = scenes_by_id or {}
    out: dict[str, Any] = {"shots": {}, "findings": []}
    prev_presence: dict[str, Any] | None = None
    prev_shot_id = ""
    for shot in shots:
        shot_id = _text(shot.get("shot_id"))
        scene = scene_map.get(_text(shot.get("scene_id"))) or {}
        presence, derived = presence_of(shot, scene=scene, registry=registry)
        ledger = continuity_for(prev_presence, presence, prev_shot_id=prev_shot_id)
        out["shots"][shot_id] = {
            "presence": presence,
            "continuity": ledger,
            "derived": derived,
        }
        label = (names or {}).get(shot_id) or shot_id
        if derived:
            out["findings"].append({
                "severity": "warning",
                "stage": "scene_plan",
                "field": f"{shot_id}.presence",
                "message": f"{label} 未写在场清单，已按现有字段派生（可能不完整）",
                "proposed_fix": "在 bible 该镜补 presence（location/characters/props）后重编译",
            })
        if not presence["location"]["id"]:
            out["findings"].append({
                "severity": "warning",
                "stage": "scene_plan",
                "field": f"{shot_id}.presence.location",
                "message": f"{label} 在场清单缺场景 id",
                "proposed_fix": "补 presence.location.id（对应 bible.locations[].id）",
            })
        if not presence["characters"] and not presence["empty_reason"]:
            out["findings"].append({
                "severity": "warning",
                "stage": "scene_plan",
                "field": f"{shot_id}.presence.characters",
                "message": f"{label} 既没有人物也没有空镜理由",
                "proposed_fix": "补 characters[]，或写 empty_reason 说明这是空镜",
            })
        for row in ledger["missing"]:
            out["findings"].append({
                "severity": "warning",
                "stage": "scene_plan",
                "field": f"{shot_id}.continuity",
                "message": (
                    f"{label} 承接缺失：{row['kind']} {row['id']} "
                    f"（{row['why']}）"
                ),
                "proposed_fix": "本镜补写该实体，或在上镜标 exits:true 明确退场",
            })
        prev_presence = presence
        prev_shot_id = shot_id
    return out


def presence_prompt_lines(
    presence: dict[str, Any],
    continuity: dict[str, Any] | None = None,
    *,
    names: dict[str, str] | None = None,
) -> list[str]:
    """把在场清单/承接表渲染成提示词段落（中文，可直接拼进生成提示词）。"""
    label = names or {}
    loc = presence.get("location") or {}
    loc_id = _text(loc.get("id"))
    loc_bits = [label.get(loc_id, loc_id)]
    for key in ("zone", "time_of_day", "light"):
        value = _text(loc.get(key))
        if value:
            loc_bits.append(value)
    scene_text = " / ".join(bit for bit in loc_bits if bit) or "未标注场景"
    char_bits = []
    for row in presence.get("characters") or []:
        bits = [f"{label.get(row['id'], row['id'])}（{row.get('zone') or '方位未标注'}"]
        if row.get("state"):
            bits.append(row["state"])
        if row.get("costume_state"):
            bits.append(f"穿着：{row['costume_state']}")
        if row.get("enters"):
            bits.append("本镜入场")
        if row.get("exits"):
            bits.append("本镜离场")
        char_bits.append("，".join(bits) + "）")
    prop_bits = []
    for row in presence.get("props") or []:
        holder = _text(row.get("holder"))
        bits = [label.get(row["id"], row["id"])]
        if holder:
            bits.append(f"{label.get(holder, holder)}持有")
        if row.get("zone"):
            bits.append(row["zone"])
        prop_bits.append("（".join([bits[0], "，".join(bits[1:]) + "）"]) if len(bits) > 1 else bits[0])
    parts = [f"场景：{scene_text}"]
    parts.append("人物：" + ("、".join(char_bits) if char_bits else "无人物"))
    if prop_bits:
        parts.append("道具：" + "、".join(prop_bits))
    if presence.get("empty_reason"):
        parts.append(f"空镜理由：{presence['empty_reason']}")
    lines = ["【在场清单】" + "；".join(parts)]
    off_frame = [
        label.get(str(ident), str(ident)) for ident in (presence.get("off_frame") or [])
    ]
    if off_frame:
        # 画外人物：在场（承接必须保留）但**不画进画面**——早前把画外人物写进
        # 主体清单，导致空镜里凭空出现人物（用户实测 sc01_05）。
        lines.append("【画外】不出现于画面但仍在场：" + "、".join(off_frame))
    if continuity:
        bits = []
        if continuity.get("from_shot"):
            bits.append(f"上镜 {continuity['from_shot']}")
        keep = continuity.get("must_keep") or []
        if keep:
            bits.append("保留：" + "、".join(
                f"{label.get(row['id'], row['id'])}"
                + ("（画外）" if row.get("off_frame") else "")
                for row in keep
            ))
        changed = continuity.get("changed") or []
        if changed:
            bits.append("变化：" + "、".join(
                f"{label.get(row['id'], row['id'])}（{row.get('why') or ''}）"
                for row in changed
            ))
        missing = continuity.get("missing") or []
        if missing:
            bits.append("缺失待确认：" + "、".join(
                f"{label.get(row['id'], row['id'])}" for row in missing
            ))
        if bits:
            lines.append("【承接】" + "；".join(bits))
    return lines
