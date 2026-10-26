#!/usr/bin/env python3
"""Live E2E: connect → soft request → purchase → payment.paid → Won → installation_id."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

# Allow `from app…` when run as scripts/_e2e_bridge_live.py
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import httpx

BASE = os.environ.get("SO_BASE", "http://127.0.0.1:8000")
BIZ_ID = int(os.environ.get("E2E_BIZ_ID", "553"))


def j(r):
    try:
        return r.json()
    except Exception:
        return {"_raw": (r.text or "")[:900]}


def main() -> int:
    user = os.environ.get("LOGIN_USERNAME") or "hello@appointhive.com"
    password = os.environ.get("LOGIN_PASSWORD") or ""
    if not password:
        print("LOGIN_PASSWORD missing")
        return 1

    c = httpx.Client(base_url=BASE, timeout=90.0, follow_redirects=True)
    print("health", c.get("/api/health").json())
    r = c.post("/api/login", json={"username": user, "password": password})
    print("login", r.status_code)
    if r.status_code != 200:
        print(j(r))
        return 1

    r = c.patch(
        f"/api/sales/prospects/{BIZ_ID}",
        json={
            "email": "e2e.owner@example.com",
            "phone": "+61400000553",
            "international_phone": "+61400000553",
            "website": "https://e2e-bridge.example",
            "address": "1 Test St",
            "city": "Sydney",
            "country": "AU",
            "main_pain": "No online booking",
            "notes": "E2E full prospect handoff",
            "product_line": "STANDALONE",
        },
    )
    print("patch", r.status_code)

    # contact endpoints vary; ensure via DB if API missing
    r = c.get(f"/api/sales/contacts/{BIZ_ID}")
    print("contacts", r.status_code, str(j(r))[:200])
    rr = c.post(
        "/api/sales/contacts",
        json={
            "business_id": BIZ_ID,
            "name": "Alex Owner",
            "email": "e2e.owner@example.com",
            "phone": "+61400000553",
            "role": "Owner",
        },
    )
    print("post contact", rr.status_code, str(j(rr))[:200])
    if rr.status_code >= 400:
        from app.db import SessionLocal
        from app.models import Contact

        db = SessionLocal()
        try:
            existing = db.query(Contact).filter(Contact.business_id == BIZ_ID).first()
            if not existing:
                db.add(
                    Contact(
                        business_id=BIZ_ID,
                        name="Alex Owner",
                        email="e2e.owner@example.com",
                        phone="+61400000553",
                        role="Owner",
                    )
                )
                db.commit()
                print("contact inserted via db")
            else:
                existing.name = existing.name or "Alex Owner"
                existing.email = existing.email or "e2e.owner@example.com"
                existing.phone = existing.phone or "+61400000553"
                existing.role = existing.role or "Owner"
                db.commit()
                print("contact updated via db")
        finally:
            db.close()

    r = c.post("/api/sales/be/connection/test")
    print("BE test", r.status_code, j(r).get("connected"), j(r).get("message"))
    if not j(r).get("connected"):
        print(j(r))
        return 2

    revs = c.get("/api/sales/be/revisions").json().get("revisions") or []
    if not revs:
        print("no revisions")
        return 3
    revision_id = revs[0].get("productRevisionId") or revs[0].get("id")
    print("revision", revision_id, revs[0].get("priceCents"), revs[0].get("edition"))

    r = c.post(
        "/api/sales/be/send-request",
        json={"business_id": BIZ_ID, "message": "E2E soft — full business details"},
    )
    soft = j(r)
    print("SOFT", r.status_code)
    print(json.dumps(soft, default=str)[:1400])

    r = c.post(
        "/api/sales/be/send-to-delivery",
        json={
            "business_id": BIZ_ID,
            "product_revision_id": revision_id,
            "create_checkout": True,
        },
    )
    purch = j(r)
    print("PURCHASE", r.status_code)
    print(json.dumps(purch, default=str)[:1400])

    request_id = None
    purchase_id = None
    delivery_id = None
    if isinstance(soft, dict):
        be = soft.get("be") or soft.get("send") or {}
        delivery = soft.get("delivery") or {}
        request_id = (
            soft.get("inbound_request_id")
            or soft.get("request_id")
            or soft.get("requestId")
            or (be.get("requestId") if isinstance(be, dict) else None)
            or (be.get("id") if isinstance(be, dict) else None)
            or (delivery.get("inbound_request_id") if isinstance(delivery, dict) else None)
        )
        delivery_id = soft.get("delivery_id") or (delivery.get("id") if isinstance(delivery, dict) else None)
    if isinstance(purch, dict) and "detail" not in purch:
        be = purch.get("be") or purch.get("send") or {}
        delivery = purch.get("delivery") or {}
        purchase_id = (
            purch.get("purchase_id")
            or purch.get("purchaseId")
            or (be.get("purchaseId") if isinstance(be, dict) else None)
            or (be.get("id") if isinstance(be, dict) else None)
            or (delivery.get("purchase_id") if isinstance(delivery, dict) else None)
        )
        delivery_id = delivery_id or purch.get("delivery_id") or (delivery.get("id") if isinstance(delivery, dict) else None)
    elif isinstance(purch, dict):
        be = purch.get("be") or purch.get("send") or {}
        if isinstance(be, dict):
            purchase_id = be.get("purchaseId") or be.get("id")

    print("ids", dict(request_id=request_id, purchase_id=purchase_id, delivery_id=delivery_id))

    from app.db import SessionLocal
    from app.services.booking_engine import connect as be_connect

    db = SessionLocal()
    try:
        secret = be_connect.get_be_shared_secret(db=db)
    except Exception as e:
        secret = None
        print("secret_err", type(e).__name__, e)
    finally:
        db.close()

    crm = f"business:{BIZ_ID}"
    pid = purchase_id or f"purch_e2e_{uuid.uuid4().hex[:8]}"
    if secret and (request_id or purchase_id):
        payloads = []
        if request_id:
            payloads.append(
                {
                    "type": "standalone.quote.created",
                    "event": "standalone.quote.created",
                    "crmLeadId": crm,
                    "externalCrmRef": crm,
                    "requestId": request_id,
                    "status": "AWAITING_PAYMENT",
                    "quoteCents": 499900,
                    "payUrl": "https://pay.example.test/e2e-checkout",
                    "purchaseId": pid,
                }
            )
        payloads.extend(
            [
                {
                    "type": "standalone.payment.paid",
                    "event": "standalone.payment.paid",
                    "crmLeadId": crm,
                    "externalCrmRef": crm,
                    "requestId": request_id,
                    "purchaseId": pid,
                    "status": "PAID",
                    "amountCents": 499900,
                },
                {
                    "type": "standalone.installation.active",
                    "event": "standalone.installation.active",
                    "crmLeadId": crm,
                    "externalCrmRef": crm,
                    "requestId": request_id,
                    "purchaseId": pid,
                    "installationId": "inst_e2e_live_001",
                    "status": "ACTIVE",
                },
            ]
        )
        for p in payloads:
            rr = c.post(
                "/api/webhooks/booking-engine",
                headers={"Authorization": f"Bearer {secret}"},
                json=p,
            )
            print("webhook", p["type"], rr.status_code, str(j(rr))[:240])

    r = c.get("/api/sales/be/deliveries")
    items = j(r).get("deliveries") or j(r).get("items") or []
    mine = [d for d in items if d.get("business_id") == BIZ_ID]
    print("DELIVERIES", json.dumps(mine, default=str)[:1200])

    r = c.get("/api/sales/prospects", params={"q": "E2E Bridge"})
    for p in j(r).get("prospects") or []:
        if p.get("id") == BIZ_ID:
            print(
                "PROSPECT",
                {
                    k: p.get(k)
                    for k in (
                        "id",
                        "name",
                        "email",
                        "stage",
                        "stage_name",
                        "pipeline_stage_name",
                        "outcome",
                        "won",
                        "be_installation_id",
                        "status",
                    )
                    if k in p or True
                },
            )
            break

    # DB truth for Won + installation
    db = SessionLocal()
    try:
        from app.models import Business, BeDelivery

        b = db.get(Business, BIZ_ID)
        print(
            "DB business",
            {
                "id": b.id,
                "email": b.email,
                "pipeline_stage_id": b.pipeline_stage_id,
                "outcome": getattr(b, "outcome", None),
                "won": getattr(b, "won", None),
                "be_installation_id": getattr(b, "be_installation_id", None),
            },
        )
        rows = (
            db.query(BeDelivery)
            .filter(BeDelivery.business_id == BIZ_ID)
            .order_by(BeDelivery.id.desc())
            .limit(5)
            .all()
        )
        for row in rows:
            print(
                "DB delivery",
                {
                    "id": row.id,
                    "status": row.status,
                    "request_id": row.inbound_request_id,
                    "purchase_id": row.purchase_id,
                    "pay_url": (row.pay_url or "")[:80],
                    "installation_id": row.installation_id,
                },
            )
    finally:
        db.close()

    soft_ok = isinstance(soft, dict) and soft.get("detail") is None and (
        soft.get("ok") is True or soft.get("requestId") or soft.get("delivery_id") or soft.get("id")
    )
    # tolerate various response shapes
    if isinstance(soft, dict) and not soft.get("detail") and r.status_code:
        soft_ok = soft_ok or bool(request_id or soft.get("status") or soft.get("delivery"))

    purchase_ok = isinstance(purch, dict) and not purch.get("detail")
    print(
        "RESULT",
        json.dumps(
            {
                "soft_ok": bool(soft_ok or request_id),
                "purchase_ok": bool(purchase_ok),
                "request_id": request_id,
                "purchase_id": purchase_id,
                "purchase_error": purch.get("detail") if isinstance(purch, dict) else None,
            },
            default=str,
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
