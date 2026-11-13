"""To contact prep → Ready to contact → confirm stays Contacted (not Follow-up)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, PipelineStage
from app.services.automation import can_ready_for_contact, ig_warm_complete, maybe_promote_follow_up
from app.services.pipeline import infer_slug_from_flags


def _lead(**overrides):
    data = dict(
        place_id=f"qa/ready-{time.time_ns()}",
        name="Ready Workflow Barber",
        city="Sydney",
        country="Australia",
        email="ready@example.com",
        phone="+61400000111",
        international_phone="+61400000111",
        marked_for_contact=True,
        ready_for_contact=False,
        outreach_channel="",
        social_links_json='{"instagram":"https://instagram.com/readywf"}',
        product_line="STANDALONE",
    )
    data.update(overrides)
    with SessionLocal() as db:
        b = Business(**data)
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


def test_ig_warm_complete_follow_plus_one():
    b = Business(ig_warm_follow=True, ig_warm_like=True, ig_warm_comment=False)
    assert ig_warm_complete(b) is True
    b2 = Business(ig_warm_follow=True, ig_warm_like=False, ig_warm_comment=False)
    assert ig_warm_complete(b2) is False
    # like+comment without follow is not enough
    b3 = Business(ig_warm_follow=False, ig_warm_like=True, ig_warm_comment=True)
    assert ig_warm_complete(b3) is False


def test_infer_slug_marked_is_to_contact():
    b = Business(marked_for_contact=True, ready_for_contact=False, contacted=False, lost=False)
    assert infer_slug_from_flags(b) == "to_contact"
    b.ready_for_contact = True
    assert infer_slug_from_flags(b) == "ready_to_contact"


def test_board_column_slug_mirrors_contact_queue_flags():
    from app.services.pipeline import board_column_slug

    avail = {"identified", "to_contact", "ready_to_contact", "contacted", "follow_up_3", "follow_up_7", "engaged"}
    # Stale stage_id on identified, but marked → Identified (to_contact) column
    marked = Business(marked_for_contact=True, ready_for_contact=False, contacted=False, lost=False)
    stale = PipelineStage(id=1, product_line="STANDALONE", slug="identified", name="Discovered", sort_order=0)
    assert board_column_slug(marked, stale, available_slugs=avail) == "to_contact"
    ready = Business(marked_for_contact=True, ready_for_contact=True, contacted=False, lost=False)
    assert board_column_slug(ready, stale, available_slugs=avail) == "ready_to_contact"
    # Confirm-sent via channel flags even if contacted flag false
    sent = Business(marked_for_contact=True, email_sent=True, contacted=False, lost=False)
    assert board_column_slug(sent, stale, available_slugs=avail) == "contacted"
    # Late stage trusted
    engaged_stage = PipelineStage(id=9, product_line="STANDALONE", slug="engaged", name="Engaged", sort_order=9)
    engaged = Business(marked_for_contact=True, contacted=True, lost=False)
    assert board_column_slug(engaged, engaged_stage, available_slugs=avail) == "engaged"
    # Parks leave To contact / Ready (surface on Discovered when not contacted)
    parked = Business(
        marked_for_contact=True,
        ready_for_contact=True,
        already_connected=True,
        contacted=False,
        lost=False,
    )
    assert board_column_slug(parked, stale, available_slugs=avail) == "identified"


def test_email_channel_can_ready():
    b = Business(
        email="a@b.com",
        outreach_channel="email",
        phone="",
        international_phone="",
        social_links_json="{}",
    )
    ok, reason = can_ready_for_contact(b)
    assert ok is True, reason


def test_promote_ready_email(auth_client):
    bid = _lead(outreach_channel="email", social_links_json="{}")
    r = auth_client.post(f"/api/sales/prospects/{bid}/promote-ready")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ready_for_contact") is True
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.ready_for_contact is True
        stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        assert stage is None or stage.slug == "ready_to_contact"


def test_promote_ig_needs_warm(auth_client):
    bid = _lead(outreach_channel="instagram")
    r = auth_client.post(f"/api/sales/prospects/{bid}/promote-ready")
    assert r.status_code == 422, r.text
    auth_client.patch(
        f"/api/businesses/{bid}",
        json={"ig_warm_follow": True, "ig_warm_like": True},
    )
    r2 = auth_client.post(f"/api/sales/prospects/{bid}/promote-ready")
    assert r2.status_code == 200, r2.text


def test_confirm_stays_contacted_not_follow_up(auth_client):
    bid = _lead(
        outreach_channel="whatsapp",
        ready_for_contact=True,
        ig_warm_follow=True,
        ig_warm_like=True,
    )
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "whatsapp", "message": "hi"},
    )
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.contacted is True
        assert b.follow_up_kind == "call_today"
        stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        assert stage is None or stage.slug == "contacted"


def test_cold_confirm_system_schedules_plus3_ignores_client_due(auth_client):
    """Cold confirm always +3 first — client due (past or far) is ignored."""
    bid = _lead(ready_for_contact=True, outreach_channel="email")
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "next_follow_up_at": "2020-01-01", "message": "hi"},
    )
    assert r.status_code == 200, r.text
    expect_due = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.follow_up_kind == "plus3"
        assert (b.next_follow_up_at.date().isoformat() if b.next_follow_up_at else "") == expect_due
        stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        assert stage is None or stage.slug == "contacted"
        assert maybe_promote_follow_up(db, b) is False


def test_due_plus3_then_kind_plus7_promotes_follow_up_7(auth_client):
    bid = _lead(ready_for_contact=True, outreach_channel="email")
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "hi"},
    )
    assert r.status_code == 200, r.text
    past = datetime.now(timezone.utc) - timedelta(days=1)
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.follow_up_kind == "plus3"
        b.next_follow_up_at = past
        b.last_contacted_at = past - timedelta(days=3)
        db.commit()
        db.refresh(b)
        assert maybe_promote_follow_up(db, b) is True
        db.refresh(b)
        stage0 = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage0 is not None and stage0.slug == "follow_up_3"
        b.follow_up_kind = "plus7"
        db.commit()
        db.refresh(b)
        assert maybe_promote_follow_up(db, b) is True
        db.refresh(b)
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage is not None and stage.slug == "follow_up_7"


def test_reconfirm_followup_system_schedules_plus7(auth_client):
    """Second confirm-sent graduates to plus7 and system-schedules +7 days (ignores client due)."""
    bid = _lead(ready_for_contact=True, outreach_channel="email")
    r1 = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "first", "next_follow_up_at": "2099-01-01"},
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert body1.get("contacted") is True
    assert body1.get("follow_up_kind") == "plus3"
    expect_plus3 = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
    assert (body1.get("next_follow_up_at") or "")[:10] == expect_plus3
    r2 = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "follow-up", "next_follow_up_at": "2020-01-01"},
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    expect_plus7 = (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()
    assert body2.get("contacted") is True
    assert body2.get("email_sent") is True
    assert body2.get("follow_up_kind") == "plus7"
    assert (body2.get("next_follow_up_at") or "")[:10] == expect_plus7
    assert (body2.get("last_contacted_at") or "")[:10] == datetime.now(timezone.utc).date().isoformat()


def test_contact_queue_follow_filter_markup():
    """Contacted timing filter + CSS hidden override ship with the UI."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    html = (root / "app/templates/index.html").read_text(encoding="utf-8")
    css = (root / "app/static/css/app.css").read_text(encoding="utf-8")
    js = (root / "app/static/js/panels/contact-queue.js").read_text(encoding="utf-8")
    assert 'id="contactQueueFollowFilter"' in html
    assert 'id="contactQueueFollowField"' in html
    assert ".field[hidden]" in css
    assert "contactQueueFollowMatch" in js
    assert "contactQueueFollowBucket" in js
    assert "data-cq-follow-send" in js
    assert "Message" in js
    assert "Engaged" in js
    assert "never since>=7 alone" in js or "never calendar-only" in js
    # Follow-up cards: single system Follow-up (no manual +3/+7 choice / Back to Ready)
    contacted_branch = js.split("}else if(isContacted){")[1].split("}else if(isCannot){")[0]
    assert "data-cq-revert" not in contacted_branch
    assert "Back to Ready" not in contacted_branch
    assert "data-cq-engaged" in contacted_branch
    assert "data-follow-days" not in contacted_branch
    assert ">Follow-up<" in contacted_branch or "Follow-up</button>" in contacted_branch


