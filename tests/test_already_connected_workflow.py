"""Already connected park + IG follow-back hold + explicit Engaged."""
from __future__ import annotations

import time

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, PipelineStage
from app.services.already_connected import find_same_contact_siblings, normalize_ig_handle
from app.services.automation import on_discovery_saved, on_score_saved
from app.services.pipeline import list_stages


def _biz(**overrides):
    data = dict(
        place_id=f"qa/ac-{time.time_ns()}",
        name="AC Shop",
        city="Melbourne",
        country="Australia",
        email="shared@example.com",
        phone="+61411112222",
        international_phone="+61411112222",
        social_links_json='{"instagram":"https://instagram.com/sharedbrand"}',
        marked_for_contact=True,
        ready_for_contact=True,
        outreach_channel="instagram",
        ig_warm_follow=True,
        ig_warm_like=True,
        product_line="STANDALONE",
    )
    data.update(overrides)
    with SessionLocal() as db:
        b = Business(**data)
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


def test_normalize_ig_handle():
    assert normalize_ig_handle("https://www.instagram.com/SharedBrand/") == "sharedbrand"
    assert normalize_ig_handle("@SharedBrand") == "sharedbrand"


def test_same_contact_siblings(auth_client):
    primary = _biz(name="Primary Loc", city="Sydney")
    other = _biz(name="Other Loc", city="Melbourne", ready_for_contact=True)
    r = auth_client.get(f"/api/sales/prospects/{other}/same-contact-siblings")
    assert r.status_code == 200, r.text
    ids = {s["id"] for s in r.json()["siblings"]}
    assert primary in ids
    shared = next(s for s in r.json()["siblings"] if s["id"] == primary)
    assert "instagram" in shared["shared_channels"] or "email" in shared["shared_channels"]


def test_already_connected_park_and_clear(auth_client):
    primary = _biz(name="Primary", ready_for_contact=False, contacted=True, instagram_sent=True)
    lead = _biz(name="Sibling", ready_for_contact=True)
    r = auth_client.post(
        f"/api/sales/prospects/{lead}/already-connected",
        json={"primary_id": primary, "notes": "same IG"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["already_connected"] is True
    assert body["already_connected_primary_id"] == primary

    cq = auth_client.get("/api/sales/contact-queue?product_line=STANDALONE&limit=0")
    assert cq.status_code == 200
    row = next(x for x in cq.json()["leads"] if x["id"] == lead)
    assert row["already_connected"] is True
    assert row["can_message"] is False
    assert row["ready_for_contact"] is False

    blocked = auth_client.post(
        f"/api/sales/prospects/{lead}/confirm-sent",
        json={"channel": "instagram"},
    )
    assert blocked.status_code == 422

    cleared = auth_client.post(f"/api/sales/prospects/{lead}/clear-already-connected")
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["already_connected"] is False


def test_ig_awaiting_follow_back_blocks_confirm(auth_client):
    lead = _biz(outreach_channel="whatsapp", ready_for_contact=True)
    # Force ready wait irrelevant for WA; hold still blocks.
    r = auth_client.post(
        f"/api/sales/prospects/{lead}/ig-awaiting-follow-back",
        json={"awaiting": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ig_awaiting_follow_back"] is True
    assert r.json()["can_message"] is False

    blocked = auth_client.post(
        f"/api/businesses/{lead}/confirm-sent",
        json={"channel": "whatsapp"},
    )
    assert blocked.status_code == 422
    assert "follow-back" in blocked.json()["detail"].lower()

    auth_client.post(
        f"/api/sales/prospects/{lead}/ig-awaiting-follow-back",
        json={"awaiting": False},
    )
    ok = auth_client.post(
        f"/api/sales/prospects/{lead}/confirm-sent",
        json={"channel": "whatsapp"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["contacted"] is True


def test_promote_ready_rejects_already_connected(auth_client):
    primary = _biz(name="P", ready_for_contact=False, contacted=True)
    lead = _biz(
        name="Q",
        ready_for_contact=False,
        outreach_channel="email",
        ig_warm_follow=False,
        ig_warm_like=False,
    )
    auth_client.post(
        f"/api/sales/prospects/{lead}/already-connected",
        json={"primary_id": primary},
    )
    r = auth_client.post(f"/api/sales/prospects/{lead}/promote-ready")
    assert r.status_code == 422


def test_discovery_and_score_do_not_auto_engaged(auth_client):
    bid = _biz(ready_for_contact=False, contacted=True, email_sent=True, outreach_channel="email")
    with SessionLocal() as db:
        b = db.get(Business, bid)
        stages = {s.slug: s for s in list_stages(db, "STANDALONE")}
        contacted = stages.get("contacted")
        if contacted:
            b.pipeline_stage_id = contacted.id
            db.commit()
        on_discovery_saved(db, b, painful_issue="short")
        db.commit()
        db.refresh(b)
        stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        assert not stage or stage.slug != "engaged"

        b.qualification_score = 6
        on_score_saved(db, b)
        db.commit()
        db.refresh(b)
        stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        assert not stage or stage.slug != "engaged"

    r = auth_client.post(f"/api/sales/prospects/{bid}/mark-engaged", json={"note": "they replied"})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        assert stage and stage.slug == "engaged"


def test_ready_status_includes_new_fields(auth_client):
    lead = _biz()
    r = auth_client.get(f"/api/sales/prospects/{lead}/ready-status")
    assert r.status_code == 200
    body = r.json()
    assert "already_connected" in body
    assert "ig_awaiting_follow_back" in body
    assert "can_message" in body


def test_find_siblings_unit():
    with SessionLocal() as db:
        a = Business(
            place_id=f"qa/sib-a-{time.time_ns()}",
            name="A",
            email="twin@x.com",
            social_links_json='{"instagram":"@twinco"}',
        )
        b = Business(
            place_id=f"qa/sib-b-{time.time_ns()}",
            name="B",
            email="twin@x.com",
            social_links_json='{"instagram":"https://instagram.com/twinco"}',
        )
        db.add_all([a, b])
        db.commit()
        db.refresh(a)
        db.refresh(b)
        sibs = find_same_contact_siblings(db, a)
        assert any(s["id"] == b.id for s in sibs)
