"""Sales metrics — honest money definitions."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select

from app.models import Business, Invoice, Payment, PipelineStage, Task


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def collected_revenue(db, *, include_test: bool = False) -> dict:
    stmt = select(Payment).where(Payment.status.in_(("succeeded", "refunded", "partial_refund")))
    if not include_test:
        stmt = stmt.where(Payment.is_test_mode.is_(False))
    payments = db.scalars(stmt).all()
    by_currency: dict[str, float] = {}
    n_succeeded = 0
    for p in payments:
        cur = (p.currency or "AUD").upper()
        amt = float(p.amount or 0)
        if p.status == "succeeded":
            by_currency[cur] = by_currency.get(cur, 0.0) + amt
            n_succeeded += 1
        elif p.status in ("refunded", "partial_refund"):
            by_currency[cur] = by_currency.get(cur, 0.0) - abs(amt)
    return {
        "definition": "Succeeded payments minus refunds. Unaccepted proposals and unpaid won deals are excluded.",
        "by_currency": by_currency,
        "sample_size": n_succeeded,
        "include_test": include_test,
    }


def open_pipeline_and_forecast(db, product_line: str = "STANDALONE") -> dict:
    stages = {
        s.id: s
        for s in db.scalars(
            select(PipelineStage).where(PipelineStage.product_line == product_line)
        ).all()
    }
    prospects = db.scalars(
        select(Business).where(
            Business.product_line == product_line,
            Business.archived.is_(False),
        )
    ).all()
    open_value: dict[str, float] = {}
    weighted: dict[str, float] = {}
    by_stage: list[dict] = []
    stage_buckets: dict[int, list[Business]] = {}
    for b in prospects:
        if not b.pipeline_stage_id:
            continue
        stage = stages.get(b.pipeline_stage_id)
        if not stage or stage.is_won or stage.is_lost:
            continue
        stage_buckets.setdefault(stage.id, []).append(b)
        cur = (b.currency or "AUD").upper()
        val = float(b.deal_value or 0)
        open_value[cur] = open_value.get(cur, 0.0) + val
        weighted[cur] = weighted.get(cur, 0.0) + val * float(stage.probability or 0)
    for stage in sorted(stages.values(), key=lambda s: s.sort_order):
        items = stage_buckets.get(stage.id, [])
        totals: dict[str, float] = {}
        for b in items:
            cur = (b.currency or "AUD").upper()
            totals[cur] = totals.get(cur, 0.0) + float(b.deal_value or 0)
        by_stage.append(
            {
                "stage": {
                    "id": stage.id,
                    "slug": stage.slug,
                    "name": stage.name,
                    "probability": stage.probability,
                },
                "count": len(items),
                "value_by_currency": totals,
            }
        )
    return {
        "product_line": product_line,
        "open_pipeline_value": open_value,
        "weighted_forecast": weighted,
        "weighted_forecast_note": "Estimate only: open deal value × configurable stage probability.",
        "by_stage": by_stage,
        "sample_size": sum(len(v) for v in stage_buckets.values()),
    }


def invoiced_outstanding(db, *, include_test: bool = False) -> dict:
    stmt = select(Invoice).where(Invoice.status == "open")
    if not include_test:
        stmt = stmt.where(Invoice.is_test_mode.is_(False))
    invoices = db.scalars(stmt).all()
    by_currency: dict[str, float] = {}
    for inv in invoices:
        cur = (inv.currency or "AUD").upper()
        by_currency[cur] = by_currency.get(cur, 0.0) + float(inv.total or 0)
    return {
        "definition": "Invoices with status open (unpaid).",
        "by_currency": by_currency,
        "sample_size": len(invoices),
    }


def task_queues(db, day: Optional[datetime] = None) -> dict:
    now = day or _utcnow()
    today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    tomorrow = today + timedelta(days=1)
    week = today + timedelta(days=7)

    open_tasks = db.scalars(select(Task).where(Task.status == "open")).all()

    def aware(dt):
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    overdue, due_today, due_soon = [], [], []
    for t in open_tasks:
        due = aware(t.due_at)
        if not due:
            continue
        if due < today:
            overdue.append(t.id)
        elif due < tomorrow:
            due_today.append(t.id)
        elif due < week:
            due_soon.append(t.id)

    no_next = db.scalars(
        select(Business).where(
            Business.archived.is_(False),
            Business.lost.is_(False),
            (Business.next_action == "") | (Business.next_action.is_(None)),
        )
    ).all()

    to_contact = (
        db.scalar(
            select(func.count())
            .select_from(Business)
            .where(
                Business.archived.is_(False),
                Business.lost.is_(False),
                Business.opt_out.is_(False),
                Business.marked_for_contact.is_(True),
                Business.contacted.is_(False),
            )
        )
        or 0
    )

    # Pipeline Follow-up columns (synced with Contacted +3/+7 passed)
    from app.services.follow_up_reminders import due_follow_up_leads
    from app.services.automation import follow_up_target_slug

    due_leads = due_follow_up_leads(db, limit=2000)
    follow_up_3 = sum(1 for b in due_leads if follow_up_target_slug(b) == "follow_up_3")
    follow_up_7 = sum(1 for b in due_leads if follow_up_target_slug(b) == "follow_up_7")

    from app.services.mail import smtp_configured
    from app.services.follow_up_reminders import email_reminders_enabled, push_reminders_enabled

    smtp_ok = smtp_configured(db=db)
    email_on = email_reminders_enabled(db)
    push_on = push_reminders_enabled(db)
    if email_on and smtp_ok:
        mail_bit = "Email digest on (cron /api/cron/follow-up-reminders)."
    elif email_on:
        mail_bit = "Email digest on — configure SMTP to send."
    else:
        mail_bit = "Email digest off (Settings → Follow-up reminders)."
    push_bit = "Browser push on." if push_on else "Browser push off."
    note = f"In-app bell + Pipeline Follow-up columns. {mail_bit} {push_bit}"

    return {
        "overdue_task_ids": overdue,
        "due_today_task_ids": due_today,
        "due_soon_task_ids": due_soon,
        "no_next_action_count": len(no_next),
        "overdue_count": len(overdue),
        "due_today_count": len(due_today),
        "due_soon_count": len(due_soon),
        "to_contact_count": int(to_contact),
        "contact_queue_count": int(to_contact),
        "follow_up_due_count": len(due_leads),
        "follow_up_3_count": int(follow_up_3),
        "follow_up_7_count": int(follow_up_7),
        "smtp_ready": smtp_ok,
        "follow_up_email_reminders_enabled": email_on,
        "follow_up_push_reminders_enabled": push_on,
        "notifications_note": note,
    }


def _parse_iso_date(raw: Optional[str]):
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.strptime(str(raw)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except Exception:
            return None


def filtered_prospect_stats(db, product_line: str, filters: Optional[dict] = None) -> dict:
    """Prospect / AH commercial counts with optional dashboard filters."""
    from app.models import BeDelivery

    filters = filters or {}
    q = select(Business).where(
        Business.product_line == product_line,
        Business.archived.is_(False),
    )
    if filters.get("country"):
        q = q.where(Business.country.ilike(str(filters["country"]).strip()))
    if filters.get("city"):
        q = q.where(Business.city.ilike(str(filters["city"]).strip()))
    if filters.get("owner_id") is not None:
        try:
            q = q.where(Business.owner_id == int(filters["owner_id"]))
        except Exception:
            pass
    if filters.get("sales_stage"):
        stage = db.scalar(
            select(PipelineStage).where(
                PipelineStage.product_line == product_line,
                PipelineStage.slug == str(filters["sales_stage"]).strip().lower(),
            )
        )
        if stage:
            q = q.where(Business.pipeline_stage_id == stage.id)
    date_from = _parse_iso_date(filters.get("date_from"))
    date_to = _parse_iso_date(filters.get("date_to"))
    if date_from:
        q = q.where(Business.created_at >= date_from)
    if date_to:
        q = q.where(Business.created_at <= date_to)

    prospects = list(db.scalars(q).all())
    ids = [p.id for p in prospects]
    stage_counts: dict[str, int] = {}
    for p in prospects:
        stage = db.get(PipelineStage, p.pipeline_stage_id) if p.pipeline_stage_id else None
        slug = (stage.slug if stage else "unstaged") or "unstaged"
        stage_counts[slug] = stage_counts.get(slug, 0) + 1

    deliveries = []
    if ids:
        deliveries = list(db.scalars(select(BeDelivery).where(BeDelivery.business_id.in_(ids))).all())
    if filters.get("package"):
        pkg = str(filters["package"]).strip().lower()
        deliveries = [
            d
            for d in deliveries
            if pkg in (d.package_name or "").lower() or pkg in (d.revision_id or "").lower()
        ]
    if filters.get("payment_status"):
        want = str(filters["payment_status"]).strip().lower()
        deliveries = [
            d
            for d in deliveries
            if want in ((d.financial_status or d.status or "").lower())
        ]
    if filters.get("delivery_status"):
        want = str(filters["delivery_status"]).strip().lower()
        deliveries = [
            d
            for d in deliveries
            if want in ((d.delivery_status or d.status or "").lower())
        ]

    return {
        "total_leads": len(prospects),
        "by_stage": stage_counts,
        "filtered_delivery_count": len(deliveries),
        "filters_applied": {k: v for k, v in filters.items() if v not in (None, "")},
    }


def dashboard_bundle(db, product_line: str = "STANDALONE", filters: Optional[dict] = None) -> dict:
    pipeline = open_pipeline_and_forecast(db, product_line)
    be_commercial = {}
    try:
        from app.services.booking_engine.commercial import monitor_bundle

        mon = monitor_bundle(db, limit=5)
        be_commercial = {
            "funnel": mon.get("funnel") or {},
            "be_verified_collected": mon.get("be_verified_collected") or {},
            "be_outstanding": mon.get("be_outstanding") or {},
            "event_counts": mon.get("event_counts") or {},
            "connection": {
                "connected": (mon.get("connection") or {}).get("connected"),
                "last_sync_ok_at": (mon.get("connection") or {}).get("last_sync_ok_at"),
                "failed_events": (mon.get("connection") or {}).get("failed_events"),
            },
            "outbound_failed": mon.get("outbound_failed") or 0,
        }
    except Exception:
        be_commercial = {}
    filtered = filtered_prospect_stats(db, product_line, filters)
    return {
        "queues": task_queues(db),
        "pipeline": pipeline,
        "collected": collected_revenue(db),
        "invoiced_outstanding": invoiced_outstanding(db),
        "qualified_count": db.scalar(
            select(func.count()).select_from(Business).where(
                Business.product_line == product_line,
                Business.qualification_score.is_not(None),
                Business.qualification_score >= 5,
                Business.archived.is_(False),
            )
        )
        or 0,
        "appointhive": be_commercial,
        "filtered": filtered,
        "filters": filters or {},
    }