def test_pipeline_filters_and_shows_all_stage_columns(auth_client):
    """Board shows every stage column (incl. Discovered) so cards can land on each."""
    bid = _lead(
        ready_for_contact=False,
        marked_for_contact=True,
        city="Sydney",
        country="Australia",
        category="Barber",
    )
    with SessionLocal() as db:
        from app.services.automation import on_marked_for_contact

        b = db.get(Business, bid)
        on_marked_for_contact(db, b)
        db.commit()
    r = auth_client.get(
        "/api/sales/pipeline",
        params={"product_line": "STANDALONE", "country": "Australia", "city": "Sydney", "category": "Barber"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "locations" in data and "categories" in data
    slugs = [c["stage"]["slug"] for c in data.get("columns") or []]
    assert "identified" in slugs  # Discovered visible for parks / unmarked
    assert "to_contact" in slugs
    assert "follow_up_3" in slugs and "follow_up_7" in slugs
    col = next(c for c in data["columns"] if c["stage"]["slug"] == "to_contact")
    assert col["stage"]["name"] == "To contact"
    assert any(p["id"] == bid for p in col["prospects"])
    assert col["cq_tab"] == "selected"
    # Card payload carries park/hold fields for mobile + web board
    card = next(p for p in col["prospects"] if p["id"] == bid)
    assert "already_connected" in card
    assert "ig_awaiting_follow_back" in card


def test_revert_goes_to_ready(auth_client):
    bid = _lead(ready_for_contact=True, outreach_channel="email")
    auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "hi"},
    )
    r = auth_client.post(f"/api/sales/prospects/{bid}/revert-to-contact")
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.contacted is False
        assert b.ready_for_contact is True
        assert (b.outreach_channel or "") == "email"
        stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        assert stage is None or stage.slug == "ready_to_contact"


