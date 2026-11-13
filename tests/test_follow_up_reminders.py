"""Pipeline Follow-up sync + reminder digest cron."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import Business, PipelineStage
from app.services.automation import maybe_promote_follow_up
from app.services.follow_up_reminders import due_follow_up_leads, run_follow_up_reminders
from app.services.pipeline import infer_slug_from_flags


def _lead(**overrides):
    data = dict(
        place_id=f"qa/fup-{time.time_ns()}",
        name="Follow Reminder Barber",
        city="Sydney",
        country="Australia",
        email="fup@example.com",
        phone="+61400000999",
        international_phone="+61400000999",
        marked_for_contact=True,
        ready_for_contact=True,
        contacted=True,
        email_sent=True,
        outreach_channel="email",
        product_line="STANDALONE",
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=4),
        next_follow_up_at=None,
        follow_up_kind="plus3",
    )
    data.update(overrides)
    with SessionLocal() as db:
        b = Business(**data)
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


def test_no_due_days_since_promotes_follow_up_3():
    bid = _lead(last_contacted_at=datetime.now(timezone.utc) - timedelta(days=4), next_follow_up_at=None)
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert infer_slug_from_flags(b) == "follow_up_3"
        assert maybe_promote_follow_up(db, b) is True
        db.refresh(b)
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage is not None and stage.slug == "follow_up_3"


def test_no_due_days_since_10_stays_follow_up_3_without_kind():
    """Calendar alone never promotes to +7 — stay on +3 until follow-up confirm graduates kind."""
    bid = _lead(
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=10),
        next_follow_up_at=None,
        follow_up_kind="plus3",
    )
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert infer_slug_from_flags(b) == "follow_up_3"
        assert maybe_promote_follow_up(db, b) is True
        db.refresh(b)
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage is not None and stage.slug == "follow_up_3"


def test_plus7_kind_promotes_follow_up_7():
    bid = _lead(
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=8),
        next_follow_up_at=None,
        follow_up_kind="plus7",
    )
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert infer_slug_from_flags(b) == "follow_up_7"
        assert maybe_promote_follow_up(db, b) is True
        db.refresh(b)
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage is not None and stage.slug == "follow_up_7"


def test_future_due_stays_contacted_board():
    bid = _lead(
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=1),
        next_follow_up_at=datetime.now(timezone.utc) + timedelta(days=2),
        follow_up_kind="plus3",
    )
    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert infer_slug_from_flags(b) == "contacted"
        assert maybe_promote_follow_up(db, b) is False


def test_reminders_dry_run_promotes_without_email(auth_client, monkeypatch):
    bid = _lead(
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=5),
        next_follow_up_at=datetime.now(timezone.utc) - timedelta(days=1),
        follow_up_kind="plus3",
    )
    sent = []

    def _fake_send(**kwargs):
        sent.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("app.services.mail.send_email", _fake_send)
    monkeypatch.setattr("app.services.mail.smtp_configured", lambda db=None: True)
    monkeypatch.setattr(
        "app.services.follow_up_reminders.reminder_recipients",
        lambda db: ["owner@example.com"],
    )

    with SessionLocal() as db:
        out = run_follow_up_reminders(db, dry_run=True)
        assert out["ok"] is True
        assert out["due_count"] >= 1
        assert out["email_skipped"] == "dry_run"
        assert not sent
        b = db.get(Business, bid)
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage is not None and stage.slug == "follow_up_3"


def test_reminders_email_disabled_by_default(auth_client, monkeypatch):
    _lead(
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=5),
        next_follow_up_at=datetime.now(timezone.utc) - timedelta(days=1),
        follow_up_kind="plus3",
    )
    sent = []
    monkeypatch.setattr("app.services.mail.send_email", lambda **kw: sent.append(kw) or {"ok": True})
    monkeypatch.setattr("app.services.mail.smtp_configured", lambda db=None: True)
    monkeypatch.setattr(
        "app.services.follow_up_reminders.reminder_recipients",
        lambda db: ["owner@example.com"],
    )
    with SessionLocal() as db:
        from app.models import AppSetting
        from sqlalchemy import select

        for key in (
            "follow_up_reminders.email_enabled",
            "follow_up_reminders.push_enabled",
            "follow_up_reminders.last_digest_day",
        ):
            row = db.scalar(select(AppSetting).where(AppSetting.key == key))
            if row:
                db.delete(row)
        db.commit()
        out = run_follow_up_reminders(db)
        assert out["email_reminders_enabled"] is False
        assert out["push_reminders_enabled"] is False
        assert out["email_skipped"] == "email_reminders_disabled"
        assert not sent


def test_reminders_sends_digest_once(auth_client, monkeypatch):
    _lead(
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=5),
        next_follow_up_at=datetime.now(timezone.utc) - timedelta(days=1),
        follow_up_kind="plus3",
    )
    sent = []

    def _fake_send(**kwargs):
        sent.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr("app.services.mail.send_email", _fake_send)
    monkeypatch.setattr("app.services.mail.smtp_configured", lambda db=None: True)
    monkeypatch.setattr(
        "app.services.follow_up_reminders.reminder_recipients",
        lambda db: ["owner@example.com"],
    )

    with SessionLocal() as db:
        # enable email + reset idempotency key
        from app.models import AppSetting
        from sqlalchemy import select

        for key, val in (
            ("follow_up_reminders.email_enabled", "true"),
            ("follow_up_reminders.last_digest_day", ""),
        ):
            row = db.scalar(select(AppSetting).where(AppSetting.key == key))
            if row:
                row.value = val
            else:
                db.add(AppSetting(key=key, value=val))
        db.commit()

        out1 = run_follow_up_reminders(db, force_email=False)
        assert out1["email_sent"] is True
        assert sent and "follow-up" in (sent[0].get("subject") or "").lower()
        n = len(sent)
        out2 = run_follow_up_reminders(db, force_email=False)
        assert out2["email_skipped"] == "already_sent_today"
        assert len(sent) == n


def test_put_reminder_settings(auth_client):
    r = auth_client.put("/api/sales/settings/follow_up_reminders.email_enabled?value=true")
    assert r.status_code == 200, r.text
    assert r.json().get("value") == "true"
    r2 = auth_client.put("/api/sales/settings/follow_up_reminders.push_enabled?value=false")
    assert r2.status_code == 200, r2.text
    assert r2.json().get("value") == "false"
    got = auth_client.get("/api/sales/settings").json().get("settings") or {}
    assert got.get("follow_up_reminders.email_enabled") == "true"
    assert got.get("follow_up_reminders.push_enabled") == "false"


def test_cron_follow_up_reminders_owner(auth_client):
    r = auth_client.post(
        "/api/cron/follow-up-reminders?dry_run=true",
        headers={"X-Cron-Secret": "cron-test-secret"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert "due_count" in body
    assert body.get("email_skipped") == "dry_run"


def test_due_follow_up_leads_lists_gap():
    bid = _lead(
        last_contacted_at=datetime.now(timezone.utc) - timedelta(days=4),
        next_follow_up_at=None,
        follow_up_kind="",
    )
    with SessionLocal() as db:
        ids = {b.id for b in due_follow_up_leads(db)}
        assert bid in ids


def test_notifications_include_follow_up_keys():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    src = (root / "app/static/js/core/notifications.js").read_text()
    assert "follow_up_due" in src
    assert "maybeBrowserPushFollowUps" in src
    assert "follow_up_push_reminders_enabled" in src
    html = (root / "app/templates/index.html").read_text()
    assert 'id="followUpEmailReminders"' in html
    assert 'id="followUpPushReminders"' in html
