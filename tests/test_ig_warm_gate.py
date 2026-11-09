"""IG warm checklist + soft-DM daily cap on confirm-sent."""
from __future__ import annotations

import time

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AppSetting, Business


def _make_ig_lead(**overrides):
    data = dict(
        place_id=f"gap/ig-warm-{time.time_ns()}",
        name="Warm Gate Barber",
        city="Sydney",
        country="Australia",
        email="warm@example.com",
        phone="+61400000999",
        international_phone="+61400000999",
        marked_for_contact=True,
        ready_for_contact=True,
        outreach_channel="instagram",
        contacted=False,
        email_sent=False,
        whatsapp_sent=False,
        instagram_sent=False,
        ig_warm_follow=False,
        ig_warm_like=False,
        ig_warm_comment=False,
        social_links_json='{"instagram":"https://instagram.com/warmgatebarber"}',
    )
    data.update(overrides)
    with SessionLocal() as db:
        b = Business(**data)
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


def _set_cap(value: str) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(AppSetting).where(AppSetting.key == "ig_soft_dm_daily_cap"))
        if not row:
            db.add(AppSetting(key="ig_soft_dm_daily_cap", value=value))
        else:
            row.value = value
        db.commit()


def _set_wait_days(value: str) -> None:
    with SessionLocal() as db:
        row = db.scalar(select(AppSetting).where(AppSetting.key == "ig_ready_wait_days"))
        if not row:
            db.add(AppSetting(key="ig_ready_wait_days", value=value))
        else:
            row.value = value
        db.commit()


def test_patch_ig_warm_ticks_round_trip(auth_client):
    bid = _make_ig_lead()
    r = auth_client.patch(
        f"/api/businesses/{bid}",
        json={"ig_warm_follow": True},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ig_warm_follow"] is True
    assert data["ig_warm_like"] is False
    assert data["ig_warm_comment"] is False
    assert data["ig_warm_complete"] is False
    assert data["instagram_sent"] is False
    assert data["contacted"] is False

    # Follow + like unlocks warm complete
    r2 = auth_client.patch(
        f"/api/businesses/{bid}",
        json={"ig_warm_like": True},
    )
    assert r2.status_code == 200, r2.text
    data2 = r2.json()
    assert data2["ig_warm_complete"] is True
    assert data2["instagram_sent"] is False


def test_ig_confirm_without_warm_rejected(auth_client):
    bid = _make_ig_lead(ig_warm_follow=True, ig_warm_like=False, ig_warm_comment=False)
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "instagram", "message": "Hi — soft ask"},
    )
    assert r.status_code == 422, r.text
    assert "warm incomplete" in r.text.lower() or "like" in r.text.lower() or "comment" in r.text.lower()
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.instagram_sent is False
        assert b.contacted is False


def test_ig_confirm_with_warm_ok(auth_client):
    # Follow + like is enough for soft DM
    bid = _make_ig_lead(ig_warm_follow=True, ig_warm_like=True, ig_warm_comment=False)
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "instagram", "message": "Hi — soft ask"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["instagram_sent"] is True
    assert data["contacted"] is True
    assert data["ig_warm_complete"] is True


def test_wa_confirm_without_warm_ok(auth_client):
    bid = _make_ig_lead(
        ig_warm_follow=False,
        ig_warm_like=False,
        ig_warm_comment=False,
        outreach_channel="whatsapp",
        ready_for_contact=True,
    )
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "whatsapp", "notes": "WA cold"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["whatsapp_sent"] is True
    assert data["contacted"] is True
    assert data["instagram_sent"] is False


