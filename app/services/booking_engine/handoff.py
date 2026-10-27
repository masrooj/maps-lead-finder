"""Send purchases/requests/upgrades and apply inbound Booking Engine webhooks."""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Activity, BeDelivery, Business, PipelineStage
from app.services.booking_engine.client import BeClientError, BookingEngineClient
from app.services.booking_engine.connect import crm_lead_id_for, require_be_connected, set_connection_status
from app.services.booking_engine.prospect import (
    build_crm_prospect,
    buyer_fields_from_prospect,
    format_crm_prospect_message,
)
from app.services.pipeline import get_stage, move_stage


def _require_prospect(db, business: Business) -> dict:
    prospect = build_crm_prospect(db, business)
    email, _name, _phone = buyer_fields_from_prospect(prospect)
    if not email:
        raise HTTPException(422, "Buyer email required on prospect (or primary contact) before handover")
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
        "last_event_json": d.last_event_json,
        "created_at": d.created_at.isoformat() if d.created_at else "",
        "updated_at": d.updated_at.isoformat() if d.updated_at else "",
    }


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
        raise HTTPException(404, "Prospect not found")
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
    try:
        req = client.create_request(
            {
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
        )
        inbound_request_id = str(req.get("id") or "")
    except BeClientError:
        req = None  # purchase can still proceed; details go on purchase.crmProspect
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
        raise HTTPException(exc.status or 502, detail={"error": str(exc), "be": exc.body}) from exc

    purchase_id = str(res.get("purchaseId") or "")
    # Prefer BE-mirrored inbound hub id (always returned after createCheckout Path B).
    inbound_from_be = str(res.get("inboundRequestId") or "").strip()
    if inbound_from_be:
        inbound_request_id = inbound_from_be
    pay_url = str(res.get("checkoutUrl") or res.get("hostedUrl") or "")
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
    row.status = "pending_payment" if pay_url else "sent"
    row.last_event_json = json.dumps({"send": res, "request": req, "crmProspect": prospect}, default=str)[:8000]
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
    return {"delivery": delivery_dict(row), "be": res, "pay_url": pay_url, "label": "Pay-ready"}


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
        raise HTTPException(404, "Prospect not found")
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
        raise HTTPException(exc.status or 502, detail={"error": str(exc), "be": exc.body}) from exc
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
        raise HTTPException(404, "Prospect not found")
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
        raise HTTPException(422, "installation_id required before upgrade — wait for AppointHive install webhook")

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
        raise HTTPException(exc.status or 502, detail={"error": str(exc), "be": exc.body}) from exc

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
        raise HTTPException(exc.status or 502, detail={"error": str(exc), "be": exc.body}) from exc
    request_id = str(req.get("id") or "")
    checkout_body: dict[str, Any] = {}
    if provider:
        checkout_body["provider"] = provider
    try:
        checkout = client.request_checkout(request_id, checkout_body)
    except BeClientError as exc:
        raise HTTPException(exc.status or 502, detail={"error": str(exc), "be": exc.body}) from exc

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
        raise HTTPException(404, "Delivery not found")
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
        raise HTTPException(exc.status or 502, detail={"error": str(exc), "be": exc.body}) from exc
    row.last_event_json = json.dumps({"refresh": be}, default=str)[:8000]
    db.commit()
    db.refresh(row)
    return {"delivery": delivery_dict(row), "be": be}


def apply_be_webhook(db, payload: dict) -> dict:
    """Apply inbound AppointHive webhook. Never marks local PSP paid / never creates invoices."""
    event = str(payload.get("event") or "")
    if event == "crm.bridge.connected":
        set_connection_status(db, "connected")
        return {"ok": True, "event": event, "connected": True}

    purchase_id = str(payload.get("purchaseId") or "")
    request_id = str(payload.get("requestId") or "")
    crm_lead = str(payload.get("crmLeadId") or payload.get("externalCrmRef") or "").strip()
    pay_url = str(payload.get("payUrl") or payload.get("checkoutUrl") or "")

    row: Optional[BeDelivery] = None
    if purchase_id:
        row = _upsert_by_purchase(db, purchase_id)
    if not row and request_id:
        row = _upsert_by_request(db, request_id)
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

    if not row and business:
        row = BeDelivery(business_id=business.id, purpose="purchase" if purchase_id else "request")
        db.add(row)
        row.crm_lead_id = crm_lead or crm_lead_id_for(business.id)
        if purchase_id:
            row.purchase_id = purchase_id
        if request_id:
            row.inbound_request_id = request_id

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
        install_id = str(payload.get("installationId") or "")
        if install_id:
            row.installation_id = install_id
            if business:
                business.be_installation_id = install_id
        row.last_event_json = json.dumps(payload, default=str)[:8000]

    status_map = {
        "standalone.purchase.recorded": "sent",
        "standalone.payment.pending": "pending_payment",
        "standalone.payment.paid": "paid",
        "standalone.payment.rejected": "rejected",
        "standalone.delivery.ready": "delivery_ready",
        "standalone.installation.active": "delivery_ready",
        "standalone.upgrade.status": None,  # derive from payload status
        "standalone.upgrade.requested": "sent",
        "standalone.upgrade.applied": "delivery_ready",
    }
    if row and event in status_map and status_map[event]:
        row.status = status_map[event]
    if row and event == "standalone.upgrade.status":
        st = str(payload.get("status") or "").upper()
        if st in ("AWAITING_PAYMENT", "QUOTED"):
            row.status = "pending_payment"
        elif st == "PAID":
            row.status = "paid"
        elif st in ("ZIP_READY", "FULFILLED", "DELIVERED"):
            row.status = "delivery_ready"
        elif st in ("REJECTED", "CANCELLED"):
            row.status = "rejected"

    if event == "standalone.payment.paid" and business:
        if row:
            row.status = "paid"
        _mirror_won(db, business, note="AppointHive standalone.payment.paid (mirror only)")
        db.add(
            Activity(
                business_id=business.id,
                activity_type="be_payment_paid",
                channel="appointhive",
                summary="AppointHive marked product paid — mirrored Won (Sales OS did not collect cash)",
                outcome="paid",
            )
        )

    if business and event in (
        "standalone.purchase.recorded",
        "standalone.payment.pending",
        "standalone.upgrade.status",
        "standalone.delivery.ready",
        "standalone.installation.active",
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

    db.commit()
    return {
        "ok": True,
        "event": event,
        "delivery_id": row.id if row else None,
        "status": row.status if row else None,
    }
