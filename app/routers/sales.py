"""AppointHive Sales OS API routes."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from app.db import SessionLocal
from app.models import (
    Activity,
    AppSetting,
    Business,
    CompliancePack,
    Contact,
    DemoNote,
    DiscoveryNote,
    Invoice,
    Payment,
    Plan,
    PlanFeature,
    PlanPrice,
    Proposal,
    SavedView,
    Suppression,
    Task,
    User,
)
from app.services.billing.service import (
    confirm_manual_payment,
    create_invoice_from_proposal,
    create_proposal,
    provider_status,
)
from app.services.metrics import dashboard_bundle
from app.services.pipeline import backfill_prospect_stages, list_stages, move_stage, stage_dict
from app.services.scoring import apply_score, score_payload

router = APIRouter(prefix="/api/sales", tags=["sales"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# require_login injected from main to avoid circular import at module load
_require_login = None


def bind_auth(require_login_fn):
    global _require_login
    _require_login = require_login_fn


def require_user(request: Request) -> str:
    if not _require_login:
        raise HTTPException(500, "Auth not bound")
    return _require_login(request)


def _iso(dt) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def prospect_sales_dict(b: Business, stage=None) -> dict:
    try:
        criteria = json.loads(b.score_criteria_json or "{}")
    except Exception:
        criteria = {}
    try:
        attachments = json.loads(b.attachment_links_json or "[]")
    except Exception:
        attachments = []
    return {
        "id": b.id,
        "name": b.name,
        "country": b.country,
        "city": b.city,
        "state": getattr(b, "state", "") or "",
        "postcode": getattr(b, "postcode", "") or "",
        "product_line": getattr(b, "product_line", None) or "STANDALONE",
        "pipeline_stage_id": b.pipeline_stage_id,
        "stage": stage_dict(stage) if stage else None,
        "owner_id": b.owner_id,
        "deal_value": b.deal_value,
        "currency": b.currency or "AUD",
        "next_action": b.next_action or "",
        "lead_source": b.lead_source or "",
        "qualification_score": b.qualification_score,
        "score_override": b.score_override,
        "score_band": score_payload(b).get("band") if b.qualification_score is not None or b.score_override is not None else None,
        "opt_out": bool(b.opt_out),
        "lost_reason": b.lost_reason or "",
        "linked_prospect_id": b.linked_prospect_id,
        "plan_id": b.plan_id,
        "attachment_links": attachments,
        "main_pain": b.main_pain or "",
        "objections": b.objections or "",
        "estimated_close_date": _iso(b.estimated_close_date),
        "score_criteria": criteria,
        "be_installation_id": getattr(b, "be_installation_id", "") or "",
    }


class StageMoveBody(BaseModel):
    stage_id: int
    note: str = ""
    lost_reason: str = ""
    force: bool = False


class ScoreBody(BaseModel):
    business_fit: int = Field(ge=0, le=2)
    operational_pain: int = Field(ge=0, le=2)
    buying_urgency: int = Field(ge=0, le=2)
    decision_maker_access: int = Field(ge=0, le=2)
    product_fit: int = Field(ge=0, le=2)
    override: Optional[int] = Field(default=None, ge=0, le=10)
    override_reason: str = ""


class TaskBody(BaseModel):
    business_id: Optional[int] = None
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    priority: str = "normal"
    due_at: Optional[str] = None


class ActivityBody(BaseModel):
    business_id: int
    activity_type: str
    summary: str = ""
    outcome: str = ""
    next_action: str = ""
    channel: str = ""
    contact_id: Optional[int] = None


class ProspectPatch(BaseModel):
    product_line: Optional[str] = None
    deal_value: Optional[float] = None
    currency: Optional[str] = None
    next_action: Optional[str] = None
    lead_source: Optional[str] = None
    state: Optional[str] = None
    postcode: Optional[str] = None
    main_pain: Optional[str] = None
    buying_trigger: Optional[str] = None
    objections: Optional[str] = None
    lost_reason: Optional[str] = None
    opt_out: Optional[bool] = None
    opt_out_channels: Optional[str] = None
    owner_id: Optional[int] = None
    plan_id: Optional[int] = None
    linked_prospect_id: Optional[int] = None
    estimated_staff: Optional[int] = None
    locations_count: Optional[int] = None
    current_booking_software: Optional[str] = None
    attachment_links: Optional[list[str]] = None
    tax_id: Optional[str] = None
    billing_entity_name: Optional[str] = None
    billing_address: Optional[str] = None
    mrr: Optional[float] = None
    trial_starts_at: Optional[str] = None
    trial_ends_at: Optional[str] = None
    saas_plan_code: Optional[str] = None


class ProposalBody(BaseModel):
    business_id: int
    title: str = ""
    currency: str = "AUD"
    plan_id: Optional[int] = None
    line_items: list[dict[str, Any]] = Field(default_factory=list)
    discount_amount: float = 0
    discount_reason: str = ""
    tax_rate: float = 0
    tax_label: str = ""
    terms_url: str = ""
    expires_days: int = 30


class InvoiceBody(BaseModel):
    proposal_id: int
    provider: str = "manual"


class ConfirmPayBody(BaseModel):
    provider_ref: str = ""


def _parse_dt(value: Optional[str]):
    if not value:
        return None
    try:
        if len(value) == 10:
            d = datetime.fromisoformat(value)
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(422, f"Invalid datetime: {e}") from e


@router.get("/dashboard")
def sales_dashboard(product_line: str = "STANDALONE", _user: str = Depends(require_user), db=Depends(get_db)):
    return dashboard_bundle(db, product_line.upper())


@router.get("/providers")
def sales_providers(_user: str = Depends(require_user)):
    return provider_status()


@router.get("/stages")
def sales_stages(product_line: str = "STANDALONE", _user: str = Depends(require_user), db=Depends(get_db)):
    return {"stages": [stage_dict(s) for s in list_stages(db, product_line.upper())]}


@router.post("/backfill")
def sales_backfill(_user: str = Depends(require_user), db=Depends(get_db)):
    return backfill_prospect_stages(db)


@router.get("/pipeline")
def sales_pipeline(product_line: str = "STANDALONE", _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import can_view_business, get_user_by_email

    stages = list_stages(db, product_line.upper())
    stage_map = {s.id: s for s in stages}
    actor = get_user_by_email(db, _user)
    prospects = [
        p
        for p in db.scalars(
            select(Business).where(
                Business.product_line == product_line.upper(),
                or_(Business.archived.is_(False), Business.archived.is_(None)),
            )
        ).all()
        if can_view_business(actor, p)
    ]
    columns = []
    for s in stages:
        items = [p for p in prospects if p.pipeline_stage_id == s.id]
        value = {}
        for p in items:
            cur = (p.currency or "AUD").upper()
            value[cur] = value.get(cur, 0.0) + float(p.deal_value or 0)
        columns.append(
            {
                "stage": stage_dict(s),
                "count": len(items),
                "value_by_currency": value,
                "prospects": [prospect_sales_dict(p, s) for p in items],
            }
        )
    unstaged = [p for p in prospects if not p.pipeline_stage_id]
    return {"product_line": product_line.upper(), "columns": columns, "unstaged": [prospect_sales_dict(p) for p in unstaged]}


@router.post("/prospects/{business_id}/stage")
def sales_move_stage(business_id: int, body: StageMoveBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.models import PipelineStage

    b = db.get(Business, business_id)
    if not b:
        raise HTTPException(404, "Prospect not found")
    owner = db.scalar(select(User).where(User.email == _user.lower()))
    move_stage(
        db,
        b,
        body.stage_id,
        actor_id=owner.id if owner else None,
        note=body.note,
        lost_reason=body.lost_reason,
        force=body.force,
    )
    stage = db.get(PipelineStage, b.pipeline_stage_id)
    return prospect_sales_dict(b, stage)


@router.post("/prospects/{business_id}/undo-stage")
def sales_undo_stage(business_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    """Revert to previous pipeline stage using StageHistory (auto or manual)."""
    from app.models import PipelineStage, StageHistory

    b = db.get(Business, business_id)
    if not b:
        raise HTTPException(404, "Prospect not found")
    last = db.scalar(
        select(StageHistory)
        .where(StageHistory.business_id == business_id)
        .order_by(StageHistory.id.desc())
        .limit(1)
    )
    if not last or not last.from_stage_id:
        raise HTTPException(422, "No previous stage to undo")
    actor = db.scalar(select(User).where(User.email == _user.lower()))
    move_stage(
        db,
        b,
        last.from_stage_id,
        actor_id=actor.id if actor else None,
        note="Undo last stage move",
        force=True,
    )
    stage = db.get(PipelineStage, b.pipeline_stage_id)
    return {"ok": True, "prospect": prospect_sales_dict(b, stage)}


@router.post("/prospects/{business_id}/link")
def sales_link_prospect(business_id: int, payload: dict[str, Any], _user: str = Depends(require_user), db=Depends(get_db)):
    """Bidirectional STANDALONE ↔ SAAS (or any) prospect link."""
    other_id = int(payload.get("linked_prospect_id") or 0)
    if not other_id or other_id == business_id:
        raise HTTPException(422, "linked_prospect_id required")
    a = db.get(Business, business_id)
    other = db.get(Business, other_id)
    if not a or not other:
        raise HTTPException(404, "Prospect not found")
    a.linked_prospect_id = other.id
    other.linked_prospect_id = a.id
    db.commit()
    return {"ok": True, "a": a.id, "b": other.id}


@router.post("/duplicates/resolve")
def sales_resolve_duplicate(payload: dict[str, Any], _user: str = Depends(require_user), db=Depends(get_db)):
    """CSV soft-duplicate actions: merge | link | ignore."""
    from app.services.authz import get_user_by_email, require_role

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner", "sales")
    action = str(payload.get("action") or "").lower()
    primary_id = int(payload.get("primary_id") or 0)
    secondary_id = int(payload.get("secondary_id") or 0)
    if action not in ("merge", "link", "ignore") or not primary_id or not secondary_id:
        raise HTTPException(422, "action + primary_id + secondary_id required")
    primary = db.get(Business, primary_id)
    secondary = db.get(Business, secondary_id)
    if not primary or not secondary:
        raise HTTPException(404, "Prospect not found")
    if action == "ignore":
        db.add(
            Activity(
                business_id=primary_id,
                activity_type="duplicate_ignored",
                summary=f"Ignored duplicate #{secondary_id}",
                created_by=actor.id if actor else None,
            )
        )
        db.commit()
        return {"ok": True, "action": "ignore"}
    if action == "link":
        primary.linked_prospect_id = secondary.id
        secondary.linked_prospect_id = primary.id
        db.commit()
        return {"ok": True, "action": "link"}
    # merge: fill blanks on primary from secondary, archive secondary
    for field in (
        "email",
        "phone",
        "international_phone",
        "website",
        "address",
        "notes",
        "next_action",
    ):
        if not (getattr(primary, field) or "").strip() and (getattr(secondary, field) or "").strip():
            setattr(primary, field, getattr(secondary, field))
    if primary.deal_value is None and secondary.deal_value is not None:
        primary.deal_value = secondary.deal_value
    secondary.archived = True
    primary.linked_prospect_id = secondary.id
    secondary.linked_prospect_id = primary.id
    db.add(
        Activity(
            business_id=primary_id,
            activity_type="duplicate_merged",
            summary=f"Merged from #{secondary_id} (archived)",
            created_by=actor.id if actor else None,
        )
    )
    db.commit()
    return {"ok": True, "action": "merge", "primary_id": primary_id, "archived_secondary": secondary_id}


@router.patch("/prospects/{business_id}")
def sales_patch_prospect(business_id: int, body: ProspectPatch, _user: str = Depends(require_user), db=Depends(get_db)):
    b = db.get(Business, business_id)
    if not b:
        raise HTTPException(404, "Prospect not found")
    data = body.model_dump(exclude_unset=True)
    if "attachment_links" in data:
        b.attachment_links_json = json.dumps(data.pop("attachment_links") or [])
    if "product_line" in data and data["product_line"]:
        pl = data["product_line"].upper()
        if pl not in ("STANDALONE", "SAAS"):
            raise HTTPException(422, "product_line must be STANDALONE or SAAS")
        data["product_line"] = pl
    for dt_key in ("trial_starts_at", "trial_ends_at"):
        if dt_key in data:
            data[dt_key] = _parse_dt(data[dt_key])
    for k, v in data.items():
        setattr(b, k, v)
    b.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(b)
    from app.models import PipelineStage

    stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
    return prospect_sales_dict(b, stage)


@router.post("/prospects/{business_id}/score")
def sales_score(business_id: int, body: ScoreBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email
    from app.services.automation import on_score_saved
    from app.services.scoring import CRITERIA

    b = db.get(Business, business_id)
    if not b:
        raise HTTPException(404, "Prospect not found")
    payload = apply_score(
        b,
        body.model_dump(include=set(CRITERIA)),
        override=body.override,
        override_reason=body.override_reason,
    )
    actor = get_user_by_email(db, _user)
    on_score_saved(db, b, actor_id=actor.id if actor else None)
    db.commit()
    return payload


@router.get("/prospects/{business_id}/score")
def sales_get_score(business_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    b = db.get(Business, business_id)
    if not b:
        raise HTTPException(404, "Prospect not found")
    return score_payload(b)


@router.get("/tasks")
def sales_tasks(view: str = "open", _user: str = Depends(require_user), db=Depends(get_db)):
    stmt = select(Task).order_by(Task.due_at.asc(), Task.id.desc())
    if view != "all":
        stmt = stmt.where(Task.status == "open")
    tasks = db.scalars(stmt).all()
    now = datetime.now(timezone.utc)
    today = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

    def ser(t: Task) -> dict:
        due = t.due_at
        if due and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        overdue = bool(due and t.status == "open" and due < today)
        return {
            "id": t.id,
            "business_id": t.business_id,
            "title": t.title,
            "description": t.description or "",
            "priority": t.priority,
            "status": t.status,
            "due_at": _iso(t.due_at),
            "completed_at": _iso(t.completed_at),
            "overdue": overdue,
        }

    return {"tasks": [ser(t) for t in tasks]}


@router.post("/tasks")
def sales_create_task(body: TaskBody, _user: str = Depends(require_user), db=Depends(get_db)):
    owner = db.scalar(select(User).where(User.email == _user.lower()))
    t = Task(
        business_id=body.business_id,
        title=body.title.strip(),
        description=body.description or "",
        priority=body.priority or "normal",
        status="open",
        due_at=_parse_dt(body.due_at),
        owner_id=owner.id if owner else None,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return {"id": t.id, "title": t.title, "due_at": _iso(t.due_at), "status": t.status}


@router.post("/tasks/{task_id}/complete")
def sales_complete_task(task_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "Task not found")
    t.status = "done"
    t.completed_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True, "id": t.id}


class RescheduleBody(BaseModel):
    due_at: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None


@router.post("/tasks/{task_id}/reschedule")
def sales_reschedule_task(
    task_id: int,
    body: Optional[RescheduleBody] = None,
    due_at: str = "",
    _user: str = Depends(require_user),
    db=Depends(get_db),
):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "Task not found")
    new_due = (body.due_at if body and body.due_at is not None else due_at) or None
    if new_due is not None:
        t.due_at = _parse_dt(new_due) if new_due else None
    if body:
        if body.title is not None and body.title.strip():
            t.title = body.title.strip()[:300]
        if body.description is not None:
            t.description = body.description
        if body.priority:
            t.priority = body.priority
    t.status = "open"
    t.completed_at = None
    db.commit()
    return {"ok": True, "id": t.id, "due_at": _iso(t.due_at), "title": t.title}


@router.delete("/tasks/{task_id}")
def sales_delete_task(task_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "Task not found")
    db.delete(t)
    db.commit()
    return {"ok": True, "deleted": task_id}


@router.delete("/activities/{activity_id}")
def sales_delete_activity(activity_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    a = db.get(Activity, activity_id)
    if not a:
        raise HTTPException(404, "Activity not found")
    db.delete(a)
    db.commit()
    return {"ok": True, "deleted": activity_id}


@router.delete("/demos/{demo_id}")
def sales_delete_demo(demo_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    d = db.get(DemoNote, demo_id)
    if not d:
        raise HTTPException(404, "Demo not found")
    db.delete(d)
    db.commit()
    return {"ok": True, "deleted": demo_id}


@router.get("/activities")
def sales_activities(business_id: Optional[int] = None, limit: int = 50, _user: str = Depends(require_user), db=Depends(get_db)):
    stmt = select(Activity).order_by(Activity.created_at.desc()).limit(min(limit, 200))
    if business_id:
        stmt = stmt.where(Activity.business_id == business_id)
    rows = db.scalars(stmt).all()
    return {
        "activities": [
            {
                "id": a.id,
                "business_id": a.business_id,
                "activity_type": a.activity_type,
                "summary": a.summary,
                "outcome": a.outcome,
                "next_action": a.next_action,
                "channel": a.channel,
                "created_at": _iso(a.created_at),
            }
            for a in rows
        ]
    }


@router.post("/activities")
def sales_add_activity(body: ActivityBody, _user: str = Depends(require_user), db=Depends(get_db)):
    if not db.get(Business, body.business_id):
        raise HTTPException(404, "Prospect not found")
    owner = db.scalar(select(User).where(User.email == _user.lower()))
    a = Activity(
        business_id=body.business_id,
        contact_id=body.contact_id,
        activity_type=body.activity_type,
        summary=body.summary or "",
        outcome=body.outcome or "",
        next_action=body.next_action or "",
        channel=body.channel or "",
        created_by=owner.id if owner else None,
    )
    db.add(a)
    b = db.get(Business, body.business_id)
    if body.next_action:
        b.next_action = body.next_action[:500]
    db.commit()
    db.refresh(a)
    return {"id": a.id}


@router.get("/plans")
def sales_plans(product_line: str = "STANDALONE", _user: str = Depends(require_user), db=Depends(get_db)):
    plans = db.scalars(
        select(Plan).where(Plan.product_line == product_line.upper(), Plan.active.is_(True)).order_by(Plan.sort_order)
    ).all()
    out = []
    for p in plans:
        prices = db.scalars(select(PlanPrice).where(PlanPrice.plan_id == p.id, PlanPrice.valid_to.is_(None))).all()
        feats = db.scalars(select(PlanFeature).where(PlanFeature.plan_id == p.id)).all()
        out.append(
            {
                "id": p.id,
                "code": p.code,
                "name": p.name,
                "description": p.description,
                "billing_period": p.billing_period,
                "prices": [{"currency": x.currency, "amount": x.amount} for x in prices],
                "features": [
                    {"key": f.feature_key, "label": f.label, "included": f.included, "limit": f.limit_value}
                    for f in feats
                ],
            }
        )
    return {"plans": out}


@router.post("/proposals")
def sales_create_proposal(body: ProposalBody, _user: str = Depends(require_user), db=Depends(get_db)):
    b = db.get(Business, body.business_id)
    if not b:
        raise HTTPException(404, "Prospect not found")
    items = body.line_items
    if body.plan_id and not items:
        plan = db.get(Plan, body.plan_id)
        price = db.scalar(
            select(PlanPrice).where(
                PlanPrice.plan_id == body.plan_id,
                PlanPrice.currency == body.currency.upper(),
                PlanPrice.valid_to.is_(None),
            )
        )
        if not plan or not price:
            raise HTTPException(422, "Plan/price not found for currency")
        items = [{"name": plan.name, "qty": 1, "amount": price.amount}]
    prop = create_proposal(
        db,
        b,
        title=body.title,
        currency=body.currency,
        line_items=items,
        discount_amount=body.discount_amount,
        discount_reason=body.discount_reason,
        tax_rate=body.tax_rate,
        tax_label=body.tax_label,
        plan_id=body.plan_id,
        terms_url=body.terms_url,
        expires_days=body.expires_days,
    )
    return {
        "id": prop.id,
        "reference": prop.reference,
        "status": prop.status,
        "total": prop.total,
        "currency": prop.currency,
        "expires_at": _iso(prop.expires_at),
    }


@router.get("/proposals")
def sales_list_proposals(business_id: Optional[int] = None, _user: str = Depends(require_user), db=Depends(get_db)):
    stmt = select(Proposal).order_by(Proposal.id.desc())
    if business_id:
        stmt = stmt.where(Proposal.business_id == business_id)
    rows = db.scalars(stmt.limit(100)).all()
    return {
        "proposals": [
            {
                "id": p.id,
                "business_id": p.business_id,
                "reference": p.reference,
                "title": p.title,
                "status": p.status,
                "total": p.total,
                "currency": p.currency,
                "expires_at": _iso(p.expires_at),
            }
            for p in rows
        ]
    }


@router.post("/proposals/{proposal_id}/send")
def sales_send_proposal(proposal_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email
    from app.services.automation import on_proposal_sent

    p = db.get(Proposal, proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    p.status = "sent"
    p.sent_at = datetime.now(timezone.utc)
    db.add(
        Activity(
            business_id=p.business_id,
            activity_type="proposal_sent",
            summary=f"Proposal {p.reference} sent",
            outcome=f"{p.total} {p.currency}",
        )
    )
    b = db.get(Business, p.business_id)
    actor = get_user_by_email(db, _user)
    if b:
        on_proposal_sent(db, b, actor_id=actor.id if actor else None)
    db.commit()
    return {"ok": True, "status": p.status}


@router.post("/invoices")
def sales_create_invoice(body: InvoiceBody, _user: str = Depends(require_user), db=Depends(get_db)):
    prop = db.get(Proposal, body.proposal_id)
    if not prop:
        raise HTTPException(404, "Proposal not found")
    # When AppointHive bridge is connected, STANDALONE product cash must use BE pay URL — not local PSP.
    biz = db.get(Business, prop.business_id)
    if biz and (biz.product_line or "STANDALONE") == "STANDALONE":
        from app.services.booking_engine.connect import be_connection_public

        conn = be_connection_public(db=db)
        if conn.get("connected"):
            raise HTTPException(
                409,
                detail={
                    "error": "AppointHive connected — use Send to delivery for STANDALONE product payment",
                    "use": "/api/sales/be/send-to-delivery",
                },
            )
    owner = db.scalar(select(User).where(User.email == _user.lower()))
    inv = create_invoice_from_proposal(db, prop, provider=body.provider, actor_id=owner.id if owner else None)
    return {
        "id": inv.id,
        "number": inv.number,
        "status": inv.status,
        "provider": inv.provider,
        "hosted_url": inv.hosted_url,
        "pay_instructions": inv.pay_instructions,
        "total": inv.total,
        "currency": inv.currency,
        "is_test_mode": inv.is_test_mode,
        "expires_at": _iso(inv.expires_at),
    }


@router.get("/invoices")
def sales_list_invoices(_user: str = Depends(require_user), db=Depends(get_db)):
    rows = db.scalars(select(Invoice).order_by(Invoice.id.desc()).limit(100)).all()
    return {
        "invoices": [
            {
                "id": i.id,
                "number": i.number,
                "business_id": i.business_id,
                "status": i.status,
                "provider": i.provider,
                "total": i.total,
                "currency": i.currency,
                "hosted_url": i.hosted_url,
                "pay_instructions": i.pay_instructions,
                "is_test_mode": i.is_test_mode,
                "expires_at": _iso(i.expires_at),
                "paid_at": _iso(i.paid_at),
            }
            for i in rows
        ]
    }


@router.post("/invoices/{invoice_id}/confirm-paid")
def sales_confirm_paid(invoice_id: int, body: ConfirmPayBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_owner_for_money

    owner = get_user_by_email(db, _user)
    require_owner_for_money(owner)
    pay = confirm_manual_payment(db, invoice_id, actor_id=owner.id if owner else None, provider_ref=body.provider_ref)
    return {
        "payment_id": pay.id,
        "amount": pay.amount,
        "currency": pay.currency,
        "status": pay.status,
        "is_test_mode": pay.is_test_mode,
    }


@router.get("/payments")
def sales_payments(_user: str = Depends(require_user), db=Depends(get_db)):
    rows = db.scalars(select(Payment).order_by(Payment.id.desc()).limit(200)).all()
    return {
        "payments": [
            {
                "id": p.id,
                "business_id": p.business_id,
                "invoice_id": p.invoice_id,
                "amount": p.amount,
                "currency": p.currency,
                "status": p.status,
                "provider": p.provider,
                "paid_at": _iso(p.paid_at),
                "is_test_mode": p.is_test_mode,
            }
            for p in rows
        ]
    }


@router.get("/payments/export.csv")
def sales_payments_export(_user: str = Depends(require_user), db=Depends(get_db)):
    import csv
    import io

    from fastapi.responses import StreamingResponse

    rows = db.scalars(select(Payment).order_by(Payment.id.asc())).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "business_id", "invoice_id", "amount", "currency", "status", "provider", "paid_at", "is_test_mode"])
    for p in rows:
        w.writerow([p.id, p.business_id, p.invoice_id, p.amount, p.currency, p.status, p.provider, _iso(p.paid_at), p.is_test_mode])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=payments.csv"})


@router.get("/compliance/{country_code}")
def sales_compliance(country_code: str, _user: str = Depends(require_user), db=Depends(get_db)):
    pack = db.scalar(select(CompliancePack).where(CompliancePack.country_code == country_code.upper()))
    if not pack:
        return {
            "country_code": country_code.upper(),
            "title": "No pack yet",
            "bullets_md": "Add a compliance pack in Settings. Not legal advice.",
            "enforce": False,
            "counsel_reviewed": False,
        }
    return {
        "country_code": pack.country_code,
        "title": pack.title,
        "bullets_md": pack.bullets_md,
        "required_fields": json.loads(pack.required_fields_json or "[]"),
        "enforce": pack.enforce,
        "counsel_reviewed": bool(pack.counsel_reviewed_at),
        "disclaimer": "Not legal advice. Confirm with counsel before enforce mode.",
    }


@router.get("/suppressions")
def sales_suppressions(_user: str = Depends(require_user), db=Depends(get_db)):
    rows = db.scalars(select(Suppression).order_by(Suppression.id.desc()).limit(500)).all()
    return {"suppressions": [{"id": s.id, "kind": s.kind, "value": s.value, "reason": s.reason} for s in rows]}


@router.post("/suppressions")
def sales_add_suppression(kind: str, value: str, reason: str = "", _user: str = Depends(require_user), db=Depends(get_db)):
    if kind not in ("email", "phone", "domain"):
        raise HTTPException(422, "kind must be email|phone|domain")
    s = Suppression(kind=kind, value=value.strip().lower(), reason=reason or "")
    db.add(s)
    db.commit()
    return {"id": s.id}


@router.get("/settings")
def sales_settings(_user: str = Depends(require_user), db=Depends(get_db)):
    rows = db.scalars(select(AppSetting)).all()
    return {"settings": {r.key: r.value for r in rows}, "providers": provider_status()}


@router.put("/settings/{key}")
def sales_put_setting(key: str, value: str, _user: str = Depends(require_user), db=Depends(get_db)):
    owner = db.scalar(select(User).where(User.email == _user.lower()))
    if owner and owner.role != "owner":
        raise HTTPException(403, "Only Owner can change settings")
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if not row:
        row = AppSetting(key=key, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()
    return {"key": key, "value": value}


@router.get("/search")
def sales_search(q: str, _user: str = Depends(require_user), db=Depends(get_db)):
    like = f"%{q.strip()}%"
    if len(q.strip()) < 2:
        return {"prospects": [], "tasks": [], "invoices": []}
    prospects = db.scalars(
        select(Business).where(or_(Business.name.ilike(like), Business.email.ilike(like), Business.city.ilike(like))).limit(20)
    ).all()
    tasks = db.scalars(select(Task).where(Task.title.ilike(like)).limit(10)).all()
    invoices = db.scalars(select(Invoice).where(Invoice.number.ilike(like)).limit(10)).all()
    return {
        "prospects": [{"id": b.id, "name": b.name, "city": b.city} for b in prospects],
        "tasks": [{"id": t.id, "title": t.title, "business_id": t.business_id} for t in tasks],
        "invoices": [{"id": i.id, "number": i.number, "status": i.status} for i in invoices],
    }


@router.post("/prospects/{business_id}/discovery")
def sales_discovery(business_id: int, payload: dict[str, Any], _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email
    from app.services.automation import on_discovery_saved

    b = db.get(Business, business_id)
    if not b:
        raise HTTPException(404, "Prospect not found")
    note = DiscoveryNote(business_id=business_id)
    for field in (
        "existing_system",
        "current_workflow",
        "painful_issue",
        "frequency_impact",
        "desired_outcome",
        "buying_authority",
        "purchase_timing",
        "switching_concerns",
        "required_capabilities",
        "call_notes",
        "next_action",
    ):
        if field in payload:
            setattr(note, field, str(payload[field] or ""))
    db.add(note)
    actor = get_user_by_email(db, _user)
    on_discovery_saved(
        db,
        b,
        actor_id=actor.id if actor else None,
        painful_issue=str(payload.get("painful_issue") or ""),
    )
    db.commit()
    return {"id": note.id, "reranked": True}


@router.post("/prospects/{business_id}/demo")
def sales_demo(business_id: int, payload: dict[str, Any], _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email
    from app.services.automation import on_demo_saved

    b = db.get(Business, business_id)
    if not b:
        raise HTTPException(404, "Prospect not found")
    note = DemoNote(business_id=business_id, demo_at=_parse_dt(payload.get("demo_at")))
    for field in (
        "outcome",
        "features_demonstrated",
        "prospect_reaction",
        "unresolved_concerns",
        "product_gaps",
        "agreed_next_step",
        "call_notes",
    ):
        if field in payload:
            setattr(note, field, str(payload[field] or ""))
    db.add(note)
    actor = get_user_by_email(db, _user)
    on_demo_saved(db, b, actor_id=actor.id if actor else None, outcome=str(payload.get("outcome") or ""))
    db.commit()
    return {"id": note.id}


@router.get("/contacts/{business_id}")
def sales_contacts(business_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    rows = db.scalars(select(Contact).where(Contact.business_id == business_id)).all()
    return {
        "contacts": [
            {
                "id": c.id,
                "name": c.name,
                "role": c.role,
                "email": c.email,
                "phone": c.phone,
                "preferred_channel": c.preferred_channel,
                "verified": c.verified,
            }
            for c in rows
        ]
    }


@router.post("/contacts")
def sales_add_contact(payload: dict[str, Any], _user: str = Depends(require_user), db=Depends(get_db)):
    bid = int(payload.get("business_id") or 0)
    if not db.get(Business, bid):
        raise HTTPException(404, "Prospect not found")
    c = Contact(
        business_id=bid,
        name=str(payload.get("name") or "")[:200],
        role=str(payload.get("role") or "")[:120],
        email=str(payload.get("email") or "")[:320],
        phone=str(payload.get("phone") or "")[:100],
        preferred_channel=str(payload.get("preferred_channel") or "")[:40],
        verified=bool(payload.get("verified")),
        notes=str(payload.get("notes") or ""),
    )
    db.add(c)
    db.commit()
    return {"id": c.id}


class AcceptProposalBody(BaseModel):
    terms_accepted: bool = True


class RefundBody(BaseModel):
    amount: Optional[float] = None
    reason: str = ""


@router.post("/proposals/{proposal_id}/accept")
def sales_accept_proposal(proposal_id: int, body: AcceptProposalBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.pipeline import get_stage, move_stage
    from app.services.authz import get_user_by_email

    p = db.get(Proposal, proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    if not body.terms_accepted:
        raise HTTPException(422, "terms_accepted must be true")
    p.status = "accepted"
    p.accepted_at = datetime.now(timezone.utc)
    p.terms_accepted = True
    db.add(
        Activity(
            business_id=p.business_id,
            activity_type="proposal_accepted",
            summary=f"Proposal {p.reference} accepted",
            outcome=f"{p.total} {p.currency}",
        )
    )
    # Only auto-won on acceptance if setting says so
    setting = db.scalar(select(AppSetting).where(AppSetting.key == "won_on"))
    won_on = (setting.value if setting else "first_payment") or "first_payment"
    if won_on == "acceptance":
        b = db.get(Business, p.business_id)
        actor = get_user_by_email(db, _user)
        if b:
            won = get_stage(db, b.product_line or "STANDALONE", "won")
            if not won and (b.product_line or "") == "SAAS":
                won = get_stage(db, "SAAS", "subscribed")
            if won:
                move_stage(db, b, won.id, actor_id=actor.id if actor else None, note="Won on proposal acceptance", force=True)
                return {"ok": True, "status": p.status, "won": True}
    db.commit()
    return {"ok": True, "status": p.status, "won": False}


@router.post("/payments/{payment_id}/refund")
def sales_refund(payment_id: int, body: RefundBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_owner_for_money
    from app.services.billing.service import record_refund

    owner = get_user_by_email(db, _user)
    require_owner_for_money(owner)
    refund = record_refund(
        db,
        payment_id,
        amount=body.amount,
        actor_id=owner.id if owner else None,
        reason=body.reason,
    )
    return {
        "payment_id": refund.id,
        "amount": refund.amount,
        "currency": refund.currency,
        "status": refund.status,
    }


@router.get("/prospects")
def sales_prospects(
    q: str = "",
    product_line: str = "",
    country: str = "",
    city: str = "",
    limit: int = 500,
    _user: str = Depends(require_user),
    db=Depends(get_db),
):
    from app.services.authz import can_view_business, get_user_by_email
    from app.models import PipelineStage

    actor = get_user_by_email(db, _user)
    stmt = select(Business).where(or_(Business.archived.is_(False), Business.archived.is_(None)))
    if product_line:
        stmt = stmt.where(Business.product_line == product_line.upper())
    if country:
        stmt = stmt.where(Business.country.ilike(country))
    if city:
        stmt = stmt.where(Business.city.ilike(city))
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Business.name.ilike(like), Business.email.ilike(like), Business.city.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = [
        b
        for b in db.scalars(stmt.order_by(Business.updated_at.desc()).limit(min(limit, 2000))).all()
        if can_view_business(actor, b)
    ]
    out = []
    for b in rows:
        stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        out.append(prospect_sales_dict(b, stage))
    return {"prospects": out, "sample_size": len(out), "total": int(total)}


@router.get("/demos")
def sales_demos(limit: int = 50, _user: str = Depends(require_user), db=Depends(get_db)):
    rows = db.scalars(select(DemoNote).order_by(DemoNote.id.desc()).limit(min(limit, 200))).all()
    return {
        "demos": [
            {
                "id": d.id,
                "business_id": d.business_id,
                "demo_at": _iso(d.demo_at),
                "outcome": d.outcome,
                "agreed_next_step": d.agreed_next_step,
                "created_at": _iso(d.created_at),
            }
            for d in rows
        ]
    }


def _saas_mrr_by_currency(db) -> dict[str, float]:
    out: dict[str, float] = {}
    for b in db.scalars(select(Business).where(Business.product_line == "SAAS", Business.mrr.is_not(None))).all():
        cur = (b.currency or "AUD").upper()
        out[cur] = round(out.get(cur, 0.0) + float(b.mrr or 0), 2)
    return out


@router.get("/reports/summary")
def sales_reports(product_line: str = "STANDALONE", _user: str = Depends(require_user), db=Depends(get_db)):
    from app.models import PipelineStage
    from app.services.metrics import collected_revenue, invoiced_outstanding, open_pipeline_and_forecast

    pl = product_line.upper()
    pipeline = open_pipeline_and_forecast(db, pl)
    lost = db.scalars(
        select(Business).where(Business.product_line == pl, Business.lost.is_(True))
    ).all()
    reasons: dict[str, int] = {}
    for b in lost:
        key = (b.lost_reason or b.outcome or "unspecified").strip() or "unspecified"
        reasons[key] = reasons.get(key, 0) + 1
    sources: dict[str, int] = {}
    score_bands = {"0-4": 0, "5-6": 0, "7-8": 0, "9-10": 0, "unscored": 0}
    funnel: list[dict[str, Any]] = []
    stages = list_stages(db, pl)
    for s in stages:
        n = db.scalar(select(func.count()).select_from(Business).where(Business.pipeline_stage_id == s.id)) or 0
        funnel.append({"slug": s.slug, "name": s.name, "count": int(n), "sort_order": s.sort_order})
    for b in db.scalars(select(Business).where(Business.product_line == pl)).all():
        src = (b.lead_source or "unknown").strip() or "unknown"
        sources[src] = sources.get(src, 0) + 1
        sc = b.score_override if b.score_override is not None else b.qualification_score
        if sc is None:
            score_bands["unscored"] += 1
        else:
            v = float(sc)
            if v < 5:
                score_bands["0-4"] += 1
            elif v < 7:
                score_bands["5-6"] += 1
            elif v < 9:
                score_bands["7-8"] += 1
            else:
                score_bands["9-10"] += 1
    won_stage = db.scalar(
        select(PipelineStage).where(
            PipelineStage.product_line == pl,
            PipelineStage.is_won.is_(True),
        )
    )
    lost_stage = db.scalar(
        select(PipelineStage).where(
            PipelineStage.product_line == pl,
            PipelineStage.is_lost.is_(True),
            PipelineStage.slug == "lost",
        )
    )
    won_n = (
        db.scalar(select(func.count()).select_from(Business).where(Business.pipeline_stage_id == won_stage.id))
        if won_stage
        else 0
    ) or 0
    lost_n = (
        db.scalar(select(func.count()).select_from(Business).where(Business.pipeline_stage_id == lost_stage.id))
        if lost_stage
        else 0
    ) or 0
    closed = int(won_n) + int(lost_n)
    discovery_n = db.scalar(select(func.count()).select_from(DiscoveryNote)) or 0
    contacted_n = (
        db.scalar(
            select(func.count())
            .select_from(Business)
            .where(Business.product_line == pl, Business.contacted.is_(True))
        )
        or 0
    )
    return {
        "product_line": pl,
        "pipeline": pipeline,
        "collected": collected_revenue(db),
        "invoiced_outstanding": invoiced_outstanding(db),
        "lost_reasons": reasons,
        "lost_reasons_n": len(lost),
        "lead_sources": sources,
        "score_bands": score_bands,
        "funnel": funnel,
        "engagement": {
            "contacted": int(contacted_n),
            "discovery_notes": int(discovery_n),
            "proposals": db.scalar(select(func.count()).select_from(Proposal)) or 0,
            "invoices_open": db.scalar(select(func.count()).select_from(Invoice).where(Invoice.status == "open")) or 0,
        },
        "saas_mrr": {
            "by_currency": _saas_mrr_by_currency(db),
            "trialing": db.scalar(
                select(func.count())
                .select_from(Business)
                .where(Business.product_line == "SAAS", Business.trial_ends_at.is_not(None))
            )
            or 0,
            "with_mrr": db.scalar(
                select(func.count()).select_from(Business).where(Business.product_line == "SAAS", Business.mrr.is_not(None))
            )
            or 0,
            "note": "MRR is manually tracked on SaaS prospects until subscription billing is wired",
        },
        "win_rate": {
            "won": int(won_n),
            "lost": int(lost_n),
            "rate": (int(won_n) / closed) if closed else None,
            "sample_size": closed,
            "note": "Avoid relying on % when n < 10",
        },
    }


@router.get("/contact-queue")
def sales_contact_queue(
    product_line: str = "STANDALONE",
    limit: int = 100,
    mode: str = "",
    _user: str = Depends(require_user),
    db=Depends(get_db),
):
    from app.services.automation import rank_contact_queue

    return rank_contact_queue(
        db,
        product_line=product_line.upper(),
        limit=limit,
        mode=(mode or "").strip() or None,
    )


@router.post("/prospects/auto-setup")
def sales_auto_setup_prospects(_user: str = Depends(require_user), db=Depends(get_db)):
    """Backfill product_line, currency, stage, and follow-up tasks for all prospects."""
    from app.services.authz import get_user_by_email, require_role

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner", "sales")
    result = backfill_prospect_stages(db)
    db.commit()
    return {"ok": True, **result}


@router.post("/demo-data/wipe")
def wipe_demo_data(_user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    rows = db.scalars(select(Business).where(Business.is_demo_data.is_(True))).all()
    n = 0
    for b in rows:
        db.delete(b)
        n += 1
    db.commit()
    return {"deleted": n}


class SavedViewBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    filters: dict[str, Any] = Field(default_factory=dict)


@router.get("/saved-views")
def list_saved_views(_user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email

    actor = get_user_by_email(db, _user)
    stmt = select(SavedView).order_by(SavedView.id.desc()).limit(100)
    if actor:
        stmt = select(SavedView).where(
            or_(SavedView.user_id == actor.id, SavedView.user_id.is_(None))
        ).order_by(SavedView.id.desc()).limit(100)
    rows = db.scalars(stmt).all()
    out = []
    for v in rows:
        try:
            filters = json.loads(v.filters_json or "{}")
        except Exception:
            filters = {}
        out.append(
            {
                "id": v.id,
                "name": v.name,
                "filters": filters,
                "user_id": v.user_id,
                "created_at": _iso(v.created_at),
            }
        )
    return {"views": out}


@router.post("/saved-views")
def create_saved_view(body: SavedViewBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner", "sales")
    view = SavedView(
        user_id=actor.id if actor else None,
        name=body.name.strip()[:200],
        filters_json=json.dumps(body.filters or {}),
    )
    db.add(view)
    db.commit()
    db.refresh(view)
    return {"id": view.id, "name": view.name, "filters": body.filters or {}}


@router.delete("/saved-views/{view_id}")
def delete_saved_view(view_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner", "sales")
    view = db.get(SavedView, view_id)
    if not view:
        raise HTTPException(404, "Saved view not found")
    if actor and view.user_id and view.user_id != actor.id and (actor.role or "") != "owner":
        raise HTTPException(403, "Cannot delete another user's saved view")
    db.delete(view)
    db.commit()
    return {"ok": True, "deleted": view_id}


# —— Ranking modes CRUD ——
class RankingModeBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = ""
    description: str = ""
    filter: dict = Field(default_factory=dict)
    boost: dict = Field(default_factory=dict)
    sort_order: int = 0
    active: bool = True


@router.get("/ranking-modes")
def list_ranking_modes(include_inactive: bool = False, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services import ranking_modes as rm

    return {"modes": rm.list_modes(db, active_only=not include_inactive)}


@router.post("/ranking-modes")
def create_ranking_mode(body: RankingModeBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.services import ranking_modes as rm
    from app.models import ContactQueueMode

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    slug = (body.slug or rm.slugify(body.name)).strip()[:80]
    if db.scalar(select(ContactQueueMode).where(ContactQueueMode.slug == slug)):
        raise HTTPException(409, "Slug already exists")
    row = ContactQueueMode(
        slug=slug,
        name=body.name.strip()[:200],
        description=(body.description or "")[:2000],
        filter_json=json.dumps(body.filter or {}),
        boost_json=json.dumps(body.boost or {}),
        sort_order=int(body.sort_order or 0),
        active=bool(body.active),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return rm.mode_to_dict(row)


@router.put("/ranking-modes/{mode_id}")
def update_ranking_mode(mode_id: int, body: RankingModeBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.services import ranking_modes as rm
    from app.models import ContactQueueMode

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    row = db.get(ContactQueueMode, mode_id)
    if not row:
        raise HTTPException(404, "Mode not found")
    slug = (body.slug or row.slug).strip()[:80]
    clash = db.scalar(select(ContactQueueMode).where(ContactQueueMode.slug == slug, ContactQueueMode.id != mode_id))
    if clash:
        raise HTTPException(409, "Slug already exists")
    row.slug = slug
    row.name = body.name.strip()[:200]
    row.description = (body.description or "")[:2000]
    row.filter_json = json.dumps(body.filter or {})
    row.boost_json = json.dumps(body.boost or {})
    row.sort_order = int(body.sort_order or 0)
    row.active = bool(body.active)
    db.commit()
    db.refresh(row)
    return rm.mode_to_dict(row)


@router.delete("/ranking-modes/{mode_id}")
def delete_ranking_mode(mode_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.models import ContactQueueMode

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    row = db.get(ContactQueueMode, mode_id)
    if not row:
        raise HTTPException(404, "Mode not found")
    if row.slug == "default":
        raise HTTPException(400, "Cannot delete the default mode — deactivate instead")
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted": mode_id}


# —— Demo media library ——
class DemoAssetBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    kind: str = "link"
    url: str = ""
    notes: str = ""
    active: bool = True


def _demo_dict(a) -> dict:
    file_url = ""
    if a.url and str(a.url).startswith("http"):
        file_url = a.url
    elif a.file_path:
        file_url = f"/uploads/{a.file_path}"
    return {
        "id": a.id,
        "title": a.title,
        "kind": a.kind,
        "url": a.url or "",
        "file_path": a.file_path or "",
        "file_url": file_url,
        "notes": a.notes or "",
        "active": bool(a.active),
        "created_at": _iso(a.created_at),
    }


@router.get("/demo-assets")
def list_demo_assets(_user: str = Depends(require_user), db=Depends(get_db)):
    from app.models import DemoAsset

    rows = db.scalars(select(DemoAsset).order_by(DemoAsset.id.desc())).all()
    return {"assets": [_demo_dict(a) for a in rows]}


@router.post("/demo-assets")
def create_demo_asset(body: DemoAssetBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.models import DemoAsset

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner", "sales")
    kind = (body.kind or "link").lower()
    if kind not in ("link", "video", "file"):
        raise HTTPException(422, "kind must be link|video|file")
    a = DemoAsset(
        title=body.title.strip()[:300],
        kind=kind,
        url=(body.url or "").strip()[:2000],
        notes=(body.notes or "")[:4000],
        active=bool(body.active),
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return _demo_dict(a)


@router.put("/demo-assets/{asset_id}")
def update_demo_asset(asset_id: int, body: DemoAssetBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.models import DemoAsset

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner", "sales")
    a = db.get(DemoAsset, asset_id)
    if not a:
        raise HTTPException(404, "Asset not found")
    a.title = body.title.strip()[:300]
    a.kind = (body.kind or a.kind).lower()
    a.url = (body.url or "").strip()[:2000]
    a.notes = (body.notes or "")[:4000]
    a.active = bool(body.active)
    db.commit()
    db.refresh(a)
    return _demo_dict(a)


@router.delete("/demo-assets/{asset_id}")
def delete_demo_asset(asset_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.models import DemoAsset

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner", "sales")
    a = db.get(DemoAsset, asset_id)
    if not a:
        raise HTTPException(404, "Asset not found")
    db.delete(a)
    db.commit()
    return {"ok": True, "deleted": asset_id}


@router.post("/demo-assets/upload")
async def upload_demo_asset(request: Request, _user: str = Depends(require_user), db=Depends(get_db)):
    """Multipart: title, notes, file. Local disk, Vercel Blob (BLOB_READ_WRITE_TOKEN), or optional S3."""
    from pathlib import Path
    import uuid
    from app.services.authz import get_user_by_email, require_role
    from app.services import storage as store
    from app.models import DemoAsset

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner", "sales")
    form = await request.form()
    upload = form.get("file")
    if not upload or not hasattr(upload, "filename"):
        raise HTTPException(422, "file required")
    title = str(form.get("title") or upload.filename or "Demo video")[:300]
    notes = str(form.get("notes") or "")[:4000]
    raw = await upload.read()
    if len(raw) > 80 * 1024 * 1024:
        raise HTTPException(413, "File too large (max 80MB)")
    ext = Path(upload.filename or "demo.bin").suffix[:12] or ".bin"
    safe = f"demos/{uuid.uuid4().hex}{ext}"
    ctype = getattr(upload, "content_type", None) or "application/octet-stream"
    try:
        saved = store.save_bytes(safe, raw, content_type=ctype)
    except Exception as exc:
        raise HTTPException(500, f"Upload failed: {exc}") from exc
    a = DemoAsset(
        title=title,
        kind="video",
        url=saved.get("url") or "",
        file_path=saved.get("file_path") or safe,
        notes=notes,
        active=True,
    )
    db.add(a)
    db.commit()
    db.refresh(a)
    return _demo_dict(a)


# —— Payment provider settings (Owner) ——
@router.get("/payment-settings")
def get_payment_settings(_user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.services.secrets import payment_settings_public

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    return {"settings": payment_settings_public(db=db), "providers": provider_status(db=db)}


class PaymentSettingBody(BaseModel):
    key: str
    value: str = ""


@router.put("/payment-settings")
def put_payment_setting(body: PaymentSettingBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.services.secrets import PAY_SETTING_KEYS, payment_settings_public, store_setting_value

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    allowed = {k: secret for k, _, secret in PAY_SETTING_KEYS}
    if body.key not in allowed:
        raise HTTPException(422, "Unknown payment setting key")
    store_setting_value(db, body.key, body.value or "", secret=bool(allowed[body.key]))
    db.commit()
    return {"ok": True, "settings": payment_settings_public(db=db)}


class PaymentSettingsBulkBody(BaseModel):
    settings: dict[str, str] = Field(default_factory=dict)


@router.put("/payment-settings/bulk")
def put_payment_settings_bulk(body: PaymentSettingsBulkBody, _user: str = Depends(require_user), db=Depends(get_db)):
    """Save many payment keys from the configure form. Empty string clears DB override."""
    from app.services.authz import get_user_by_email, require_role
    from app.services.secrets import PAY_SETTING_KEYS, payment_settings_public, store_setting_value

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    allowed = {k: secret for k, _, secret in PAY_SETTING_KEYS}
    saved = []
    for key, value in (body.settings or {}).items():
        if key not in allowed:
            raise HTTPException(422, f"Unknown payment setting key: {key}")
        # Skip blank secret fields so we don't wipe existing secrets when form leaves password empty
        if allowed[key] and not str(value or "").strip():
            continue
        store_setting_value(db, key, value or "", secret=bool(allowed[key]))
        saved.append(key)
    db.commit()
    return {"ok": True, "saved": saved, "settings": payment_settings_public(db=db), "providers": provider_status(db=db)}


class PaymentValidateBody(BaseModel):
    provider: str = Field(min_length=2, max_length=40)


@router.post("/payment-settings/validate")
def validate_payment_provider(body: PaymentValidateBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.services.payment_validate import validate_provider

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    return validate_provider(body.provider)


@router.post("/payment-settings/fill-local-defaults")
def fill_local_payment_defaults(_user: str = Depends(require_user), db=Depends(get_db)):
    """
    Owner-only LOCAL helpers: test/sandbox modes, public URL, Payoneer/Skrill payee + webhook secrets.
    Never invents live Stripe/PayPal API keys — those must be pasted from the provider dashboard.
    """
    import secrets as pysecrets

    from app.services.authz import get_user_by_email, require_role
    from app.services.secrets import payment_settings_public, store_setting_value

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")

    defaults = {
        "pay.stripe_mode": ("test", False),
        "pay.paypal_mode": ("sandbox", False),
        "pay.public_base_url": ("http://localhost:8000", False),
        "pay.payoneer_payee_email": ("hello@appointhive.com", False),
        "pay.skrill_pay_to_email": ("hello@appointhive.com", False),
        "pay.payoneer_webhook_secret": (f"local_po_{pysecrets.token_hex(12)}", True),
        "pay.skrill_webhook_secret": (f"local_sk_{pysecrets.token_hex(12)}", True),
    }
    saved = []
    for key, (value, is_secret) in defaults.items():
        store_setting_value(db, key, value, secret=is_secret)
        saved.append(key)
    db.commit()
    return {
        "ok": True,
        "saved": saved,
        "note": "Local defaults applied. Stripe/PayPal Checkout still need real sk_test / client credentials in Pay setup.",
        "settings": payment_settings_public(db=db),
        "providers": provider_status(db=db),
    }


@router.get("/mail-settings")
def get_mail_settings(_user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.services.mail import mail_settings_public, smtp_configured
    from app.services import storage as store

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    return {"settings": mail_settings_public(db=db), "smtp_ready": smtp_configured(db=db), "storage": store.storage_status()}


@router.put("/mail-settings")
def put_mail_setting(body: PaymentSettingBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.services.mail import MAIL_SETTING_KEYS, mail_settings_public
    from app.services.secrets import store_setting_value

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    allowed = {k: secret for k, _, secret in MAIL_SETTING_KEYS}
    if body.key not in allowed:
        raise HTTPException(422, "Unknown mail setting key")
    store_setting_value(db, body.key, body.value or "", secret=bool(allowed[body.key]))
    db.commit()
    return {"ok": True, "settings": mail_settings_public(db=db)}


# —— Invoice view / PDF / compose helpers ——
@router.get("/invoices/{invoice_id}/view")
def invoice_view(invoice_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.models import BillingEntity, Proposal
    from app.services.invoices_pdf import invoice_view_payload

    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    b = db.get(Business, inv.business_id)
    ent = db.get(BillingEntity, inv.billing_entity_id) if inv.billing_entity_id else db.scalar(
        select(BillingEntity).where(BillingEntity.active.is_(True))
    )
    prop = db.get(Proposal, inv.proposal_id) if inv.proposal_id else None
    return invoice_view_payload(inv, b, ent, prop)


@router.get("/invoices/{invoice_id}/pdf")
def invoice_pdf(invoice_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    from fastapi.responses import Response
    from app.models import BillingEntity, Proposal
    from app.services.invoices_pdf import build_invoice_pdf_bytes, invoice_view_payload

    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    b = db.get(Business, inv.business_id)
    ent = db.get(BillingEntity, inv.billing_entity_id) if inv.billing_entity_id else db.scalar(
        select(BillingEntity).where(BillingEntity.active.is_(True))
    )
    prop = db.get(Proposal, inv.proposal_id) if inv.proposal_id else None
    payload = invoice_view_payload(inv, b, ent, prop)
    try:
        pdf = build_invoice_pdf_bytes(payload)
    except Exception as exc:
        raise HTTPException(500, f"PDF generation failed: {exc}") from exc
    fname = f"{(inv.number or 'invoice').replace('/', '-')}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.get("/invoices/{invoice_id}/compose-copy")
def invoice_compose_copy(
    invoice_id: int,
    template_id: Optional[int] = None,
    _user: str = Depends(require_user),
    db=Depends(get_db),
):
    """Message body for copy/paste outreach; PDF is downloaded separately (clipboard cannot attach files)."""
    from app.models import BillingEntity, MessageTemplate, Proposal
    from app.services.invoices_pdf import invoice_email_body, invoice_view_payload

    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    b = db.get(Business, inv.business_id)
    ent = db.get(BillingEntity, inv.billing_entity_id) if inv.billing_entity_id else None
    prop = db.get(Proposal, inv.proposal_id) if inv.proposal_id else None
    payload = invoice_view_payload(inv, b, ent, prop)
    tpl = None
    if template_id:
        tpl = db.get(MessageTemplate, template_id)
        if not tpl or tpl.channel != "invoice":
            raise HTTPException(404, "Invoice template not found")
    else:
        tpl = db.scalar(
            select(MessageTemplate)
            .where(MessageTemplate.channel == "invoice", MessageTemplate.is_default.is_(True))
            .limit(1)
        ) or db.scalar(select(MessageTemplate).where(MessageTemplate.channel == "invoice").limit(1))
    subject, body = invoice_email_body(
        payload,
        subject_tpl=(tpl.subject if tpl else "") or "",
        body_tpl=(tpl.body if tpl else "") or "",
    )
    from app.services.mail import smtp_configured

    return {
        "subject": subject,
        "body": body,
        "template_id": tpl.id if tpl else None,
        "template_name": tpl.name if tpl else None,
        "pdf_url": f"/api/sales/invoices/{inv.id}/pdf",
        "view_url": f"/api/sales/invoices/{inv.id}/view",
        "to": (b.email if b else "") or "",
        "smtp_ready": smtp_configured(),
        "note": (
            "SMTP ready — use Send email with PDF."
            if smtp_configured()
            else "SMTP not configured — copy message and attach PDF manually, or set Mail settings under Money."
        ),
    }


class InvoiceSendBody(BaseModel):
    to: str = ""
    subject: str = ""
    body: str = ""
    template_id: Optional[int] = None


@router.post("/invoices/{invoice_id}/send-email")
def invoice_send_email(
    invoice_id: int,
    body: InvoiceSendBody,
    _user: str = Depends(require_user),
    db=Depends(get_db),
):
    """Send invoice email with PDF attachment via SMTP."""
    from app.models import BillingEntity, MessageTemplate, Proposal, Activity
    from app.services.authz import get_user_by_email, require_role
    from app.services.invoices_pdf import build_invoice_pdf_bytes, invoice_email_body, invoice_view_payload
    from app.services.mail import send_email, smtp_configured

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner", "sales")
    if not smtp_configured():
        raise HTTPException(503, "SMTP not configured — set Mail settings (host + from) under Money")
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    b = db.get(Business, inv.business_id)
    ent = db.get(BillingEntity, inv.billing_entity_id) if inv.billing_entity_id else None
    prop = db.get(Proposal, inv.proposal_id) if inv.proposal_id else None
    payload = invoice_view_payload(inv, b, ent, prop)
    tpl = None
    if body.template_id:
        tpl = db.get(MessageTemplate, body.template_id)
    else:
        tpl = db.scalar(
            select(MessageTemplate)
            .where(MessageTemplate.channel == "invoice", MessageTemplate.is_default.is_(True))
            .limit(1)
        )
    subject, text = invoice_email_body(
        payload,
        subject_tpl=(tpl.subject if tpl else "") or "",
        body_tpl=(tpl.body if tpl else "") or "",
    )
    if body.subject.strip():
        subject = body.subject.strip()
    if body.body.strip():
        text = body.body.strip()
    to = (body.to or "").strip() or ((b.email if b else "") or "").strip()
    if not to:
        raise HTTPException(422, "Recipient email required")
    try:
        pdf = build_invoice_pdf_bytes(payload)
    except Exception as exc:
        raise HTTPException(500, f"PDF generation failed: {exc}") from exc
    fname = f"{(inv.number or 'invoice').replace('/', '-')}.pdf"
    try:
        result = send_email(to=to, subject=subject, body=text, attachment_bytes=pdf, attachment_name=fname)
    except Exception as exc:
        raise HTTPException(502, f"Send failed: {exc}") from exc
    try:
        db.add(
            Activity(
                business_id=inv.business_id,
                activity_type="invoice_email_sent",
                channel="email",
                summary=f"Invoice {inv.number} emailed to {to}",
                outcome="sent",
                created_by=actor.id if actor else None,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
    return {"ok": True, **result, "invoice_id": inv.id, "number": inv.number}


# —— AppointHive Booking Engine bridge ——
class BeManualConnectBody(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)
    shared_secret: str = Field(min_length=8, max_length=500)


class BeSendPurchaseBody(BaseModel):
    business_id: int
    product_revision_id: str = Field(min_length=1, max_length=200)
    provider: Optional[str] = None
    proposal_id: Optional[int] = None


class BeSendRequestBody(BaseModel):
    business_id: int
    message: str = ""
    requested_edition: Optional[str] = None


class BeSendUpgradeBody(BaseModel):
    business_id: int
    to_revision_id: str = Field(min_length=1, max_length=200)
    from_revision_id: Optional[str] = None
    provider: Optional[str] = None


@router.get("/be/connection")
def be_get_connection(_user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.booking_engine.connect import be_connection_public

    return be_connection_public(db=db)


@router.post("/be/connection/pair-code")
def be_pair_code(_user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.services.booking_engine.connect import generate_pairing_code

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    return generate_pairing_code(db)


@router.post("/be/connection/manual")
def be_manual_connect(body: BeManualConnectBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.services.booking_engine.connect import save_manual_connection

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    return save_manual_connection(db, base_url=body.base_url, shared_secret=body.shared_secret)


@router.post("/be/connection/test")
def be_test_connection(_user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.services.booking_engine.connect import test_be_connection

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    return test_be_connection(db)


@router.post("/be/connection/disconnect")
def be_disconnect(_user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email, require_role
    from app.services.booking_engine.connect import disconnect_be

    actor = get_user_by_email(db, _user)
    require_role(actor, "owner")
    return disconnect_be(db)


@router.get("/be/catalog")
def be_catalog(product_line: str = "STANDALONE", _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.booking_engine.client import BeClientError, BookingEngineClient

    client = BookingEngineClient(db=db)
    try:
        return client.get_catalog(product_line)
    except BeClientError as exc:
        raise HTTPException(exc.status or 502, detail={"error": str(exc), "be": exc.body}) from exc
    except HTTPException:
        raise


@router.get("/be/revisions")
def be_revisions(_user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.booking_engine.client import BeClientError, BookingEngineClient

    client = BookingEngineClient(db=db)
    try:
        return client.list_revisions()
    except BeClientError as exc:
        raise HTTPException(exc.status or 502, detail={"error": str(exc), "be": exc.body}) from exc


@router.get("/be/upgrade-quote")
def be_upgrade_quote(
    installation_id: Optional[str] = None,
    from_revision_id: Optional[str] = None,
    to_revision_id: Optional[str] = None,
    requested_edition: Optional[str] = None,
    _user: str = Depends(require_user),
    db=Depends(get_db),
):
    from app.services.booking_engine.client import BeClientError, BookingEngineClient

    params = {}
    if installation_id:
        params["installationId"] = installation_id
    if from_revision_id:
        params["fromRevisionId"] = from_revision_id
    if to_revision_id:
        params["toRevisionId"] = to_revision_id
    if requested_edition:
        params["requestedEdition"] = requested_edition
    client = BookingEngineClient(db=db)
    try:
        return client.upgrade_quote(params)
    except BeClientError as exc:
        raise HTTPException(exc.status or 502, detail={"error": str(exc), "be": exc.body}) from exc


@router.post("/be/send-to-delivery")
def be_send_to_delivery(body: BeSendPurchaseBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email
    from app.services.booking_engine.handoff import send_purchase

    actor = get_user_by_email(db, _user)
    return send_purchase(
        db,
        business_id=body.business_id,
        product_revision_id=body.product_revision_id,
        provider=body.provider,
        proposal_id=body.proposal_id,
        actor_id=actor.id if actor else None,
    )


@router.post("/be/send-request")
def be_send_request(body: BeSendRequestBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email
    from app.services.booking_engine.handoff import send_request

    actor = get_user_by_email(db, _user)
    return send_request(
        db,
        business_id=body.business_id,
        message=body.message or "",
        requested_edition=body.requested_edition,
        actor_id=actor.id if actor else None,
    )


@router.post("/be/send-upgrade")
def be_send_upgrade(body: BeSendUpgradeBody, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.authz import get_user_by_email
    from app.services.booking_engine.handoff import send_upgrade

    actor = get_user_by_email(db, _user)
    return send_upgrade(
        db,
        business_id=body.business_id,
        to_revision_id=body.to_revision_id,
        from_revision_id=body.from_revision_id,
        provider=body.provider,
        actor_id=actor.id if actor else None,
    )


@router.get("/be/deliveries")
def be_list_deliveries(business_id: Optional[int] = None, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.models import BeDelivery
    from app.services.booking_engine.handoff import delivery_dict

    q = select(BeDelivery).order_by(BeDelivery.id.desc()).limit(100)
    if business_id:
        q = select(BeDelivery).where(BeDelivery.business_id == business_id).order_by(BeDelivery.id.desc()).limit(50)
    rows = db.scalars(q).all()
    return {"deliveries": [delivery_dict(r) for r in rows]}


@router.post("/be/deliveries/{delivery_id}/refresh")
def be_refresh_delivery(delivery_id: int, _user: str = Depends(require_user), db=Depends(get_db)):
    from app.services.booking_engine.handoff import refresh_delivery

    return refresh_delivery(db, delivery_id)
