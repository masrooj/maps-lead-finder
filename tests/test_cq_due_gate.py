"""Due-gate: Contacted waits until follow-up due date; only then Follow-up +3/+7.

Covers API ladder, pipeline board slug, promote, due-lead metrics, and CQ UI contracts.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.db import SessionLocal
from app.models import Business, PipelineStage
from app.services.automation import (
    follow_up_badge,
    maybe_promote_follow_up,
    promote_due_follow_ups,
)
from app.services.follow_up_reminders import due_follow_up_leads
from app.services.metrics import task_queues
from app.services.pipeline import board_column_slug, infer_slug_from_flags

_REPO = Path(__file__).resolve().parents[1]


def _lead(**overrides):
    data = dict(
        place_id=f"qa/duegate-{time.time_ns()}",
        name="Due Gate Shop",
        city="Sydney",
        country="Australia",
        email="duegate@example.com",
        phone="+61400000222",
        international_phone="+61400000222",
        marked_for_contact=True,
        ready_for_contact=True,
        outreach_channel="email",
        contacted=False,
        email_sent=False,
        product_line="STANDALONE",
    )
    data.update(overrides)
    with SessionLocal() as db:
        b = Business(**data)
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


def test_cold_confirm_stays_contacted_until_due(auth_client):
    """Confirm sent → Contacted + scheduled +3; not Follow-up column / not due list."""
    bid = _lead()
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "cold"},
    )
    assert r.status_code == 200, r.text
    expect = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.contacted is True
        assert b.follow_up_kind == "plus3"
        assert b.next_follow_up_at.date().isoformat() == expect
        assert infer_slug_from_flags(b) == "contacted"
        assert maybe_promote_follow_up(db, b) is False
        badge = follow_up_badge(b)
        assert badge["kind"] == "scheduled"
        assert "due in" in badge["label"].lower()
        stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        assert stage is None or stage.slug == "contacted"
        due_ids = {x.id for x in due_follow_up_leads(db, limit=2000)}
        assert bid not in due_ids


def test_future_due_not_promoted_by_cron_batch():
    bid = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus3",
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=1),
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=2),
    )
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert infer_slug_from_flags(b) == "contacted"
        before = b.pipeline_stage_id
        moved = promote_due_follow_ups(db)
        db.refresh(b)
        assert maybe_promote_follow_up(db, b) is False
        assert b.pipeline_stage_id == before or (
            db.get(PipelineStage, b.pipeline_stage_id)
            and db.get(PipelineStage, b.pipeline_stage_id).slug == "contacted"
        )
        assert isinstance(moved, int)


def test_due_date_passed_moves_to_follow_up_3():
    """When next_follow_up_at is due/past → board + promote → follow_up_3."""
    bid = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus3",
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=4),
        next_follow_up_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert infer_slug_from_flags(b) == "follow_up_3"
        assert follow_up_badge(b)["kind"] in ("overdue", "due_today")
        assert maybe_promote_follow_up(db, b) is True
        db.refresh(b)
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage is not None and stage.slug == "follow_up_3"
        due_ids = {x.id for x in due_follow_up_leads(db, limit=2000)}
        assert bid in due_ids


def test_plus7_future_due_stays_contacted_after_plus3_send(auth_client):
    """After +3 Confirm sent, kind=plus7 with +7 due — still Contacted until that date."""
    bid = _lead()
    assert auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "cold"},
    ).status_code == 200
    assert auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "+3"},
    ).status_code == 200
    expect7 = (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.follow_up_kind == "plus7"
        assert b.next_follow_up_at.date().isoformat() == expect7
        assert infer_slug_from_flags(b) == "contacted"
        assert maybe_promote_follow_up(db, b) is False
        assert follow_up_badge(b)["kind"] == "scheduled"
        due_ids = {x.id for x in due_follow_up_leads(db, limit=2000)}
        assert bid not in due_ids


def test_plus7_due_passed_moves_to_follow_up_7():
    bid = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus7",
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=8),
        next_follow_up_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert infer_slug_from_flags(b) == "follow_up_7"
        assert maybe_promote_follow_up(db, b) is True
        db.refresh(b)
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage is not None and stage.slug == "follow_up_7"


def test_queues_counts_only_due_follow_ups(auth_client):
    """Dashboard follow_up_3/7 counts exclude future-due Contacted leads."""
    waiting = _lead(
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus3",
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=1),
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=2),
    )
    due = _lead(
        place_id=f"qa/duegate-due-{time.time_ns()}",
        contacted=True,
        email_sent=True,
        ready_for_contact=False,
        follow_up_kind="plus3",
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=5),
        next_follow_up_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    with SessionLocal() as db:
        due_ids = {x.id for x in due_follow_up_leads(db, limit=2000)}
        assert waiting not in due_ids
        assert due in due_ids
        q = task_queues(db)
        # Counts are global; at least ensure due lead contributes to +3 path
        assert int(q.get("follow_up_3_count") or 0) >= 1


def test_board_column_matches_infer_for_waiting_and_due():
    with SessionLocal() as db:
        waiting = Business(
            place_id=f"qa/duegate-board-w-{time.time_ns()}",
            name="Wait Board",
            contacted=True,
            email_sent=True,
            follow_up_kind="plus3",
            product_line="STANDALONE",
            last_contacted_at=datetime.now(timezone.utc) - timedelta(days=1),
            next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=3),
        )
        due = Business(
            place_id=f"qa/duegate-board-d-{time.time_ns()}",
            name="Due Board",
            contacted=True,
            email_sent=True,
            follow_up_kind="plus3",
            product_line="STANDALONE",
            last_contacted_at=datetime.now(timezone.utc) - timedelta(days=4),
            next_follow_up_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        db.add_all([waiting, due])
        db.commit()
        avail = {"contacted", "follow_up_3", "follow_up_7", "identified", "to_contact", "ready_to_contact"}
        assert board_column_slug(waiting, None, available_slugs=avail) == "contacted"
        assert board_column_slug(due, None, available_slugs=avail) == "follow_up_3"


def test_sales_confirm_sent_also_stays_contacted_until_due(auth_client):
    bid = _lead()
    r = auth_client.post(
        f"/api/sales/prospects/{bid}/confirm-sent",
        json={"channel": "email", "message": "cold"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("follow_up_kind") == "plus3"
    badge = body.get("follow_up_badge") or {}
    assert badge.get("kind") == "scheduled", badge
    prospect = body.get("prospect") or {}
    stage = prospect.get("stage") or {}
    assert (stage.get("slug") or "contacted") == "contacted"
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert infer_slug_from_flags(b) == "contacted"


def test_cq_ui_due_gate_contracts():
    """Contact queue: Contacted = waiting; +3/+7 tabs = due date passed only."""
    html = (_REPO / "app/templates/index.html").read_text(encoding="utf-8")
    js = (_REPO / "app/static/js/panels/contact-queue.js").read_text(encoding="utf-8")
    assert 'data-cq-tab="plus3"' in html
    assert 'data-cq-tab="plus7"' in html
    assert "contactQueueWaitingContactedLeads" in js
    assert "contactQueuePlus3Leads" in js
    assert "contactQueuePlus7Leads" in js
    assert "due date passes" in js or "Due date passed" in js
    # Contacted tab uses waiting list; +3/+7 tabs use due buckets
    compact = "".join(js.split())
    assert "contactQueueTab==='contacted')base=contactQueueWaitingContactedLeads()" in compact
    assert "contactQueueTab==='plus3')base=contactQueuePlus3Leads()" in compact
    assert "contactQueueTab==='plus7')base=contactQueuePlus7Leads()" in compact
    assert "due&&!duePassed" in compact
    assert "duePassed||(!due&&since>=3)" in compact
    # Dropdown no longer shortcuts into +3/+7 (those are tabs)
    follow_block = html.split('id="contactQueueFollowFilter"')[1].split("</select>")[0]
    assert 'value="plus3"' not in follow_block
    assert 'value="plus7"' not in follow_block