def test_ig_soft_dm_daily_cap_blocks(auth_client):
    _set_cap("2")
    try:
        ids = []
        for i in range(3):
            ids.append(
                _make_ig_lead(
                    name=f"Cap Lead {i}",
                    ig_warm_follow=True,
                    ig_warm_like=True,
                    ig_warm_comment=True,
                )
            )

        r0 = auth_client.post(f"/api/businesses/{ids[0]}/confirm-sent", json={"channel": "instagram"})
        assert r0.status_code == 200, r0.text
        r1 = auth_client.post(f"/api/businesses/{ids[1]}/confirm-sent", json={"channel": "instagram"})
        assert r1.status_code == 200, r1.text
        r2 = auth_client.post(f"/api/businesses/{ids[2]}/confirm-sent", json={"channel": "instagram"})
        assert r2.status_code == 422, r2.text
        assert "cap" in r2.text.lower()
        with SessionLocal() as db:
            b = db.get(Business, ids[2])
            assert b.instagram_sent is False
    finally:
        _set_cap("8")


def test_ig_soft_dm_cap_setting_allowlist(auth_client):
    r = auth_client.put("/api/sales/settings/ig_soft_dm_daily_cap?value=7")
    assert r.status_code == 200, r.text
    assert r.json()["value"] == "7"
    settings = auth_client.get("/api/sales/settings")
    assert settings.status_code == 200, settings.text
    body = settings.json()
    assert body["settings"].get("ig_soft_dm_daily_cap") == "7"
    assert "ig_outreach_quota" in body
    assert body["ig_outreach_quota"]["cap"] == 7
    # restore
    auth_client.put("/api/sales/settings/ig_soft_dm_daily_cap?value=8")


def test_patch_instagram_sent_bypass_rejected(auth_client):
    bid = _make_ig_lead(ig_warm_follow=True, ig_warm_like=True, ig_warm_comment=True)
    r = auth_client.patch(f"/api/businesses/{bid}", json={"instagram_sent": True})
    assert r.status_code == 422, r.text
    assert "confirm sent" in r.text.lower()
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.instagram_sent is False
        assert b.contacted is False


def test_bulk_instagram_sent_bypass_rejected(auth_client):
    bid = _make_ig_lead(ig_warm_follow=True, ig_warm_like=True, ig_warm_comment=True)
    r = auth_client.post(
        "/api/businesses/bulk",
        json={"ids": [bid], "instagram_sent": True},
    )
    assert r.status_code == 422, r.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.instagram_sent is False


def test_ig_reconfirm_does_not_burn_second_cap_slot(auth_client):
    from app.models import Activity

    _set_cap("1")
    try:
        first = _make_ig_lead(
            ig_warm_follow=True, ig_warm_like=True, ig_warm_comment=True
        )
        second = _make_ig_lead(
            ig_warm_follow=True, ig_warm_like=True, ig_warm_comment=True
        )
        r1 = auth_client.post(
            f"/api/businesses/{first}/confirm-sent",
            json={"channel": "instagram", "message": "first"},
        )
        assert r1.status_code == 200, r1.text
        # Re-confirm same lead — must not consume another cap unit
        r1b = auth_client.post(
            f"/api/businesses/{first}/confirm-sent",
            json={"channel": "instagram", "notes": "bump note"},
        )
        assert r1b.status_code == 200, r1b.text
        r2 = auth_client.post(
            f"/api/businesses/{second}/confirm-sent",
            json={"channel": "instagram"},
        )
        assert r2.status_code == 422, r2.text
        assert "cap" in r2.text.lower()
        with SessionLocal() as db:
            sends = db.scalars(
                select(Activity).where(
                    Activity.business_id == first,
                    Activity.activity_type == "outreach_sent",
                    Activity.channel == "instagram",
                )
            ).all()
            assert len(sends) == 1
    finally:
        _set_cap("8")


def test_ig_cap_zero_blocks_all(auth_client):
    _set_cap("0")
    try:
        bid = _make_ig_lead(
            ig_warm_follow=True, ig_warm_like=True, ig_warm_comment=True
        )
        r = auth_client.post(
            f"/api/businesses/{bid}/confirm-sent",
            json={"channel": "instagram"},
        )
        assert r.status_code == 422, r.text
        assert "cap" in r.text.lower()
        settings = auth_client.get("/api/sales/settings")
        assert settings.json()["ig_outreach_quota"]["cap"] == 0
        assert settings.json()["ig_outreach_quota"]["remaining"] == 0
    finally:
        _set_cap("8")


