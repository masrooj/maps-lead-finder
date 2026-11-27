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

    if job.job_type == "maps_booking_verify":
        # v2: Playwright Maps Reserve/Book verify — 1–3 leads per tick.
        from app.models import Business
        from app.services.maps_booking_verify import (
            apply_maps_verify_to_business,
            verify_maps_booking,
        )

        ids = list(payload.get("business_ids") or [])
        if not ids:
            # Resolve fresh-ish leads with Maps URL from country/city
            country = str(payload.get("country") or "").strip()
            city = str(payload.get("city") or "").strip()
            limit = max(1, min(50, int(payload.get("limit") or 25)))
            stmt = select(Business).where(
                Business.google_maps_url.is_not(None),
                Business.google_maps_url != "",
            )
            if country:
                stmt = stmt.where(Business.country == country)
            if city:
                stmt = stmt.where(Business.city == city)
            ids = [int(b.id) for b in db.scalars(stmt.limit(limit)).all()]
            payload["business_ids"] = ids
            job.payload_json = json.dumps(payload)

        start = int(cursor.get("offset") or 0)
        batch = ids[start : start + 2]
        stats = cursor.get("stats") or {
            "parked": 0,
            "to_contact": 0,
            "skipped": 0,
            "errors": 0,
        }
        park_only = bool(payload.get("park_only_on_confirmed", True))
        runner = None
        if payload.get("mock_results") and isinstance(payload["mock_results"], dict):
            mock_map = {str(k): v for k, v in payload["mock_results"].items()}

            def runner(url: str):
                # Match by business in this batch via URL equality fallback
                for bid in batch:
                    b = db.get(Business, int(bid))
                    if b and (b.google_maps_url or "") == url:
                        raw = mock_map.get(str(bid)) or mock_map.get(url)
                        if raw:
                            return raw
                return {"status": "uncertain", "evidence": "mock_miss"}

        for bid in batch:
            b = db.get(Business, int(bid))
            if not b:
                stats["skipped"] = int(stats.get("skipped") or 0) + 1
                continue
            try:
                verify = verify_maps_booking(
                    b.google_maps_url or "",
                    runner=runner,
                    park_only_on_confirmed=park_only,
                )
                action = apply_maps_verify_to_business(db, b, verify)
                if action == "parked":
                    stats["parked"] = int(stats.get("parked") or 0) + 1
                elif action == "to_contact":
                    stats["to_contact"] = int(stats.get("to_contact") or 0) + 1
                else:
                    stats["skipped"] = int(stats.get("skipped") or 0) + 1
            except Exception:
                stats["errors"] = int(stats.get("errors") or 0) + 1
        cursor["offset"] = start + len(batch)
        cursor["stats"] = stats
        job.cursor_json = json.dumps(cursor)
        if start + len(batch) >= len(ids):
            job.status = "done"
        job.updated_at = _utcnow()
        db.commit()
        return {
            "id": job.id,
            "status": job.status,
            "stats": stats,
            "offset": cursor["offset"],
            "total": len(ids),
            "done": job.status == "done",
        }

    if job.job_type == "outreach_batch":
        # v2+: channel + WA promote Ready (IG stays for human warm).
        from app.models import Business
        from app.services.outreach_batch import process_outreach_lead, select_outreach_batch_ids

        ids = list(payload.get("business_ids") or [])
        if not ids:
            ids = select_outreach_batch_ids(
                db,
                country=str(payload.get("country") or ""),
                city=str(payload.get("city") or ""),
                limit=int(payload.get("limit") or 25),
            )
            payload["business_ids"] = ids
            job.payload_json = json.dumps(payload)

        start = int(cursor.get("offset") or 0)
        batch = ids[start : start + 10]
        stats = cursor.get("stats") or {
            "instagram_warm": 0,
            "whatsapp_ready": 0,
            "email_ready": 0,
            "skipped": 0,
        }
        for bid in batch:
            b = db.get(Business, int(bid))
            if not b:
                stats["skipped"] = int(stats.get("skipped") or 0) + 1
                continue
            out = process_outreach_lead(db, b)
            action = out.get("action") or "skipped"
            if action in stats:
                stats[action] = int(stats.get(action) or 0) + 1
            else:
                stats["skipped"] = int(stats.get("skipped") or 0) + 1
        cursor["offset"] = start + len(batch)
        cursor["stats"] = stats
        job.cursor_json = json.dumps(cursor)
        if start + len(batch) >= len(ids):
            job.status = "done"
        job.updated_at = _utcnow()
        db.commit()
        return {
            "id": job.id,
            "status": job.status,
            "stats": stats,
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
