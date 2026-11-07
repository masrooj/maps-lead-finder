"""Send purchases/requests/upgrades and apply inbound Booking Engine webhooks."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Activity, BeDelivery, BeMilestoneSnapshot, Business, PipelineStage
from app.services.booking_engine.client import BeClientError, BookingEngineClient
from app.services.booking_engine.connect import crm_lead_id_for, require_be_connected, set_connection_status
from app.services.booking_engine.errors import be_http_detail
from app.services.booking_engine.events import normalize_event_type
from app.services.booking_engine.policy import should_mirror_won
from app.services.booking_engine.prospect import (
    build_crm_prospect,
    buyer_fields_from_prospect,
    format_crm_prospect_message,
)
from app.services.pipeline import get_stage, move_stage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_prospect(db, business: Business) -> dict:
    prospect = build_crm_prospect(db, business)
    email, _name, _phone = buyer_fields_from_prospect(prospect)
    if not email:
        raise HTTPException(
            422,
            detail={
                "error": (
                    "This prospect needs one valid buyer email before Send to delivery. "
                    "Open the Contact tab, set a real email, then retry."
                ),
                "code": "BUYER_EMAIL_REQUIRED",
            },
        )
    return prospect


def delivery_dict(d: BeDelivery) -> dict:
    return {
        "id": d.id,
        "business_id": d.business_id,
        "proposal_id": d.proposal_id,
        "purpose": d.purpose,
        "purchase_id": d.purchase_id,
        "inbound_request_id": d.inbound_request_id,
        "revision_id": d.revision_id,
        "from_revision_id": d.from_revision_id,
        "installation_id": d.installation_id,
        "pay_url": d.pay_url,
        "quoted_amount_cents": d.quoted_amount_cents,
        "currency": d.currency,
        "crm_lead_id": d.crm_lead_id,
        "status": d.status,
        "customer_ref": getattr(d, "customer_ref", "") or "",
        "order_reference": getattr(d, "order_reference", "") or "",
        "package_name": getattr(d, "package_name", "") or "",
        "order_type": getattr(d, "order_type", "") or "",
        "quote_status": getattr(d, "quote_status", "") or "",
        "agreement_status": getattr(d, "agreement_status", "") or "",
        "invoice_status": getattr(d, "invoice_status", "") or "",
        "financial_status": getattr(d, "financial_status", "") or "",
        "delivery_status": getattr(d, "delivery_status", "") or "",
        "upgrade_status": getattr(d, "upgrade_status", "") or "",
        "paid_amount_cents": int(getattr(d, "paid_amount_cents", 0) or 0),
        "outstanding_cents": int(getattr(d, "outstanding_cents", 0) or 0),
        "release_status": getattr(d, "release_status", "") or "",
        "last_synced_at": d.last_synced_at.isoformat() if getattr(d, "last_synced_at", None) else "",
        "last_event_json": d.last_event_json,
        "created_at": d.created_at.isoformat() if d.created_at else "",
        "updated_at": d.updated_at.isoformat() if d.updated_at else "",
    }


def _apply_snapshot_fields(row: BeDelivery, payload: dict) -> None:
    """Copy read-only commercial fields from BE payload when present."""
    mapping = {
        "customer_ref": ("customerRef", "customer_ref", "ahCustomerId"),
        "order_reference": ("orderReference", "order_reference", "orderRef"),
        "package_name": ("packageName", "package_name", "productName", "label"),
        "order_type": ("orderType", "order_type"),
        "quote_status": ("quoteStatus", "quotationStatus"),
        "agreement_status": ("agreementStatus",),
        "invoice_status": ("invoiceStatus",),
        "financial_status": ("financialStatus", "paymentStatus"),
        "delivery_status": ("deliveryStatus",),
        "upgrade_status": ("upgradeStatus",),
        "release_status": ("releaseStatus",),
    }
    for attr, keys in mapping.items():
        for k in keys:
            if payload.get(k) is not None and str(payload.get(k)).strip() != "":
                val = str(payload.get(k)).strip()[:200]
                # AH enums arrive UPPER_SNAKE; store lowercase for UI/filters.
                if attr in (
                    "financial_status",
                    "delivery_status",
                    "upgrade_status",
                    "quote_status",
                    "agreement_status",
                    "invoice_status",
                    "release_status",
                    "order_type",
                ):
                    val = val.lower()
                setattr(row, attr, val)
                break
    for cents_key, attr in (
        ("paidAmountCents", "paid_amount_cents"),
        ("paid_amount_cents", "paid_amount_cents"),
        ("outstandingCents", "outstanding_cents"),
        ("outstanding_cents", "outstanding_cents"),
    ):
        if payload.get(cents_key) is not None:
            try:
                setattr(row, attr, int(payload.get(cents_key) or 0))
            except Exception:
                pass
    row.last_synced_at = _utcnow()


def _sync_milestones(db, row: BeDelivery, payload: dict) -> None:
    raw = payload.get("milestones") or payload.get("milestoneList")
    if not isinstance(raw, list):
        # Single milestone event
        mid = str(payload.get("milestoneId") or payload.get("ahMilestoneId") or "")
        if not mid and not payload.get("milestoneName"):
            return
        raw = [
            {
                "id": mid or f"ms-{row.id}-{payload.get('event') or 'x'}",
                "name": payload.get("milestoneName") or payload.get("name") or "Milestone",
                "amountCents": payload.get("amountCents") or payload.get("priceCents") or 0,
                "currency": payload.get("currency") or row.currency,
                "dueAt": payload.get("dueAt") or payload.get("due_at"),
                "status": payload.get("milestoneStatus") or payload.get("status") or "pending",
                "payUrl": payload.get("payUrl") or payload.get("checkoutUrl") or "",
            }
        ]
    now = _utcnow()
    for item in raw:
        if not isinstance(item, dict):
            continue
        ah_id = str(item.get("id") or item.get("milestoneId") or item.get("ahMilestoneId") or "")[:120]
        if not ah_id:
            continue
        existing = db.scalar(
            select(BeMilestoneSnapshot).where(
                BeMilestoneSnapshot.delivery_id == row.id,
                BeMilestoneSnapshot.ah_milestone_id == ah_id,
            )
        )
        ms = existing or BeMilestoneSnapshot(
            delivery_id=row.id,
            business_id=row.business_id,
            ah_milestone_id=ah_id,
        )
        if not existing:
            db.add(ms)
        ms.name = str(item.get("name") or item.get("label") or ms.name or "Milestone")[:200]
        try:
            ms.amount_cents = int(item.get("amountCents") or item.get("amount_cents") or ms.amount_cents or 0)
        except Exception:
            pass
        ms.currency = str(item.get("currency") or row.currency or "USD")[:10]
        due = item.get("dueAt") or item.get("due_at")
        if due:
            try:
                ms.due_at = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
            except Exception:
                pass
        st = str(item.get("status") or "pending").lower()
        if st in ("paid", "pending", "overdue"):
            ms.status = st
        elif "overdue" in st:
            ms.status = "overdue"
        elif "partial" in st:
            # AH PARTIALLY_PAID / partially_paid — still open, not Won
            ms.status = "pending"
        elif "paid" in st:
            ms.status = "paid"
        else:
            ms.status = "pending"
        ms.pay_url = str(item.get("payUrl") or item.get("checkoutUrl") or ms.pay_url or "")
        ms.last_synced_at = now


def _upsert_by_purchase(db, purchase_id: str) -> Optional[BeDelivery]:
    if not purchase_id:
        return None
    return db.scalar(select(BeDelivery).where(BeDelivery.purchase_id == purchase_id))


def _upsert_by_request(db, request_id: str) -> Optional[BeDelivery]:
    if not request_id:
        return None
    return db.scalar(select(BeDelivery).where(BeDelivery.inbound_request_id == request_id))


def _advance_awaiting_payment(db, business: Business, *, actor_id: Optional[int] = None) -> None:
    line = business.product_line or "STANDALONE"
    # Never demote a Won (or other terminal won) deal on soft/purchase re-send.
    current = db.get(PipelineStage, business.pipeline_stage_id) if business.pipeline_stage_id else None
    if current and (getattr(current, "is_won", False) or (getattr(current, "slug", "") or "").lower() == "won"):
        return
    # Prefer negotiation / proposal_sent as "awaiting payment" — never Won.
    for slug in ("negotiation", "proposal_sent"):
        stage = get_stage(db, line, slug)
        if stage and business.pipeline_stage_id != stage.id:
            try:
                move_stage(db, business, stage.id, actor_id=actor_id, note="AppointHive handover sent", force=True)
                return
            except Exception:
                continue


def _mirror_won(db, business: Business, *, note: str = "AppointHive payment.paid") -> None:
    line = business.product_line or "STANDALONE"
    won = get_stage(db, line, "won")
    if won and business.pipeline_stage_id != won.id:
        move_stage(db, business, won.id, actor_id=None, note=note, force=True)


def _fail_outbound(db, *, business_id: Optional[int], operation: str, error: Exception, payload: Optional[dict] = None) -> None:
    from app.services.booking_engine.status import record_outbound_failure
    from app.services.booking_engine.sync import mark_sync_fail

    record_outbound_failure(
        db,
        business_id=business_id,
        operation=operation,
        error=str(error),
        payload=payload or {},
    )
    mark_sync_fail(db, note=f"{operation}: {error}", commit=False)
    db.commit()


def _maybe_mirror_won(db, business: Business, row: BeDelivery, *, event: str) -> dict:
    allowed, reason = should_mirror_won(db, row)
    if not allowed:
        db.add(
            Activity(
                business_id=business.id,
                activity_type="be_payment_update",
                channel="appointhive",
                summary=f"AppointHive {event} — financial snapshot updated (Won not auto-applied: {reason})",
                outcome="financial_only",
            )
        )
        return {"won_mirrored": False, "reason": reason}
    _mirror_won(db, business, note=f"AppointHive {event} (won_policy)")
    db.add(
        Activity(
            business_id=business.id,
            activity_type="be_payment_paid",
            channel="appointhive",
            summary="AppointHive payment verified — mirrored Won per won_policy (Sales OS did not collect cash)",
            outcome="paid",
        )
    )
    return {"won_mirrored": True, "reason": reason}


def send_purchase(
    db,
    *,
    business_id: int,
    product_revision_id: str,
    provider: Optional[str] = None,
    proposal_id: Optional[int] = None,
    actor_id: Optional[int] = None,
) -> dict:
    require_be_connected(db)
    business = db.get(Business, business_id)
    if not business:
        raise HTTPException(404, detail={"error": "Prospect not found", "code": "PROSPECT_NOT_FOUND"})
    lead = crm_lead_id_for(business.id)
    prospect = _require_prospect(db, business)
    buyer_email, buyer_name, buyer_phone = buyer_fields_from_prospect(prospect)
    # Idempotent: reuse open delivery for same business+revision
    existing = db.scalar(
        select(BeDelivery).where(
            BeDelivery.business_id == business.id,
            BeDelivery.revision_id == product_revision_id,
            BeDelivery.purpose == "purchase",
            BeDelivery.purchase_id != "",
        ).order_by(BeDelivery.id.desc())
    )
    client = BookingEngineClient(db=db)
    # Soft intake first so Super Admin gets full business/contact details even if purchase snapshot is lean.
    inbound_request_id = ""
    soft_fail_note = ""
    req = None
    soft_payload = {
        "action": "create",
        "externalCrmRef": lead,
        "contactEmail": buyer_email,
        "businessName": business.name,
        "message": format_crm_prospect_message(
            prospect, intro=f"Pay-ready purchase handover for revision {product_revision_id}"
        ),
        # Do not send installationId on new-sale soft intake — BE FK-rejects unknown ids (500).
        "crmProspect": prospect,
        "contactPhone": buyer_phone or None,
        "contactName": buyer_name or None,
    }
    try:
        req = client.create_request(soft_payload)
        inbound_request_id = str(req.get("id") or "")
    except BeClientError as soft_exc:
        # Purchase can still proceed with crmProspect on the purchase body — but never silent.
        soft_fail_note = str(soft_exc)
        from app.services.booking_engine.status import record_outbound_failure

        record_outbound_failure(
            db,
            business_id=business.id,
            operation="send_purchase_soft_intake",
            error=soft_fail_note,
            payload=soft_payload,
        )
        db.add(
            Activity(
                business_id=business.id,
                activity_type="be_outbound_warn",
                channel="appointhive",
                summary=f"Soft intake before purchase failed — continuing pay-ready · {soft_fail_note[:180]}",
                outcome="warn",
                created_by=actor_id,
            )
        )
        db.flush()
    payload: dict[str, Any] = {
        "productRevisionId": product_revision_id,
        "buyerEmail": buyer_email,
        "buyerName": buyer_name or business.name or None,
        "crmLeadId": lead,
        "createCheckout": True,
        "crmProspect": prospect,
        "contactPhone": buyer_phone or None,
        "contactName": buyer_name or None,
    }
    if inbound_request_id:
        payload["inboundRequestId"] = inbound_request_id
    if provider:
        payload["provider"] = provider
    try:
        res = client.create_purchase(payload)
    except BeClientError as exc:
        from app.services.booking_engine.status import record_outbound_failure
        from app.services.booking_engine.sync import mark_sync_fail

        record_outbound_failure(
            db,
            business_id=business.id,
            operation="send_purchase",
            error=str(exc),
            payload=payload,
        )
        mark_sync_fail(db, note=f"send_purchase: {exc}", commit=False)
        db.commit()
        raise HTTPException(exc.status or 502, detail=be_http_detail(exc)) from exc

    purchase_id = str(res.get("purchaseId") or "")
    # Prefer BE-mirrored inbound hub id (always returned after createCheckout Path B).
    inbound_from_be = str(res.get("inboundRequestId") or "").strip()
    if inbound_from_be:
        inbound_request_id = inbound_from_be
    pay_url = str(res.get("checkoutUrl") or res.get("hostedUrl") or "")
    order_ref = str(res.get("orderReference") or res.get("order_reference") or "").strip()
    row = _upsert_by_purchase(db, purchase_id) or existing
    if not row:
        row = BeDelivery(business_id=business.id, purpose="purchase")
        db.add(row)
    row.proposal_id = proposal_id
    row.purpose = "purchase"
    row.purchase_id = purchase_id
    row.inbound_request_id = inbound_request_id or row.inbound_request_id or ""
    row.revision_id = str(res.get("revisionId") or product_revision_id)
    row.pay_url = pay_url
    row.quoted_amount_cents = int(res.get("priceCents") or 0)
    row.currency = str(res.get("currency") or business.currency or "USD")
    row.crm_lead_id = lead
    if order_ref:
        row.order_reference = order_ref[:120]
    row.status = "pending_payment" if pay_url else "sent"
    row.last_event_json = json.dumps(
        {
            "send": res,
            "request": req,
            "crmProspect": prospect,
            "soft_intake_error": soft_fail_note or None,
        },
        default=str,
    )[:8000]
    _advance_awaiting_payment(db, business, actor_id=actor_id)
    db.add(
        Activity(
            business_id=business.id,
            activity_type="be_purchase_sent",
            channel="appointhive",
            summary=f"Sent to AppointHive delivery · {purchase_id}" + (f" · pay URL ready" if pay_url else ""),
            outcome="sent",
            created_by=actor_id,
        )
    )
    db.commit()
    db.refresh(row)
    out: dict[str, Any] = {
        "delivery": delivery_dict(row),
        "be": res,
        "pay_url": pay_url,
        "label": "Pay-ready",
    }
    if soft_fail_note:
        out["soft_intake_warning"] = soft_fail_note
    return out


def submit_payment_evidence(
    db,
    *,
    business_id: int,
    purchase_id: Optional[str] = None,
    method: str = "BANK_TRANSFER",
    amount_cents: int,
    currency: Optional[str] = None,
    reference_note: str = "",
    recorded_by_email: str,
    actor_id: Optional[int] = None,
) -> dict:
    """Forward bank/manual evidence to AH as PENDING only — never PAID/REJECTED/finalize."""
    require_be_connected(db)
    business = db.get(Business, business_id)
    if not business:
        raise HTTPException(404, detail={"error": "Prospect not found", "code": "PROSPECT_NOT_FOUND"})
    row: Optional[BeDelivery] = None
    pid = (purchase_id or "").strip()
    if pid:
        row = _upsert_by_purchase(db, pid)
    if not row:
        row = db.scalar(
            select(BeDelivery)
            .where(BeDelivery.business_id == business.id, BeDelivery.purpose == "purchase")
            .order_by(BeDelivery.id.desc())
        )
    if not row or not (row.purchase_id or "").strip():
        raise HTTPException(
            422,
            detail={
                "error": "No AppointHive purchase to attach evidence to",
                "code": "BE_PURCHASE_REQUIRED",
            },
        )
    method_u = (method or "BANK_TRANSFER").strip().upper()
    if method_u not in ("BANK_TRANSFER", "MANUAL_INVOICE"):
        raise HTTPException(
            422,
            detail={"error": "method must be BANK_TRANSFER or MANUAL_INVOICE", "code": "BE_EVIDENCE_METHOD"},
        )
    if int(amount_cents or 0) < 0:
        raise HTTPException(422, detail={"error": "amount_cents must be >= 0", "code": "BE_EVIDENCE_AMOUNT"})
    payload = {
        "purchaseId": row.purchase_id,
        "method": method_u,
        "status": "PENDING",
        "amountCents": int(amount_cents),
        "currency": (currency or row.currency or business.currency or "USD"),
        "referenceNote": (reference_note or "")[:2000] or None,
        "recordedByEmail": recorded_by_email,
    }
    client = BookingEngineClient(db=db)
    try:
        res = client.submit_payment_evidence(payload)
    except BeClientError as exc:
        from app.services.booking_engine.status import record_outbound_failure
        from app.services.booking_engine.sync import mark_sync_fail

        record_outbound_failure(
            db,
            business_id=business.id,
            operation="submit_payment_evidence",
            error=str(exc),
            payload=payload,
        )
        mark_sync_fail(db, note=f"submit_payment_evidence: {exc}", commit=False)
        db.commit()
        raise HTTPException(exc.status or 502, detail=be_http_detail(exc)) from exc

    row.financial_status = "pending"
    if row.status not in ("paid", "delivery_ready"):
        row.status = "pending_payment"
    row.last_synced_at = _utcnow()
    db.add(
        Activity(
            business_id=business.id,
            activity_type="be_payment_evidence",
            channel="appointhive",
            summary=f"Submitted payment evidence to AppointHive (PENDING) · {row.purchase_id}",
            outcome="pending",
            created_by=actor_id,
        )
    )
    db.commit()
    db.refresh(row)
    return {
        "delivery": delivery_dict(row),
        "be": res,
        "verification_required": True,
        "label": "Evidence submitted — AppointHive verifies",
    }


def send_request(
    db,
    *,
    business_id: int,
    message: str = "",
    requested_edition: Optional[str] = None,
    actor_id: Optional[int] = None,
) -> dict:
    require_be_connected(db)
    business = db.get(Business, business_id)
    if not business:
        raise HTTPException(404, detail={"error": "Prospect not found", "code": "PROSPECT_NOT_FOUND"})
    lead = crm_lead_id_for(business.id)
    prospect = _require_prospect(db, business)
    buyer_email, buyer_name, buyer_phone = buyer_fields_from_prospect(prospect)
    client = BookingEngineClient(db=db)
    # Reuse open soft request for same lead when still awaiting quote (idempotent).
    existing = db.scalar(
        select(BeDelivery).where(
            BeDelivery.business_id == business.id,
            BeDelivery.purpose == "request",
            BeDelivery.inbound_request_id != "",
            BeDelivery.status.in_(["sent", "pending_payment"]),
        ).order_by(BeDelivery.id.desc())
    )
    # Only attach installationId for upgrade intake (requested_edition set). New-sale soft
    # requests must omit it — BE has an FK to StandaloneInstallation and unknown ids → 500.
    install_for_upgrade = ""
    if requested_edition:
        install_for_upgrade = (business.be_installation_id or "").strip()
    payload = {
        "action": "create",
        "externalCrmRef": lead,
        "contactEmail": buyer_email,
        "businessName": business.name,
        "message": format_crm_prospect_message(
            prospect, intro=message or f"Soft handover from AppointHive Sales OS for {business.name}"
        ),
        "installationId": install_for_upgrade or None,
        "requestedEdition": requested_edition,
        "crmProspect": prospect,
        "contactPhone": buyer_phone or None,
        "contactName": buyer_name or None,
    }
    if existing and existing.inbound_request_id:
        payload["action"] = "update"
        payload["requestId"] = existing.inbound_request_id
    try:
        res = client.create_request(payload)
    except BeClientError as exc:
        from app.services.booking_engine.status import record_outbound_failure
        from app.services.booking_engine.sync import mark_sync_fail

        record_outbound_failure(
            db,
            business_id=business.id,
            operation="send_request",
            error=str(exc),
            payload=payload,
        )
        mark_sync_fail(db, note=f"send_request: {exc}", commit=False)
        db.commit()
        raise HTTPException(exc.status or 502, detail=be_http_detail(exc)) from exc
    request_id = str(res.get("id") or (existing.inbound_request_id if existing else "") or "")
    row = _upsert_by_request(db, request_id) or existing
    if not row:
        row = BeDelivery(business_id=business.id, purpose="request")
        db.add(row)
    row.purpose = "request"
    row.inbound_request_id = request_id
    row.crm_lead_id = lead
    row.installation_id = business.be_installation_id or ""
    row.status = "sent"
    row.last_event_json = json.dumps({"send": res, "crmProspect": prospect}, default=str)[:8000]
    _advance_awaiting_payment(db, business, actor_id=actor_id)
    db.add(
        Activity(
            business_id=business.id,
            activity_type="be_request_sent",
            channel="appointhive",
            summary=f"Soft request to AppointHive · {request_id} · Awaiting AppointHive quote",
            outcome="sent",
            created_by=actor_id,
        )
    )
    db.commit()
    db.refresh(row)
    return {
        "delivery": delivery_dict(row),
        "be": res,
        "pay_url": "",
        "label": "Awaiting AppointHive quote",
    }


def send_upgrade(
    db,
    *,
    business_id: int,
    to_revision_id: str,
    from_revision_id: Optional[str] = None,
    provider: Optional[str] = None,
    actor_id: Optional[int] = None,
) -> dict:
    require_be_connected(db)
    business = db.get(Business, business_id)
    if not business:
        raise HTTPException(404, detail={"error": "Prospect not found", "code": "PROSPECT_NOT_FOUND"})
    install_id = (business.be_installation_id or "").strip()
    if not install_id:
        # Try latest delivery
        last = db.scalar(
            select(BeDelivery)
            .where(BeDelivery.business_id == business.id, BeDelivery.installation_id != "")
            .order_by(BeDelivery.id.desc())
        )
        if last:
            install_id = last.installation_id
    if not install_id:
        raise HTTPException(
            422,
            detail={
                "error": (
                    "This prospect has no AppointHive installation yet. "
                    "Wait until a paid install is active, then send the upgrade."
                ),
                "code": "INSTALLATION_REQUIRED",
            },
        )

    lead = crm_lead_id_for(business.id)
    prospect = _require_prospect(db, business)
    buyer_email, buyer_name, buyer_phone = buyer_fields_from_prospect(prospect)
    client = BookingEngineClient(db=db)
    quote_params: dict[str, str] = {"installationId": install_id, "toRevisionId": to_revision_id}
    if from_revision_id:
        quote_params["fromRevisionId"] = from_revision_id
    try:
        quote = client.upgrade_quote(quote_params)
    except BeClientError as exc:
        _fail_outbound(db, business_id=business.id, operation="upgrade_quote", error=exc, payload=quote_params)
        raise HTTPException(exc.status or 502, detail=be_http_detail(exc)) from exc

    try:
        req = client.create_request(
            {
                "action": "create",
                "externalCrmRef": lead,
                "contactEmail": buyer_email,
                "businessName": business.name,
                "message": format_crm_prospect_message(
                    prospect, intro=f"Upgrade handover to revision {to_revision_id}"
                ),
                "installationId": install_id,
                "crmProspect": prospect,
                "contactPhone": buyer_phone or None,
                "contactName": buyer_name or None,
            }
        )
    except BeClientError as exc:
        _fail_outbound(db, business_id=business.id, operation="send_upgrade_request", error=exc)
        raise HTTPException(exc.status or 502, detail=be_http_detail(exc)) from exc
    request_id = str(req.get("id") or "")
    checkout_body: dict[str, Any] = {}
    if provider:
        checkout_body["provider"] = provider
    try:
        checkout = client.request_checkout(request_id, checkout_body)
    except BeClientError as exc:
        _fail_outbound(
            db,
            business_id=business.id,
            operation="send_upgrade_checkout",
            error=exc,
            payload={"requestId": request_id, **checkout_body},
        )
        raise HTTPException(exc.status or 502, detail=be_http_detail(exc)) from exc

    pay_url = str(checkout.get("payUrl") or checkout.get("checkoutUrl") or checkout.get("hostedCheckoutUrl") or "")
    row = _upsert_by_request(db, request_id) or BeDelivery(business_id=business.id, purpose="upgrade")
    if not row.id:
        db.add(row)
    row.purpose = "upgrade"
    row.inbound_request_id = request_id
    row.revision_id = to_revision_id
    row.from_revision_id = from_revision_id or ""
    row.installation_id = install_id
    row.pay_url = pay_url
    row.quoted_amount_cents = int(quote.get("deltaCents") or quote.get("quoteCents") or quote.get("priceCents") or 0)
    row.currency = str(quote.get("currency") or business.currency or "USD")
    row.crm_lead_id = lead
    row.status = "pending_payment" if pay_url else "sent"
    row.last_event_json = json.dumps({"quote": quote, "request": req, "checkout": checkout}, default=str)[:8000]
    _advance_awaiting_payment(db, business, actor_id=actor_id)
    db.add(
        Activity(
            business_id=business.id,
            activity_type="be_upgrade_sent",
            channel="appointhive",
            summary=f"Upgrade checkout · {request_id}" + (f" · pay URL ready" if pay_url else ""),
            outcome="sent",
            created_by=actor_id,
        )
    )
    db.commit()
    db.refresh(row)
    return {"delivery": delivery_dict(row), "quote": quote, "be": checkout, "pay_url": pay_url, "label": "Pay-ready"}


def refresh_delivery(db, delivery_id: int) -> dict:
    require_be_connected(db)
    row = db.get(BeDelivery, delivery_id)
    if not row:
        raise HTTPException(
            404,
            detail={"error": "No delivery record found for this prospect yet.", "code": "DELIVERY_NOT_FOUND"},
        )
    client = BookingEngineClient(db=db)
    be: dict = {}
    try:
        if row.purchase_id:
            be = client.get_purchase(row.purchase_id)
            status = be.get("status") if isinstance(be.get("status"), dict) else be
            pay = ""
            if isinstance(status, dict):
                pay = str(status.get("checkoutUrl") or "")
                payment = status.get("payment") or {}
                if isinstance(payment, dict) and str(payment.get("status") or "").upper() == "PAID":
                    row.status = "paid"
                install = status.get("installation") or {}
                if isinstance(install, dict) and install.get("installationId"):
                    row.installation_id = str(install["installationId"])
                    biz = db.get(Business, row.business_id)
                    if biz:
                        biz.be_installation_id = row.installation_id
            else:
                pay = str(be.get("checkoutUrl") or be.get("hostedUrl") or "")
            if pay:
                row.pay_url = pay
                if row.status in ("sent",):
                    row.status = "pending_payment"
        elif row.inbound_request_id:
            be = client.get_request(row.inbound_request_id)
            pay = str(be.get("payUrl") or "")
            if pay:
                row.pay_url = pay
                if row.status in ("sent",):
                    row.status = "pending_payment"
            st = str(be.get("status") or "").upper()
            if st in ("AWAITING_PAYMENT", "QUOTED"):
                row.status = "pending_payment"
            elif st == "PAID":
                row.status = "paid"
            if be.get("quoteCents") is not None:
                row.quoted_amount_cents = int(be.get("quoteCents") or 0)
            if be.get("installationId"):
                row.installation_id = str(be["installationId"])
                biz = db.get(Business, row.business_id)
                if biz:
                    biz.be_installation_id = row.installation_id
    except BeClientError as exc:
        raise HTTPException(exc.status or 502, detail=be_http_detail(exc)) from exc
    row.last_event_json = json.dumps({"refresh": be}, default=str)[:8000]
    db.commit()
    db.refresh(row)
    return {"delivery": delivery_dict(row), "be": be}


def apply_be_webhook(db, payload: dict, *, inbox_row=None) -> dict:
    """Public entry: apply webhook and commit. Prefer ops router inbox path."""
    result = apply_be_webhook_payload(db, payload, inbox_row=inbox_row)
    db.commit()
    return result


def apply_be_webhook_payload(db, payload: dict, *, inbox_row=None) -> dict:
    """Apply inbound AppointHive webhook. Never marks local PSP paid / never creates invoices.

    Payment events update financial snapshot only; Won uses be.won_policy.
    """
    raw_event = str(payload.get("event") or "")
    event = normalize_event_type(raw_event)
    if event == "crm.bridge.connected":
        set_connection_status(db, "connected")
        from app.services.booking_engine.sync import mark_sync_ok

        mark_sync_ok(db, note="crm.bridge.connected")
        if inbox_row is not None:
            from app.services.booking_engine.events import mark_inbox_processed

            mark_inbox_processed(db, inbox_row)
        return {"ok": True, "event": event, "connected": True, "raw_event": raw_event}

    purchase_id = str(payload.get("purchaseId") or "")
    request_id = str(payload.get("requestId") or "")
    installation_id = str(payload.get("installationId") or "").strip()
    crm_lead = str(payload.get("crmLeadId") or payload.get("externalCrmRef") or "").strip()
    pay_url = str(payload.get("payUrl") or payload.get("checkoutUrl") or "")

    row: Optional[BeDelivery] = None
    if purchase_id:
        row = _upsert_by_purchase(db, purchase_id)
    if not row and request_id:
        row = _upsert_by_request(db, request_id)
    # upgrade.applied often omits deal aliases — match by installationId
    if not row and installation_id:
        row = db.scalar(
            select(BeDelivery)
            .where(BeDelivery.installation_id == installation_id)
            .order_by(BeDelivery.id.desc())
        )
        if not row:
            biz_by_install = db.scalar(
                select(Business).where(Business.be_installation_id == installation_id)
            )
            if biz_by_install:
                row = db.scalar(
                    select(BeDelivery)
                    .where(BeDelivery.business_id == biz_by_install.id)
                    .order_by(BeDelivery.id.desc())
                )
    if not row and crm_lead.startswith("business:"):
        try:
            bid = int(crm_lead.split(":", 1)[1])
            row = db.scalar(
                select(BeDelivery).where(BeDelivery.business_id == bid).order_by(BeDelivery.id.desc())
            )
        except Exception:
            row = None

    business: Optional[Business] = None
    if row:
        business = db.get(Business, row.business_id)
    elif crm_lead.startswith("business:"):
        try:
            business = db.get(Business, int(crm_lead.split(":", 1)[1]))
        except Exception:
            business = None
    elif installation_id and not business:
        business = db.scalar(select(Business).where(Business.be_installation_id == installation_id))

    if not row and business:
        purpose = "upgrade" if event.startswith("standalone.upgrade") else ("purchase" if purchase_id else "request")
        row = BeDelivery(business_id=business.id, purpose=purpose)
        db.add(row)
        row.crm_lead_id = crm_lead or crm_lead_id_for(business.id)
        if purchase_id:
            row.purchase_id = purchase_id
        if request_id:
            row.inbound_request_id = request_id
        if installation_id:
            row.installation_id = installation_id

    won_info: dict = {}
    if row:
        if purchase_id:
            row.purchase_id = purchase_id
        if request_id:
            row.inbound_request_id = request_id
        if pay_url:
            row.pay_url = pay_url
        if payload.get("revisionId"):
            row.revision_id = str(payload["revisionId"])
        if payload.get("priceCents") is not None:
            row.quoted_amount_cents = int(payload["priceCents"] or 0)
        if payload.get("quoteCents") is not None:
            row.quoted_amount_cents = int(payload["quoteCents"] or 0)
        if payload.get("currency"):
            row.currency = str(payload["currency"])
        if installation_id:
            row.installation_id = installation_id
            if business:
                business.be_installation_id = installation_id
        _apply_snapshot_fields(row, payload)
        cust = str(payload.get("customerRef") or payload.get("customer_ref") or payload.get("ahCustomerId") or "").strip()
        if cust:
            row.customer_ref = cust[:120]
            if business:
                from app.services.booking_engine.status import apply_customer_ref

                apply_customer_ref(db, business, cust)
        row.last_event_json = json.dumps(payload, default=str)[:8000]

    status_map = {
        "standalone.purchase.recorded": "sent",
        "standalone.payment.pending": "pending_payment",
        "standalone.payment.paid": "paid",
        "standalone.payment.rejected": "rejected",
        "payment.partially_paid": "pending_payment",
        "payment.refunded": "rejected",
        "payment.disputed": "pending_payment",
        "standalone.delivery.ready": "delivery_ready",
        "delivery.started": "delivery_ready",
        "delivery.finalized": "delivery_ready",
        "standalone.installation.active": "delivery_ready",
        "standalone.upgrade.status": None,
        "standalone.upgrade.requested": "sent",
        "standalone.upgrade.applied": "delivery_ready",
        "quote.created": "sent",
        "quote.accepted": "pending_payment",
        "agreement.accepted": None,
        "invoice.created": None,
    }
    if row and event in status_map and status_map[event]:
        row.status = status_map[event]
    if row and event == "standalone.upgrade.status":
        st = str(payload.get("status") or "").upper()
        if st in ("AWAITING_PAYMENT", "QUOTED"):
            row.status = "pending_payment"
            row.upgrade_status = st.lower()
        elif st == "PAID":
            row.status = "paid"
            row.financial_status = "paid"
            row.upgrade_status = "paid"
        elif st in ("ZIP_READY", "FULFILLED", "DELIVERED"):
            row.status = "delivery_ready"
            row.delivery_status = "ready"
            row.upgrade_status = st.lower()
        elif st in ("REJECTED", "CANCELLED"):
            row.status = "rejected"
            row.upgrade_status = st.lower()

    # Financial snapshot updates (never invent local PSP payments)
    if row and event == "standalone.payment.pending":
        row.financial_status = "pending"
    if row and event == "payment.partially_paid":
        row.financial_status = "partially_paid"
        row.status = "pending_payment"
        if payload.get("paidAmountCents") is not None:
            row.paid_amount_cents = int(payload.get("paidAmountCents") or 0)
        if payload.get("outstandingCents") is not None:
            row.outstanding_cents = int(payload.get("outstandingCents") or 0)
        elif row.quoted_amount_cents and row.paid_amount_cents:
            row.outstanding_cents = max(0, int(row.quoted_amount_cents) - int(row.paid_amount_cents))
    if row and event == "standalone.payment.paid":
        row.status = "paid"
        row.financial_status = "paid"
        if not row.paid_amount_cents and row.quoted_amount_cents:
            row.paid_amount_cents = int(row.quoted_amount_cents)
        row.outstanding_cents = 0
    if row and event == "standalone.payment.rejected":
        row.financial_status = "failed"
    if row and event == "payment.refunded":
        row.financial_status = "refunded"
    if row and event == "payment.disputed":
        row.financial_status = "disputed"
    if row and event in ("quote.created",):
        row.quote_status = "created"
    if row and event in ("quote.accepted",):
        row.quote_status = "accepted"
    if row and event == "agreement.accepted":
        row.agreement_status = "accepted"
    if row and event == "invoice.created":
        row.invoice_status = "created"
    if row and event in ("standalone.delivery.ready", "delivery.delivered", "delivery.finalized"):
        row.delivery_status = "ready" if "ready" in event or event.endswith("delivered") else "finalized"
        if event == "delivery.started":
            row.delivery_status = "started"
    if row and event == "delivery.started":
        row.delivery_status = "started"
    if row and event == "standalone.installation.active":
        row.delivery_status = "ready"
        row.release_status = "active"
    if row and event == "standalone.upgrade.applied":
        row.upgrade_status = "applied"
        row.delivery_status = "ready"
        if payload.get("toRevisionId"):
            row.revision_id = str(payload["toRevisionId"])
        if payload.get("fromRevisionId"):
            row.from_revision_id = str(payload["fromRevisionId"])

    if row and event == "standalone.license.issued":
        # Reference only — never store SOS license keys (AH strips them).
        row.release_status = "licensed"
        if payload.get("licenseId"):
            snap = {}
            try:
                snap = json.loads(row.last_event_json or "{}") or {}
            except Exception:
                snap = {}
            snap["licenseId"] = str(payload.get("licenseId"))[:120]
            snap["licenseIssued"] = True
            row.last_event_json = json.dumps(snap, default=str)[:8000]

    if event == "standalone.catalog.updated":
        # Catalog ping — no delivery mutation required.
        pass

    if row and event.startswith("milestone."):
        if event == "milestone.overdue":
            payload = {**payload, "milestoneStatus": "overdue"}
        elif event == "milestone.paid":
            payload = {**payload, "milestoneStatus": "paid"}
        elif event == "milestone.created":
            payload = {**payload, "milestoneStatus": payload.get("milestoneStatus") or "pending"}
        _sync_milestones(db, row, payload)
    elif row and isinstance(payload.get("milestones"), list):
        _sync_milestones(db, row, payload)

    if event == "standalone.payment.paid" and business and row:
        won_info = _maybe_mirror_won(db, business, row, event=event)
    elif event == "payment.partially_paid" and business:
        db.add(
            Activity(
                business_id=business.id,
                activity_type="be_payment_update",
                channel="appointhive",
                summary="AppointHive partial payment — financial snapshot only (not Won)",
                outcome="partially_paid",
            )
        )

    if business and event in (
        "standalone.purchase.recorded",
        "standalone.payment.pending",
        "standalone.upgrade.status",
        "standalone.delivery.ready",
        "standalone.installation.active",
        "standalone.license.issued",
        "standalone.inquiry.received",
        "quote.created",
        "quote.accepted",
        "agreement.accepted",
        "invoice.created",
        "milestone.created",
        "milestone.paid",
        "milestone.overdue",
        "standalone.upgrade.applied",
        "delivery.started",
        "delivery.finalized",
        "payment.partially_paid",
        "payment.refunded",
        "payment.disputed",
    ):
        db.add(
            Activity(
                business_id=business.id,
                activity_type="be_webhook",
                channel="appointhive",
                summary=f"AppointHive event {event}",
                outcome=event.split(".")[-1],
            )
        )

    if inbox_row is not None:
        from app.services.booking_engine.events import mark_inbox_processed

        mark_inbox_processed(
            db,
            inbox_row,
            delivery_id=row.id if row else None,
            business_id=business.id if business else None,
        )

    from app.services.booking_engine.sync import mark_sync_ok

    try:
        mark_sync_ok(db, note=event)
    except Exception:
        pass

    return {
        "ok": True,
        "event": event,
        "raw_event": raw_event,
        "delivery_id": row.id if row else None,
        "status": row.status if row else None,
        "financial_status": getattr(row, "financial_status", None) if row else None,
        "won": won_info,
    }
