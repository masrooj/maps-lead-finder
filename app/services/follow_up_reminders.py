"""Due follow-up reminders: promote Pipeline columns + optional SMTP digest + in-app counts."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import or_, select

from app.models import AppSetting, Business, Task, User
from app.services.automation import (
    days_since_last_contact,
    follow_up_target_slug,
    promote_due_follow_ups,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _setting_get(db, key: str, default: str = "") -> str:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if not row or row.value is None:
        return default
    return str(row.value)


def _setting_set(db, key: str, value: str) -> None:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def _truthy_setting(db, key: str, *, default: bool = False) -> bool:
    """AppSetting bool; missing key → default (email/push reminders default off)."""
    raw = _setting_get(db, key, "true" if default else "false").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off", ""):
        return False
    return default


def email_reminders_enabled(db) -> bool:
    return _truthy_setting(db, "follow_up_reminders.email_enabled", default=False)


def push_reminders_enabled(db) -> bool:
    return _truthy_setting(db, "follow_up_reminders.push_enabled", default=False)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def due_follow_up_leads(db, *, limit: int = 200) -> list[Business]:
    """Contacted leads that belong in Follow-up +3/+7 (due/past or days-since gap)."""
    rows = db.scalars(
        select(Business)
        .where(
            Business.contacted.is_(True),
            or_(Business.lost.is_(False), Business.lost.is_(None)),
            or_(Business.archived.is_(False), Business.archived.is_(None)),
        )
        .limit(max(1, min(int(limit), 2000)))
    ).all()
    out: list[Business] = []
    now = _utcnow()
    for b in rows:
        kind = (getattr(b, "follow_up_kind", None) or "").strip()
        if kind == "call_today":
            continue
        due = _aware(getattr(b, "next_follow_up_at", None))
        since = days_since_last_contact(b)
        if due and due <= now:
            out.append(b)
        elif not due and since >= 3:
            out.append(b)
    return out


def reminder_recipients(db) -> list[str]:
    """Active owner/sales emails; fall back to LOGIN_USERNAME."""
    users = db.scalars(
        select(User).where(
            User.active.is_(True),
            User.role.in_(("owner", "sales")),
        )
    ).all()
    emails = sorted({(u.email or "").strip().lower() for u in users if (u.email or "").strip()})
    if emails:
        return emails
    fallback = (os.getenv("LOGIN_USERNAME") or "").strip().lower()
    return [fallback] if fallback and "@" in fallback else []


def build_digest_body(leads: list[Business], *, promoted: int) -> str:
    lines = [
        "AppointHive Sales OS — follow-up reminder",
        "",
        f"Pipeline promotions this run: {promoted}",
        f"Due / overdue follow-ups: {len(leads)}",
        "",
    ]
    for b in leads[:40]:
        due = _aware(getattr(b, "next_follow_up_at", None))
        due_s = due.date().isoformat() if due else "—"
        slug = follow_up_target_slug(b)
        city = (b.city or "").strip()
        lines.append(f"- {b.name} ({city or '—'}) · due {due_s} · → {slug}")
    if len(leads) > 40:
        lines.append(f"… and {len(leads) - 40} more")
    lines.extend(
        [
            "",
            "Open Contact queue → Contacted (filter +3 / +7) or Pipeline Follow-up columns.",
            "Cron: POST /api/cron/follow-up-reminders with X-Cron-Secret.",
        ]
    )
    return "\n".join(lines)


def run_follow_up_reminders(
    db,
    *,
    force_email: bool = False,
    dry_run: bool = False,
    actor_id: Optional[int] = None,
) -> dict:
    """Promote due follow-ups, then optionally email a once-per-day digest.

    Idempotent email: AppSetting follow_up_reminders.last_digest_day = YYYY-MM-DD (UTC).
    """
    promoted = promote_due_follow_ups(db, actor_id=actor_id)
    leads = due_follow_up_leads(db)
    today = _utcnow().date().isoformat()
    last_day = _setting_get(db, "follow_up_reminders.last_digest_day", "")
    recipients = reminder_recipients(db)

    from app.services.mail import send_email, smtp_configured

    smtp_ok = smtp_configured(db=db)
    email_on = email_reminders_enabled(db)
    push_on = push_reminders_enabled(db)
    email_sent = False
    email_skipped = None
    emailed_to: list[str] = []

    should_email = email_on and smtp_ok and recipients and (force_email or last_day != today)
    if dry_run:
        email_skipped = "dry_run"
    elif not email_on:
        email_skipped = "email_reminders_disabled"
    elif not smtp_ok:
        email_skipped = "smtp_not_configured"
    elif not recipients:
        email_skipped = "no_recipients"
    elif last_day == today and not force_email:
        email_skipped = "already_sent_today"
    elif should_email:
        subject = f"Sales OS: {len(leads)} follow-up{'s' if len(leads) != 1 else ''} due"
        body = build_digest_body(leads, promoted=promoted)
        errors = []
        for to in recipients:
            try:
                send_email(to=to, subject=subject, body=body)
                emailed_to.append(to)
            except Exception as e:  # noqa: BLE001 — collect per-recipient failures
                errors.append(f"{to}: {e}")
        if emailed_to:
            email_sent = True
            _setting_set(db, "follow_up_reminders.last_digest_day", today)
            _setting_set(db, "follow_up_reminders.last_digest_at", _utcnow().isoformat())
            _setting_set(db, "follow_up_reminders.last_digest_count", str(len(leads)))
        if errors and not emailed_to:
            email_skipped = "; ".join(errors)[:400]
        elif errors:
            email_skipped = "partial: " + "; ".join(errors)[:400]

    # Always persist promotions; dry_run only skips email side-effects.
    db.commit()

    return {
        "ok": True,
        "promoted": promoted,
        "due_count": len(leads),
        "sample": [
            {
                "id": b.id,
                "name": b.name,
                "next_follow_up_at": (_aware(b.next_follow_up_at).isoformat() if b.next_follow_up_at else ""),
                "target": follow_up_target_slug(b),
            }
            for b in leads[:20]
        ],
        "email_sent": email_sent,
        "email_skipped": email_skipped,
        "emailed_to": emailed_to,
        "smtp_ready": smtp_ok,
        "email_reminders_enabled": email_on,
        "push_reminders_enabled": push_on,
        "digest_day": today,
        "last_digest_day": last_day if not email_sent else today,
    }
