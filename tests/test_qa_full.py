"""
QA engineer suite — remaining plan coverage.
Synthetic data only.
"""
from __future__ import annotations

import io

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, MessageTemplate, Proposal, User
from app.services.pipeline import init_new_prospect, list_stages, move_stage
from app.services.billing.service import (
    create_invoice_from_proposal,
    create_proposal,
    mark_invoice_paid,
    record_refund,
)
from app.services.metrics import collected_revenue
from app.services.authz import can_view_business
from app.models import Task


def make_business(place_id: str, name: str, **kwargs) -> int:
    with SessionLocal() as db:
        b = Business(place_id=place_id, name=name, **kwargs)
        db.add(b)
        db.flush()
        init_new_prospect(db, b)
        db.commit()
        return b.id


def test_qa_health_and_home(client):
    assert client.get("/api/health").json()["ok"] is True
    r = client.get("/")
    assert r.status_code == 200
    assert b"AppointHive" in r.content or b"Dashboard" in r.content


def test_qa_unauthenticated_sales_idor_surface(client):
    # Shared session client may still hold a login cookie from earlier tests
    client.post("/api/logout")
    client.cookies.clear()
    assert client.get("/api/sales/prospects").status_code == 401
    assert client.get("/api/sales/reports/summary").status_code == 401
    assert client.post("/api/sales/payments/1/refund", json={}).status_code == 401


def test_qa_prospects_list_and_search(auth_client):
    make_business("qa/p1", "QA Salon Melbourne", city="Melbourne", country="Australia")
    r = auth_client.get("/api/sales/prospects?q=Melbourne")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()["prospects"]]
    assert "QA Salon Melbourne" in names
    assert r.json()["sample_size"] >= 1


def test_qa_proposal_accept_terms_and_refund_adjusts_collected(auth_client):
    bid = make_business("qa/pay1", "Refund Shop")
    with SessionLocal() as db:
        b = db.get(Business, bid)
        prop = create_proposal(
            db, b, title="Deal", currency="AUD", line_items=[{"name": "Starter", "qty": 1, "amount": 1000}]
        )
        pid = prop.id
    r = auth_client.post(f"/api/sales/proposals/{pid}/accept", json={"terms_accepted": False})
    assert r.status_code == 422
    r = auth_client.post(f"/api/sales/proposals/{pid}/accept", json={"terms_accepted": True})
    assert r.status_code == 200
    assert r.json()["status"] == "accepted"
    with SessionLocal() as db:
        prop = db.get(Proposal, pid)
        inv = create_invoice_from_proposal(db, prop, provider="manual")
        pay = mark_invoice_paid(db, inv, provider_ref="qa-pay")
        before = collected_revenue(db)["by_currency"].get("AUD", 0)
        refund = record_refund(db, pay.id, amount=200, reason="partial goodwill")
        assert refund.status == "partial_refund"
        after = collected_revenue(db)["by_currency"].get("AUD", 0)
        assert after == before - 200


def test_qa_sales_visibility_rules(auth_client):
    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.email == "sales@appointhive.com")):
            db.add(User(email="sales@appointhive.com", name="Sales", role="sales", active=True))
            db.commit()
        owner = db.scalar(select(User).where(User.role == "owner"))
        sales = db.scalar(select(User).where(User.email == "sales@appointhive.com"))
        other = Business(place_id="qa/other-owned", name="Owned by owner", owner_id=owner.id)
        db.add(other)
        db.flush()
        init_new_prospect(db, other)
        db.commit()
        assert can_view_business(sales, other) is False
        assert can_view_business(owner, other) is True


def test_qa_stage_tasks_survive_and_reports(auth_client):
    bid = make_business("qa/stage1", "Stage Shop")
    with SessionLocal() as db:
        stages = {s.slug: s for s in list_stages(db, "STANDALONE")}
        b = db.get(Business, bid)
        db.add(Task(business_id=bid, title="Keep me", status="open"))
        db.commit()
        move_stage(db, b, stages["engaged"].id, force=True)
        assert db.scalars(select(Task).where(Task.business_id == bid, Task.title == "Keep me")).first()
    r = auth_client.get("/api/sales/reports/summary?product_line=STANDALONE")
    assert r.status_code == 200
    body = r.json()
    assert "win_rate" in body and "sample_size" in body["win_rate"]
    assert "collected" in body


