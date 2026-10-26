"""AppointHive Sales OS core tests (synthetic data only)."""
from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, Task
from app.services.pipeline import init_new_prospect, list_stages, move_stage
from app.services.scoring import band_for_score, compute_score
from app.services.billing.service import create_invoice_from_proposal, create_proposal, mark_invoice_paid
from app.services.metrics import collected_revenue


def test_health(client):
    assert client.get("/api/health").json()["ok"] is True


def test_auth_required(client):
    client.post("/api/logout")
    r = client.get("/api/sales/dashboard")
    assert r.status_code == 401


def test_score_calculation():
    total, cleaned = compute_score(
        {
            "business_fit": 2,
            "operational_pain": 2,
            "buying_urgency": 1,
            "decision_maker_access": 2,
            "product_fit": 1,
        }
    )
    assert total == 8
    assert band_for_score(total) == "Priority"
    assert cleaned["business_fit"] == 2


def test_stage_move_and_history_and_tasks_survive(auth_client):
    with SessionLocal() as db:
        stages = list_stages(db, "STANDALONE")
        assert len(stages) >= 10
        identified = next(s for s in stages if s.slug == "identified")
        contacted = next(s for s in stages if s.slug == "contacted")
        lost = next(s for s in stages if s.slug == "lost")
        b = Business(place_id="test/place-1", name="Test Salon AU")
        db.add(b)
        db.flush()
        init_new_prospect(db, b)
        b.pipeline_stage_id = identified.id
        b.country = "Australia"
        b.city = "Sydney"
        db.add(Task(business_id=b.id, title="Call owner", status="open"))
        db.commit()
        bid = b.id
        move_stage(db, b, contacted.id, note="Reached out", force=True)
        tasks = db.scalars(select(Task).where(Task.business_id == bid, Task.status == "open")).all()
        assert len(tasks) == 1
        try:
            move_stage(db, b, lost.id, note="No fit")
            assert False, "expected lost_reason required"
        except Exception as e:
            assert "lost_reason" in str(e).lower() or "422" in str(e)
        move_stage(db, b, lost.id, note="No fit", lost_reason="No budget", force=False)
        db.refresh(b)
        assert b.lost is True
        assert b.lost_reason == "No budget"
        assert db.scalars(select(Task).where(Task.business_id == bid)).all()


def test_proposal_invoice_payment_collected_not_proposal(auth_client):
    with SessionLocal() as db:
        b = Business(place_id="test/place-2", name="Pay Test Barbers")
        db.add(b)
        db.flush()
        init_new_prospect(db, b)
        db.commit()
        prop = create_proposal(
            db,
            b,
            title="Starter",
            currency="AUD",
            line_items=[{"name": "Standalone Starter", "qty": 1, "amount": 1500}],
            tax_rate=0.1,
            tax_label="GST",
        )
        assert prop.total == 1650.0
        inv = create_invoice_from_proposal(db, prop, provider="manual")
        assert inv.status == "open"
        before = collected_revenue(db, include_test=False)
        pay = mark_invoice_paid(db, inv, provider_ref="bank-1")
        assert pay.status == "succeeded"
        assert pay.is_test_mode is False
        after = collected_revenue(db, include_test=False)
        assert after["by_currency"].get("AUD", 0) >= before["by_currency"].get("AUD", 0) + 1650.0 - 0.01


def test_sales_api_dashboard_and_pipeline(auth_client):
    r = auth_client.get("/api/sales/dashboard")
    assert r.status_code == 200
    body = r.json()
    assert "queues" in body
    assert "collected" in body
    r2 = auth_client.get("/api/sales/pipeline?product_line=STANDALONE")
    assert r2.status_code == 200
    assert "columns" in r2.json()
    r3 = auth_client.get("/api/sales/plans")
    assert r3.status_code == 200
    assert len(r3.json()["plans"]) == 3
    r4 = auth_client.get("/api/sales/providers")
    assert r4.status_code == 200
    assert "stripe" in r4.json()


def test_opt_out_field_and_score_api(auth_client):
    with SessionLocal() as db:
        b = Business(place_id="test/place-3", name="Score Me")
        db.add(b)
        db.flush()
        init_new_prospect(db, b)
        db.commit()
        bid = b.id
    r = auth_client.post(
        f"/api/sales/prospects/{bid}/score",
        json={
            "business_fit": 2,
            "operational_pain": 1,
            "buying_urgency": 1,
            "decision_maker_access": 1,
            "product_fit": 1,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["computed_score"] == 6
    assert r.json()["band"] == "Investigate"
    r2 = auth_client.patch(f"/api/sales/prospects/{bid}", json={"opt_out": True, "next_action": "Do not contact"})
    assert r2.status_code == 200
    assert r2.json()["opt_out"] is True
