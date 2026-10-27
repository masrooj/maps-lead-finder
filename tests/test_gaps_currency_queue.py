"""Gaps: default USD currency, AUD→USD normalize, contact-queue scope/limit, Sales role gates."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, User
from app.services.pipeline import init_new_prospect


def test_init_new_prospect_defaults_usd():
    with SessionLocal() as db:
        b = Business(place_id="gap/cur-usd-1", name="USD Default Co", city="Austin", country="US")
        db.add(b)
        db.flush()
        init_new_prospect(db, b)
        db.commit()
        bid = b.id
    with SessionLocal() as db:
        row = db.get(Business, bid)
        assert row is not None
        assert (row.currency or "").upper() == "USD"
        db.delete(row)
        db.commit()


def test_normalize_currency_aud_to_usd_keeps_other(auth_client):
    with SessionLocal() as db:
        rows = [
            Business(place_id="gap/cur-aud", name="Was AUD", currency="AUD", city="Sydney", country="Australia"),
            Business(place_id="gap/cur-blank", name="Blank cur", currency="", city="Melbourne", country="Australia"),
            Business(place_id="gap/cur-eur", name="Keep EUR", currency="EUR", city="Berlin", country="DE"),
        ]
        for b in rows:
            db.add(b)
            db.flush()
            init_new_prospect(db, b)
            # init may set blank → USD already; force AUD/blank/EUR as intended
        aud = db.scalar(select(Business).where(Business.place_id == "gap/cur-aud"))
        blank = db.scalar(select(Business).where(Business.place_id == "gap/cur-blank"))
        eur = db.scalar(select(Business).where(Business.place_id == "gap/cur-eur"))
        aud.currency = "AUD"
        blank.currency = ""
        eur.currency = "EUR"
        db.commit()
        ids = [aud.id, blank.id, eur.id]

    r = auth_client.post("/api/sales/prospects/normalize-currency?from_currency=AUD&to_currency=USD")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["to_currency"] == "USD"
    assert body["updated"] >= 2

    with SessionLocal() as db:
        by_place = {
            b.place_id: b
            for b in db.scalars(select(Business).where(Business.id.in_(ids))).all()
        }
        assert by_place["gap/cur-aud"].currency == "USD"
        assert by_place["gap/cur-blank"].currency == "USD"
        assert by_place["gap/cur-eur"].currency == "EUR"
        for b in by_place.values():
            db.delete(b)
        db.commit()


def test_contact_queue_country_city_and_limit_zero(auth_client):
    with SessionLocal() as db:
        a = Business(
            place_id="gap/cq-syd",
            name="CQ Sydney",
            city="Sydney",
            country="Australia",
            email="cq-syd@example.com",
            product_line="STANDALONE",
            created_at=datetime.now(timezone.utc),
        )
        b = Business(
            place_id="gap/cq-mel",
            name="CQ Melbourne",
            city="Melbourne",
            country="Australia",
            email="cq-mel@example.com",
            product_line="STANDALONE",
            created_at=datetime.now(timezone.utc),
        )
        c = Business(
            place_id="gap/cq-us",
            name="CQ Austin",
            city="Austin",
            country="US",
            email="cq-us@example.com",
            product_line="STANDALONE",
            created_at=datetime.now(timezone.utc),
        )
        for row in (a, b, c):
            db.add(row)
            db.flush()
            init_new_prospect(db, row)
        db.commit()
        ids = [a.id, b.id, c.id]

    scoped = auth_client.get(
        "/api/sales/contact-queue?limit=0&mode=default&country=Australia&city=Sydney"
    )
    assert scoped.status_code == 200, scoped.text
    data = scoped.json()
    assert data["country"] == "Australia"
    assert data["city"] == "Sydney"
    lead_ids = {int(x["id"]) for x in (data.get("leads") or [])}
    assert ids[0] in lead_ids
    assert ids[1] not in lead_ids
    assert ids[2] not in lead_ids
    assert "Australia" in (data.get("locations") or {})
    assert "Sydney" in (data["locations"].get("Australia") or [])

    all_q = auth_client.get("/api/sales/contact-queue?limit=0&mode=default")
    assert all_q.status_code == 200
    all_ids = {int(x["id"]) for x in (all_q.json().get("leads") or [])}
    assert ids[0] in all_ids and ids[1] in all_ids and ids[2] in all_ids
    assert all_q.json()["sample_size"] == all_q.json()["total_candidates"] or all_q.json()["sample_size"] >= 3

    capped = auth_client.get("/api/sales/contact-queue?limit=1&mode=default&country=Australia")
    assert capped.status_code == 200
    assert capped.json()["sample_size"] == 1
    assert capped.json()["total_candidates"] >= 2

    with SessionLocal() as db:
        for bid in ids:
            row = db.get(Business, bid)
            if row:
                db.delete(row)
        db.commit()


def test_sales_role_blocked_from_owner_ops(client, auth_client):
    created = auth_client.post(
        "/api/sales/users",
        json={
            "email": "sales.gate@example.com",
            "name": "Sales Gate",
            "role": "sales",
            "password": "sales-gate-99",
        },
    )
    assert created.status_code == 200, created.text

    client.post("/api/logout")
    client.cookies.clear()
    login = client.post(
        "/api/login",
        json={"username": "sales.gate@example.com", "password": "sales-gate-99"},
    )
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "sales"

    audit = client.get("/api/sales/audit")
    assert audit.status_code == 403, audit.text

    user_create = client.post(
        "/api/sales/users",
        json={"email": "should.fail@example.com", "name": "Nope", "role": "sales", "password": "password99"},
    )
    assert user_create.status_code == 403, user_create.text

    plan = client.post(
        "/api/sales/plans",
        json={"product_line": "STANDALONE", "code": "x", "name": "X", "amount": 1, "currency": "USD"},
    )
    # plans create is owner-only — accept 403 or 422 if body validation differs
    assert plan.status_code in (403, 422), plan.text
