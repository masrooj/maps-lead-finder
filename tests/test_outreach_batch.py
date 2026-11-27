"""v2+ outreach_batch job: IG warm vs WA Ready."""
from __future__ import annotations

import json

from app.db import SessionLocal
from app.models import Business
from app.services.outreach_batch import process_outreach_lead, select_outreach_batch_ids


def _seed(**kwargs) -> int:
    with SessionLocal() as db:
        b = Business(
            place_id=kwargs.pop("place_id"),
            name=kwargs.pop("name"),
            country=kwargs.pop("country", "Australia"),
            city=kwargs.pop("city", "Sydney"),
            category=kwargs.pop("category", "barbers"),
            marked_for_contact=kwargs.pop("marked_for_contact", True),
            **kwargs,
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        return int(b.id)


def test_process_outreach_wa_promotes_ready():
    bid = _seed(
        place_id="ob/wa-1",
        name="WA Shop",
        phone="+61400000001",
        social_links_json="{}",
    )
    with SessionLocal() as db:
        b = db.get(Business, bid)
        out = process_outreach_lead(db, b)
        assert out["action"] == "whatsapp_ready"
        db.refresh(b)
        assert b.outreach_channel == "whatsapp"
        assert b.ready_for_contact is True


def test_process_outreach_ig_stays_for_warm():
    bid = _seed(
        place_id="ob/ig-1",
        name="IG Shop",
        phone="",
        social_links_json=json.dumps({"instagram": "https://www.instagram.com/cutshop/"}),
    )
    with SessionLocal() as db:
        b = db.get(Business, bid)
        out = process_outreach_lead(db, b)
        assert out["action"] == "instagram_warm"
        db.refresh(b)
        assert b.outreach_channel == "instagram"
        assert b.ready_for_contact is False


def test_outreach_batch_job(auth_client):
    wa_id = _seed(
        place_id="ob/job-wa",
        name="Job WA",
        phone="+61400000099",
    )
    ig_id = _seed(
        place_id="ob/job-ig",
        name="Job IG",
        phone="",
        social_links_json=json.dumps({"instagram": "https://instagram.com/jobig"}),
    )

    job = auth_client.post(
        "/api/sales/jobs",
        json={
            "job_type": "outreach_batch",
            "payload": {"business_ids": [wa_id, ig_id], "limit": 10},
        },
    )
    assert job.status_code == 200, job.text
    jid = job.json()["id"]
    tick = auth_client.post(f"/api/jobs/{jid}/tick")
    assert tick.status_code == 200, tick.text
    assert tick.json().get("done") is True
    stats = tick.json()["stats"]
    assert stats.get("whatsapp_ready", 0) >= 1
    assert stats.get("instagram_warm", 0) >= 1

    with SessionLocal() as db:
        wa = db.get(Business, wa_id)
        ig = db.get(Business, ig_id)
        assert wa.ready_for_contact is True
        assert ig.outreach_channel == "instagram"
        assert ig.ready_for_contact is False


def test_select_outreach_batch_ids_respects_city():
    _seed(place_id="ob/syd", name="Syd", city="Sydney", phone="+61411")
    _seed(place_id="ob/mel", name="Mel", city="Melbourne", phone="+61422")
    with SessionLocal() as db:
        ids = select_outreach_batch_ids(db, country="Australia", city="Sydney", limit=20)
        names = []
        for i in ids:
            b = db.get(Business, i)
            if b:
                names.append(b.city)
        assert all(c == "Sydney" for c in names)