def test_promote_ready_allowed_when_ig_cap_zero(auth_client):
    """Daily cap blocks Confirm sent only — To contact → Ready must stay doable."""
    _set_cap("0")
    _set_wait_days("0")  # unlock Ready-wait so Confirm reaches the cap gate
    try:
        bid = _make_ig_lead(
            ready_for_contact=False,
            ig_warm_follow=True,
            ig_warm_like=True,
            ig_warm_comment=True,
        )
        prom = auth_client.post(f"/api/sales/prospects/{bid}/promote-ready")
        assert prom.status_code == 200, prom.text
        assert prom.json().get("ready_for_contact") is True
        with SessionLocal() as db:
            b = db.get(Business, bid)
            assert b.ready_for_contact is True
        blocked = auth_client.post(
            f"/api/businesses/{bid}/confirm-sent",
            json={"channel": "instagram", "message": "soft"},
        )
        assert blocked.status_code == 422, blocked.text
        assert "cap" in blocked.text.lower()
    finally:
        _set_cap("8")
        _set_wait_days("1")


def test_ig_soft_dm_cap_rejects_invalid(auth_client):
    bad = auth_client.put("/api/sales/settings/ig_soft_dm_daily_cap?value=nope")
    assert bad.status_code == 422, bad.text
    over = auth_client.put("/api/sales/settings/ig_soft_dm_daily_cap?value=101")
    assert over.status_code == 422, over.text
    zero = auth_client.put("/api/sales/settings/ig_soft_dm_daily_cap?value=0")
    assert zero.status_code == 200, zero.text
    assert zero.json()["value"] == "0"
    auth_client.put("/api/sales/settings/ig_soft_dm_daily_cap?value=8")


def test_already_contacted_only_needs_follow_warm(auth_client):
    """WA-contacted lead: IG soft send unlocks with Follow only (not like/comment)."""
    bid = _make_ig_lead(
        contacted=True,
        whatsapp_sent=True,
        ig_warm_follow=False,
        ig_warm_like=False,
        ig_warm_comment=False,
    )
    blocked = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "instagram"},
    )
    assert blocked.status_code == 422, blocked.text
    assert "follow" in blocked.text.lower()

    # Like/comment PATCH ignored once contacted; Follow is accepted.
    ignore = auth_client.patch(
        f"/api/businesses/{bid}",
        json={"ig_warm_like": True, "ig_warm_comment": True},
    )
    assert ignore.status_code == 200, ignore.text
    assert ignore.json()["ig_warm_like"] is False
    assert ignore.json()["ig_warm_comment"] is False
    assert ignore.json()["ig_warm_follow_only"] is True
    assert ignore.json()["ig_warm_complete"] is False

    follow = auth_client.patch(f"/api/businesses/{bid}", json={"ig_warm_follow": True})
    assert follow.status_code == 200, follow.text
    assert follow.json()["ig_warm_complete"] is True

    ok = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "instagram", "message": "soft ask"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["instagram_sent"] is True


def test_revert_preserves_ig_warm(auth_client):
    bid = _make_ig_lead(
        ig_warm_follow=True, ig_warm_like=True, ig_warm_comment=True
    )
    assert (
        auth_client.post(
            f"/api/businesses/{bid}/confirm-sent",
            json={"channel": "instagram"},
        ).status_code
        == 200
    )
    rev = auth_client.post(f"/api/sales/prospects/{bid}/revert-to-contact")
    assert rev.status_code == 200, rev.text
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.instagram_sent is False
        assert b.contacted is False
        assert b.marked_for_contact is True
        assert b.ig_warm_follow is True
        assert b.ig_warm_like is True
        assert b.ig_warm_comment is True
