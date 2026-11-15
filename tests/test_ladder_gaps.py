"""Gap closure: system-owned dues, park cancels, Ready wait all channels, grace notify, cron."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AppSetting, Business, Task
from app.services.metrics import task_queues


def _lead(**overrides):
    data = dict(
        place_id=f"qa/gap-{time.time_ns()}",
        name="Gap Shop",
        city="Sydney",
        country="Australia",
        email="gap@example.com",
        phone="+61400000222",
        international_phone="+61400000222",
        marked_for_contact=True,
        ready_for_contact=True,
        outreach_channel="email",
        contacted=False,
        email_sent=False,
        product_line="STANDALONE",
        opt_out=False,
        lost=False,
        archived=False,
    )
    data.update(overrides)
    with SessionLocal() as db:
        b = Business(**data)
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


def test_patch_ignores_manual_follow_up_due(auth_client):
    bid = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus3",
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    r = auth_client.patch(
        f"/api/businesses/{bid}",
        json={"next_follow_up_at": "2020-01-01", "follow_up_kind": "plus7", "notes": "x"},
    )
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.follow_up_kind == "plus3"
        assert b.next_follow_up_at is not None
        assert b.next_follow_up_at.date() > datetime.now(timezone.utc).date()


def test_cannot_contact_cancels_ladder(auth_client):
    bid = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus3",
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=2),
    )
    with SessionLocal() as db:
        b = db.get(Business, bid)
        db.add(
            Task(
                business_id=b.id,
                title=f"{b.name} · Follow up after email",
                status="open",
                due_at=b.next_follow_up_at,
                priority="high",
            )
        )
        db.commit()
    r = auth_client.post(
        f"/api/businesses/{bid}/cannot-contact",
        json={"via": "both", "notes": "dead number"},
    )
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.cannot_contact is True
        assert (b.follow_up_kind or "") == ""
        assert b.next_follow_up_at is None
        open_fu = db.scalars(
            select(Task).where(
                Task.business_id == bid,
                Task.status == "open",
                Task.title.ilike("%follow%"),
            )
        ).all()
        assert open_fu == []


def test_already_connected_cancels_ladder(auth_client):
    primary = _lead(name="Primary Loc", place_id=f"qa/gap-prim-{time.time_ns()}")
    bid = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus7",
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=5),
    )
    r = auth_client.post(
        f"/api/sales/prospects/{bid}/already-connected",
        json={"primary_id": primary, "notes": "same IG"},
    )
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.already_connected is True
        assert (b.follow_up_kind or "") == ""
        assert b.next_follow_up_at is None


def test_email_ready_wait_applies_after_promote(auth_client):
    with SessionLocal() as db:
        row = db.scalar(select(AppSetting).where(AppSetting.key == "ig_ready_wait_days"))
        if not row:
            db.add(AppSetting(key="ig_ready_wait_days", value="1"))
        else:
            row.value = "1"
        db.commit()

    bid = _lead(ready_for_contact=False, outreach_channel="email", ig_ready_at=None)
    r = auth_client.post(f"/api/sales/prospects/{bid}/promote-ready")
    assert r.status_code == 200, r.text
    body = r.json()
    wait = body.get("ig_ready_wait") or {}
    assert wait.get("applies") is True
    assert wait.get("unlocked") is False
    assert wait.get("can_message") is False

    blocked = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "too soon"},
    )
    assert blocked.status_code == 422, blocked.text

    with SessionLocal() as db:
        b = db.get(Business, bid)
        # Unlock by backdating stamp
        b.ig_ready_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.commit()

    ok = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "cold"},
    )
    assert ok.status_code == 200, ok.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.follow_up_kind == "plus3"


def test_ladder_grace_counts_in_queues():
    _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="ladder_complete",
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="ladder_complete",
        next_follow_up_at=datetime.now(timezone.utc) - timedelta(hours=1),
        place_id=f"qa/gap-close-{time.time_ns()}",
        name="Closing Soon",
    )
    with SessionLocal() as db:
        q = task_queues(db)
        assert q.get("ladder_grace_count", 0) >= 1
        assert q.get("ladder_closing_count", 0) >= 1


def test_cron_follow_up_get_and_last_promote(auth_client):
    r = auth_client.get(
        "/api/cron/follow-up-reminders?dry_run=true",
        headers={"X-Cron-Secret": "cron-test-secret"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert "last_promote_at" in body
    assert body.get("last_promote_at")
    with SessionLocal() as db:
        row = db.scalar(
            select(AppSetting).where(AppSetting.key == "follow_up_reminders.last_promote_at")
        )
        assert row is not None and (row.value or "").strip()
