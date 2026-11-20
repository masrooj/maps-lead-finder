"""Lined up tab: promoted but not sendable; Ready = unlocked only."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
        place_id=f"qa/lined-{time.time_ns()}",
        name="Lined Up Shop",
        city="Sydney",
        country="Australia",
        email="lined@example.com",
        phone="+61400000111",
        international_phone="+61400000111",
        marked_for_contact=True,
        ready_for_contact=True,
        outreach_channel="email",
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


def test_lined_up_vs_ready_tab_split(client):
    h = _bearer(client)
    waiting = _lead(
        name="Still Warming",
        ig_ready_at=datetime.now(timezone.utc),  # wait applies → not unlocked yet (default 1 day)
    )
    unlocked = _lead(
        name="Sendable Now",
        ig_ready_at=datetime.now(timezone.utc) - timedelta(days=3),
    )
    # Grandfather (no stamp) is unlocked
    grandpa = _lead(name="Grandfather Ready", ig_ready_at=None)

    lined = client.get(
        "/api/sales/contact-queue",
        headers=h,
        params={"tab": "lined_up", "limit": 5000},
    )
    assert lined.status_code == 200, lined.text
    lined_ids = {int(x["id"]) for x in (lined.json().get("leads") or [])}
    assert waiting in lined_ids
    assert unlocked not in lined_ids
    assert grandpa not in lined_ids

    ready = client.get(
        "/api/sales/contact-queue",
        headers=h,
        params={"tab": "ready", "limit": 5000},
    )
    assert ready.status_code == 200, ready.text
    ready_ids = {int(x["id"]) for x in (ready.json().get("leads") or [])}
    assert unlocked in ready_ids
    assert grandpa in ready_ids
    assert waiting not in ready_ids


def test_follow_back_hold_is_lined_up(client):
    h = _bearer(client)
    bid = _lead(
        name="Follow Back Hold",
        ig_ready_at=datetime.now(timezone.utc) - timedelta(days=5),
        ig_awaiting_follow_back=True,
    )
    lined = client.get(
        "/api/sales/contact-queue",
        headers=h,
        params={"tab": "lined_up", "limit": 5000},
    )
    assert lined.status_code == 200
    assert bid in {int(x["id"]) for x in (lined.json().get("leads") or [])}
    ready = client.get(
        "/api/sales/contact-queue",
        headers=h,
        params={"tab": "ready", "limit": 5000},
    )
    assert bid not in {int(x["id"]) for x in (ready.json().get("leads") or [])}


def test_ui_has_lined_up_tab():
    root = Path(__file__).resolve().parents[1]
    html = (root / "app/templates/index.html").read_text(encoding="utf-8")
    js = (root / "app/static/js/panels/contact-queue.js").read_text(encoding="utf-8")
    assert 'data-cq-tab="lined_up"' in html
    assert 'id="contactQueueLinedUpCount"' in html
    assert 'id="contactQueueLinedUpBar"' in html
    assert "contactQueueLinedUpLeads" in js
    assert "leadIsLinedUpWaiting" in js


def test_promote_ready_returns_queue_tab(client):
    h = _bearer(client)
    bid = _lead(
        ready_for_contact=False,
        marked_for_contact=True,
        outreach_channel="email",
        email="promo@example.com",
        ig_ready_at=None,
    )
    # Promote with wait stamp — new promote always stamps, so lined_up when wait_days>0
    with SessionLocal() as db:
        b = db.get(Business, bid)
        b.ig_warm_follow = True  # not needed for email
        db.commit()
    r = client.post(f"/api/sales/prospects/{bid}/promote-ready", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ready_for_contact") is True
    assert "lined_up" in body
    assert body.get("queue_tab") in ("lined_up", "ready")
    prospect = body.get("prospect") or {}
    assert "queue_tab" in prospect
    assert "lined_up" in prospect
