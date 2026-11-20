"""Contact queue: Discover→To contact keep, Engaged tab + customer_response, sent-channel filter, mobile APIs."""
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
        place_id=f"qa/cq-eng-{time.time_ns()}",
        name="CQ Engaged Shop",
        city="Sydney",
        country="Australia",
        email="cqeng@example.com",
        phone="+61400000999",
        international_phone="+61400000999",
        marked_for_contact=False,
        ready_for_contact=False,
        contacted=False,
        product_line="STANDALONE",
    )
    data.update(overrides)
    with SessionLocal() as db:
        b = Business(**data)
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


def test_mark_for_contact_sales_endpoint_lands_on_to_contact_tab(client):
    h = _bearer(client)
    bid = _lead()

    # Non-default ranking mode would previously drop unmarked→marked leads from CQ.
    r = client.post(
        f"/api/sales/prospects/{bid}/mark-for-contact",
        headers=h,
        json={"marked": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("marked_for_contact") is True
    prospect = body.get("prospect") or {}
    assert prospect.get("marked_for_contact") is True
    stage_slug = (prospect.get("stage") or {}).get("slug")
    assert stage_slug == "to_contact", stage_slug

    # Mode with email filter — marked lead without matching filter must still appear (keep_for_tab).
    q = client.get(
        "/api/sales/contact-queue",
        headers=h,
        params={"mode": "no_website_instagram", "limit": 5000, "tab": "selected"},
    )
    assert q.status_code == 200, q.text
    ids = {int(x["id"]) for x in (q.json().get("leads") or [])}
    assert bid in ids, "marked lead must survive ranking mode filter on To contact tab"


def test_bulk_mark_for_contact(client):
    h = _bearer(client)
    a = _lead(name="Bulk A")
    b = _lead(name="Bulk B")
    r = client.post(
        "/api/sales/prospects/bulk-mark-for-contact",
        headers=h,
        json={"ids": [a, b], "marked": True},
    )
    assert r.status_code == 200, r.text
    assert r.json().get("updated") == 2
    assert all(p.get("marked_for_contact") for p in r.json().get("prospects") or [])


def test_mark_engaged_saves_customer_response_and_tab_filter(client):
    h = _bearer(client)
    bid = _lead(
        marked_for_contact=True,
        contacted=True,
        email_sent=True,
        follow_up_kind="plus3",
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=2),
    )
    r = client.post(
        f"/api/sales/prospects/{bid}/mark-engaged",
        headers=h,
        json={"note": "warm", "customer_response": "Yes interested in a demo next week"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("engaged") is True
    assert "demo" in (body.get("customer_response") or "").lower()
    prospect = body.get("prospect") or {}
    assert prospect.get("engaged") is True
    assert "demo" in (prospect.get("customer_response") or "").lower()
    assert (prospect.get("stage") or {}).get("slug") == "engaged"

    engaged = client.get(
        "/api/sales/contact-queue",
        headers=h,
        params={"tab": "engaged", "limit": 5000},
    )
    assert engaged.status_code == 200, engaged.text
    eids = {int(x["id"]) for x in (engaged.json().get("leads") or [])}
    assert bid in eids

    contacted = client.get(
        "/api/sales/contact-queue",
        headers=h,
        params={"tab": "contacted", "limit": 5000},
    )
    assert contacted.status_code == 200
    cids = {int(x["id"]) for x in (contacted.json().get("leads") or [])}
    assert bid not in cids


def test_contacted_sent_channels_filter(client):
    h = _bearer(client)
    wa = _lead(
        marked_for_contact=True,
        contacted=True,
        whatsapp_sent=True,
        email_sent=False,
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=2),
    )
    em = _lead(
        marked_for_contact=True,
        contacted=True,
        email_sent=True,
        whatsapp_sent=False,
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=2),
    )
    both = _lead(
        marked_for_contact=True,
        contacted=True,
        whatsapp_sent=True,
        email_sent=True,
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=2),
    )

    only_wa = client.get(
        "/api/sales/contact-queue",
        headers=h,
        params={"tab": "contacted", "sent_channels": "whatsapp", "limit": 5000},
    )
    assert only_wa.status_code == 200, only_wa.text
    ids = {int(x["id"]) for x in (only_wa.json().get("leads") or [])}
    assert wa in ids
    assert both in ids
    assert em not in ids

    multi = client.get(
        "/api/sales/contact-queue",
        headers=h,
        params={"tab": "contacted", "sent_channels": "wa,email", "limit": 5000},
    )
    assert multi.status_code == 200
    mids = {int(x["id"]) for x in (multi.json().get("leads") or [])}
    assert wa in mids and em in mids and both in mids


def test_cq_ui_has_engaged_tab_and_sent_channel_checkboxes(client):
    h = _bearer(client)
    # HTML shell is cookie/session; use cookie login for page
    client.post(
        "/api/login",
        json={"username": LOGIN_USERNAME, "password": "test-password-sales-os"},
    )
    html = client.get("/").text
    assert 'data-cq-tab="engaged"' in html
    assert 'id="contactQueueEngagedCount"' in html
    assert 'id="contactQueueSentChannelField"' in html
    assert 'data-cq-sent-ch="whatsapp"' in html
    assert 'data-cq-sent-ch="instagram"' in html
    assert 'data-cq-sent-ch="email"' in html

    js = client.get("/static/js/panels/contact-queue.js").text
    assert "contactQueueEngagedLeads" in js
    assert "contactQueueSentChannelMatch" in js
    assert "customer_response" in js
    assert "bulkMarkForContact" in js or "bulk-mark-for-contact" in js