def test_qa_demos_and_activities_and_discovery(auth_client):
    bid = make_business("qa/demo1", "Demo Shop")
    assert auth_client.post(f"/api/sales/prospects/{bid}/discovery", json={"painful_issue": "No-shows"}).status_code == 200
    assert (
        auth_client.post(
            f"/api/sales/prospects/{bid}/demo",
            json={"outcome": "interested", "agreed_next_step": "Send proposal"},
        ).status_code
        == 200
    )
    assert auth_client.get("/api/sales/demos").json()["demos"]
    assert (
        auth_client.post(
            "/api/sales/activities",
            json={"business_id": bid, "activity_type": "note", "summary": "QA note"},
        ).status_code
        == 200
    )
    acts = auth_client.get("/api/sales/activities").json()["activities"]
    assert any(a["summary"] == "QA note" for a in acts)


def test_qa_governance_blocks_preview(auth_client):
    bid = make_business("qa/gov1", "Gov Shop")
    with SessionLocal() as db:
        t = MessageTemplate(
            channel="email",
            name="Bad claims",
            subject="Hi",
            body="We have 1000 customers",
            governance_ok=False,
        )
        db.add(t)
        db.commit()
        tid = t.id
    r = auth_client.post(
        "/api/sales/templates/preview",
        json={"business_id": bid, "template_id": tid, "channel": "email"},
    )
    assert r.status_code == 403


def test_qa_csv_import_dedupe_hints_no_merge(auth_client):
    make_business(
        "qa/exist-web",
        "Existing Co",
        website="https://existingco.example",
        city="Sydney",
        country="Australia",
    )
    csv_body = (
        "Name,City,Country,Website\n"
        "Existing Co,Sydney,Australia,https://existingco.example\n"
        "Brand New,Perth,Australia,https://brandnew.example\n"
    )
    files = {"file": ("leads.csv", io.BytesIO(csv_body.encode()), "text/csv")}
    r = auth_client.post("/api/import.csv", files=files)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "duplicate_hints" in data
    assert data["duplicate_hint_count"] >= 1
    assert data.get("note")


def test_qa_demo_data_wipe(auth_client):
    with SessionLocal() as db:
        b = Business(place_id="qa/demo-data", name="Demo Pack Lead", is_demo_data=True)
        db.add(b)
        db.flush()
        init_new_prospect(db, b)
        db.commit()
    r = auth_client.post("/api/sales/demo-data/wipe")
    assert r.status_code == 200
    assert r.json()["deleted"] >= 1
    with SessionLocal() as db:
        assert db.scalar(select(Business).where(Business.place_id == "qa/demo-data")) is None


def test_qa_persistence_across_sessions(auth_client):
    bid = make_business("qa/persist", "Persist Shop")
    with SessionLocal() as db:
        b = db.scalar(select(Business).where(Business.id == bid))
        assert b is not None
        assert b.name == "Persist Shop"


def test_qa_saas_mrr_fields_and_pipeline(auth_client):
    auth_client.put("/api/sales/settings/saas_enabled?value=true")
    bid = make_business("qa/saas1", "SaaS Lead")
    r = auth_client.patch(
        f"/api/sales/prospects/{bid}",
        json={"product_line": "SAAS", "mrr": 99, "saas_plan_code": "saas_growth"},
    )
    assert r.status_code == 200
    pipe = auth_client.get("/api/sales/pipeline?product_line=SAAS")
    assert pipe.status_code == 200
    assert pipe.json()["product_line"] == "SAAS"


def test_qa_search_endpoint(auth_client):
    make_business("qa/search1", "UniqueZebraCuts", city="Brisbane")
    r = auth_client.get("/api/sales/search?q=Zebra")
    assert r.status_code == 200
    assert any("Zebra" in p["name"] for p in r.json()["prospects"])
