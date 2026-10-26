"""Team, audit, jobs, cron, catalog CRUD, template gate, webhooks."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

import os

from app.services import secrets as sec
from app.db import SessionLocal
from app.models import (
    AppSetting,
    AuditEvent,
    Business,
    Invoice,
    MessageTemplate,
    Plan,
    PlanFeature,
    PlanPrice,
    SalesTerritory,
    User,
)
from app.services.authz import get_user_by_email, require_owner_for_money, require_role
from app.services.billing.service import mark_invoice_paid, record_webhook_once
from app.services.compliance import template_copy_allowed
from app.services.jobs import create_job, tick_job
from app.services.passwords import hash_password
from app.services.webhooks_crypto import verify_paypal_headers, verify_stripe_signature

router = APIRouter(tags=["ops"])
_require_login = None


def bind_auth(fn):
    global _require_login
    _require_login = fn


def require_user(request: Request) -> str:
    if not _require_login:
        raise HTTPException(500, "Auth not bound")
    return _require_login(request)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _audit(db, actor_id: Optional[int], action: str, entity_type: str = "", entity_id: Optional[int] = None, detail: Optional[dict] = None):
    db.add(
        AuditEvent(
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            detail_json=json.dumps(detail or {}),
        )
    )


class TerritoryBody(BaseModel):
    name: str
    country: str = ""
    region: str = ""
    owner_id: Optional[int] = None
    active: bool = True


class AssignBody(BaseModel):
    owner_id: Optional[int] = None


class PlanBody(BaseModel):
    product_line: str = "STANDALONE"
    code: str
    name: str
    description: str = ""
    sort_order: int = 0
    billing_period: str = "ONE_TIME"
    active: bool = True
    prices: list[dict[str, Any]] = Field(default_factory=list)
    features: list[dict[str, Any]] = Field(default_factory=list)


class PreviewBody(BaseModel):
    business_id: int
    template_id: Optional[int] = None
    channel: str = "email"
    subject: str = ""
    body: str = ""


@router.get("/api/sales/users")
def list_users(_user: str = Depends(require_user), db=Depends(get_db)):
    rows = db.scalars(select(User).order_by(User.id)).all()
    return {
        "users": [
            {
                "id": u.id,
                "email": u.email,
                "name": u.name,
                "role": u.role,
                "timezone": u.timezone,
                "active": u.active,
                "has_password": bool(u.password_hash),
            }
            for u in rows
        ]
    }


@router.post("/api/sales/users")
def create_user(payload: dict[str, Any], _user: str = Depends(require_user), db=Depends(get_db)):
    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    email = str(payload.get("email") or "").strip().lower()
    if not email:
        raise HTTPException(422, "email required")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "User already exists")
    role = str(payload.get("role") or "sales")
    if role not in ("owner", "sales", "viewer"):
        raise HTTPException(422, "invalid role")
    password = str(payload.get("password") or "")
    if password and len(password) < 8:
        raise HTTPException(422, "password must be at least 8 characters")
    u = User(
        email=email,
        name=str(payload.get("name") or "")[:200],
        role=role,
        timezone=str(payload.get("timezone") or "Australia/Sydney")[:80],
        active=True,
        password_hash=hash_password(password) if password else "",
    )
    db.add(u)
    db.flush()
    _audit(
        db,
        actor.id if actor else None,
        "user_create",
        "user",
        u.id,
        {"email": email, "role": role, "has_password": bool(password)},
    )
    db.commit()
    return {"id": u.id, "email": u.email, "role": u.role, "has_password": bool(password)}


@router.post("/api/sales/users/{user_id}/password")
def set_user_password(user_id: int, payload: dict[str, Any], _user: str = Depends(require_user), db=Depends(get_db)):
    actor = get_user_by_email(db, _user)
    if not actor:
        raise HTTPException(403, "Active user required")
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    # Owner can set any; user can set own
    if actor.role != "owner" and actor.id != target.id:
        raise HTTPException(403, "Cannot set another user's password")
    password = str(payload.get("password") or "")
    if len(password) < 8:
        raise HTTPException(422, "password must be at least 8 characters")
    target.password_hash = hash_password(password)
    _audit(db, actor.id, "user_password_set", "user", target.id, {})
    db.commit()
    return {"ok": True, "id": target.id}


@router.get("/api/sales/territories")
def list_territories(_user: str = Depends(require_user), db=Depends(get_db)):
    rows = db.scalars(select(SalesTerritory).order_by(SalesTerritory.id)).all()
    return {
        "territories": [
            {
                "id": t.id,
                "name": t.name,
                "country": t.country,
                "region": t.region,
                "owner_id": t.owner_id,
                "active": t.active,
            }
            for t in rows
        ]
    }


@router.post("/api/sales/territories")
def create_territory(body: TerritoryBody, _user: str = Depends(require_user), db=Depends(get_db)):
    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    t = SalesTerritory(
        name=body.name[:200],
        country=body.country[:120],
        region=body.region[:120],
        owner_id=body.owner_id,
        active=body.active,
    )
    db.add(t)
    db.flush()
    _audit(db, actor.id if actor else None, "territory_create", "territory", t.id, body.model_dump())
    db.commit()
    return {"id": t.id}


@router.post("/api/sales/territories/{territory_id}/assign-prospects")
def assign_territory_prospects(territory_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    """Assign unassigned prospects matching territory country to territory.owner_id."""
    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    t = db.get(SalesTerritory, territory_id)
    if not t or not t.active:
        raise HTTPException(404, "Territory not found")
    if not t.owner_id:
        raise HTTPException(422, "Territory has no owner_id")
    country = (t.country or "").strip()
    if not country:
        raise HTTPException(422, "Territory country required for auto-assign")
    rows = db.scalars(
        select(Business).where(
            Business.country.ilike(country),
            Business.owner_id.is_(None),
        )
    ).all()
    n = 0
    for b in rows:
        b.owner_id = t.owner_id
        n += 1
    _audit(db, actor.id if actor else None, "territory_assign_batch", "territory", t.id, {"assigned": n, "country": country})
    db.commit()
    return {"ok": True, "assigned": n, "territory_id": t.id, "owner_id": t.owner_id}


@router.post("/api/sales/prospects/{business_id}/assign")
def assign_prospect(business_id: int, body: AssignBody, _user: str = Depends(require_user), db=Depends(get_db)):
    actor = get_user_by_email(db, _user)
    require_role(actor, "owner", "sales")
    b = db.get(Business, business_id)
    if not b:
        raise HTTPException(404, "Prospect not found")
    if actor.role == "sales" and b.owner_id not in (None, actor.id):
        raise HTTPException(403, "Sales can only assign unassigned or own prospects")
    if body.owner_id is not None and not db.get(User, body.owner_id):
        raise HTTPException(422, "owner_id not found")
    b.owner_id = body.owner_id
    _audit(db, actor.id if actor else None, "prospect_assign", "business", b.id, {"owner_id": body.owner_id})
    db.commit()
    return {"id": b.id, "owner_id": b.owner_id}


@router.get("/api/sales/audit")
def list_audit(limit: int = 100, _user: str = Depends(require_user), db=Depends(get_db)):
    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    rows = db.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(min(limit, 500))).all()
    return {
        "events": [
            {
                "id": e.id,
                "actor_id": e.actor_id,
                "action": e.action,
                "entity_type": e.entity_type,
                "entity_id": e.entity_id,
                "detail": json.loads(e.detail_json or "{}"),
                "created_at": e.created_at.isoformat() if e.created_at else "",
            }
            for e in rows
        ]
    }


@router.post("/api/sales/plans")
def create_plan(body: PlanBody, _user: str = Depends(require_user), db=Depends(get_db)):
    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    if db.scalar(select(Plan).where(Plan.code == body.code)):
        raise HTTPException(409, "Plan code exists")
    plan = Plan(
        product_line=body.product_line.upper(),
        code=body.code,
        name=body.name,
        description=body.description,
        sort_order=body.sort_order,
        billing_period=body.billing_period,
        active=body.active,
    )
    db.add(plan)
    db.flush()
    for p in body.prices:
        db.add(
            PlanPrice(
                plan_id=plan.id,
                currency=str(p.get("currency") or "AUD").upper(),
                amount=float(p.get("amount") or 0),
                valid_from=datetime.now(timezone.utc),
            )
        )
    for f in body.features:
        db.add(
            PlanFeature(
                plan_id=plan.id,
                feature_key=str(f.get("key") or f.get("feature_key") or ""),
                label=str(f.get("label") or ""),
                included=bool(f.get("included", True)),
                limit_value=f.get("limit"),
            )
        )
    _audit(db, actor.id if actor else None, "plan_create", "plan", plan.id, {"code": plan.code})
    db.commit()
    return {"id": plan.id, "code": plan.code}


@router.patch("/api/sales/plans/{plan_id}")
def patch_plan(plan_id: int, payload: dict[str, Any], _user: str = Depends(require_user), db=Depends(get_db)):
    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    plan = db.get(Plan, plan_id)
    if not plan:
        raise HTTPException(404, "Plan not found")
    for key in ("name", "description", "sort_order", "active", "billing_period"):
        if key in payload:
            setattr(plan, key, payload[key])
    if "prices" in payload and isinstance(payload["prices"], list):
        # Close old prices and add new rows (preserve history)
        now = datetime.now(timezone.utc)
        for old in db.scalars(select(PlanPrice).where(PlanPrice.plan_id == plan.id, PlanPrice.valid_to.is_(None))).all():
            old.valid_to = now
        for p in payload["prices"]:
            db.add(
                PlanPrice(
                    plan_id=plan.id,
                    currency=str(p.get("currency") or "AUD").upper(),
                    amount=float(p.get("amount") or 0),
                    valid_from=now,
                )
            )
    _audit(db, actor.id if actor else None, "plan_update", "plan", plan.id, payload)
    db.commit()
    return {"id": plan.id, "ok": True}


@router.post("/api/sales/templates/preview")
def preview_template(body: PreviewBody, _user: str = Depends(require_user), db=Depends(get_db)):
    b = db.get(Business, body.business_id)
    if not b:
        raise HTTPException(404, "Prospect not found")
    tpl = db.get(MessageTemplate, body.template_id) if body.template_id else None
    block = template_copy_allowed(db, b, tpl, body.channel)
    if block:
        raise HTTPException(403, block)
    # Fill verified fields only
    mapping = {
        "{{name}}": b.name or "",
        "{{city}}": b.city or "",
        "{{category}}": b.category or "",
        "{{phone}}": b.phone or b.international_phone or "",
        "{{website}}": b.website or "",
        "{{country}}": b.country or "",
    }
    subject, text = body.subject, body.body
    if tpl:
        subject = tpl.subject or subject
        text = tpl.body or text
    for k, v in mapping.items():
        subject = subject.replace(k, v)
        text = text.replace(k, v)
    return {"ok": True, "subject": subject, "body": text, "blocked": False}


@router.post("/api/jobs")
def start_job(job_type: str = "unpaid_invoice_followups", _user: str = Depends(require_user), db=Depends(get_db)):
    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    job = create_job(db, job_type)
    return {"id": job.id, "status": job.status, "job_type": job.job_type}


@router.post("/api/sales/jobs")
async def start_sales_job(request: Request, _user: str = Depends(require_user), db=Depends(get_db)):
    actor = get_user_by_email(db, _user)
    require_role(actor, "owner", "sales")
    try:
        body = await request.json()
    except Exception:
        body = {}
    jt = str((body or {}).get("job_type") or "noop")
    payload = (body or {}).get("payload") or {}
    if jt not in ("unpaid_invoice_followups", "enrich_batch", "noop"):
        raise HTTPException(422, "Unsupported job_type")
    job = create_job(db, jt, payload=payload if isinstance(payload, dict) else {})
    return {"id": job.id, "status": job.status, "job_type": job.job_type}


@router.post("/api/jobs/{job_id}/tick")
def job_tick(job_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    return tick_job(db, job_id)


@router.post("/api/cron/unpaid-invoices")
def cron_unpaid(request: Request, x_cron_secret: str = Header(default=""), db=Depends(get_db)):
    # If CRON_SECRET is configured: header must match (session alone is not enough).
    # If CRON_SECRET is empty: Owner session may run manually.
    cron_secret = os.getenv("CRON_SECRET", "").strip()
    if cron_secret:
        if x_cron_secret != cron_secret:
            raise HTTPException(401, "Invalid cron secret")
    else:
        if not _require_login:
            raise HTTPException(401, "Cron secret or login required")
        user = _require_login(request)
        actor = get_user_by_email(db, user)
        require_role(actor, "owner")
    job = create_job(db, "unpaid_invoice_followups")
    result = tick_job(db, job.id)
    while not result.get("done"):
        result = tick_job(db, job.id)
    return result


@router.post("/api/webhooks/stripe")
async def stripe_webhook(request: Request, db=Depends(get_db)):
    payload = await request.body()
    verify_stripe_signature(payload, request.headers.get("stripe-signature", ""), sec.stripe_webhook_secret())
    try:
        data = json.loads(payload.decode() or "{}")
    except Exception:
        raise HTTPException(400, "Invalid JSON")
    event_id = str(data.get("id") or "")
    if not event_id:
        raise HTTPException(400, "Missing event id")
    if not record_webhook_once(db, "stripe", event_id, data):
        return {"ok": True, "duplicate": True}
    etype = data.get("type") or ""
    obj = (data.get("data") or {}).get("object") or {}
    if etype in ("checkout.session.completed", "invoice.paid"):
        ref = str(obj.get("client_reference_id") or obj.get("metadata", {}).get("invoice_number") or "")
        inv = None
        if ref:
            inv = db.scalar(select(Invoice).where(Invoice.number == ref))
        if not inv and obj.get("id"):
            inv = db.scalar(select(Invoice).where(Invoice.provider_ref == str(obj.get("id"))))
        if inv and inv.status != "paid":
            mark_invoice_paid(db, inv, provider_ref=str(obj.get("id") or ""), method="stripe_webhook")
    return {"ok": True}


@router.post("/api/webhooks/paypal")
async def paypal_webhook(request: Request, db=Depends(get_db)):
    raw = await request.body()
    verify_paypal_headers(
        webhook_id=sec.paypal_webhook_id(),
        transmission_id=request.headers.get("paypal-transmission-id"),
        transmission_sig=request.headers.get("paypal-transmission-sig"),
        client_secret=sec.paypal_client_secret(),
        body=raw,
    )
    try:
        data = json.loads(raw.decode() or "{}")
    except Exception:
        raise HTTPException(400, "Invalid JSON")
    event_id = str(data.get("id") or data.get("event_id") or "")
    if not event_id:
        raise HTTPException(400, "Missing event id")
    if not record_webhook_once(db, "paypal", event_id, data):
        return {"ok": True, "duplicate": True}
    etype = data.get("event_type") or data.get("type") or ""
    resource = data.get("resource") or {}
    if "PAYMENT.CAPTURE.COMPLETED" in etype or etype.endswith("COMPLETED"):
        custom = str(resource.get("custom_id") or resource.get("invoice_id") or "")
        inv = db.scalar(select(Invoice).where(Invoice.number == custom)) if custom else None
        if inv and inv.status != "paid":
            mark_invoice_paid(db, inv, provider_ref=event_id, method="paypal_webhook")
    return {"ok": True}


def _require_shared_webhook_secret(request: Request, configured: str, header_name: str = "X-Webhook-Secret"):
    if not configured:
        raise HTTPException(503, f"Webhook not configured — set secret for {header_name}")
    got = (request.headers.get(header_name) or request.headers.get("x-webhook-secret") or "").strip()
    if not got or got != configured:
        raise HTTPException(401, "Invalid webhook secret")


def _find_invoice_by_ref(db, data: dict) -> Optional[Invoice]:
    ref = str(
        data.get("provider_ref")
        or data.get("reference")
        or data.get("payment_reference")
        or data.get("invoice_number")
        or data.get("custom_id")
        or ""
    ).strip()
    if not ref:
        return None
    inv = db.scalar(select(Invoice).where(Invoice.provider_ref == ref))
    if inv:
        return inv
    return db.scalar(select(Invoice).where(Invoice.number == ref))


@router.post("/api/webhooks/payoneer")
async def payoneer_webhook(request: Request, db=Depends(get_db)):
    """Mark invoice paid from Payoneer notify. Header: X-Webhook-Secret."""
    _require_shared_webhook_secret(request, sec.payoneer_webhook_secret())
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")
    event_id = str(data.get("id") or data.get("event_id") or data.get("transaction_id") or "")
    if not event_id:
        raise HTTPException(400, "Missing event id")
    if not record_webhook_once(db, "payoneer", event_id, data):
        return {"ok": True, "duplicate": True}
    status = str(data.get("status") or data.get("payment_status") or "completed").lower()
    if status in ("completed", "paid", "success", "succeeded", "released"):
        inv = _find_invoice_by_ref(db, data)
        if inv and inv.status != "paid":
            mark_invoice_paid(db, inv, provider_ref=event_id, method="payoneer_webhook")
            return {"ok": True, "invoice": inv.number}
    return {"ok": True, "ignored": True}


@router.post("/api/webhooks/skrill")
async def skrill_webhook(request: Request, db=Depends(get_db)):
    """Mark invoice paid from Skrill status URL / notify. Header: X-Webhook-Secret."""
    _require_shared_webhook_secret(request, sec.skrill_webhook_secret())
    # Skrill often posts form-encoded; accept JSON too
    ctype = (request.headers.get("content-type") or "").lower()
    data: dict[str, Any] = {}
    if "application/json" in ctype:
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON")
    else:
        form = await request.form()
        data = {k: str(v) for k, v in form.items()}
    event_id = str(data.get("transaction_id") or data.get("mb_transaction_id") or data.get("id") or "")
    if not event_id:
        raise HTTPException(400, "Missing transaction id")
    if not record_webhook_once(db, "skrill", event_id, data):
        return {"ok": True, "duplicate": True}
    status = str(data.get("status") or data.get("payment_status") or "").lower()
    # Skrill: 2 = processed
    if status in ("2", "processed", "completed", "paid", "success", "succeeded"):
        # Skrill field often "transaction_id" for merchant ref
        if not data.get("provider_ref") and data.get("transaction_id"):
            data = {**data, "provider_ref": data.get("transaction_id")}
        if data.get("merchant_fields") or data.get("field1"):
            data = {**data, "provider_ref": data.get("field1") or data.get("merchant_fields")}
        inv = _find_invoice_by_ref(db, data)
        if not inv and data.get("invoice_number"):
            inv = db.scalar(select(Invoice).where(Invoice.number == str(data["invoice_number"])))
        if inv and inv.status != "paid":
            mark_invoice_paid(db, inv, provider_ref=event_id, method="skrill_webhook")
            return {"ok": True, "invoice": inv.number}
    return {"ok": True, "ignored": True}


@router.get("/api/sales/me")
def sales_me(_user: str = Depends(require_user), db=Depends(get_db)):
    u = get_user_by_email(db, _user)
    settings = {r.key: r.value for r in db.scalars(select(AppSetting)).all()}
    return {
        "username": _user,
        "user": None
        if not u
        else {"id": u.id, "email": u.email, "name": u.name, "role": u.role, "timezone": u.timezone},
        "saas_enabled": (settings.get("saas_enabled") or "false").lower() in ("1", "true", "yes"),
        "won_on": settings.get("won_on") or "first_payment",
    }


# —— AppointHive product pairing (public) + Booking Engine webhooks ——
_PAIRING_HITS: list[float] = []


class ProductPairingBody(BaseModel):
    code: str = Field(min_length=4, max_length=200)
    productBaseUrl: str = Field(min_length=8, max_length=500)


@router.post("/api/v1/public/product-pairing")
def public_product_pairing(body: ProductPairingBody, request: Request, db=Depends(get_db)):
    """Booking Engine Super Admin pairs here with a one-time Owner code."""
    import time

    from app.config import PUBLIC_BASE_URL
    from app.services.booking_engine.connect import consume_pairing_code, issue_pairing_secret

    now = time.time()
    # Simple IP-agnostic rate limit: 30 attempts / 15 min process-wide
    _PAIRING_HITS[:] = [t for t in _PAIRING_HITS if now - t < 900]
    if len(_PAIRING_HITS) >= 30:
        raise HTTPException(429, "Too many pairing attempts")
    _PAIRING_HITS.append(now)

    if not consume_pairing_code(db, body.code.strip()):
        raise HTTPException(401, detail={"error": "Invalid or expired pairing code"})
    result = issue_pairing_secret(
        db,
        product_base_url=body.productBaseUrl,
        public_base=PUBLIC_BASE_URL,
    )
    # Never log secret
    return {
        "secret": result["secret"],
        "sharedSecret": result["sharedSecret"],
        "webhookUrl": result["webhookUrl"],
        "product": "AppointHive Sales OS",
    }


@router.post("/api/webhooks/booking-engine")
async def booking_engine_webhook(request: Request, db=Depends(get_db)):
    """Inbound AppointHive events. Bearer shared secret. Never creates local product PSP invoices."""
    from app.services.booking_engine.connect import get_be_shared_secret, get_connection_status, is_bridge_enabled
    from app.services.booking_engine.handoff import apply_be_webhook
    from app.services.billing.service import record_webhook_once

    if not is_bridge_enabled(db=db) and get_connection_status(db=db) == "disconnected":
        # Ignore quietly when fully off
        return {"ok": True, "ignored": True, "reason": "bridge_disabled"}

    secret = get_be_shared_secret(db=db)
    if not secret:
        raise HTTPException(503, "Booking Engine webhook secret not configured")
    auth = (request.headers.get("authorization") or "").strip()
    token = ""
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token or token != secret:
        raise HTTPException(401, "Invalid webhook bearer")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(400, "Expected object")

    event = str(payload.get("event") or "unknown")
    event_id = str(
        payload.get("eventId")
        or payload.get("id")
        or f"{event}:{payload.get('purchaseId') or ''}:{payload.get('requestId') or ''}:{payload.get('installationId') or ''}:{payload.get('connectedAt') or ''}"
    )
    if not record_webhook_once(db, "booking_engine", event_id[:200], payload):
        return {"ok": True, "duplicate": True}
    return apply_be_webhook(db, payload)
