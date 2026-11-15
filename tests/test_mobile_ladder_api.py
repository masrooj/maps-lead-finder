"""Mobile /api/sales/* ladder contract: confirm-sent, queues, parks, ready-status."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from app.config import LOGIN_USERNAME
from app.db import SessionLocal
from app.models import Business


def _bearer(client):
    client.post("/api/logout")
    client.cookies.clear()
    r = client.post(
        "/api/login",
        json={"username": LOGIN_USERNAME, "password": "test-password-sales-os"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    client.cookies.clear()
    return {"Authorization": f"Bearer {token}"}


def _lead(**overrides):
    data = dict(
        place_id=f"qa/mobile-ladder-{time.time_ns()}",
        name="Mobile Ladder Shop",
        city="Sydney",
        country="Australia",
        email="mobileladder@example.com",
        phone="+61400000333",
        international_phone="+61400000333",
        marked_for_contact=True,
        ready_for_contact=True,
        outreach_channel="email",
        contacted=False,
        email_sent=False,
        product_line="STANDALONE",
        ig_ready_at=None,  # grandfather unlocked
    )
    data.update(overrides)
    with SessionLocal() as db:
        b = Business(**data)
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


def test_sales_confirm_sent_returns_ladder_fields(client):
    h = _bearer(client)
    bid = _lead()
    r = client.post(
        f"/api/sales/prospects/{bid}/confirm-sent",
        headers=h,
        json={"channel": "email", "message": "cold", "next_follow_up_at": "2020-01-01"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("follow_up_kind") == "plus3"
    assert body.get("ladder_complete") is False
    assert body.get("next_follow_up_at")
    assert isinstance(body.get("follow_up_badge"), dict)
    prospect = body.get("prospect") or {}
    assert prospect.get("follow_up_kind") == "plus3"
    assert "follow_up_badge" in prospect
    assert "next_follow_up_at" in prospect
    assert "can_message" in prospect
    assert "ig_ready_wait" in prospect

    r2 = client.post(
        f"/api/sales/prospects/{bid}/confirm-sent",
        headers=h,
        json={"channel": "email", "message": "+3"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json().get("follow_up_kind") == "plus7"

    r3 = client.post(
        f"/api/sales/prospects/{bid}/confirm-sent",
        headers=h,
        json={"channel": "email", "message": "+7"},
    )
    assert r3.status_code == 200, r3.text
    assert r3.json().get("follow_up_kind") == "ladder_complete"
    assert r3.json().get("ladder_complete") is True


def test_sales_mark_engaged_clears_ladder_in_prospect(client):
    h = _bearer(client)
    bid = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus3",
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=2),
    )
    r = client.post(
        f"/api/sales/prospects/{bid}/mark-engaged",
        headers=h,
        json={"note": "replied"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("follow_up_kind") == ""
    prospect = body.get("prospect") or {}
    assert prospect.get("follow_up_kind") == ""
    assert not prospect.get("next_follow_up_at")
    assert (prospect.get("stage") or {}).get("slug") == "engaged"


def test_sales_cannot_contact_alias(client):
    h = _bearer(client)
    bid = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus7",
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=4),
    )
    r = client.post(
        f"/api/sales/prospects/{bid}/cannot-contact",
        headers=h,
        json={"via": "whatsapp", "notes": "dead"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("cannot_contact") is True
    assert body.get("follow_up_kind") == ""
    prospect = body.get("prospect") or {}
    assert prospect.get("cannot_contact") is True
    assert not prospect.get("next_follow_up_at")

    cleared = client.post(
        f"/api/sales/prospects/{bid}/clear-cannot-contact",
        headers=h,
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json().get("cannot_contact") is False


def test_dashboard_queues_ladder_keys(client):
    h = _bearer(client)
    r = client.get("/api/sales/dashboard/queues", headers=h)
    assert r.status_code == 200, r.text
    q = (r.json().get("queues") or {})
    for key in (
        "ready_unlocked_count",
        "follow_up_due_count",
        "follow_up_3_count",
        "follow_up_7_count",
        "ladder_grace_count",
        "ladder_closing_count",
    ):
        assert key in q, f"missing queue key {key}"


def test_ready_status_includes_ladder_and_wait(client):
    h = _bearer(client)
    bid = _lead(ready_for_contact=True, ig_ready_at=None)
    r = client.get(f"/api/sales/prospects/{bid}/ready-status", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "ig_ready_wait" in body
    assert "follow_up_kind" in body
    assert "follow_up_badge" in body
    assert "can_confirm_sent" in body
    assert "ladder_complete" in body
