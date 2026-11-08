"""IG Ready wait: soft ask unlocks N Settings-timezone days after promote to Ready."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AppSetting, Business
from app.services.automation import ig_ready_wait_payload


def _make_ig_lead(**overrides):
    data = dict(
        place_id=f"qa/ig-ready-wait-{time.time_ns()}",
        name="Ready Wait Barber",
        city="Sydney",
        country="Australia",
        email="readywait@example.com",
        phone="+61400000888",
        international_phone="+61400000888",
        marked_for_contact=True,
        ready_for_contact=False,
        outreach_channel="instagram",
        contacted=False,
        email_sent=False,
        whatsapp_sent=False,
        instagram_sent=False,
        ig_warm_follow=True,
        ig_warm_like=True,
        ig_warm_comment=False,
        social_links_json='{"instagram":"https://instagram.com/readywaitbarber"}',
    )
    data.update(overrides)
    with SessionLocal() as db:
        b = Business(**data)
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


def _set_wait_days(value: str) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(AppSetting).where(AppSetting.key == "ig_ready_wait_days"))
        if not row:
            db.add(AppSetting(key="ig_ready_wait_days", value=value))
        else:
            row.value = value
        db.commit()


def _set_cap(value: str) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(AppSetting).where(AppSetting.key == "ig_soft_dm_daily_cap"))
        if not row:
            db.add(AppSetting(key="ig_soft_dm_daily_cap", value=value))
        else:
            row.value = value
        db.commit()


def test_ig_ready_wait_setting_allowlist(auth_client):
    r = auth_client.put("/api/sales/settings/ig_ready_wait_days?value=3")
    assert r.status_code == 200, r.text
    assert r.json()["value"] == "3"
    settings = auth_client.get("/api/sales/settings")
    assert settings.status_code == 200, settings.text
    assert settings.json()["settings"].get("ig_ready_wait_days") == "3"
    bad = auth_client.put("/api/sales/settings/ig_ready_wait_days?value=15")
    assert bad.status_code == 422, bad.text
    nope = auth_client.put("/api/sales/settings/ig_ready_wait_days?value=nope")
    assert nope.status_code == 422, nope.text
    auth_client.put("/api/sales/settings/ig_ready_wait_days?value=1")


def test_promote_stamps_ig_ready_at(auth_client):
    _set_wait_days("1")
    bid = _make_ig_lead()
    r = auth_client.post(f"/api/sales/prospects/{bid}/promote-ready")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ready_for_contact") is True
    assert body.get("ig_ready_at")
    wait = body.get("ig_ready_wait") or {}
    assert wait.get("applies") is True
    assert wait.get("unlocked") is False
    assert wait.get("can_message") is False
    assert "tomorrow" in (wait.get("reason") or "").lower() or "unlock" in (wait.get("reason") or "").lower()
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.ig_ready_at is not None
        assert b.ready_for_contact is True


def test_confirm_blocked_during_wait(auth_client):
    _set_wait_days("1")
    _set_cap("8")
    bid = _make_ig_lead()
    assert auth_client.post(f"/api/sales/prospects/{bid}/promote-ready").status_code == 200
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "instagram", "message": "too soon"},
    )
    assert r.status_code == 422, r.text
    assert "warm" in r.text.lower() or "tomorrow" in r.text.lower() or "unlock" in r.text.lower()
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.instagram_sent is False
        assert b.contacted is False


def test_confirm_ok_after_wait_elapsed(auth_client):
    _set_wait_days("1")
    _set_cap("8")
    bid = _make_ig_lead()
    assert auth_client.post(f"/api/sales/prospects/{bid}/promote-ready").status_code == 200
    with SessionLocal() as db:
        b = db.get(Business, bid)
        b.ig_ready_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.commit()
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "instagram", "message": "soft ask"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["instagram_sent"] is True
    assert r.json().get("ig_ready_at") in ("", None)
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.ig_ready_at is None


def test_wait_days_zero_allows_same_day(auth_client):
    _set_wait_days("0")
    try:
        bid = _make_ig_lead()
        prom = auth_client.post(f"/api/sales/prospects/{bid}/promote-ready")
        assert prom.status_code == 200, prom.text
        wait = (prom.json().get("ig_ready_wait") or {})
        assert wait.get("unlocked") is True
        r = auth_client.post(
            f"/api/businesses/{bid}/confirm-sent",
            json={"channel": "instagram", "message": "same day"},
        )
        assert r.status_code == 200, r.text
    finally:
        _set_wait_days("1")


def test_wa_promote_no_wait(auth_client):
    _set_wait_days("1")
    bid = _make_ig_lead(
        outreach_channel="whatsapp",
        ig_warm_follow=False,
        ig_warm_like=False,
        ig_warm_comment=False,
    )
    prom = auth_client.post(f"/api/sales/prospects/{bid}/promote-ready")
    assert prom.status_code == 200, prom.text
    assert not (prom.json().get("ig_ready_at") or "")
    wait = prom.json().get("ig_ready_wait") or {}
    assert wait.get("applies") is False
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "whatsapp", "message": "hi"},
    )
    assert r.status_code == 200, r.text


def test_demote_clears_stamp(auth_client):
    bid = _make_ig_lead()
    assert auth_client.post(f"/api/sales/prospects/{bid}/promote-ready").status_code == 200
    dem = auth_client.post(f"/api/sales/prospects/{bid}/demote-to-contact")
    assert dem.status_code == 200, dem.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.ready_for_contact is False
        assert b.ig_ready_at is None


def test_grandfather_missing_stamp_allows_confirm(auth_client):
    """Existing Ready leads without ig_ready_at stay unlocked."""
    _set_wait_days("1")
    _set_cap("8")
    bid = _make_ig_lead(ready_for_contact=True, ig_ready_at=None)
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "instagram", "message": "legacy"},
    )
    assert r.status_code == 200, r.text


def test_already_contacted_skips_wait(auth_client):
    _set_wait_days("3")
    _set_cap("8")
    bid = _make_ig_lead(
        contacted=True,
        whatsapp_sent=True,
        ready_for_contact=False,
        ig_warm_follow=True,
        ig_warm_like=False,
        ig_warm_comment=False,
        ig_ready_at=datetime.now(timezone.utc),  # would block if wait applied
    )
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "instagram", "message": "bump"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["instagram_sent"] is True


def test_ready_status_api_for_mobile(auth_client):
    _set_wait_days("1")
    bid = _make_ig_lead()
    before = auth_client.get(f"/api/sales/prospects/{bid}/ready-status")
    assert before.status_code == 200, before.text
    body0 = before.json()
    assert body0["ready_for_contact"] is False
    assert body0["can_confirm_sent"] is False
    assert "ready" in (body0.get("block_reason") or "").lower()

    prom = auth_client.post(f"/api/sales/prospects/{bid}/promote-ready")
    assert prom.status_code == 200, prom.text
    status = auth_client.get(f"/api/sales/prospects/{bid}/ready-status")
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["ok"] is True
    assert body["ready_for_contact"] is True
    assert body["outreach_channel"] == "instagram"
    assert body["ig_ready_wait"]["applies"] is True
    assert body["ig_ready_wait"]["unlocked"] is False
    assert body["can_confirm_sent"] is False
    assert body["can_open_message"] is False
    assert body["block_reason"]
    assert "ig_warm" in body
    assert "ig_outreach_quota" in body

    with SessionLocal() as db:
        b = db.get(Business, bid)
        b.ig_ready_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.commit()
    unlocked = auth_client.get(f"/api/sales/prospects/{bid}/ready-status")
    assert unlocked.status_code == 200, unlocked.text
    body2 = unlocked.json()
    assert body2["ig_ready_wait"]["unlocked"] is True
    assert body2["can_confirm_sent"] is True
    assert body2["block_reason"] == ""


def test_contact_queue_includes_ig_ready_wait(auth_client):
    _set_wait_days("1")
    bid = _make_ig_lead()
    assert auth_client.post(f"/api/sales/prospects/{bid}/promote-ready").status_code == 200
    q = auth_client.get("/api/sales/contact-queue?product_line=STANDALONE&limit=0")
    assert q.status_code == 200, q.text
    lead = next((x for x in (q.json().get("leads") or []) if x["id"] == bid), None)
    assert lead is not None
    assert lead.get("ig_ready_at")
    assert lead.get("ig_ready_wait", {}).get("applies") is True
    assert lead["ig_ready_wait"]["unlocked"] is False


def test_payload_helper_unlock_math():
    _set_wait_days("1")
    bid = _make_ig_lead(ready_for_contact=True)
    with SessionLocal() as db:
        b = db.get(Business, bid)
        b.ig_ready_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(b)
        locked = ig_ready_wait_payload(db, b)
        assert locked["unlocked"] is False
        b.ig_ready_at = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
        unlocked = ig_ready_wait_payload(db, b)
        assert unlocked["unlocked"] is True
