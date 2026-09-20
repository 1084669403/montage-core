"""Pure prompt-contract projection for m1 evidence.

The contract separates three evidence layers: text anchors in the prompt,
references actually sent to the provider, and optional VLM verification.
Missing VLM evidence is never reported as visual verification.
"""

from __future__ import annotations

import hashlib
from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def prompt_hash(prompt: str) -> str:
    """Stable hash for traceability; empty prompts hash to empty."""
    value = str(prompt or "")
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def build_prompt_contract(
    shot: dict[str, Any],
    *,
    registry: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build required anchors and forbidden terms from scene facts."""
    from montage.engine.edit_metrics import shot_anchor_groups

    groups = shot_anchor_groups(shot, registry=registry)
    explicit = (
        shot.get("prompt_contract")
        if isinstance(shot.get("prompt_contract"), dict)
        else {}
    )
    required_characters: list[str] = []
    required_outfits: list[str] = []
    required_props: list[str] = []
    required_locations: list[str] = []
    for group in groups:
        kind = str(group.get("kind"))
        gid = str(group.get("id"))
        if kind == "character_appearance":
            required_characters.append(gid)
        elif kind == "character_outfit":
            required_outfits.append(gid)
        elif kind == "prop":
            required_props.append(gid)
        elif kind == "location":
            required_locations.append(gid)
    return {
        "source": "explicit" if explicit else "derived",
        "required_characters": _unique(required_characters),
        "required_outfits": _unique(required_outfits),
        "required_props": _unique(required_props),
        "required_locations": _unique(required_locations),
        "forbidden_objects": [
            _text(row) for row in (explicit.get("forbidden_objects") or [])
            if _text(row)
        ],
        "forbidden_text": [
            _text(row) for row in (explicit.get("forbidden_text") or [])
            if _text(row)
        ],
    }


def _sent_reference_rows(binding: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = ((binding or {}).get("refs") or [])
    return [row for row in rows if isinstance(row, dict)]


def _ref_covers(row: dict[str, Any], kind: str, target_id: str) -> bool:
    """Whether a sent/recomputed binding row covers a required anchor id."""
    if not target_id:
        return False
    kind_fields = {
        "character": ("character_id", "id"),
        "location": ("location_id", "id"),
        "prop": ("prop_id", "id"),
    }
    return any(_text(row.get(field)) == target_id for field in kind_fields[kind])


def reference_requirements(
    contract: dict[str, Any],
) -> list[dict[str, str]]:
    """Flatten required identities, locations, and props into ref targets."""
    rows: list[dict[str, str]] = []
    for cid in contract.get("required_characters") or []:
        rows.append({"kind": "character", "id": str(cid)})
    for oid in contract.get("required_outfits") or []:
        rows.append({"kind": "character", "id": str(oid)})
    for lid in contract.get("required_locations") or []:
        rows.append({"kind": "location", "id": str(lid)})
    for pid in contract.get("required_props") or []:
        rows.append({"kind": "prop", "id": str(pid)})
    return rows


def evaluate_reference_coverage(
    contract: dict[str, Any],
    binding: dict[str, Any] | None,
    *,
    evidence_available: bool = True,
) -> dict[str, Any]:
    """Audit required refs against image bindings, not prompt text.

    ``evidence_available=False``（项目里根本没有 image_bindings 产物，例如
    本地/degraded 跑法）表示**没测到**而不是没达标：按仓库 P0-4 口径标
    ``skipped``，不进 m1 失败统计。一旦有绑定产物（哪怕只记了一部分），
    缺参考仍然逐镜报出来——B1「禁静默降级」不受影响。
    """
    rows = _sent_reference_rows(binding)
    required = reference_requirements(contract)
    if not evidence_available and binding is None:
        return {
            "required": len(required),
            "covered": 0,
            "missing": [],
            "coverage": 1.0,
            "sent": 0,
            "sent_coverage": 1.0,
            "binding_present": False,
            "skipped": True,
            "reason": "no_image_bindings",
        }
    covered_rows: list[dict[str, str]] = []
    missing_rows: list[dict[str, str]] = []
    sent_rows = 0
    for requirement in required:
        kind = requirement["kind"]
        target = requirement["id"]
        matching = [row for row in rows if _ref_covers(row, kind, target)]
        item = {"kind": kind, "id": target}
        if matching:
            covered_rows.append(item)
            if any(
                row.get("picture_index") is not None
                or str(row.get("source") or "") == "sent_plan"
                for row in matching
            ):
                sent_rows += 1
        else:
            missing_rows.append(item)
    total = len(required)
    covered = len(covered_rows)
    return {
        "required": total,
        "covered": covered,
        "missing": missing_rows,
        "coverage": round(covered / total, 3) if total else 1.0,
        "sent": sent_rows,
        "sent_coverage": round(sent_rows / total, 3) if total else 1.0,
        "binding_present": binding is not None,
        "skipped": False,
    }


def audit_forbidden_text(contract: dict[str, Any], prompt: str) -> list[str]:
    """Return forbidden strings that literally entered the prompt."""
    value = str(prompt or "")
    return [term for term in (contract.get("forbidden_text") or []) if term in value]


def evaluate_prompt_contract(
    shot: dict[str, Any],
    *,
    prompt: str,
    registry: dict[str, dict[str, Any]] | None = None,
    image_binding: dict[str, Any] | None = None,
    vlm_row: dict[str, Any] | None = None,
    reference_evidence_available: bool = True,
) -> dict[str, Any]:
    """Build one shot's text/reference/visual evidence, read-only."""
    from montage.engine.edit_metrics import shot_anchor_groups, _hit_any

    contract = build_prompt_contract(shot, registry=registry)
    groups = shot_anchor_groups(shot, registry=registry)
    missing_groups = [
        group for group in groups if not _hit_any(group["tokens"], str(prompt or ""))
    ]
    group_count = len(groups)
    covered = group_count - len(missing_groups)
    text_coverage = round(covered / group_count, 3) if group_count else 1.0
    reference = evaluate_reference_coverage(
        contract, image_binding, evidence_available=reference_evidence_available,
    )
    forbidden = audit_forbidden_text(contract, prompt)
    row = vlm_row or {}
    skipped = bool(row.get("skipped")) if row else True
    checked = bool(row) and not skipped
    return {
        "prompt_hash": prompt_hash(prompt),
        "contract": contract,
        "text": {
            "groups": group_count,
            "covered": covered,
            "missing": [
                f"{group.get('kind')}/{group.get('id')}" for group in missing_groups
            ],
            "coverage": text_coverage,
        },
        "reference": reference,
        "forbidden_text_hits": forbidden,
        "visual_verification": {
            "checked": checked,
            "skipped": not checked,
            "ok": bool(row.get("ok")) if checked else False,
            "verified": checked and bool(row.get("ok")),
        },
    }


def annotate_prompt_pair(
    shot: dict[str, Any],
    pair: dict[str, Any],
    *,
    registry: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach a coverage table to a prompt pair without changing old fields.

    The audit uses the combined first-frame + video prompt because identity
    anchors may intentionally live only in the static side of i2v prompts.
    """
    combined_prompt = "\n".join([
        str(pair.get("first_frame_prompt") or ""),
        str(pair.get("video_prompt") or ""),
    ])
    evidence = evaluate_prompt_contract(
        shot,
        prompt=combined_prompt,
        registry=registry,
    )
    return {
        **pair,
        "prompt_contract": evidence["contract"],
        "prompt_hash": evidence["prompt_hash"],
        "anchor_coverage": evidence["text"],
        "reference_requirements": reference_requirements(evidence["contract"]),
        "forbidden_text_hits": evidence["forbidden_text_hits"],
    }
