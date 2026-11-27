"""Outreach assist batch (v2+): channel pick + WA promote Ready.

IG leads get channel=instagram and stay on To contact for human warm.
WA leads get channel=whatsapp and promote_to_ready when gates pass.
Does not send DMs or tick IG warm.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import or_, select

from app.models import Business
from app.services.automation import (
    apply_outreach_channel,
    mark_for_contact,
    promote_to_ready,
    _channel_facts,
)


def _is_to_contact_candidate(b: Business) -> bool:
    if not bool(getattr(b, "marked_for_contact", False)):
        return False
    if bool(getattr(b, "ready_for_contact", False)):
        return False
    if bool(getattr(b, "contacted", False)):
        return False
    if bool(getattr(b, "lost", False)):
        return False
    if (getattr(b, "current_booking_software", None) or "").strip():
        return False
    if bool(getattr(b, "already_connected", False)):
        return False
    if bool(getattr(b, "cannot_contact", False)):
        return False
    if bool(getattr(b, "archived", False)):
        return False
    return True


def select_outreach_batch_ids(
    db,
    *,
    country: str = "",
    city: str = "",
    limit: int = 25,
    business_ids: Optional[list[int]] = None,
) -> list[int]:
    limit = max(1, min(100, int(limit or 25)))
    if business_ids:
        ids = [int(i) for i in business_ids if i is not None][:limit]
        rows = db.scalars(select(Business).where(Business.id.in_(ids))).all()
        by_id = {b.id: b for b in rows}
        return [i for i in ids if i in by_id and _is_to_contact_candidate(by_id[i])]

    stmt = select(Business).where(
        Business.marked_for_contact.is_(True),
        or_(Business.ready_for_contact.is_(False), Business.ready_for_contact.is_(None)),
        or_(Business.contacted.is_(False), Business.contacted.is_(None)),
        or_(Business.lost.is_(False), Business.lost.is_(None)),
        or_(Business.current_booking_software.is_(None), Business.current_booking_software == ""),
        or_(Business.already_connected.is_(False), Business.already_connected.is_(None)),
        or_(Business.cannot_contact.is_(False), Business.cannot_contact.is_(None)),
        or_(Business.archived.is_(False), Business.archived.is_(None)),
    )
    country = (country or "").strip()
    city = (city or "").strip()
    if country:
        stmt = stmt.where(Business.country == country)
    if city:
        stmt = stmt.where(Business.city == city)
    stmt = stmt.order_by(Business.id.asc()).limit(limit)
    return [int(b.id) for b in db.scalars(stmt).all()]


def process_outreach_lead(db, b: Business, *, actor_id: Optional[int] = None) -> dict[str, Any]:
    """Set channel and optionally promote WA. Returns action summary."""
    if not _is_to_contact_candidate(b):
        return {"id": b.id, "action": "skipped", "reason": "not_to_contact"}

    # Ensure marked (idempotent)
    if not b.marked_for_contact:
        mark_for_contact(db, b, actor_id=actor_id, marked=True)

    facts = _channel_facts(b)
    try:
        if facts.get("has_instagram"):
            apply_outreach_channel(b, "instagram")
            db.commit()
            db.refresh(b)
            return {
                "id": b.id,
                "action": "instagram_warm",
                "outreach_channel": "instagram",
                "ready_for_contact": bool(b.ready_for_contact),
                "reason": "IG handle — left for human warm",
            }
        if facts.get("has_phone"):
            apply_outreach_channel(b, "whatsapp")
            db.commit()
            result = promote_to_ready(db, b, actor_id=actor_id)
            return {
                "id": b.id,
                "action": "whatsapp_ready",
                "outreach_channel": "whatsapp",
                "ready_for_contact": True,
                "lined_up": result.get("lined_up"),
                "reason": "",
            }
        if facts.get("has_email"):
            apply_outreach_channel(b, "email")
            db.commit()
            result = promote_to_ready(db, b, actor_id=actor_id)
            return {
                "id": b.id,
                "action": "email_ready",
                "outreach_channel": "email",
                "ready_for_contact": True,
                "lined_up": result.get("lined_up"),
                "reason": "",
            }
    except ValueError as exc:
        db.rollback()
        return {"id": b.id, "action": "skipped", "reason": str(exc)}

    return {"id": b.id, "action": "skipped", "reason": "no_channel"}
