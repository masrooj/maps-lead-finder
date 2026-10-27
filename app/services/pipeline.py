"""Pipeline stage moves, history, and backfill from legacy flags."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Activity, Business, PipelineStage, StageHistory, Task


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_stage(db, product_line: str, slug: str) -> Optional[PipelineStage]:
    return db.scalar(
        select(PipelineStage).where(
            PipelineStage.product_line == product_line,
            PipelineStage.slug == slug,
            PipelineStage.active.is_(True),
        )
    )


def stage_by_id(db, stage_id: int) -> Optional[PipelineStage]:
    return db.get(PipelineStage, stage_id)


def list_stages(db, product_line: str = "STANDALONE") -> list[PipelineStage]:
    return list(
        db.scalars(
            select(PipelineStage)
            .where(PipelineStage.product_line == product_line, PipelineStage.active.is_(True))
            .order_by(PipelineStage.sort_order)
        ).all()
    )


def init_new_prospect(db, b: Business, *, lead_source: str = "google_places") -> None:
    """Set Sales OS defaults on a newly created business row."""
    from app.config import DEFAULT_CURRENCY

    b.product_line = b.product_line or "STANDALONE"
    b.currency = b.currency or DEFAULT_CURRENCY
    b.lead_source = b.lead_source or lead_source
    if not b.date_identified:
        b.date_identified = _utcnow()
    if not b.pipeline_stage_id:
        stage = get_stage(db, b.product_line or "STANDALONE", "identified")
        if stage:
            b.pipeline_stage_id = stage.id


def infer_slug_from_flags(b: Business) -> str:
    if b.lost:
        return "lost"
    if b.contacted:
        if b.next_follow_up_at:
            return "follow_up"
        return "contacted"
    if b.marked_for_contact:
        return "ready_to_contact"
    return "identified"


def backfill_prospect_stages(db) -> dict:
    """Assign pipeline_stage_id and product_line to existing businesses lacking them."""
    stages = {
        ("STANDALONE", s.slug): s
        for s in db.scalars(select(PipelineStage).where(PipelineStage.product_line == "STANDALONE")).all()
    }
    updated = 0
    businesses = db.scalars(select(Business)).all()
    for b in businesses:
        changed = False
        if not getattr(b, "product_line", None):
            b.product_line = "STANDALONE"
            changed = True
        if not getattr(b, "currency", None):
            from app.config import DEFAULT_CURRENCY

            b.currency = DEFAULT_CURRENCY
            changed = True
        if not b.pipeline_stage_id:
            slug = infer_slug_from_flags(b)
            stage = stages.get(("STANDALONE", slug)) or stages.get(("STANDALONE", "identified"))
            if stage:
                b.pipeline_stage_id = stage.id
                db.add(
                    StageHistory(
                        business_id=b.id,
                        from_stage_id=None,
                        to_stage_id=stage.id,
                        changed_by=None,
                        note="Backfill from legacy flags",
                    )
                )
                changed = True
        if b.next_follow_up_at and b.id:
            existing_task = db.scalar(
                select(Task).where(
                    Task.business_id == b.id,
                    Task.status == "open",
                    Task.title == "Follow up",
                )
            )
            if not existing_task:
                db.add(
                    Task(
                        business_id=b.id,
                        title="Follow up",
                        description="Migrated from next_follow_up_at",
                        priority="normal",
                        status="open",
                        due_at=b.next_follow_up_at,
                    )
                )
                changed = True
        if not b.date_identified and b.created_at:
            b.date_identified = b.created_at
            changed = True
        if changed:
            updated += 1
    db.commit()
    return {"updated": updated, "total": len(businesses)}


def move_stage(
    db,
    business: Business,
    to_stage_id: int,
    *,
    actor_id: Optional[int] = None,
    note: str = "",
    lost_reason: str = "",
    force: bool = False,
) -> Business:
    to_stage = stage_by_id(db, to_stage_id)
    if not to_stage:
        raise HTTPException(404, "Stage not found")
    if to_stage.product_line != (business.product_line or "STANDALONE"):
        raise HTTPException(422, "Stage product_line does not match prospect")

    from_id = business.pipeline_stage_id
    from_stage = stage_by_id(db, from_id) if from_id else None

    if to_stage.is_lost and not (lost_reason or business.lost_reason) and not force:
        raise HTTPException(422, "lost_reason is required when moving to Lost")
    if to_stage.slug == "proposal_sent" and not force:
        # Soft rule — allow with force; warn via note
        pass
    if to_stage.slug == "qualified" and business.qualification_score is None and business.score_override is None and not force:
        raise HTTPException(422, "Save a qualification score (or override) before Qualified, or force=true")

    if lost_reason:
        business.lost_reason = lost_reason[:500]

    business.pipeline_stage_id = to_stage.id
    # Keep legacy flags in sync for Discover filters
    if to_stage.is_lost:
        business.lost = True
        business.contacted = True
    elif to_stage.is_won:
        business.lost = False
        business.contacted = True
    elif to_stage.slug in ("contacted", "follow_up", "engaged", "qualified", "demo_scheduled", "demo_completed", "proposal_sent", "negotiation"):
        business.contacted = True
        business.lost = False
    elif to_stage.slug == "ready_to_contact":
        business.marked_for_contact = True

    business.updated_at = _utcnow()
    db.add(
        StageHistory(
            business_id=business.id,
            from_stage_id=from_id,
            to_stage_id=to_stage.id,
            changed_by=actor_id,
            note=note or (f"Moved to {to_stage.name}"),
        )
    )
    db.add(
        Activity(
            business_id=business.id,
            activity_type="stage_change",
            summary=f"{from_stage.name if from_stage else '—'} → {to_stage.name}",
            outcome=note or "",
            next_action=business.next_action or "",
            created_by=actor_id,
        )
    )
    # Never delete tasks on stage change
    db.commit()
    db.refresh(business)
    return business


def stage_dict(s: PipelineStage) -> dict:
    return {
        "id": s.id,
        "product_line": s.product_line,
        "slug": s.slug,
        "name": s.name,
        "sort_order": s.sort_order,
        "probability": s.probability,
        "is_terminal": bool(s.is_terminal),
        "is_won": bool(s.is_won),
        "is_lost": bool(s.is_lost),
    }
