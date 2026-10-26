"""Extended Sales OS tests — compliance, jobs, SaaS plans, webhooks, roles."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, Invoice, Proposal, Suppression
from app.services.pipeline import init_new_prospect
from app.services.jobs import create_job, tick_job
from app.services.billing.service import create_invoice_from_proposal, create_proposal, record_webhook_once


def test_saas_plans_seeded(auth_client):
    r = auth_client.get("/api/sales/plans?product_line=SAAS")
    assert r.status_code == 200
    assert len(r.json()["plans"]) >= 3


def test_opt_out_blocks_template_preview(auth_client):
    with SessionLocal() as db:
        b = Business(place_id="ext/optout-1", name="Opt Out Shop", opt_out=True)
        db.add(b)
        db.flush()
        init_new_prospect(db, b)
        db.commit()
        bid = b.id
    r = auth_client.post(
        "/api/sales/templates/preview",
        json={"business_id": bid, "channel": "email", "subject": "Hi", "body": "Hello {{name}}"},
    )
    assert r.status_code == 403


def test_suppression_blocks_preview(auth_client):
    with SessionLocal() as db:
        db.add(Suppression(kind="email", value="blocked@example.com", reason="requested"))
        b = Business(place_id="ext/supp-1", name="Supp Shop", email="blocked@example.com")
        db.add(b)
        db.flush()
        init_new_prospect(db, b)
        db.commit()
        bid = b.id
    r = auth_client.post(
        "/api/sales/templates/preview",
        json={"business_id": bid, "channel": "email", "body": "Hi"},
    )
    assert r.status_code == 403


def test_job_tick_unpaid_followups(auth_client):
    with SessionLocal() as db:
        b = Business(place_id="ext/inv-1", name="Invoice Shop")
        db.add(b)
        db.flush()
        init_new_prospect(db, b)
        db.commit()
        prop = create_proposal(
            db, b, title="T", currency="AUD", line_items=[{"name": "X", "qty": 1, "amount": 100}]
        )
        inv = create_invoice_from_proposal(db, prop, provider="manual", expires_days=14)
        inv.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()
        job = create_job(db, "unpaid_invoice_followups")
        result = tick_job(db, job.id)
        while not result.get("done"):
            result = tick_job(db, job.id)
        assert result["done"] is True


def test_webhook_idempotent():
    with SessionLocal() as db:
        assert record_webhook_once(db, "stripe", "evt_test_1", {"id": "evt_test_1"}) is True
        assert record_webhook_once(db, "stripe", "evt_test_1", {"id": "evt_test_1"}) is False


def test_sales_me_and_saas_flag(auth_client):
    r = auth_client.get("/api/sales/me")
    assert r.status_code == 200
    assert "saas_enabled" in r.json()
    r2 = auth_client.put("/api/sales/settings/saas_enabled?value=true")
    assert r2.status_code == 200
    assert auth_client.get("/api/sales/me").json()["saas_enabled"] is True


def test_assign_and_audit(auth_client):
    with SessionLocal() as db:
        b = Business(place_id="ext/assign-1", name="Assign Me")
        db.add(b)
        db.flush()
        init_new_prospect(db, b)
        db.commit()
        bid = b.id
    users = auth_client.get("/api/sales/users").json()["users"]
    owner_id = users[0]["id"]
    r = auth_client.post(f"/api/sales/prospects/{bid}/assign", json={"owner_id": owner_id})
    assert r.status_code == 200
    assert r.json()["owner_id"] == owner_id
    audit = auth_client.get("/api/sales/audit")
    assert audit.status_code == 200
    assert any(e["action"] == "prospect_assign" for e in audit.json()["events"])


def test_cron_secret(client, auth_client):
    r = client.post("/api/cron/unpaid-invoices", headers={"X-Cron-Secret": "wrong"})
    assert r.status_code in (401, 403)
    r2 = client.post("/api/cron/unpaid-invoices", headers={"X-Cron-Secret": "cron-test-secret"})
    assert r2.status_code == 200
    assert r2.json().get("done") is True
