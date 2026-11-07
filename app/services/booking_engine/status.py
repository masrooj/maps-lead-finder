"""Latest AppointHive commercial status helpers for pipeline / timeline."""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select

from app.models import BeDelivery, BeEventInbox, BeOutboundFailure, Business


def latest_delivery_for(db, business_id: int) -> Optional[BeDelivery]:
    return db.scalar(
        select(BeDelivery).where(BeDelivery.business_id == business_id).order_by(BeDelivery.id.desc())
    )


def commercial_status_for(db, business_id: int) -> dict:
    row = latest_delivery_for(db, business_id)
    if not row:
        return {
            "financial_status": "",
            "delivery_status": "",
            "upgrade_status": "",
            "payment_status": "",
            "pay_url": "",
            "purchase_id": "",
            "customer_ref": "",
            "purpose": "",
            "status": "",
        }
    fin = (row.financial_status or "").strip() or (row.status or "")
    return {
        "financial_status": fin,
        "delivery_status": (row.delivery_status or "").strip()
        or (("ready" if row.status == "delivery_ready" else "") if row.status else ""),
        "upgrade_status": (row.upgrade_status or "").strip(),
        "payment_status": fin,
        "pay_url": row.pay_url or "",
        "purchase_id": row.purchase_id or "",
        "customer_ref": row.customer_ref or "",
        "purpose": row.purpose or "",
        "status": row.status or "",
        "quoted_amount_cents": int(row.quoted_amount_cents or 0),
        "paid_amount_cents": int(row.paid_amount_cents or 0),
        "outstanding_cents": int(row.outstanding_cents or 0),
        "currency": row.currency or "USD",
    }


def commercial_status_map(db, business_ids: list[int]) -> dict[int, dict]:
    if not business_ids:
        return {}
    rows = list(
        db.scalars(
            select(BeDelivery)
            .where(BeDelivery.business_id.in_(business_ids))
            .order_by(BeDelivery.id.desc())
        ).all()
    )
    out: dict[int, dict] = {}
    for row in rows:
        if row.business_id in out:
            continue
        out[row.business_id] = commercial_status_for(db, row.business_id)
        # reuse row without second query
        fin = (row.financial_status or "").strip() or (row.status or "")
        out[row.business_id] = {
            "financial_status": fin,
            "delivery_status": (row.delivery_status or "").strip()
            or (("ready" if row.status == "delivery_ready" else "") if row.status else ""),
            "upgrade_status": (row.upgrade_status or "").strip(),
            "payment_status": fin,
            "pay_url": row.pay_url or "",
            "purchase_id": row.purchase_id or "",
            "customer_ref": row.customer_ref or "",
            "purpose": row.purpose or "",
            "status": row.status or "",
            "quoted_amount_cents": int(row.quoted_amount_cents or 0),
            "paid_amount_cents": int(row.paid_amount_cents or 0),
            "outstanding_cents": int(row.outstanding_cents or 0),
            "currency": row.currency or "USD",
        }
    return out


def apply_customer_ref(db, business: Business, customer_ref: str) -> None:
    """Store AH customer ref on prospect; avoid inventing a second customer identity."""
    ref = (customer_ref or "").strip()
    if not ref or not business:
        return
    business.be_customer_ref = ref[:120]


def record_outbound_failure(
    db,
    *,
    business_id: Optional[int],
    operation: str,
    error: str,
    payload: Optional[dict] = None,
) -> BeOutboundFailure:
    import json
    from datetime import datetime, timezone

    row = BeOutboundFailure(
        business_id=business_id,
        operation=(operation or "")[:80],
        error=(error or "")[:2000],
        payload_json=json.dumps(payload or {}, default=str)[:20000],
        status="failed",
        attempts=1,
        last_attempt_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def resolve_outbound_failure(db, failure_id: int) -> Optional[BeOutboundFailure]:
    from datetime import datetime, timezone

    row = db.get(BeOutboundFailure, failure_id)
    if not row:
        return None
    row.status = "resolved"
    row.resolved_at = datetime.now(timezone.utc)
    db.add(row)
    return row


def timeline_be_items(db, business_id: int) -> list[dict]:
    """BE inbox + stage history fragments for unified timeline."""
    from app.models import PipelineStage, StageHistory

    items = []
    for ev in db.scalars(
        select(BeEventInbox)
        .where(BeEventInbox.business_id == business_id)
        .order_by(BeEventInbox.id.desc())
        .limit(100)
    ).all():
        items.append(
            {
                "source": "appointhive",
                "id": ev.id,
                "business_id": business_id,
                "action": ev.event_type,
                "channel": "appointhive",
                "note": f"AppointHive event · {ev.event_type} · {ev.status}",
                "message": ev.error or "",
                "outcome": ev.status,
                "created_at": ev.created_at.isoformat() if ev.created_at else "",
                "sort_at": (ev.processed_at or ev.created_at).isoformat()
                if (ev.processed_at or ev.created_at)
                else "",
            }
        )
    for h in db.scalars(
        select(StageHistory)
        .where(StageHistory.business_id == business_id)
        .order_by(StageHistory.id.desc())
        .limit(100)
    ).all():
        fr = db.get(PipelineStage, h.from_stage_id) if h.from_stage_id else None
        to = db.get(PipelineStage, h.to_stage_id) if h.to_stage_id else None
        items.append(
            {
                "source": "stage",
                "id": h.id,
                "business_id": business_id,
                "action": "stage_change",
                "channel": "pipeline",
                "note": f"{(fr.name if fr else '—')} → {(to.name if to else '—')}"
                + (f" · {h.note}" if h.note else ""),
                "message": h.note or "",
                "outcome": (to.slug if to else ""),
                "created_at": h.created_at.isoformat() if h.created_at else "",
                "sort_at": h.created_at.isoformat() if h.created_at else "",
            }
        )
    return items
