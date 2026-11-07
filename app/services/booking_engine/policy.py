"""Sales Won policy — payment events must not alone decide Won unless configured."""
from __future__ import annotations

import json
from typing import Any

from app.services.secrets import resolve_secret, store_setting_value

# Default: financial snapshot only on BE payment; operators (or policy flags) move Won.
DEFAULT_WON_POLICY: dict[str, Any] = {
    "auto_won_on_be_paid": False,
    "require_agreement_accepted": False,
    "require_initial_milestone_paid": False,
    "require_fully_paid": True,
}


def get_won_policy(db=None) -> dict[str, Any]:
    raw = resolve_secret("be.won_policy", "", "", db=db) or ""
    if not raw:
        return dict(DEFAULT_WON_POLICY)
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            return dict(DEFAULT_WON_POLICY)
        out = dict(DEFAULT_WON_POLICY)
        out.update({k: data[k] for k in DEFAULT_WON_POLICY if k in data})
        return out
    except Exception:
        return dict(DEFAULT_WON_POLICY)


def save_won_policy(db, policy: dict) -> dict:
    out = dict(DEFAULT_WON_POLICY)
    if isinstance(policy, dict):
        out.update({k: bool(policy[k]) for k in DEFAULT_WON_POLICY if k in policy})
    store_setting_value(db, "be.won_policy", json.dumps(out), secret=False)
    db.commit()
    return out


def should_mirror_won(db, delivery) -> tuple[bool, str]:
    """Return (allowed, reason). Payment alone is insufficient unless auto_won_on_be_paid."""
    policy = get_won_policy(db)
    if not policy.get("auto_won_on_be_paid"):
        return False, "auto_won_on_be_paid=false — payment updates financial snapshot only"
    if policy.get("require_fully_paid"):
        fin = (getattr(delivery, "financial_status", None) or getattr(delivery, "status", "") or "").lower()
        if fin not in ("paid", "fully_paid"):
            return False, "require_fully_paid — financial_status is not paid"
        # Partial payments must never count as fully paid.
        if fin in ("partial", "partially_paid"):
            return False, "partial payment is not fully paid"
    if policy.get("require_agreement_accepted"):
        agr = (getattr(delivery, "agreement_status", None) or "").lower()
        if agr not in ("accepted", "signed", "active"):
            return False, "require_agreement_accepted — agreement not accepted"
    if policy.get("require_initial_milestone_paid"):
        from sqlalchemy import select
        from app.models import BeMilestoneSnapshot

        ms = list(
            db.scalars(
                select(BeMilestoneSnapshot).where(BeMilestoneSnapshot.delivery_id == delivery.id)
            ).all()
        )
        if not ms:
            return False, "require_initial_milestone_paid — no milestone snapshots"
        first = sorted(ms, key=lambda m: (m.due_at is None, m.due_at or m.id, m.id))[0]
        if (first.status or "").lower() != "paid":
            return False, "require_initial_milestone_paid — first milestone not paid"
    return True, "won_policy satisfied"
