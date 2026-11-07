"""AppointHive inbound event normalization, inbox, and safe retry."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import func, select

from app.models import BeEventInbox, Business

# Canonical internal types → accepted inbound aliases (live BE uses standalone.*).
EVENT_ALIASES: dict[str, str] = {
    # Live Plan 2 names (identity)
    "crm.bridge.connected": "crm.bridge.connected",
    "standalone.purchase.recorded": "standalone.purchase.recorded",
    "standalone.payment.pending": "standalone.payment.pending",
    "standalone.payment.paid": "standalone.payment.paid",
    "standalone.payment.rejected": "standalone.payment.rejected",
    "standalone.delivery.ready": "standalone.delivery.ready",
    "standalone.installation.active": "standalone.installation.active",
    "standalone.upgrade.requested": "standalone.upgrade.requested",
    "standalone.upgrade.applied": "standalone.upgrade.applied",
    "standalone.upgrade.status": "standalone.upgrade.status",
    "standalone.quote.created": "quote.created",
    "standalone.inquiry.received": "standalone.inquiry.received",
    "standalone.license.issued": "standalone.license.issued",
    "standalone.catalog.updated": "standalone.catalog.updated",
    # Prompt-style / future aliases
    "purchase.created": "standalone.purchase.recorded",
    "quote.created": "quote.created",
    "quote.accepted": "quote.accepted",
    "agreement.accepted": "agreement.accepted",
    "invoice.created": "invoice.created",
    "payment.pending": "standalone.payment.pending",
    "payment.partially_paid": "payment.partially_paid",
    "payment.paid": "standalone.payment.paid",
    "payment.failed": "standalone.payment.rejected",
    "payment.refunded": "payment.refunded",
    "payment.disputed": "payment.disputed",
    "milestone.created": "milestone.created",
    "milestone.paid": "milestone.paid",
    "milestone.overdue": "milestone.overdue",
    "delivery.started": "delivery.started",
    "delivery.finalized": "delivery.finalized",
    "delivery.delivered": "standalone.delivery.ready",
    "upgrade.created": "standalone.upgrade.requested",
    "upgrade.applied": "standalone.upgrade.applied",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_event_type(raw: str) -> str:
    key = (raw or "").strip()
    return EVENT_ALIASES.get(key, key)


def extract_event_id(payload: dict) -> str:
    event = str(payload.get("event") or "unknown")
    return str(
        payload.get("eventId")
        or payload.get("id")
        or f"{event}:{payload.get('purchaseId') or ''}:{payload.get('requestId') or ''}:"
        f"{payload.get('installationId') or ''}:{payload.get('milestoneId') or ''}:"
        f"{payload.get('connectedAt') or ''}"
    )[:200]


def extract_event_version(payload: dict) -> int:
    try:
        return max(1, int(payload.get("eventVersion") or payload.get("version") or 1))
    except Exception:
        return 1


def guess_business_id(payload: dict) -> Optional[int]:
    crm_lead = str(payload.get("crmLeadId") or payload.get("externalCrmRef") or "").strip()
    if crm_lead.startswith("business:"):
        try:
            return int(crm_lead.split(":", 1)[1])
        except Exception:
            return None
    return None


def inbox_dict(row: BeEventInbox) -> dict:
    return {
        "id": row.id,
        "event_id": row.event_id,
        "event_type": row.event_type,
        "event_version": row.event_version,
        "status": row.status,
        "business_id": row.business_id,
        "delivery_id": row.delivery_id,
        "error": row.error or "",
        "attempts": row.attempts,
        "processed_at": row.processed_at.isoformat() if row.processed_at else "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        "payload": _safe_payload(row.payload_json),
    }


def _safe_payload(raw: str) -> Any:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {"raw": data}
    except Exception:
        return {}


def record_inbox_start(db, *, event_id: str, event_type: str, payload: dict, version: int = 1) -> BeEventInbox:
    """Insert or return existing inbox row. Caller handles duplicate short-circuit."""
    existing = db.scalar(select(BeEventInbox).where(BeEventInbox.event_id == event_id))
    if existing:
        return existing
    row = BeEventInbox(
        event_id=event_id,
        event_type=normalize_event_type(event_type),
        event_version=version,
        status="pending",
        business_id=guess_business_id(payload),
        payload_json=json.dumps(payload, default=str)[:50000],
        attempts=1,
    )
    db.add(row)
    db.flush()
    return row


def is_already_processed(row: BeEventInbox) -> bool:
    return row.status in ("processed", "duplicate") and row.processed_at is not None


def mark_inbox_processed(db, row: BeEventInbox, *, delivery_id: Optional[int] = None, business_id: Optional[int] = None) -> None:
    row.status = "processed"
    row.error = ""
    row.processed_at = _utcnow()
    if delivery_id is not None:
        row.delivery_id = delivery_id
    if business_id is not None:
        row.business_id = business_id
    db.add(row)


def mark_inbox_failed(db, row: BeEventInbox, error: str) -> None:
    row.status = "failed"
    row.error = (error or "")[:2000]
    row.attempts = int(row.attempts or 1)
    db.add(row)


def mark_inbox_duplicate(db, row: BeEventInbox) -> None:
    row.status = "duplicate"
    row.processed_at = _utcnow()
    db.add(row)


def event_counts(db) -> dict:
    rows = db.execute(
        select(BeEventInbox.status, func.count()).group_by(BeEventInbox.status)
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    return {
        "pending": counts.get("pending", 0),
        "processed": counts.get("processed", 0),
        "failed": counts.get("failed", 0),
        "duplicate": counts.get("duplicate", 0),
        "total": sum(counts.values()),
    }


def list_events(db, *, status: Optional[str] = None, limit: int = 50) -> list[BeEventInbox]:
    q = select(BeEventInbox).order_by(BeEventInbox.id.desc()).limit(min(limit, 200))
    if status:
        q = select(BeEventInbox).where(BeEventInbox.status == status).order_by(BeEventInbox.id.desc()).limit(min(limit, 200))
    return list(db.scalars(q).all())


def get_event(db, event_pk: int) -> Optional[BeEventInbox]:
    return db.get(BeEventInbox, event_pk)


def retry_failed_event(db, event_pk: int) -> dict:
    """Re-apply a failed inbox event. Safe for idempotent handoff handlers."""
    from app.services.booking_engine.handoff import apply_be_webhook_payload

    row = db.get(BeEventInbox, event_pk)
    if not row:
        from fastapi import HTTPException

        raise HTTPException(404, detail={"error": "Event not found", "code": "EVENT_NOT_FOUND"})
    if row.status not in ("failed", "pending"):
        from fastapi import HTTPException

        raise HTTPException(
            409,
            detail={"error": "Only failed or pending events can be retried", "code": "EVENT_NOT_RETRYABLE"},
        )
    payload = _safe_payload(row.payload_json)
    if not isinstance(payload, dict) or not payload:
        from fastapi import HTTPException

        raise HTTPException(422, detail={"error": "Empty event payload", "code": "EVENT_EMPTY"})
    row.attempts = int(row.attempts or 0) + 1
    row.status = "pending"
    row.error = ""
    db.add(row)
    db.flush()
    try:
        result = apply_be_webhook_payload(db, payload, inbox_row=row)
        return {"ok": True, "event": inbox_dict(row), "result": result}
    except Exception as exc:
        mark_inbox_failed(db, row, str(exc))
        db.commit()
        from fastapi import HTTPException

        raise HTTPException(502, detail={"error": str(exc), "code": "EVENT_RETRY_FAILED"}) from exc


def retry_failed_events_batch(db, *, limit: int = 10, max_attempts: int = 8) -> dict:
    """Cron-safe batch retry of failed (and stuck pending) inbox rows."""
    from app.services.booking_engine.handoff import apply_be_webhook_payload

    limit = max(1, min(int(limit or 10), 50))
    max_attempts = max(1, min(int(max_attempts or 8), 20))
    rows = list(
        db.scalars(
            select(BeEventInbox)
            .where(
                BeEventInbox.status.in_(["failed", "pending"]),
                BeEventInbox.attempts < max_attempts,
            )
            .order_by(BeEventInbox.id.asc())
            .limit(limit)
        ).all()
    )
    retried = succeeded = failed = 0
    details: list[dict] = []
    for row in rows:
        retried += 1
        payload = _safe_payload(row.payload_json)
        if not isinstance(payload, dict) or not payload:
            mark_inbox_failed(db, row, "Empty event payload")
            failed += 1
            details.append({"id": row.id, "ok": False, "error": "empty"})
            continue
        row.attempts = int(row.attempts or 0) + 1
        row.status = "pending"
        row.error = ""
        db.add(row)
        db.flush()
        try:
            apply_be_webhook_payload(db, payload, inbox_row=row)
            succeeded += 1
            details.append({"id": row.id, "ok": True})
        except Exception as exc:
            mark_inbox_failed(db, row, str(exc))
            failed += 1
            details.append({"id": row.id, "ok": False, "error": str(exc)[:200]})
            db.commit()
    if retried:
        try:
            db.commit()
        except Exception:
            db.rollback()
    return {
        "retried": retried,
        "succeeded": succeeded,
        "failed": failed,
        "details": details,
        "counts": event_counts(db),
    }


def prospect_name_for(db, business_id: Optional[int]) -> str:
    if not business_id:
        return ""
    biz = db.get(Business, business_id)
    return biz.name if biz else ""
