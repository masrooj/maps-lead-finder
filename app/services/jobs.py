"""Postgres-backed jobs for Vercel (no Redis)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Invoice, Job, Task


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_job(db, job_type: str, payload: Optional[dict] = None) -> Job:
    job = Job(
        job_type=job_type,
        status="pending",
        cursor_json="{}",
        payload_json=json.dumps(payload or {}),
        error="",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def tick_job(db, job_id: int) -> dict[str, Any]:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status in ("done", "failed"):
        return {"id": job.id, "status": job.status, "done": True}

    try:
        cursor = json.loads(job.cursor_json or "{}")
        payload = json.loads(job.payload_json or "{}")
    except Exception:
        cursor, payload = {}, {}

    job.status = "running"
    job.updated_at = _utcnow()

    if job.job_type == "unpaid_invoice_followups":
        # Create follow-up tasks for expired open invoices missing a task
        now = _utcnow()
        invoices = db.scalars(select(Invoice).where(Invoice.status == "open")).all()
        created = 0
        start = int(cursor.get("offset") or 0)
        batch = invoices[start : start + 25]
        for inv in batch:
            if inv.expires_at:
                exp = inv.expires_at if inv.expires_at.tzinfo else inv.expires_at.replace(tzinfo=timezone.utc)
                if exp > now:
                    continue
            title = f"Follow up unpaid invoice {inv.number}"
            exists = db.scalar(
                select(Task).where(
                    Task.business_id == inv.business_id,
                    Task.status == "open",
                    Task.title == title,
                )
            )
            if exists:
                continue
            db.add(
                Task(
                    business_id=inv.business_id,
                    title=title,
                    description="Cron/job tick: invoice past expiry still open",
                    priority="high",
                    status="open",
                    due_at=now,
                )
            )
            created += 1
        cursor["offset"] = start + len(batch)
        job.cursor_json = json.dumps(cursor)
        if start + len(batch) >= len(invoices):
            job.status = "done"
        job.updated_at = _utcnow()
        db.commit()
        return {"id": job.id, "status": job.status, "created_tasks": created, "done": job.status == "done"}

    if job.job_type == "enrich_batch":
        # Process business_ids in batches of 10 — marks last_verified_at; caller may enrich separately.
        from app.models import Business

        ids = list(payload.get("business_ids") or [])
        start = int(cursor.get("offset") or 0)
        batch = ids[start : start + 10]
        touched = 0
        now = _utcnow()
        for bid in batch:
            b = db.get(Business, int(bid))
            if not b:
                continue
            b.last_verified_at = now
            b.updated_at = now
            touched += 1
        cursor["offset"] = start + len(batch)
        job.cursor_json = json.dumps(cursor)
        if start + len(batch) >= len(ids):
            job.status = "done"
        job.updated_at = _utcnow()
        db.commit()
        return {
            "id": job.id,
            "status": job.status,
            "touched": touched,
            "offset": cursor["offset"],
            "total": len(ids),
            "done": job.status == "done",
        }

    if job.job_type == "noop":
        job.status = "done"
        job.updated_at = _utcnow()
        db.commit()
        return {"id": job.id, "status": "done", "done": True, "payload": payload}

    job.status = "failed"
    job.error = f"Unknown job_type: {job.job_type}"
    job.updated_at = _utcnow()
    db.commit()
    raise HTTPException(422, job.error)
