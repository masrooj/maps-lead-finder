"""Sales flow automation: outreach → activity/task → stage advances → contact ranking."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import or_, select

from app.models import Activity, AppSetting, Business, DiscoveryNote, PipelineStage, Task
from app.services.pipeline import get_stage, list_stages, move_stage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


STANDALONE_ORDER = [
    "identified",
    "ready_to_contact",
    "contacted",
    "follow_up",
    "engaged",
    "qualified",
    "demo_scheduled",
    "demo_completed",
    "proposal_sent",
    "negotiation",
    "won",
]

SAAS_ORDER = [
    "identified",
    "ready_to_contact",
    "contacted",
    "follow_up",
    "engaged",
    "qualified",
    "demo_scheduled",
    "demo_completed",
    "trial_offered",
    "trial_active",
    "subscribed",
]

# Map STANDALONE-oriented targets onto SaaS equivalents when needed
_SAAS_ALIAS = {
    "proposal_sent": "trial_offered",
    "negotiation": "trial_active",
    "won": "subscribed",
}


def _order_for(product_line: str) -> list[str]:
    return SAAS_ORDER if (product_line or "").upper() == "SAAS" else STANDALONE_ORDER


def _stage_index(slug: str, product_line: str = "STANDALONE") -> int:
    order = _order_for(product_line)
    try:
        return order.index(slug)
    except ValueError:
        return -1


def advance_at_least(db, b: Business, target_slug: str, *, actor_id: Optional[int] = None, note: str = "") -> bool:
    """Move forward only (never regress). Returns True if stage changed."""
    pl = (b.product_line or "STANDALONE").upper()
    if pl == "SAAS":
        target_slug = _SAAS_ALIAS.get(target_slug, target_slug)
    target = get_stage(db, pl, target_slug)
    if not target:
        return False
    current = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
    terminal_block = ("won", "lost", "nurture", "subscribed", "churned")
    if current and current.slug in terminal_block:
        return False
    if current and _stage_index(current.slug, pl) >= _stage_index(target_slug, pl):
        return False
    move_stage(db, b, target.id, actor_id=actor_id, note=note or f"Auto → {target_slug}", force=True)
    return True


def on_outreach_confirmed(
    db,
    b: Business,
    *,
    channel: str,
    actor_id: Optional[int] = None,
    follow_up_at: Optional[datetime] = None,
    outcome: str = "",
    notes: str = "",
) -> dict:
    """Email/WA/IG confirm-sent: activity + follow-up task + auto stage advance."""
    db.add(
        Activity(
            business_id=b.id,
            activity_type="outreach_sent",
            channel=channel,
            summary=f"Confirmed {channel} send",
            outcome=outcome or "",
            next_action=(notes or "")[:500],
            created_by=actor_id,
        )
    )
    due = follow_up_at or (_utcnow() + timedelta(days=3))
    existing = db.scalar(
        select(Task).where(
            Task.business_id == b.id,
            Task.status == "open",
            Task.title.ilike("%follow%"),
        )
    )
    if not existing:
        db.add(
            Task(
                business_id=b.id,
                title=f"Follow up after {channel}",
                description="Auto-created when outreach was confirmed sent",
                priority="high",
                status="open",
                due_at=due,
                owner_id=actor_id,
            )
        )
    advance_at_least(db, b, "contacted", actor_id=actor_id, note=f"Auto after {channel} confirm-sent")
    if follow_up_at or due:
        advance_at_least(db, b, "follow_up", actor_id=actor_id, note="Auto follow-up scheduled")
    return {"ok": True, "channel": channel}


def on_marked_for_contact(db, b: Business, *, actor_id: Optional[int] = None) -> None:
    advance_at_least(db, b, "ready_to_contact", actor_id=actor_id, note="Auto: marked for contact")


def on_discovery_saved(db, b: Business, *, actor_id: Optional[int] = None, painful_issue: str = "") -> None:
    advance_at_least(db, b, "engaged", actor_id=actor_id, note="Auto: discovery note saved")
    if painful_issue and len(painful_issue.strip()) > 20:
        advance_at_least(db, b, "qualified", actor_id=actor_id, note="Auto: discovery pain captured")


def on_demo_saved(db, b: Business, *, actor_id: Optional[int] = None, outcome: str = "") -> None:
    advance_at_least(db, b, "demo_completed", actor_id=actor_id, note="Auto: demo logged")
    oc = (outcome or "").lower()
    if any(k in oc for k in ("positive", "interested", "booked", "yes")):
        advance_at_least(db, b, "proposal_sent", actor_id=actor_id, note="Auto: positive demo")


def on_score_saved(db, b: Business, *, actor_id: Optional[int] = None) -> None:
    score = b.score_override if b.score_override is not None else b.qualification_score
    if score is not None and float(score) >= 7:
        advance_at_least(db, b, "qualified", actor_id=actor_id, note=f"Auto: score {score} ≥ 7")
    elif score is not None and float(score) >= 5:
        advance_at_least(db, b, "engaged", actor_id=actor_id, note=f"Auto: score {score} ≥ 5")


def on_proposal_sent(db, b: Business, *, actor_id: Optional[int] = None) -> None:
    advance_at_least(db, b, "proposal_sent", actor_id=actor_id, note="Auto: proposal/invoice created")


def _discovery_boost(db, business_id: int) -> float:
    notes = db.scalars(
        select(DiscoveryNote).where(DiscoveryNote.business_id == business_id).order_by(DiscoveryNote.id.desc()).limit(3)
    ).all()
    if not notes:
        return 0.0
    boost = 1.5
    latest = notes[0]
    filled = sum(
        1
        for f in (
            "painful_issue",
            "desired_outcome",
            "buying_authority",
            "purchase_timing",
            "required_capabilities",
        )
        if (getattr(latest, f, None) or "").strip()
    )
    boost += filled * 0.6
    if (latest.painful_issue or "").strip():
        boost += min(2.0, len(latest.painful_issue.strip()) / 80.0)
    return boost


def _channel_facts(b: Business) -> dict[str, Any]:
    try:
        socials = json.loads(b.social_links_json or "{}") or {}
    except Exception:
        socials = {}
    if not isinstance(socials, dict):
        socials = {}
    phone = (b.international_phone or b.phone or "").strip()
    digits = re.sub(r"\D+", "", phone)
    emails = [e.strip() for e in str(b.email or "").split(",") if e.strip()]
    website = (b.website or "").strip()
    ig = str(socials.get("instagram") or "").strip()
    return {
        "has_email": bool(emails),
        "has_phone": len(digits) >= 8,
        "has_website": bool(website),
        "has_instagram": bool(ig),
        "social_links": socials,
        "email": b.email or "",
        "phone": b.phone or "",
        "international_phone": b.international_phone or "",
        "website": website,
    }


def _get_queue_mode(db, mode: Optional[str] = None) -> dict:
    from app.services import ranking_modes as rm

    modes = rm.list_modes(db, active_only=True)
    by_slug = {m["slug"]: m for m in modes}
    if mode and mode.strip() in by_slug:
        return by_slug[mode.strip()]
    row = db.scalar(select(AppSetting).where(AppSetting.key == "contact_queue_mode"))
    val = (row.value if row else "") or "default"
    if val in by_slug:
        return by_slug[val]
    return modes[0] if modes else {"slug": "default", "name": "Balanced", "description": "", "filter": {}, "boost": {}}


def rank_contact_queue(
    db,
    *,
    product_line: str = "STANDALONE",
    limit: int = 2000,
    mode: Optional[str] = None,
    country: Optional[str] = None,
    city: Optional[str] = None,
) -> dict:
    """Rank open prospects using a DB-backed ranking mode (CRUD)."""
    from app.services import ranking_modes as rm

    mode_meta = _get_queue_mode(db, mode)
    active = mode_meta.get("slug") or "default"
    filt = mode_meta.get("filter") or {}
    boost_cfg = mode_meta.get("boost") or {}
    stages = {s.id: s for s in list_stages(db, product_line)}
    country_f = (country or "").strip()
    city_f = (city or "").strip()
    base_clauses = [
        or_(Business.archived.is_(False), Business.archived.is_(None)),
        or_(Business.opt_out.is_(False), Business.opt_out.is_(None)),
        Business.product_line == product_line,
    ]
    # Unscoped rows power the country/city filter menus (never collapse to the active filter).
    all_rows = db.scalars(select(Business).where(*base_clauses)).all()
    loc_map: dict[str, set[str]] = {}
    for b in all_rows:
        ctry = (b.country or "").strip() or "—"
        cty = (b.city or "").strip()
        loc_map.setdefault(ctry, set())
        if cty:
            loc_map[ctry].add(cty)
    locations = {
        ctry: sorted(cities, key=lambda x: x.lower())
        for ctry, cities in sorted(loc_map.items(), key=lambda kv: kv[0].lower())
    }
    rows = all_rows
    if country_f or city_f:
        rows = [
            b
            for b in all_rows
            if (not country_f or (b.country or "").strip().lower() == country_f.lower())
            and (not city_f or (b.city or "").strip().lower() == city_f.lower())
        ]
    ranked = []
    for b in rows:
        stage = stages.get(b.pipeline_stage_id) if b.pipeline_stage_id else None
        if stage and (stage.is_won or stage.is_lost or stage.slug in ("won", "lost", "churned")):
            continue
        if b.lost:
            continue
        ch = _channel_facts(b)
        flags = {
            "email_sent": bool(b.email_sent),
            "whatsapp_sent": bool(b.whatsapp_sent),
            "instagram_sent": bool(b.instagram_sent),
            "marked_for_contact": bool(b.marked_for_contact),
            "contacted": bool(b.contacted),
        }
        if not rm.match_filter(filt, ch=ch, b_flags=flags):
            continue
        base = float(b.score_override if b.score_override is not None else (b.qualification_score or 0) or 0)
        disc = _discovery_boost(db, b.id)
        marked = 2.0 if b.marked_for_contact else 0.0
        contacted_penalty = -1.5 if b.contacted and not b.next_follow_up_at else 0.0
        stage_bonus = 0.0
        if stage:
            stage_bonus = {
                "ready_to_contact": 3.0,
                "identified": 1.0,
                "contacted": 0.5,
                "follow_up": 2.5,
                "engaged": 2.0,
                "qualified": 1.5,
            }.get(stage.slug, 0.0)
        mode_pts, mode_tags = rm.mode_boost_points(boost_cfg)
        if active == "default":
            if ch["has_instagram"]:
                mode_pts += 0.4
                mode_tags.append("has IG")
            if ch["has_website"] and not ch["has_email"]:
                mode_pts += 0.3
                mode_tags.append("site w/o email")
        rank = round(base + disc + marked + contacted_penalty + stage_bonus + mode_pts, 2)
        why = []
        if base:
            why.append(f"score {base:g}")
        if disc:
            why.append(f"discovery +{disc:.1f}")
        if marked:
            why.append("starred")
        if stage:
            why.append(stage.name)
        why.extend(mode_tags)
        ranked.append(
            {
                "id": b.id,
                "name": b.name,
                "city": b.city,
                "country": b.country,
                "category": b.category,
                "product_line": b.product_line,
                "stage": {"id": stage.id, "name": stage.name, "slug": stage.slug} if stage else None,
                "qualification_score": b.qualification_score,
                "score_override": b.score_override,
                "marked_for_contact": bool(b.marked_for_contact),
                "contacted": bool(b.contacted),
                "opt_out": bool(b.opt_out),
                "outcome": b.outcome or "",
                "email": ch["email"],
                "phone": ch["phone"],
                "international_phone": ch["international_phone"],
                "website": ch["website"],
                "social_links": ch["social_links"],
                "email_sent": bool(b.email_sent),
                "whatsapp_sent": bool(b.whatsapp_sent),
                "instagram_sent": bool(b.instagram_sent),
                "channels": {
                    "email": ch["has_email"],
                    "whatsapp": ch["has_phone"],
                    "instagram": ch["has_instagram"],
                },
                "google_maps_url": b.google_maps_url or "",
                "current_booking_software": (getattr(b, "current_booking_software", None) or "").strip(),
                "has_booking": bool((getattr(b, "current_booking_software", None) or "").strip()),
                "next_follow_up_at": b.next_follow_up_at.isoformat() if b.next_follow_up_at else "",
                "rank_score": rank,
                "why": ", ".join(why) or "base priority",
                "why_parts": why,
            }
        )
    ranked.sort(key=lambda r: (-r["rank_score"], (r.get("country") or ""), (r.get("city") or ""), r["name"] or ""))
    # limit<=0 means return every matching candidate (hard safety ceiling still applies)
    hard_ceiling = 10000
    if int(limit or 0) <= 0:
        out = ranked[:hard_ceiling]
    else:
        out = ranked[: max(1, min(int(limit), hard_ceiling))]
    modes_ui = [
        {"id": m["slug"], "slug": m["slug"], "label": m["name"], "name": m["name"], "description": m["description"]}
        for m in rm.list_modes(db, active_only=True)
    ]
    return {
        "mode": active,
        "modes": modes_ui,
        "definition": mode_meta.get("description") or "",
        "mode_label": mode_meta.get("name") or active,
        "sample_size": len(out),
        "total_candidates": len(ranked),
        "country": country_f,
        "city": city_f,
        "locations": locations,
        "leads": out,
    }
