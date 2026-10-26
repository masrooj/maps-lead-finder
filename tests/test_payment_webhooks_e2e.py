"""E2E: payment webhooks mark invoices paid (synthetic secrets — no live Stripe/PayPal keys required)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import BillingEntity, Business, Invoice
from app.services.secrets import store_setting_value


def _make_invoice(number: str, provider: str = "manual") -> int:
    with SessionLocal() as db:
        b = Business(
            place_id=f"pay-wh-{number}",
            name=f"Pay WH {number}",
            email="paywh@example.com",
            city="Sydney",
            country="AU",
        )
        db.add(b)
        db.flush()
        ent = db.scalar(select(BillingEntity).where(BillingEntity.active.is_(True)).limit(1))
        if not ent:
            ent = BillingEntity(legal_name="AppointHive Test", active=True)
            db.add(ent)
            db.flush()
        inv = Invoice(
            business_id=b.id,
            billing_entity_id=ent.id,
            number=number,
            status="open",
            currency="AUD",
            subtotal=99.0,
            tax_amount=0.0,
            total=99.0,
            provider=provider,
            provider_ref=f"{provider}_ref_{number}",
            line_items_json=json.dumps([{"name": "Setup", "qty": 1, "amount": 99}]),
            created_at=datetime.now(timezone.utc),
        )
        db.add(inv)
        db.commit()
        return inv.id


def _inv_status(iid: int) -> str:
    with SessionLocal() as db:
        return db.get(Invoice, iid).status


def test_stripe_webhook_marks_paid(auth_client):
    secret = "whsec_e2e_stripe"
    auth_client.put(
        "/api/sales/payment-settings",
        json={"key": "pay.stripe_webhook_secret", "value": secret},
    )
    number = "INV-WH-STRIPE-1"
    iid = _make_invoice(number, "stripe")
    body = json.dumps(
        {
            "id": "evt_e2e_stripe_1",
            "type": "checkout.session.completed",
            "data": {"object": {"id": "cs_e2e_1", "client_reference_id": number}},
        }
    ).encode()
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    r = auth_client.post(
        "/api/webhooks/stripe",
        content=body,
        headers={"Content-Type": "application/json", "stripe-signature": f"t={ts},v1={sig}"},
    )
    assert r.status_code == 200, r.text
    assert _inv_status(iid) == "paid"
    # idempotent
    r2 = auth_client.post(
        "/api/webhooks/stripe",
        content=body,
        headers={"Content-Type": "application/json", "stripe-signature": f"t={ts},v1={sig}"},
    )
    assert r2.status_code == 200
    assert r2.json().get("duplicate") is True


def test_paypal_webhook_marks_paid(auth_client):
    auth_client.put("/api/sales/payment-settings/bulk", json={"settings": {
        "pay.paypal_webhook_id": "WH-E2E-PP",
        "pay.paypal_client_secret": "pp_secret_e2e",
    }})
    number = "INV-WH-PP-1"
    iid = _make_invoice(number, "paypal")
    body = json.dumps(
        {
            "id": "WH-EVT-PP-1",
            "event_type": "PAYMENT.CAPTURE.COMPLETED",
            "resource": {"custom_id": number},
        }
    ).encode()
    sig = hmac.new(b"pp_secret_e2e", body, hashlib.sha256).hexdigest()
    r = auth_client.post(
        "/api/webhooks/paypal",
        content=body,
        headers={
            "Content-Type": "application/json",
            "paypal-transmission-id": "tx-1",
            "paypal-transmission-sig": sig,
        },
    )
    assert r.status_code == 200, r.text
    assert _inv_status(iid) == "paid"


def test_payoneer_webhook_marks_paid(auth_client):
    secret = "po_whsec_e2e"
    auth_client.put(
        "/api/sales/payment-settings",
        json={"key": "pay.payoneer_webhook_secret", "value": secret},
    )
    number = "INV-WH-PO-1"
    iid = _make_invoice(number, "payoneer")
    # match by invoice number
    r = auth_client.post(
        "/api/webhooks/payoneer",
        json={"id": "po_txn_e2e_1", "status": "completed", "invoice_number": number},
        headers={"X-Webhook-Secret": secret},
    )
    assert r.status_code == 200, r.text
    assert _inv_status(iid) == "paid"


def test_skrill_webhook_marks_paid(auth_client):
    secret = "skrill_whsec_e2e"
    auth_client.put(
        "/api/sales/payment-settings",
        json={"key": "pay.skrill_webhook_secret", "value": secret},
    )
    number = "INV-WH-SK-1"
    iid = _make_invoice(number, "skrill")
    r = auth_client.post(
        "/api/webhooks/skrill",
        data={
            "transaction_id": "sk_txn_e2e_1",
            "status": "2",
            "invoice_number": number,
        },
        headers={"X-Webhook-Secret": secret},
    )
    assert r.status_code == 200, r.text
    assert _inv_status(iid) == "paid"


def test_payoneer_webhook_rejects_bad_secret(auth_client):
    auth_client.put(
        "/api/sales/payment-settings",
        json={"key": "pay.payoneer_webhook_secret", "value": "good_secret"},
    )
    r = auth_client.post(
        "/api/webhooks/payoneer",
        json={"id": "x", "status": "completed"},
        headers={"X-Webhook-Secret": "bad"},
    )
    assert r.status_code == 401


def test_manual_confirm_paid(auth_client):
    number = "INV-WH-MAN-1"
    iid = _make_invoice(number, "manual")
    r = auth_client.post(f"/api/sales/invoices/{iid}/confirm-paid", json={"provider_ref": "bank-ref-1"})
    assert r.status_code == 200, r.text
    assert _inv_status(iid) == "paid"


def test_fill_local_payment_defaults(auth_client):
    """Seed non-live defaults used for local Pay setup (modes, public URL, notify secrets)."""
    r = auth_client.post("/api/sales/payment-settings/fill-local-defaults")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert "pay.public_base_url" in (body.get("saved") or [])
    assert "Stripe" in (body.get("note") or "") or "PayPal" in (body.get("note") or "")
    v_po = auth_client.post("/api/sales/payment-settings/validate", json={"provider": "payoneer"})
    assert v_po.status_code == 200 and v_po.json()["valid"] is True
    v_sk = auth_client.post("/api/sales/payment-settings/validate", json={"provider": "skrill"})
    assert v_sk.status_code == 200 and v_sk.json()["valid"] is True
    v_man = auth_client.post("/api/sales/payment-settings/validate", json={"provider": "manual"})
    assert v_man.status_code == 200 and v_man.json()["valid"] is True
    # Stripe/PayPal without live keys must report invalid (honest gap)
    v_st = auth_client.post("/api/sales/payment-settings/validate", json={"provider": "stripe"})
    assert v_st.status_code == 200 and v_st.json()["valid"] is False
    v_pp = auth_client.post("/api/sales/payment-settings/validate", json={"provider": "paypal"})
    assert v_pp.status_code == 200 and v_pp.json()["valid"] is False
