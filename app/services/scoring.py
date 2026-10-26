"""Explainable 0–10 qualification scorecard."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.models import Business

CRITERIA = (
    "business_fit",
    "operational_pain",
    "buying_urgency",
    "decision_maker_access",
    "product_fit",
)

CRITERIA_LABELS = {
    "business_fit": "Business fit",
    "operational_pain": "Operational pain",
    "buying_urgency": "Buying urgency",
    "decision_maker_access": "Access to decision-maker",
    "product_fit": "Product and implementation fit",
}

LEVEL_DESCRIPTIONS = {
    0: "No evidence / poor fit",
    1: "Partial evidence / mixed fit",
    2: "Strong evidence / clear fit",
}


def band_for_score(score: int) -> str:
    if score >= 8:
        return "Priority"
    if score >= 5:
        return "Investigate"
    return "Deprioritize"


def compute_score(criteria: dict[str, Any]) -> tuple[int, dict[str, int]]:
    cleaned: dict[str, int] = {}
    total = 0
    for key in CRITERIA:
        raw = criteria.get(key, 0)
        try:
            val = int(raw)
        except (TypeError, ValueError):
            val = 0
        if val < 0 or val > 2:
            raise HTTPException(422, f"{key} must be 0, 1, or 2")
        cleaned[key] = val
        total += val
    return total, cleaned


def apply_score(
    business: Business,
    criteria: dict[str, Any],
    *,
    override: int | None = None,
    override_reason: str = "",
) -> dict:
    total, cleaned = compute_score(criteria)
    if override is not None:
        if override < 0 or override > 10:
            raise HTTPException(422, "override must be 0–10")
        if not (override_reason or "").strip():
            raise HTTPException(422, "override_reason is required when overriding score")
        business.score_override = override
        business.score_override_reason = override_reason.strip()[:500]
        display = override
    else:
        business.score_override = None
        business.score_override_reason = ""
        display = total
    business.qualification_score = total
    business.score_criteria_json = json.dumps(cleaned)
    business.scores_verified_at = datetime.now(timezone.utc)
    return score_payload(business, display)


def score_payload(business: Business, display: int | None = None) -> dict:
    try:
        criteria = json.loads(business.score_criteria_json or "{}")
    except Exception:
        criteria = {}
    computed = business.qualification_score
    if display is None:
        display = business.score_override if business.score_override is not None else computed
    return {
        "computed_score": computed,
        "display_score": display,
        "override": business.score_override,
        "override_reason": business.score_override_reason or "",
        "band": band_for_score(int(display or 0)),
        "disclaimer": "A high score does not automatically mean a sale is likely.",
        "criteria": criteria,
        "criteria_labels": CRITERIA_LABELS,
        "level_descriptions": LEVEL_DESCRIPTIONS,
        "verified_at": business.scores_verified_at.isoformat() if business.scores_verified_at else "",
    }
