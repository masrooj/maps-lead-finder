"""Pipeline stage moves, history, and backfill from legacy flags."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Activity, Business, PipelineStage, StageHistory, Task

# timezone used by infer_slug_from_flags for due comparisons


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


def _contacted_like(b: Business) -> bool:
    """Match Contact queue Contacted tab (confirm-sent or legacy contacted flag)."""
    return bool(
        getattr(b, "contacted", False)
        or getattr(b, "email_sent", False)
        or getattr(b, "whatsapp_sent", False)
        or getattr(b, "instagram_sent", False)
    )


def infer_slug_from_flags(b: Business) -> str:
    if b.lost:
        return "lost"
    if _contacted_like(b):
        kind = (getattr(b, "follow_up_kind", None) or "").strip()
        due = getattr(b, "next_follow_up_at", None)
        if due and kind != "call_today":
            due_aware = due if due.tzinfo else due.replace(tzinfo=timezone.utc)
            if due_aware <= _utcnow():
                from app.services.automation import follow_up_target_slug

                return follow_up_target_slug(b)
        return "contacted"
    if getattr(b, "ready_for_contact", False) and b.marked_for_contact:
        return "ready_to_contact"
    if b.marked_for_contact:
        return "to_contact"
    return "identified"


# Early funnel columns mirror Contact queue; later stages keep pipeline_stage_id.
_EARLY_BOARD_SLUGS = frozenset(
    {
        "identified",
        "to_contact",
        "ready_to_contact",
        "contacted",
        "follow_up",
        "follow_up_3",
        "follow_up_7",
    }
)


def board_column_slug(b: Business, stage: Optional[PipelineStage], *, available_slugs: Optional[set[str]] = None) -> str:
    """Column slug for Pipeline kanban — CQ flags for early stages, stage_id after Follow-up."""
    avail = available_slugs or set()
    if stage and stage.slug not in _EARLY_BOARD_SLUGS:
        return stage.slug
    has_booking = bool((getattr(b, "current_booking_software", None) or "").strip())
    # Contact queue parks Has booking off To contact / Ready — keep them off those columns.
    if has_booking and not _contacted_like(b) and not b.lost:
        return "identified"
    slug = infer_slug_from_flags(b)
    # Self-heal when DB still has legacy single follow_up only.
    if slug in ("follow_up_3", "follow_up_7") and slug not in avail and "follow_up" in avail:
        return "follow_up"
    if slug == "follow_up" and slug not in avail and "follow_up_3" in avail:
        return "follow_up_3"
    return slug


def backfill_prospect_stages(db) -> dict:
    """Assign pipeline_stage_id and product_line to existing businesses lacking them.

    Also remaps marked-but-not-ready leads that still sit on ready_to_contact → to_contact.
    """
    all_stages = list(db.scalars(select(PipelineStage)).all())
    stages = {(s.product_line, s.slug): s for s in all_stages}
    updated = 0
    businesses = db.scalars(select(Business)).all()
    for b in businesses:
        changed = False
        pl = (getattr(b, "product_line", None) or "STANDALONE").upper()
        if not getattr(b, "product_line", None):
            b.product_line = "STANDALONE"
            pl = "STANDALONE"
            changed = True
        if not getattr(b, "currency", None):
            from app.config import DEFAULT_CURRENCY

            b.currency = DEFAULT_CURRENCY
            changed = True
        # Split legacy ready_to_contact (was "To contact" UI) into prep vs ready
        cur = stage_by_id(db, b.pipeline_stage_id) if b.pipeline_stage_id else None
        if (
            cur
            and cur.slug == "ready_to_contact"
            and b.marked_for_contact
            and not getattr(b, "ready_for_contact", False)
            and not b.contacted
            and not b.lost
        ):
            to_stage = stages.get((pl, "to_contact")) or stages.get(("STANDALONE", "to_contact"))
            if to_stage:
                b.pipeline_stage_id = to_stage.id
                db.add(
                    StageHistory(
                        business_id=b.id,
                        from_stage_id=cur.id,
                        to_stage_id=to_stage.id,
                        changed_by=None,
                        note="Backfill: marked prep → to_contact",
                    )
                )
                changed = True
        # Retire single follow_up → follow_up_3 / follow_up_7
        if cur and cur.slug == "follow_up" and not b.lost:
            from app.services.automation import follow_up_target_slug

            target_slug = follow_up_target_slug(b)
            to_stage = stages.get((pl, target_slug)) or stages.get(("STANDALONE", target_slug))
            if to_stage:
                b.pipeline_stage_id = to_stage.id
                db.add(
                    StageHistory(
                        business_id=b.id,
                        from_stage_id=cur.id,
                        to_stage_id=to_stage.id,
                        changed_by=None,
                        note=f"Backfill: legacy follow_up → {target_slug}",
                    )
                )
                changed = True
                cur = to_stage
        if not b.pipeline_stage_id:
            slug = infer_slug_from_flags(b)
            stage = stages.get((pl, slug)) or stages.get(("STANDALONE", slug)) or stages.get(("STANDALONE", "identified"))
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
    elif to_stage.slug in (
        "contacted",
        "follow_up",
        "follow_up_3",
        "follow_up_7",
        "engaged",
        "qualified",
        "demo_scheduled",
        "demo_completed",
        "proposal_sent",
        "negotiation",
    ):
        business.contacted = True
        business.lost = False
    elif to_stage.slug == "ready_to_contact":
        from app.services.automation import assert_ready_for_contact, stamp_ig_ready_at

        try:
            assert_ready_for_contact(business)
        except ValueError as e:
            if not force:
                raise HTTPException(422, str(e)) from e
        business.marked_for_contact = True
        business.ready_for_contact = True
        stamp_ig_ready_at(business)
        business.contacted = False
        business.lost = False
    elif to_stage.slug == "to_contact":
        from app.services.automation import clear_ig_ready_at

        business.marked_for_contact = True
        business.ready_for_contact = False
        clear_ig_ready_at(business)
        business.contacted = False
        business.lost = False
    elif to_stage.slug == "identified":
        from app.services.automation import clear_ig_ready_at

        business.contacted = False
        business.lost = False
        business.marked_for_contact = False
        business.ready_for_contact = False
        clear_ig_ready_at(business)

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
