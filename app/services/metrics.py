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
        "notifications_note": "In-app follow-ups only. Email/push reminders are not enabled unless Cron + email provider are configured.",
    }


def dashboard_bundle(db, product_line: str = "STANDALONE") -> dict:
    pipeline = open_pipeline_and_forecast(db, product_line)
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
    }
