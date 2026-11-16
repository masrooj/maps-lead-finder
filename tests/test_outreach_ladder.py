"""System-owned outreach ladder: cold → +3 → +7 → ladder_complete → Lost; Engaged cancels."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import Business, PipelineStage, Task
from sqlalchemy import select

from app.services.automation import (
    backfill_due_contacted_to_plus3,
    close_exhausted_ladders,
    maybe_promote_follow_up,
    promote_due_follow_ups,
)
from app.services.metrics import task_queues


def _lead(**overrides):
    data = dict(
        place_id=f"qa/ladder-{time.time_ns()}",
        name="Ladder Shop",
        city="Sydney",
        country="Australia",
        email="ladder@example.com",
        phone="+61400000111",
        international_phone="+61400000111",
        marked_for_contact=True,
        ready_for_contact=True,
        outreach_channel="email",
        contacted=False,
        email_sent=False,
        whatsapp_sent=False,
        instagram_sent=False,
        product_line="STANDALONE",
    )
    data.update(overrides)
    with SessionLocal() as db:
        b = Business(**data)
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


def test_cold_confirm_always_plus3_even_with_phone_wa(auth_client):
    bid = _lead(outreach_channel="whatsapp", ready_for_contact=True)
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "whatsapp", "message": "cold", "next_follow_up_at": "2020-01-01"},
    )
    assert r.status_code == 200, r.text
    expect = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.follow_up_kind == "plus3"
        assert b.next_follow_up_at.date().isoformat() == expect
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage is None or stage.slug == "contacted"


def test_ladder_cold_plus3_then_plus7_then_complete(auth_client):
    bid = _lead(ready_for_contact=True, outreach_channel="email")
    r1 = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "cold"},
    )
    assert r1.status_code == 200, r1.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.follow_up_kind == "plus3"

    r2 = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "+3 follow"},
    )
    assert r2.status_code == 200, r2.text
    expect7 = (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.follow_up_kind == "plus7"
        assert b.next_follow_up_at.date().isoformat() == expect7

    r3 = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "+7 follow"},
    )
    assert r3.status_code == 200, r3.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.follow_up_kind == "ladder_complete"
        assert b.next_follow_up_at.date().isoformat() == expect7
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage is not None and stage.slug == "follow_up_7"


def test_ladder_complete_grace_closes_to_lost():
    bid = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="ladder_complete",
        next_follow_up_at=datetime.now(timezone.utc) - timedelta(days=1),
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=8),
    )
    with SessionLocal() as db:
        b = db.get(Business, bid)
        db.add(
            Task(
                business_id=b.id,
                title=f"{b.name} · Reply window after +7",
                status="open",
                due_at=b.next_follow_up_at,
                priority="high",
            )
        )
        db.commit()
        closed = close_exhausted_ladders(db, commit=True)
        assert closed >= 1
        db.refresh(b)
        assert b.lost is True
        assert (b.follow_up_kind or "") == ""
        assert b.next_follow_up_at is None
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage is not None and stage.slug == "lost"
        tasks = db.scalars(select(Task).where(Task.business_id == bid, Task.status == "open")).all()
        assert not any(
            "follow" in (t.title or "").lower() or "reply" in (t.title or "").lower() for t in tasks
        )


def test_promote_due_also_closes_exhausted():
    bid = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="ladder_complete",
        next_follow_up_at=datetime.now(timezone.utc) - timedelta(hours=1),
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=8),
    )
    with SessionLocal() as db:
        n = promote_due_follow_ups(db)
        assert n >= 1
        b = db.get(Business, bid)
        assert b.lost is True


def test_mark_engaged_cancels_follow_ups(auth_client):
    bid = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus3",
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=2),
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=1),
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

    r = auth_client.post(f"/api/sales/prospects/{bid}/mark-engaged", json={"note": "replied"})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert (b.follow_up_kind or "") == ""
        assert b.next_follow_up_at is None
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage is not None and stage.slug == "engaged"
        open_fu = db.scalars(
            select(Task).where(
                Task.business_id == bid,
                Task.status == "open",
                Task.title.ilike("%follow%"),
            )
        ).all()
        assert open_fu == []


def test_opt_out_cancels_follow_ups(auth_client):
    bid = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus7",
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=3),
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    r = auth_client.patch(f"/api/sales/prospects/{bid}", json={"opt_out": True})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.opt_out is True
        assert (b.follow_up_kind or "") == ""
        assert b.next_follow_up_at is None


def test_ready_unlocked_count_in_queues(auth_client):
    bid = _lead(
        ready_for_contact=True,
        contacted=False,
        outreach_channel="email",
        opt_out=False,
        lost=False,
        archived=False,
    )
    with SessionLocal() as db:
        q = task_queues(db)
        assert "ready_unlocked_count" in q
        assert q["ready_unlocked_count"] >= 1
        # touch lead so it exists
        assert db.get(Business, bid) is not None


def test_due_plus3_promotes_not_ladder_complete():
    bid = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus3",
        next_follow_up_at=datetime.now(timezone.utc) - timedelta(days=1),
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=4),
    )
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert maybe_promote_follow_up(db, b) is True
        db.refresh(b)
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage is not None and stage.slug == "follow_up_3"
        assert b.follow_up_kind == "plus3"


def test_backfill_due_contacted_to_plus3(auth_client):
    due = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="",
        next_follow_up_at=datetime.now(timezone.utc) - timedelta(days=2),
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=5),
    )
    call = _lead(
        place_id=f"qa/ladder-call-{time.time_ns()}",
        name="Call Today Legacy",
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="call_today",
        next_follow_up_at=datetime.now(timezone.utc) - timedelta(hours=1),
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    future = _lead(
        place_id=f"qa/ladder-fut-{time.time_ns()}",
        name="Not Due Yet",
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus3",
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=2),
        last_contacted_at=datetime.now(timezone.utc),
    )
    plus7 = _lead(
        place_id=f"qa/ladder-p7-{time.time_ns()}",
        name="Already Plus7",
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus7",
        next_follow_up_at=datetime.now(timezone.utc) - timedelta(days=1),
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=8),
    )
    r = auth_client.post("/api/sales/prospects/backfill-due-plus3")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("kind_updated", 0) >= 2
    assert body.get("promoted_follow_up_3", 0) >= 2
    with SessionLocal() as db:
        b_due = db.get(Business, due)
        b_call = db.get(Business, call)
        b_fut = db.get(Business, future)
        b_p7 = db.get(Business, plus7)
        assert b_due.follow_up_kind == "plus3"
        assert b_call.follow_up_kind == "plus3"
        assert (db.get(PipelineStage, b_due.pipeline_stage_id).slug == "follow_up_3")
        assert (db.get(PipelineStage, b_call.pipeline_stage_id).slug == "follow_up_3")
        assert b_fut.follow_up_kind == "plus3"
        assert b_p7.follow_up_kind == "plus7"
        fut_stage = db.get(PipelineStage, b_fut.pipeline_stage_id) if b_fut.pipeline_stage_id else None
        assert fut_stage is None or fut_stage.slug in ("contacted", "identified", "to_contact", "ready_to_contact")
        assert maybe_promote_follow_up(db, b_fut) is False
        if b_p7.pipeline_stage_id:
            p7_stage = db.get(PipelineStage, b_p7.pipeline_stage_id)
            assert p7_stage is None or p7_stage.slug != "follow_up_3"
