"""Read-only projection of explicit cross-scene transition contracts.

A transition contract is an explicit editorial decision.  This module never
chooses a transition or rewrites compose data; it only makes missing or invalid
decisions visible before the project is assembled.
"""

from __future__ import annotations

from typing import Any


CONTRACT_DECISIONS = {
    "fade",
    "xfade",
    "match_cut",
    "audio_bridge",
    "shared_element",
    "establishing_shot",
    "user_accepted_hard_cut",
}


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _chapter_map(scene_plan: dict[str, Any] | None) -> dict[str, str]:
    scenes = (scene_plan or {}).get("scenes") or []
    return {
        _text(row.get("id")): _text(row.get("chapter_id"))
        for row in scenes
        if isinstance(row, dict) and _text(row.get("id"))
    }


def _boundary_contract(
    compose_plan: dict[str, Any], to_shot_id: str
) -> dict[str, Any] | None:
    """Read the additive boundary contract; incoming shot wins."""
    contracts = compose_plan.get("transition_contracts")
    if isinstance(contracts, dict):
        value = contracts.get(to_shot_id)
        return value if isinstance(value, dict) else None
    if isinstance(contracts, list):
        for row in contracts:
            if isinstance(row, dict) and _text(row.get("to_shot_id")) == to_shot_id:
                return row
    return None


def _normalize_contract(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        return {"errors": ["transition_contract_not_object"]}
    decision = _text(value.get("decision"))
    reason = _text(value.get("reason"))
    errors: list[str] = []
    if not decision:
        errors.append("missing_decision")
    elif decision not in CONTRACT_DECISIONS:
        errors.append("invalid_decision")
    if not reason:
        errors.append("missing_reason")
    return {
        "decision": decision or None,
        "reason": reason or None,
        "accepted_by": _text(value.get("accepted_by")) or None,
        "evidence": _text(value.get("evidence")) or None,
        "errors": errors,
    }


def _execution_status(
    contract: dict[str, Any] | None, transition: str, duration: float | None
) -> str:
    if contract is None:
        return "missing_contract"
    if contract.get("errors"):
        return "invalid_contract"
    decision = str(contract.get("decision"))
    is_hard_cut = transition == "cut" or duration is None or duration <= 0
    if decision == "user_accepted_hard_cut":
        return "accepted_hard_cut" if is_hard_cut else "mismatched_transition"
    if decision == "fade":
        expected = transition in {"fade", "crossfade"} and not is_hard_cut
        return "contracted" if expected else "mismatched_transition"
    if decision == "xfade":
        return "contracted" if not is_hard_cut else "mismatched_transition"
    # Narrative bridges (match cut, audio, shared element, establishing shot)
    # may be executed as a hard cut; compose-side execution is audited later.
    return "contracted"


def build_transition_contract_projection(
    *,
    compose_plan: dict[str, Any] | None,
    scene_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project all cross-scene boundaries and their explicit decisions."""
    shots = [
        row
        for row in (compose_plan or {}).get("shots") or []
        if isinstance(row, dict)
    ]
    chapters = _chapter_map(scene_plan)
    boundaries: list[dict[str, Any]] = []
    for index, incoming in enumerate(shots[1:], start=1):
        outgoing = shots[index - 1]
        from_scene = _text(outgoing.get("scene_id"))
        to_scene = _text(incoming.get("scene_id"))
        if not from_scene or not to_scene or from_scene == to_scene:
            continue
        from_chapter = chapters.get(from_scene) or _text(outgoing.get("chapter_id"))
        to_chapter = chapters.get(to_scene) or _text(incoming.get("chapter_id"))
        raw_contract = incoming.get("transition_contract")
        if raw_contract is None:
            raw_contract = _boundary_contract(compose_plan or {}, _text(incoming.get("shot_id")))
        contract = _normalize_contract(raw_contract)
        transition = _text(incoming.get("transition")) or "cut"
        duration = _safe_float(incoming.get("transition_duration"))
        status = _execution_status(contract, transition, duration)
        boundaries.append({
            "boundary_index": index,
            "from_shot_id": _text(outgoing.get("shot_id")),
            "to_shot_id": _text(incoming.get("shot_id")),
            "from_scene_id": from_scene,
            "to_scene_id": to_scene,
            "from_chapter_id": from_chapter or None,
            "to_chapter_id": to_chapter or None,
            "same_chapter": bool(from_chapter and from_chapter == to_chapter),
            "current_transition": transition,
            "current_transition_duration_seconds": duration or 0.0,
            "contract": contract if raw_contract is not None else None,
            "status": status,
        })

    summary = {
        "cross_scene_boundaries": len(boundaries),
        "missing_contract": sum(row["status"] == "missing_contract" for row in boundaries),
        "invalid_contract": sum(row["status"] == "invalid_contract" for row in boundaries),
        "mismatched_transition": sum(
            row["status"] == "mismatched_transition" for row in boundaries
        ),
        "contracted": sum(row["status"] == "contracted" for row in boundaries),
        "accepted_hard_cut": sum(
            row["status"] == "accepted_hard_cut" for row in boundaries
        ),
    }
    return {
        "kind": "transition_contract_projection",
        "changes_compose_plan": False,
        "boundaries": boundaries,
        "summary": summary,
    }


def format_transition_contract_projection(row: dict[str, Any] | None) -> str:
    """Render compact read-only facts for CLI and review cards."""
    summary = (row or {}).get("summary") or {}
    return (
        f"contracts={summary.get('cross_scene_boundaries', 0)} "
        f"missing={summary.get('missing_contract', 0)} "
        f"invalid={summary.get('invalid_contract', 0)} "
        f"mismatched={summary.get('mismatched_transition', 0)} "
        f"contracted={summary.get('contracted', 0)} "
        f"acceptedHard={summary.get('accepted_hard_cut', 0)}"
    )
