"""Read-only commercial status DTOs for prospect drawer / monitoring."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select

from app.models import BeDelivery, BeMilestoneSnapshot, Business
from app.services.booking_engine.connect import be_connection_public, crm_lead_id_for
from app.services.booking_engine.handoff import delivery_dict


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def milestone_dict(m: BeMilestoneSnapshot) -> dict:
    overdue = False
    st = (m.status or "").lower()
    if st == "overdue":
        overdue = True
    elif st == "pending" and m.due_at and m.due_at < _utcnow():
        overdue = True
    return {
        "id": m.id,
        "delivery_id": m.delivery_id,
        "ah_milestone_id": m.ah_milestone_id,
        "name": m.name,
        "amount_cents": m.amount_cents,
        "currency": m.currency,
        "due_at": m.due_at.isoformat() if m.due_at else "",
        "status": "overdue" if overdue and st == "pending" else (m.status or "pending"),
        "pay_url": m.pay_url or "",
        "last_synced_at": m.last_synced_at.isoformat() if m.last_synced_at else "",
        "read_only": True,
    }


def milestone_summary(milestones: list[BeMilestoneSnapshot]) -> dict:
    paid = pending = overdue = 0
    collected = outstanding = 0
    next_due = None
    now = _utcnow()
    for m in milestones:
        st = (m.status or "").lower()
        amt = int(m.amount_cents or 0)
        if st == "paid":
            paid += 1
            collected += amt
        else:
            is_overdue = st == "overdue" or (st == "pending" and m.due_at and m.due_at < now)
            if is_overdue:
                overdue += 1
            else:
                pending += 1
            outstanding += amt
            if m.due_at and (next_due is None or m.due_at < next_due):
                next_due = m.due_at
    return {
        "milestone_count": len(milestones),
        "paid_milestones": paid,
        "pending_milestones": pending,
        "overdue_milestones": overdue,
        "amount_collected_cents": collected,
        "outstanding_balance_cents": outstanding,
        "next_due_at": next_due.isoformat() if next_due else "",
        "read_only": True,
    }


def commercial_for_business(db, business_id: int) -> dict:
    business = db.get(Business, business_id)
    if not business:
        from fastapi import HTTPException

        raise HTTPException(404, detail={"error": "Prospect not found", "code": "PROSPECT_NOT_FOUND"})
    conn = be_connection_public(db=db)
    deliveries = list(
        db.scalars(
            select(BeDelivery).where(BeDelivery.business_id == business_id).order_by(BeDelivery.id.desc())
        ).all()
    )
    latest = deliveries[0] if deliveries else None
    milestones: list[BeMilestoneSnapshot] = []
    if latest:
        milestones = list(
            db.scalars(
                select(BeMilestoneSnapshot)
                .where(BeMilestoneSnapshot.delivery_id == latest.id)
                .order_by(BeMilestoneSnapshot.id.asc())
            ).all()
        )
    d = delivery_dict(latest) if latest else None
    install = (latest.installation_id if latest else "") or (business.be_installation_id or "")
    return {
        "connection": {
            "connected": conn.get("connected"),
            "connection_status": conn.get("connection_status"),
            "base_url": conn.get("base_url"),
            "last_health_at": conn.get("last_health_at"),
            "last_sync_ok_at": conn.get("last_sync_ok_at"),
            "last_sync_fail_at": conn.get("last_sync_fail_at"),
        },
        "customer_reference": (latest.customer_ref if latest and latest.customer_ref else "")
        or crm_lead_id_for(business.id),
        # Prefer AH orderReference; fall back to purchaseId for older rows.
        "order_reference": (latest.order_reference if latest and latest.order_reference else "") or "",
        "purchase_order_reference": (
            (latest.order_reference if latest and latest.order_reference else "")
            or (latest.purchase_id if latest else "")
            or ""
        ),
        "purchase_id": (latest.purchase_id if latest else "") or "",
        "inbound_request_id": (latest.inbound_request_id if latest else "") or "",
        "package": (latest.package_name if latest else "") or "",
        "package_revision": (latest.revision_id if latest else "") or "",
        "order_type": (latest.order_type if latest else "") or (latest.purpose if latest else "") or "",
        "quotation_status": (latest.quote_status if latest else "") or "",
        "agreement_status": (latest.agreement_status if latest else "") or "",
        "invoice_status": (latest.invoice_status if latest else "") or "",
        "payment_status": (latest.financial_status if latest else "") or (latest.status if latest else "") or "",
        "paid_amount_cents": int(latest.paid_amount_cents or 0) if latest else 0,
        "outstanding_balance_cents": int(latest.outstanding_cents or 0) if latest else 0,
        "currency": (latest.currency if latest else business.currency) or "USD",
        "payment_url": (latest.pay_url if latest else "") or "",
        "delivery_status": (latest.delivery_status if latest else "") or "",
        "release_status": (latest.release_status if latest else "") or "",
        "installation_id": install,
        "upgrade_status": (latest.upgrade_status if latest else "") or "",
        "last_synchronized_at": (
            latest.last_synced_at.isoformat()
            if latest and latest.last_synced_at
            else (latest.updated_at.isoformat() if latest and latest.updated_at else "")
        ),
        "delivery": d,
        "deliveries": [delivery_dict(x) for x in deliveries[:20]],
        "milestones": [milestone_dict(m) for m in milestones],
        "milestone_summary": milestone_summary(milestones),
        "read_only_commercial": True,
    }


def monitor_bundle(db, *, limit: int = 12) -> dict:
    from app.services.booking_engine.connect import be_connection_public
    from app.services.booking_engine.events import event_counts, inbox_dict, list_events, prospect_name_for

    conn = be_connection_public(db=db)
    events = list_events(db, limit=limit)
    event_rows = []
    for ev in events:
        item = inbox_dict(ev)
        item["prospect_name"] = prospect_name_for(db, ev.business_id)
        event_rows.append(item)

    def _recent(purpose: Optional[str] = None, status_prefix: str = ""):
        q = select(BeDelivery).order_by(BeDelivery.id.desc()).limit(limit)
        rows = list(db.scalars(q).all())
        out = []
        for r in rows:
            if purpose and r.purpose != purpose:
                continue
            if status_prefix and not (r.status or "").startswith(status_prefix) and status_prefix not in (
                r.financial_status or "",
                r.delivery_status or "",
                r.upgrade_status or "",
            ):
                # loose filter — include by purpose primarily
                pass
            d = delivery_dict(r)
            d["prospect_name"] = prospect_name_for(db, r.business_id)
            out.append(d)
            if len(out) >= limit:
                break
        return out

    purchases = _recent("purchase")
    requests = _recent("request")
    upgrades = _recent("upgrade")
    paid = [
        delivery_dict(r)
        | {"prospect_name": prospect_name_for(db, r.business_id)}
        for r in db.scalars(
            select(BeDelivery)
            .where(
                (BeDelivery.status == "paid")
                | (BeDelivery.financial_status.in_(["paid", "partially_paid", "pending"]))
            )
            .order_by(BeDelivery.id.desc())
            .limit(limit)
        ).all()
    ]
    deliveries_ready = [
        delivery_dict(r)
        | {"prospect_name": prospect_name_for(db, r.business_id)}
        for r in db.scalars(
            select(BeDelivery)
            .where(
                (BeDelivery.status == "delivery_ready")
                | (BeDelivery.delivery_status.in_(["ready", "delivered", "started", "finalized"]))
            )
            .order_by(BeDelivery.id.desc())
            .limit(limit)
        ).all()
    ]

    # Commercial funnel counts for dashboard
    funnel = {
        "appointhive_requests": db.scalar(
            select(func.count()).select_from(BeDelivery).where(BeDelivery.purpose == "request")
        )
        or 0,
        "awaiting_quotations": db.scalar(
            select(func.count())
            .select_from(BeDelivery)
            .where(BeDelivery.purpose == "request", BeDelivery.status.in_(["sent"]))
        )
        or 0,
        "awaiting_agreements": db.scalar(
            select(func.count())
            .select_from(BeDelivery)
            .where(BeDelivery.quote_status.in_(["accepted", "quoted"]))
            .where(BeDelivery.agreement_status.in_(["pending", "sent"]))
        )
        or 0,
        "awaiting_payments": db.scalar(
            select(func.count())
            .select_from(BeDelivery)
            .where(BeDelivery.status.in_(["pending_payment", "sent"]))
            .where(BeDelivery.pay_url != "")
        )
        or 0,
        "partially_paid_orders": db.scalar(
            select(func.count())
            .select_from(BeDelivery)
            .where(BeDelivery.financial_status == "partially_paid")
        )
        or 0,
        "fully_paid_orders": db.scalar(
            select(func.count())
            .select_from(BeDelivery)
            .where((BeDelivery.status == "paid") | (BeDelivery.financial_status == "paid"))
        )
        or 0,
        "awaiting_delivery": db.scalar(
            select(func.count())
            .select_from(BeDelivery)
            .where(
                (BeDelivery.status == "paid")
                | (BeDelivery.financial_status == "paid"),
                BeDelivery.delivery_status.notin_(["delivered", "ready"]),
            )
        )
        or 0,
        "delivered_customers": db.scalar(
            select(func.count())
            .select_from(BeDelivery)
            .where(
                (BeDelivery.status == "delivery_ready")
                | (BeDelivery.delivery_status.in_(["delivered", "ready"]))
            )
        )
        or 0,
        "upgrade_opportunities": db.scalar(
            select(func.count()).select_from(Business).where(Business.be_installation_id != "")
        )
        or 0,
    }

    be_collected: dict[str, float] = {}
    be_outstanding: dict[str, float] = {}
    for r in db.scalars(select(BeDelivery).where(BeDelivery.paid_amount_cents > 0)).all():
        cur = (r.currency or "USD").upper()
        be_collected[cur] = be_collected.get(cur, 0.0) + (int(r.paid_amount_cents or 0) / 100.0)
    for r in db.scalars(select(BeDelivery).where(BeDelivery.outstanding_cents > 0)).all():
        cur = (r.currency or "USD").upper()
        be_outstanding[cur] = be_outstanding.get(cur, 0.0) + (int(r.outstanding_cents or 0) / 100.0)

    from app.models import BeOutboundFailure

    outbound_failed = db.scalar(
        select(func.count()).select_from(BeOutboundFailure).where(BeOutboundFailure.status == "failed")
    ) or 0
    outbound_rows = [
        {
            "id": r.id,
            "business_id": r.business_id,
            "operation": r.operation,
            "error": r.error,
            "attempts": r.attempts,
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else "",
            "prospect_name": prospect_name_for(db, r.business_id),
        }
        for r in db.scalars(
            select(BeOutboundFailure)
            .where(BeOutboundFailure.status == "failed")
            .order_by(BeOutboundFailure.id.desc())
            .limit(limit)
        ).all()
    ]

    return {
        "connection": conn,
        "event_counts": event_counts(db),
        "recent_events": event_rows,
        "recent_purchases": purchases,
        "recent_quotations": requests,
        "recent_payment_updates": paid,
        "recent_deliveries": deliveries_ready,
        "recent_upgrades": upgrades,
        "funnel": funnel,
        "be_verified_collected": {"by_currency": be_collected, "note": "From AppointHive payment snapshots only"},
        "be_outstanding": {"by_currency": be_outstanding},
        "outbound_failed": int(outbound_failed),
        "recent_outbound_failures": outbound_rows,
    }
