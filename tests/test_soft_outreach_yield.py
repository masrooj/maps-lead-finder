"""Soft templates, To-contact yield ranking, same-day call nudge."""
from __future__ import annotations

import time

from sqlalchemy import select

from app.db import SessionLocal
from app.main import seed_default_templates
from app.models import Business, MessageTemplate, Task
from app.services.automation import rank_contact_queue


def test_soft_opener_templates_seeded(auth_client):
    with SessionLocal() as db:
        seed_default_templates(db)
        soft = db.scalars(
            select(MessageTemplate).where(MessageTemplate.purpose == "soft_opener")
        ).all()
        assert len(soft) >= 4
        channels = {t.channel for t in soft}
        assert "instagram" in channels
        assert "whatsapp" in channels

    store = auth_client.get("/api/templates")
    assert store.status_code == 200, store.text
    data = store.json()
    ig = data.get("instagram") or []
    wa = data.get("whatsapp") or []
    assert any((t.get("purpose") or "") == "soft_opener" for t in ig)
    assert any((t.get("purpose") or "") == "soft_opener" for t in wa)
    soft_ig = next(t for t in ig if (t.get("purpose") or "") == "soft_opener")
    assert "60-sec" in (soft_ig.get("body") or "") or "bumping" in (soft_ig.get("body") or "").lower() or "booking" in (soft_ig.get("body") or "").lower()


def test_default_queue_ranks_phone_ig_first(auth_client):
    ns = time.time_ns()
    with SessionLocal() as db:
        rows = [
            Business(
                place_id=f"qa/rank-{ns}-rest",
                name="ZZ Rest Only",
                city="Sydney",
                country="Australia",
                marked_for_contact=True,
                website="https://example.com",
            ),
            Business(
                place_id=f"qa/rank-{ns}-phone",
                name="YY Phone Only",
                city="Sydney",
                country="Australia",
                marked_for_contact=True,
                phone="+61400000011",
                international_phone="+61400000011",
            ),
            Business(
                place_id=f"qa/rank-{ns}-ig",
                name="XX IG Only",
                city="Sydney",
                country="Australia",
                marked_for_contact=True,
                social_links_json='{"instagram":"https://instagram.com/xxigonly"}',
            ),
            Business(
                place_id=f"qa/rank-{ns}-both",
                name="AA Phone IG",
                city="Sydney",
                country="Australia",
                marked_for_contact=True,
                phone="+61400000022",
                international_phone="+61400000022",
                social_links_json='{"instagram":"https://instagram.com/aaphoneig"}',
            ),
        ]
        for b in rows:
            db.add(b)
        db.commit()
        ids = {b.name: b.id for b in rows}

    with SessionLocal() as db:
        ranked = rank_contact_queue(db, mode="default", limit=0)
    by_id = {x["id"]: x for x in ranked.get("leads") or []}
    assert ids["AA Phone IG"] in by_id
    assert ids["XX IG Only"] in by_id
    assert ids["YY Phone Only"] in by_id
    both = by_id[ids["AA Phone IG"]]["rank_score"]
    ig = by_id[ids["XX IG Only"]]["rank_score"]
    phone = by_id[ids["YY Phone Only"]]["rank_score"]
    rest = by_id[ids["ZZ Rest Only"]]["rank_score"]
    assert both > ig > phone > rest
    assert "phone + IG" in (both and by_id[ids["AA Phone IG"]].get("why") or "")


def test_same_day_call_task_when_phone_present(auth_client):
    with SessionLocal() as db:
        b = Business(
            place_id=f"qa/call-today-{time.time_ns()}",
            name="Call Today Barber",
            city="Sydney",
            country="Australia",
            phone="+61400000033",
            international_phone="+61400000033",
            marked_for_contact=True,
            ig_warm_follow=True,
            ig_warm_like=True,
            ig_warm_comment=True,
            social_links_json='{"instagram":"https://instagram.com/calltoday"}',
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        bid = b.id

    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "instagram", "message": "soft ask"},
    )
    assert r.status_code == 200, r.text
    assert r.json().get("instagram_sent") is True
    assert r.json().get("next_follow_up_at")

    with SessionLocal() as db:
        task = db.scalars(select(Task).where(Task.business_id == bid, Task.status == "open")).first()
        assert task is not None
        assert "call today" in (task.title or "").lower()
        assert "instagram" in (task.title or "").lower()


def test_explicit_follow_up_skips_same_day_title(auth_client):
    with SessionLocal() as db:
        b = Business(
            place_id=f"qa/call-explicit-{time.time_ns()}",
            name="Explicit Follow Barber",
            city="Sydney",
            country="Australia",
            phone="+61400000044",
            international_phone="+61400000044",
            marked_for_contact=True,
            social_links_json='{"instagram":"https://instagram.com/explicitfu"}',
            ig_warm_follow=True,
            ig_warm_like=True,
            ig_warm_comment=True,
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        bid = b.id

    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "instagram", "next_follow_up_at": "2099-01-15", "message": "later"},
    )
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        task = db.scalars(select(Task).where(Task.business_id == bid, Task.status == "open")).first()
        assert task is not None
        assert "call today" not in (task.title or "").lower()
        assert "follow up" in (task.title or "").lower()