def test_channel_switch_to_ig_demotes_ready(auth_client):
    bid = _lead(outreach_channel="email", ready_for_contact=True)
    r = auth_client.patch(f"/api/businesses/{bid}", json={"outreach_channel": "instagram"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["outreach_channel"] == "instagram"
    assert data["ready_for_contact"] is False


def test_confirm_unready_rejected(auth_client):
    bid = _lead(ready_for_contact=False, outreach_channel="email")
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "skip ready"},
    )
    assert r.status_code == 422, r.text
    assert "ready" in r.text.lower()
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.contacted is False
        assert b.email_sent is False


def test_confirm_allow_unready_ok(auth_client):
    bid = _lead(ready_for_contact=False, outreach_channel="email")
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email", "message": "invoice", "allow_unready": True},
    )
    assert r.status_code == 200, r.text
    assert r.json().get("contacted") is True


def test_patch_ready_true_requires_channel(auth_client):
    bid = _lead(outreach_channel="", ready_for_contact=False)
    r = auth_client.patch(f"/api/businesses/{bid}", json={"ready_for_contact": True})
    assert r.status_code == 422, r.text
    auth_client.patch(f"/api/businesses/{bid}", json={"outreach_channel": "email"})
    r2 = auth_client.patch(f"/api/businesses/{bid}", json={"ready_for_contact": True})
    assert r2.status_code == 200, r2.text
    assert r2.json().get("ready_for_contact") is True


def test_booking_clears_to_contact_flags(auth_client):
    bid = _lead(outreach_channel="email", ready_for_contact=True, marked_for_contact=True)
    r = auth_client.patch(
        f"/api/sales/prospects/{bid}",
        json={"current_booking_software": "Has booking"},
    )
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert (b.current_booking_software or "").strip() == "Has booking"
        assert b.marked_for_contact is False
        assert b.ready_for_contact is False
    # Still listed on contact-queue so Has booking tab can show it (even under marked-only modes)
    q = auth_client.get("/api/sales/contact-queue?product_line=STANDALONE&limit=0&mode=starred_uncontacted")
    assert q.status_code == 200, q.text
    lead = next((x for x in (q.json().get("leads") or []) if x["id"] == bid), None)
    assert lead is not None
    assert lead.get("has_booking") is True
    assert lead.get("marked_for_contact") is False


def test_promote_short_phone_rejected(auth_client):
    bid = _lead(
        outreach_channel="whatsapp",
        phone="123",
        international_phone="123",
        email="",
        social_links_json="{}",
    )
    r = auth_client.post(f"/api/sales/prospects/{bid}/promote-ready")
    assert r.status_code == 422, r.text


def test_ig_like_comment_without_follow_not_ready():
    from app.services.automation import can_ready_for_contact, ig_warm_complete

    b = Business(
        outreach_channel="instagram",
        social_links_json='{"instagram":"https://instagram.com/x"}',
        ig_warm_follow=False,
        ig_warm_like=True,
        ig_warm_comment=True,
    )
    assert ig_warm_complete(b) is False
    ok, reason = can_ready_for_contact(b)
    assert ok is False
    assert "warm" in reason.lower() or "follow" in reason.lower()
