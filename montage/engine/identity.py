"""identity — 角色级身份记忆库（v8.2 P0-identity-memory；纯逻辑 + JSON 读写）。

长片跨天批次的核心命门是「同一个角色在不同镜里还是同一个人」。仓库已有
两条锚各自的短板：

- 参考图锚（``<Picture N>`` 喂定妆照）缺「哪张才是真源」——``_ref_index``
  取最后一张，重拍定妆后旧图仍在 manifest 里就会被静默顶掉；
- 帧级连续锚（首尾桥）只保相邻镜连续性，跨场/跨天断链后无身份约束。

本模块补上**canonical 身份锚**这一中间层（思想借鉴 Memento/SlotMem，非复现）：

1. 每个 ``(character_id, form_id)`` 只认一条 canonical 参考图（真源唯一）；
2. **VLM 过检才保守更新**：只有过检且分数不低于现任 canonical 的候选才换锚，
   其余只进 ``history``（留痕，不换真源）；
3. **漂移检测触发重拍定妆**：镜级 VLM 累计「人物不一致」达阈值 → ``retake``，
   下一轮 ``shot_runner`` 把该角色重新排进定妆队列（重拍后漂移计数清零）。

双锚协同：canonical 供 ``<Picture N>`` 参考图锚，首尾桥供帧级锚，两者互不
取代。本模块不触网、不调 LLM，输入只有 VLM 已产出的 review 字典。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

IDENTITY_MEMORY_ARTIFACT = "identity_memory"

# 过检分数线：VLM score >= 该值才算「过检」。低于此值即便 ok=True 也不换锚。
DEFAULT_MIN_SCORE = 0.6

# 连续漂移告警次数阈值：达到即触发重拍定妆。
DEFAULT_DRIFT_THRESHOLD = 2

# canonical 之外保留的候选历史条数上限（审计用，不参与运行时决策）。
MAX_HISTORY = 8

# 漂移判定命中的 VLM issue kind。
DRIFT_KINDS = ("人物不一致",)


def identity_key(character_id: str, form_id: str = "") -> str:
    """记忆库键：隐式形态 ``cid``；显式形态 ``cid:form``。"""
    cid = str(character_id or "").strip()
    fid = str(form_id or "").strip()
    return f"{cid}:{fid}" if fid else cid


def parse_identity_key(key: str) -> tuple[str, str]:
    """``cid:form`` → ``(cid, form)``；无冒号则 form 为空串。"""
    text = str(key or "")
    if ":" in text:
        cid, _sep, fid = text.partition(":")
        return cid, fid
    return text, ""


def identity_subject(character_id: str, form_id: str = "") -> str:
    """人机接口 subject（与 ``--retry`` token 同形）：``portrait/<cid>[:<form>]``。"""
    cid, fid = str(character_id or "").strip(), str(form_id or "").strip()
    return f"portrait/{cid}:{fid}" if fid else f"portrait/{cid}"


def empty_memory() -> dict[str, Any]:
    return {"version": 1, "characters": {}}


def memory_path(project_dir: str | Path) -> Path:
    return Path(project_dir) / "artifacts" / f"{IDENTITY_MEMORY_ARTIFACT}.json"


def load_memory(project_dir: str | Path) -> dict[str, Any]:
    """读记忆库；缺失/损坏返回空库（不抛，旧项目零迁移成本）。"""
    path = memory_path(project_dir)
    if not path.is_file():
        return empty_memory()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_memory()
    if not isinstance(raw, dict):
        return empty_memory()
    chars = raw.get("characters")
    if not isinstance(chars, dict):
        raw["characters"] = {}
    raw.setdefault("version", 1)
    return raw


def save_memory(project_dir: str | Path, memory: dict[str, Any]) -> str:
    """原子写 ``artifacts/identity_memory.json``；返回路径字符串。"""
    path = memory_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return str(path)


def entry_of(memory: dict[str, Any], key: str, *, create: bool = False) -> dict[str, Any]:
    """取某键的条目；``create=True`` 时不存在则建空条目。"""
    chars = memory.setdefault("characters", {}) if isinstance(memory, dict) else {}
    existing = chars.get(key)
    if isinstance(existing, dict):
        return existing
    if not create:
        return {}
    entry: dict[str, Any] = {"key": key, "canonical": {}, "history": [], "drift_count": 0, "retake": False}
    chars[key] = entry
    return entry


def canonical_ref(memory: dict[str, Any] | None, key: str) -> dict[str, Any]:
    """canonical 参考图（含 ref_id/path/url/score）。无则空 dict。"""
    entry = entry_of(memory or {}, key)
    ref = entry.get("canonical")
    return dict(ref) if isinstance(ref, dict) else {}


def canonical_ref_id(memory: dict[str, Any] | None, key: str) -> str:
    return str(canonical_ref(memory, key).get("ref_id") or "")


def review_passed(review: dict[str, Any] | None, min_score: float = DEFAULT_MIN_SCORE) -> bool:
    """VLM 过检：非 skipped 且 ok 且分数达标（分数缺失视为不达标——宁保守）。"""
    data = review if isinstance(review, dict) else {}
    if data.get("skipped"):
        return False
    if not data.get("ok", False):
        return False
    score = data.get("score")
    try:
        return float(score) >= float(min_score)
    except (TypeError, ValueError):
        return False


def _score_of(ref: dict[str, Any]) -> float:
    try:
        return float(ref.get("score"))
    except (TypeError, ValueError):
        return 0.0


def evaluate_candidate(
    memory: dict[str, Any],
    key: str,
    *,
    review: dict[str, Any] | None,
    ref_id: str,
    path: str = "",
    url: str = "",
    min_score: float = DEFAULT_MIN_SCORE,
) -> dict[str, Any]:
    """决定候选参考图能否成为新的 canonical（保守更新）。

    返回 ``{"action": "init"|"adopt"|"hold"|"reject", "reason": str, "entry": dict}``：

    - 无 canonical：过检 → ``init``；VLM 跳过（未配置密钥）→ ``init`` 但标
      ``verified=False``（不然零密钥环境永远没有身份锚）；过检失败 → ``reject``。
    - 有 canonical：过检且分数 ≥ 现任 → ``adopt``（旧锚进 history）；过检但
      分数更低 → ``hold``（保住真源）；未过检 → ``hold``（候选只留痕）。
    """
    entry = entry_of(memory, key, create=True)
    candidate = {
        "ref_id": str(ref_id or ""),
        "path": str(path or ""),
        "url": str(url or ""),
        "score": review.get("score") if isinstance(review, dict) else None,
    }
    current = canonical_ref(memory, key)
    passed = review_passed(review, min_score)

    if not current:
        if isinstance(review, dict) and review.get("skipped"):
            candidate["verified"] = False
            entry["canonical"] = candidate
            return {"action": "init", "reason": "首见定妆（VLM 未配置，未核验）", "entry": entry}
        if not passed:
            entry.setdefault("history", []).append({**candidate, "reason": "init_failed_review"})
            entry["history"] = entry["history"][-MAX_HISTORY:]
            return {"action": "reject", "reason": "首次定妆未过检，不建锚", "entry": entry}
        candidate["verified"] = True
        entry["canonical"] = candidate
        return {"action": "init", "reason": "首见定妆过检，建 canonical 锚", "entry": entry}

    if passed and _score_of(candidate) >= _score_of(current):
        entry.setdefault("history", []).append({**current, "reason": "superseded"})
        entry["history"] = entry["history"][-MAX_HISTORY:]
        candidate["verified"] = True
        entry["canonical"] = candidate
        return {"action": "adopt", "reason": "候选过检且不劣于现任，换锚", "entry": entry}
    if passed:
        entry.setdefault("history", []).append({**candidate, "reason": "lower_score"})
        entry["history"] = entry["history"][-MAX_HISTORY:]
        return {"action": "hold", "reason": "候选过检但分数低于现任，保住真源", "entry": entry}
    entry.setdefault("history", []).append({**candidate, "reason": "failed_review"})
    entry["history"] = entry["history"][-MAX_HISTORY:]
    return {"action": "hold", "reason": "候选未过检，不换锚", "entry": entry}


def drift_issues(review: dict[str, Any] | None) -> list[dict[str, Any]]:
    """从镜级 VLM 结果里挑出身份漂移 issue（kind=人物不一致，critical）。"""
    data = review if isinstance(review, dict) else {}
    if data.get("skipped"):
        return []
    out: list[dict[str, Any]] = []
    for issue in data.get("issues") or []:
        if not isinstance(issue, dict):
            continue
        if str(issue.get("kind") or "") in DRIFT_KINDS and str(issue.get("severity") or "") == "critical":
            out.append(issue)
    return out


def record_drift(
    memory: dict[str, Any],
    key: str,
    *,
    shot_id: str = "",
    message: str = "",
    threshold: int = DEFAULT_DRIFT_THRESHOLD,
) -> dict[str, Any]:
    """记一次身份漂移观测；连续达阈值则置 ``retake``。返回本次判定。"""
    entry = entry_of(memory, key, create=True)
    count = int(entry.get("drift_count") or 0) + 1
    entry["drift_count"] = count
    if shot_id:
        entry["last_drift_shot"] = str(shot_id)
    if message:
        entry["last_drift_message"] = str(message)
    retake = count >= max(1, int(threshold))
    if retake:
        entry["retake"] = True
        entry["retake_reason"] = f"连续 {count} 镜身份漂移"
    return {"key": key, "count": count, "retake": retake, "threshold": int(threshold)}


def clear_drift(memory: dict[str, Any], key: str) -> None:
    """过检观测清零漂移计数（连续计数语义）。"""
    entry = entry_of(memory, key, create=True)
    entry["drift_count"] = 0


def retake_subjects(memory: dict[str, Any] | None) -> list[str]:
    """待重拍定妆的 subject 列表（按 key 稳定排序）。"""
    chars = (memory or {}).get("characters") or {}
    out: list[str] = []
    for key in sorted(chars):
        entry = chars.get(key)
        if isinstance(entry, dict) and entry.get("retake"):
            cid, fid = parse_identity_key(key)
            out.append(identity_subject(cid, fid))
    return out


def mark_retaken(memory: dict[str, Any], key: str, *, ref_id: str = "") -> dict[str, Any]:
    """重拍定妆完成：清 retake 标记与漂移计数（canonical 由后续过检决定）。"""
    entry = entry_of(memory, key, create=True)
    entry["retake"] = False
    entry["drift_count"] = 0
    entry.pop("retake_reason", None)
    entry["last_retake_ref"] = str(ref_id or "")
    return entry


def findings(memory: dict[str, Any] | None, *, field_prefix: str = "identity") -> list[dict[str, Any]]:
    """把记忆库里需要人看的信号转成 shot_runner findings。"""
    chars = (memory or {}).get("characters") or {}
    out: list[dict[str, Any]] = []
    for key in sorted(chars):
        entry = chars.get(key)
        if not isinstance(entry, dict):
            continue
        cid, fid = parse_identity_key(key)
        subject = identity_subject(cid, fid)
        if entry.get("retake"):
            out.append({
                "severity": "warning",
                "field": f"{field_prefix}/{subject}",
                "message": (
                    f"身份漂移触发重拍定妆：{entry.get('retake_reason') or '达阈值'}"
                    f"（最近坏镜 {entry.get('last_drift_shot') or '?'}）"
                ),
                "proposed_fix": f"重跑定妆 --retry {subject}，或改显式 agnes_mode=keyframe 用帧级锚",
            })
    return out
