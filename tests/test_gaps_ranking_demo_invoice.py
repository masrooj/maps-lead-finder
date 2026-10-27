"""Gaps: ranking CRUD, demo assets, payment settings, invoice PDF/compose."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import BillingEntity, Business, Invoice


def test_ranking_modes_crud(auth_client):
    r = auth_client.get("/api/sales/ranking-modes")
    assert r.status_code == 200, r.text
    modes = r.json()["modes"]
    assert modes, "expected seeded ranking modes"
    assert any(m["slug"] == "default" for m in modes)

    create = auth_client.post(
        "/api/sales/ranking-modes",
        json={
            "name": "QA Custom Mode",
            "description": "test",
            "filter": {"has_email": True},
            "boost": {"base_boost": 3, "tag": "qa"},
            "sort_order": 99,
            "active": True,
        },
    )
    assert create.status_code == 200, create.text
    mid = create.json()["id"]
    assert create.json()["slug"]

    upd = auth_client.put(
        f"/api/sales/ranking-modes/{mid}",
        json={
            "name": "QA Custom Mode 2",
            "description": "updated",
            "filter": {"has_phone": True},
            "boost": {"base_boost": 4, "tag": "qa2"},
            "sort_order": 100,
            "active": True,
        },
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["name"] == "QA Custom Mode 2"

    all_modes = auth_client.get("/api/sales/ranking-modes?include_inactive=true")
    assert all_modes.status_code == 200
    assert any(m["id"] == mid for m in all_modes.json()["modes"])

    deleted = auth_client.delete(f"/api/sales/ranking-modes/{mid}")
    assert deleted.status_code == 200, deleted.text


def test_demo_assets_url_crud(auth_client):
    r = auth_client.post(
        "/api/sales/demo-assets",
        json={
            "title": "Loom demo",
            "kind": "link",
            "url": "https://www.loom.com/share/example",
            "notes": "client share",
            "active": True,
        },
    )
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    listed = auth_client.get("/api/sales/demo-assets")
    assert listed.status_code == 200
    assert any(a["id"] == aid for a in listed.json()["assets"])
    deleted = auth_client.delete(f"/api/sales/demo-assets/{aid}")
    assert deleted.status_code == 200


def test_payment_settings_owner(auth_client):
    r = auth_client.get("/api/sales/payment-settings")
    assert r.status_code == 200, r.text
    settings = r.json()["settings"]
    assert any(s["key"] == "pay.stripe_secret_key" for s in settings)

    put = auth_client.put(
        "/api/sales/payment-settings",
        json={"key": "pay.stripe_mode", "value": "test"},
    )
    assert put.status_code == 200, put.text
    bad = auth_client.put(
        "/api/sales/payment-settings",
        json={"key": "pay.not_a_key", "value": "x"},
    )
    assert bad.status_code == 422


def test_invoice_view_pdf_compose(auth_client):
    with SessionLocal() as db:
        b = Business(
            place_id="qa-invoice-place-001",
            name="Invoice QA Co",
            city="Sydney",
            country="AU",
            email="qa@example.com",
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
            number="INV-QA-001",
            status="open",
            currency="AUD",
            subtotal=100.0,
            tax_amount=0.0,
            total=100.0,
            provider="manual",
            line_items_json=json.dumps([{"name": "Setup", "qty": 1, "amount": 100}]),
            created_at=datetime.now(timezone.utc),
        )
        db.add(inv)
        db.commit()
        iid = inv.id

    view = auth_client.get(f"/api/sales/invoices/{iid}/view")
    assert view.status_code == 200, view.text
    body = view.json()
    assert body["number"] == "INV-QA-001"
    assert body["total"] == 100.0
    assert "logo_url" in body
    assert body.get("total_fmt")
    assert body.get("subtotal_fmt")
    assert body.get("billing_entity", {}).get("legal_name")
    assert body.get("business", {}).get("name") == "Invoice QA Co"
    assert body.get("line_items")
    assert body["line_items"][0].get("line_total") == 100.0
    assert body["line_items"][0].get("line_total_fmt")

    pdf = auth_client.get(f"/api/sales/invoices/{iid}/pdf")
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers.get("content-type", "").startswith("application/pdf")
    assert pdf.content[:4] == b"%PDF"
    assert len(pdf.content) > 800

    compose = auth_client.get(f"/api/sales/invoices/{iid}/compose-copy")
    assert compose.status_code == 200, compose.text
    body = compose.json()
    assert "INV-QA-001" in body["subject"] or "INV-QA-001" in body["body"]
    assert body["pdf_url"].endswith(f"/invoices/{iid}/pdf")

    listed = auth_client.get("/api/sales/invoices")
    assert listed.status_code == 200, listed.text
    row = next((i for i in listed.json()["invoices"] if i["id"] == iid), None)
    assert row is not None
    assert row.get("business_name") == "Invoice QA Co"

    voided = auth_client.post(f"/api/sales/invoices/{iid}/void", json={"reason": "QA duplicate"})
    assert voided.status_code == 200, voided.text
    assert voided.json()["status"] == "void"

    again = auth_client.post(f"/api/sales/invoices/{iid}/void", json={})
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "void"


def test_void_rejects_paid_invoice(auth_client):
    with SessionLocal() as db:
        b = Business(
            name="QA Paid Void",
            place_id="places/qa-void-paid",
            category="salon",
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
            number="INV-QA-PAID-001",
            status="paid",
            currency="AUD",
            subtotal=50.0,
            tax_amount=0.0,
            total=50.0,
            provider="manual",
            line_items_json=json.dumps([{"name": "Setup", "qty": 1, "amount": 50}]),
            paid_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
        )
        db.add(inv)
        db.commit()
        iid = inv.id

    r = auth_client.post(f"/api/sales/invoices/{iid}/void", json={"reason": "should fail"})
    assert r.status_code == 400, r.text
