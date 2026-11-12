"""Sales flow automation: outreach → activity/task → stage advances → contact ranking."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import or_, select

from app.models import Activity, AppSetting, Business, DiscoveryNote, PipelineStage, Task
from app.services.already_connected import (
    already_connected_payload,
    clear_already_connected,
    is_already_connected,
)
from app.services.cannot_contact import cannot_contact_payload, clear_cannot_contact, is_cannot_contact
from app.services.pipeline import get_stage, list_stages, move_stage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


STANDALONE_ORDER = [
    "identified",
    "to_contact",
    "ready_to_contact",
    "contacted",
    "follow_up_3",
    "follow_up_7",
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
    "to_contact",
    "ready_to_contact",
    "contacted",
    "follow_up_3",
    "follow_up_7",
    "engaged",
    "qualified",
    "demo_scheduled",
    "demo_completed",
    "trial_offered",
    "trial_active",
    "subscribed",
]

# Legacy single follow_up maps into the +3 column for ordering comparisons.
_FOLLOW_UP_SLUGS = frozenset({"follow_up", "follow_up_3", "follow_up_7"})

OUTREACH_CHANNELS = ("whatsapp", "instagram", "email")

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
    if slug == "follow_up":
        slug = "follow_up_3"
    try:
        return order.index(slug)
    except ValueError:
        return -1


def days_since_last_contact(b: Business) -> int:
    """Calendar days since last_contacted_at (0 = today). -1 if unknown."""
    last = getattr(b, "last_contacted_at", None)
    if not last:
        return -1
    last_aware = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
    return (_utcnow().date() - last_aware.date()).days


def follow_up_target_slug(b: Business) -> str:
    """Pick Follow-up +3 vs +7 from kind, days since contact, or scheduled horizon.

    Aligned with Contacted timing buckets: 7+ days since → +7; else +3 default.
    """
    kind = (getattr(b, "follow_up_kind", None) or "").strip().lower()
    if kind in ("plus7", "plus_7", "+7"):
        return "follow_up_7"
    if kind in ("plus3", "plus_3", "+3", "call_today"):
        return "follow_up_3"
    since = days_since_last_contact(b)
    if since >= 7:
        return "follow_up_7"
    due = getattr(b, "next_follow_up_at", None)
    if due:
        due_aware = due if due.tzinfo else due.replace(tzinfo=timezone.utc)
        # Horizon when due was still ahead; overdue defaults to +3 unless since≥7.
        delta_days = (due_aware.date() - _utcnow().date()).days
        if delta_days > 3:
            return "follow_up_7"
    return "follow_up_3"


def classify_follow_up_kind(follow_up_at: Optional[datetime], *, same_day_call: bool = False) -> str:
    if same_day_call:
        return "call_today"
    if not follow_up_at:
        return "plus3"
    due_aware = follow_up_at if follow_up_at.tzinfo else follow_up_at.replace(tzinfo=timezone.utc)
    delta_days = (due_aware.date() - _utcnow().date()).days
    if delta_days > 3:
        return "plus7"
    return "plus3"


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


def ig_warm_follow_only(b: Business) -> bool:
    """Already-contacted leads only need Follow (not like/comment) for IG soft-ask."""
    return bool(getattr(b, "contacted", False) or getattr(b, "instagram_sent", False))


def ig_warm_tick_count(b: Business) -> int:
    return sum(
        1
        for field in ("ig_warm_follow", "ig_warm_like", "ig_warm_comment")
        if getattr(b, field, False)
    )


def ig_warm_complete(b: Business) -> bool:
    """Cold: Follow required + ≥1 of like/comment (2 of 3). Contacted: Follow only."""
    follow = bool(getattr(b, "ig_warm_follow", False))
    if ig_warm_follow_only(b):
        return follow
    if not follow:
        return False
    return bool(getattr(b, "ig_warm_like", False) or getattr(b, "ig_warm_comment", False))


def ig_warm_payload(b: Business) -> dict:
    follow = bool(getattr(b, "ig_warm_follow", False))
    like = bool(getattr(b, "ig_warm_like", False))
    comment = bool(getattr(b, "ig_warm_comment", False))
    follow_only = ig_warm_follow_only(b)
    complete = ig_warm_complete(b)
    return {
        "ig_warm_follow": follow,
        "ig_warm_like": like,
        "ig_warm_comment": comment,
        "ig_warm_follow_only": follow_only,
        "ig_warm_complete": complete,
        "ig_warm_tick_count": ig_warm_tick_count(b),
    }


def normalize_outreach_channel(value: Any) -> str:
    ch = str(value or "").strip().lower()
    return ch if ch in OUTREACH_CHANNELS else ""


def can_ready_for_contact(b: Business, channel: Optional[str] = None) -> tuple[bool, str]:
    """Whether promote-to-Ready is allowed. Returns (ok, reason).

    Intentionally ignores IG soft-DM daily cap — that gate is Confirm sent only.
    """
    if is_already_connected(b):
        return False, "Already connected to another location — clear park first"
    if is_cannot_contact(b):
        return False, "Can't contact — clear park first"
    if bool((getattr(b, "current_booking_software", None) or "").strip()):
        return False, "Has booking — clear park first"
    ch = normalize_outreach_channel(channel if channel is not None else getattr(b, "outreach_channel", ""))
    if not ch:
        return False, "Pick WhatsApp, Instagram, or Email first"
    facts = _channel_facts(b)
    if ch == "whatsapp":
        if not facts.get("has_phone"):
            return False, "No phone for WhatsApp"
        return True, ""
    if ch == "email":
        if not facts.get("has_email"):
            return False, "No email address"
        return True, ""
    if ch == "instagram":
        if not facts.get("has_instagram"):
            return False, "No Instagram handle"
        if not ig_warm_complete(b):
            return False, "IG warm needs Follow + like or comment"
        return True, ""
    return False, "Unknown channel"


def assert_ready_for_contact(b: Business, channel: Optional[str] = None) -> None:
    ok, reason = can_ready_for_contact(b, channel)
    if not ok:
        raise ValueError(reason or "Not ready for contact")


def assert_confirm_sent_ready(b: Business, *, allow_unready: bool = False) -> None:
    """First cold confirm-sent requires Ready (UI + API). Invoice/pay may pass allow_unready."""
    if is_already_connected(b):
        raise ValueError("Already connected — do not message this location")
    if bool(getattr(b, "ig_awaiting_follow_back", False)):
        raise ValueError("Awaiting IG follow-back — clear hold before messaging")
    if allow_unready or _is_contacted_queue_state(b):
        return
    if not bool(getattr(b, "ready_for_contact", False)):
        raise ValueError(
            "Move to Ready to contact first (pick channel + IG warm on To contact)"
        )


def apply_outreach_channel(b: Business, channel: str) -> None:
    """Set outreach channel; demote from Ready if switching to IG without warm."""
    ch = normalize_outreach_channel(channel)
    if not ch:
        raise ValueError("outreach_channel must be whatsapp, instagram, or email")
    facts = _channel_facts(b)
    if ch == "whatsapp" and not facts.get("has_phone"):
        raise ValueError("No phone for WhatsApp")
    if ch == "email" and not facts.get("has_email"):
        raise ValueError("No email address")
    if ch == "instagram" and not facts.get("has_instagram"):
        raise ValueError("No Instagram handle")
    b.outreach_channel = ch
    if ch == "instagram" and not ig_warm_complete(b):
        b.ready_for_contact = False
        clear_ig_ready_at(b)
    elif ch == "instagram" and bool(getattr(b, "ready_for_contact", False)):
        stamp_ig_ready_at(b)
    elif ch != "instagram":
        clear_ig_ready_at(b)
    b.updated_at = _utcnow()


def stamp_ig_ready_at(b: Business) -> None:
    """Clock starts when lead enters Ready on Instagram (Settings-timezone day wait)."""
    ch = normalize_outreach_channel(getattr(b, "outreach_channel", ""))
    if ch == "instagram":
        b.ig_ready_at = _utcnow()
    else:
        b.ig_ready_at = None


def clear_ig_ready_at(b: Business) -> None:
    b.ig_ready_at = None


def ig_ready_wait_days(db) -> int:
    raw = _setting_value(db, "ig_ready_wait_days", "1").strip() or "1"
    try:
        return max(0, min(14, int(raw)))
    except ValueError:
        return 1


def ig_ready_wait_payload(db, b: Business) -> dict:
    """IG soft-ask unlock after N calendar days in Settings timezone (promote stamp).

    Missing stamp → grandfather (unlocked). WA/Email and already-contacted/IG-sent skip wait.
    """
    wait_days = ig_ready_wait_days(db)
    tz, tz_name = _timezone_for_quota(db)
    ch = normalize_outreach_channel(getattr(b, "outreach_channel", ""))
    stamp = getattr(b, "ig_ready_at", None)
    stamp_iso = stamp.isoformat() if stamp else ""
    base = {
        "applies": False,
        "wait_days": wait_days,
        "unlocked": True,
        "ig_ready_at": stamp_iso,
        "unlocks_on": None,
        "unlocks_at": None,
        "timezone": tz_name,
        "reason": "",
        "can_message": True,
    }
    if bool(getattr(b, "ig_awaiting_follow_back", False)):
        return {
            **base,
            "applies": True,
            "unlocked": False,
            "can_message": False,
            "reason": "Awaiting IG follow-back",
        }
    if ch != "instagram":
        return base
    # Bumps / already contacted: no Ready wait (Follow-only warm still applies separately).
    if ig_warm_follow_only(b):
        return base
    ready = bool(getattr(b, "ready_for_contact", False))
    if not ready:
        return {**base, "can_message": False}
    # Grandfather existing Ready leads without a stamp.
    if not stamp:
        return {**base, "applies": True, "unlocked": True, "can_message": True}
    stamp_aware = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    stamp_local = stamp_aware.astimezone(tz)
    if wait_days <= 0:
        return {
            **base,
            "applies": True,
            "unlocked": True,
            "can_message": True,
            "unlocks_on": stamp_local.date().isoformat(),
        }

    unlock_date = stamp_local.date() + timedelta(days=wait_days)
    now_local = datetime.now(timezone.utc).astimezone(tz)
    unlocked = now_local.date() >= unlock_date
    unlocks_at = datetime.combine(unlock_date, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    reason = ""
    if not unlocked:
        days_left = (unlock_date - now_local.date()).days
        if wait_days == 1 and days_left == 1:
            reason = "Warming — soft ask tomorrow"
        elif days_left <= 1:
            reason = f"Warming — soft ask unlocks {unlock_date.isoformat()}"
        else:
            reason = f"Warming — soft ask in {days_left} days ({unlock_date.isoformat()})"
    return {
        "applies": True,
        "wait_days": wait_days,
        "unlocked": unlocked,
        "ig_ready_at": stamp_iso,
        "unlocks_on": unlock_date.isoformat(),
        "unlocks_at": unlocks_at.isoformat(),
        "timezone": tz_name,
        "reason": reason,
        "can_message": unlocked,
    }


def assert_ig_ready_wait_elapsed(db, b: Business) -> None:
    payload = ig_ready_wait_payload(db, b)
    if payload.get("applies") and not payload.get("unlocked"):
        raise ValueError(payload.get("reason") or "IG Ready wait not elapsed")


def messaging_gate_payload(db, b: Business) -> dict:
    """Unified can_message for CQ / ready-status / mobile."""
    wait = ig_ready_wait_payload(db, b)
    reason = ""
    can = True
    if is_already_connected(b):
        can = False
        reason = "Already connected to another location"
    elif is_cannot_contact(b):
        can = False
        reason = "Can't contact"
    elif bool((getattr(b, "current_booking_software", None) or "").strip()):
        can = False
        reason = "Has booking"
    elif bool(getattr(b, "ig_awaiting_follow_back", False)):
        can = False
        reason = "Awaiting IG follow-back"
    elif not bool(getattr(b, "ready_for_contact", False)) and not _is_contacted_queue_state(b):
        can = False
        reason = "Not on Ready"
    elif wait.get("applies") and not wait.get("unlocked"):
        can = False
        reason = wait.get("reason") or "IG Ready wait not elapsed"
    elif wait.get("can_message") is False:
        can = False
        reason = wait.get("reason") or "Cannot message yet"
    return {
        "can_message": can,
        "can_message_reason": reason,
        "ig_awaiting_follow_back": bool(getattr(b, "ig_awaiting_follow_back", False)),
        "ig_ready_wait": wait,
        **already_connected_payload(b),
    }


def set_ig_awaiting_follow_back(b: Business, awaiting: bool) -> None:
    b.ig_awaiting_follow_back = bool(awaiting)
    b.updated_at = _utcnow()


def promote_to_ready(db, b: Business, *, actor_id: Optional[int] = None) -> dict:
    assert_ready_for_contact(b)
    b.marked_for_contact = True
    b.ready_for_contact = True
    stamp_ig_ready_at(b)
    clear_cannot_contact(b, rematch=False)
    if is_already_connected(b):
        clear_already_connected(b, rematch=False)
        b.marked_for_contact = True
        b.ready_for_contact = True
    b.updated_at = _utcnow()
    advance_at_least(db, b, "ready_to_contact", actor_id=actor_id, note="Promoted to Ready to contact")
    db.commit()
    db.refresh(b)
    wait = ig_ready_wait_payload(db, b)
    gate = messaging_gate_payload(db, b)
    return {
        "ok": True,
        "ready_for_contact": True,
        "outreach_channel": b.outreach_channel or "",
        "ig_ready_at": wait.get("ig_ready_at") or "",
        "ig_ready_wait": wait,
        **gate,
    }


def demote_to_to_contact(db, b: Business, *, actor_id: Optional[int] = None, note: str = "") -> dict:
    """Ready → To contact prep; keep channel + warm ticks."""
    b.marked_for_contact = True
    b.ready_for_contact = False
    b.contacted = False
    clear_ig_ready_at(b)
    b.updated_at = _utcnow()
    db.add(
        Activity(
            business_id=b.id,
            activity_type="demoted_to_contact",
            summary="Demoted from Ready to To contact",
            outcome=(note or "")[:500],
            created_by=actor_id,
        )
    )
    pl = (b.product_line or "STANDALONE").upper()
    target = get_stage(db, pl, "to_contact")
    moved = False
    if target:
        # move_stage commits flags + stage together
        move_stage(db, b, target.id, actor_id=actor_id, note=note or "Demoted to To contact", force=True)
        moved = True
    else:
        db.commit()
        db.refresh(b)
    return {"ok": True, "ready_for_contact": False, "stage_moved": moved, "ig_ready_at": ""}


def maybe_promote_follow_up(db, b: Business, *, actor_id: Optional[int] = None) -> bool:
    """Move contacted → follow_up_3 / follow_up_7 when due/past or days-since gap (not call_today).

    Syncs Pipeline Follow-up columns with Contacted +3/+7 passed buckets.
    """
    if not b.contacted or b.lost:
        return False
    kind = (getattr(b, "follow_up_kind", None) or "").strip()
    if kind == "call_today":
        return False
    due = getattr(b, "next_follow_up_at", None)
    since = days_since_last_contact(b)
    ready = False
    if due:
        due_aware = due if due.tzinfo else due.replace(tzinfo=timezone.utc)
        if due_aware <= _utcnow():
            ready = True
    elif since >= 3:
        # No due date but Contacted filter already shows +3/+7 passed
        ready = True
    if not ready:
        return False
    target = follow_up_target_slug(b)
    current = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
    if current and current.slug not in ("contacted", "ready_to_contact", "to_contact", "identified", "follow_up"):
        if current.slug in _FOLLOW_UP_SLUGS:
            # Allow +3 → +7 if kind/horizon says so; never regress.
            if _stage_index(current.slug, b.product_line or "STANDALONE") >= _stage_index(
                target, b.product_line or "STANDALONE"
            ):
                return False
        elif _stage_index(current.slug, b.product_line or "STANDALONE") > _stage_index(
            "follow_up_7", b.product_line or "STANDALONE"
        ):
            return False
    return advance_at_least(
        db, b, target, actor_id=actor_id, note=f"Auto: scheduled follow-up due → {target}"
    )


def follow_up_badge(b: Business) -> dict:
    """UI badge helpers for Contacted / Follow-up columns."""
    due = getattr(b, "next_follow_up_at", None)
    kind = (getattr(b, "follow_up_kind", None) or "").strip()
    if kind == "call_today":
        return {"kind": "call_today", "label": "Call today", "days": 0}
    if not due:
        return {"kind": "", "label": "", "days": None}
    due_aware = due if due.tzinfo else due.replace(tzinfo=timezone.utc)
    now = _utcnow()
    delta_days = (due_aware.date() - now.date()).days
    if delta_days < 0:
        return {"kind": "overdue", "label": f"Overdue {-delta_days}d", "days": delta_days}
    if delta_days == 0:
        return {"kind": "due_today", "label": "Due today", "days": 0}
    if delta_days <= 3:
        return {"kind": "plus3", "label": f"+{delta_days} day follow-up", "days": delta_days}
    if delta_days <= 7:
        return {"kind": "plus7", "label": f"+{delta_days} day follow-up", "days": delta_days}
    return {"kind": "scheduled", "label": f"+{delta_days} day follow-up", "days": delta_days}


def _setting_value(db, key: str, default: str = "") -> str:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if not row or row.value is None:
        return default
    return str(row.value)


def ig_soft_dm_daily_cap(db) -> int:
    raw = _setting_value(db, "ig_soft_dm_daily_cap", "8").strip() or "8"
    try:
        return max(0, int(raw))
    except ValueError:
        return 8


def _timezone_for_quota(db):
    from zoneinfo import ZoneInfo

    name = _setting_value(db, "default_timezone", "Australia/Sydney").strip() or "Australia/Sydney"
    try:
        return ZoneInfo(name), name
    except Exception:
        return ZoneInfo("Australia/Sydney"), "Australia/Sydney"


def ig_soft_dm_day_bounds(db) -> tuple[datetime, datetime, str]:
    """UTC-aware [start, end) for 'today' in default_timezone."""
    tz, name = _timezone_for_quota(db)
    now_local = datetime.now(timezone.utc).astimezone(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc), name


def ig_soft_dm_usage_today(db) -> int:
    """Count IG outreach_sent activities created today (default_timezone day)."""
    start, end, _ = ig_soft_dm_day_bounds(db)
    rows = db.scalars(
        select(Activity).where(
            Activity.activity_type == "outreach_sent",
            Activity.channel == "instagram",
            Activity.created_at >= start,
            Activity.created_at < end,
        )
    ).all()
    return len(rows)


def _lock_ig_soft_dm_cap_setting(db) -> AppSetting:
    """Row-lock the cap setting so concurrent confirms serialize quota checks."""
    row = db.scalar(
        select(AppSetting).where(AppSetting.key == "ig_soft_dm_daily_cap").with_for_update()
    )
    if row:
        return row
    db.add(AppSetting(key="ig_soft_dm_daily_cap", value="8"))
    db.flush()
    row = db.scalar(
        select(AppSetting).where(AppSetting.key == "ig_soft_dm_daily_cap").with_for_update()
    )
    if not row:
        raise RuntimeError("ig_soft_dm_daily_cap setting missing after insert")
    return row


def ig_outreach_quota(db) -> dict:
    cap = ig_soft_dm_daily_cap(db)
    used = ig_soft_dm_usage_today(db)
    _, _, tz_name = ig_soft_dm_day_bounds(db)
    remaining = max(0, cap - used)
    return {
        "cap": cap,
        "used": used,
        "remaining": remaining,
        "timezone": tz_name,
    }


def assert_ig_soft_send_allowed(db, b: Business, *, skip_cap: bool = False) -> None:
    """Raise ValueError with operator-facing message if IG soft confirm is blocked.

    When skip_cap is True (already instagram_sent), warm is not re-required for note-only
    re-confirms. Fresh IG sends on already-contacted leads only need Follow ticked.
    """
    if not skip_cap:
        _lock_ig_soft_dm_cap_setting(db)
    if not ig_warm_complete(b):
        if skip_cap:
            # Re-confirm / note update on a lead already marked IG-sent.
            return
        if ig_warm_follow_only(b):
            raise ValueError("IG warm incomplete: follow")
        missing = []
        if not getattr(b, "ig_warm_follow", False):
            missing.append("follow")
        if not (getattr(b, "ig_warm_like", False) or getattr(b, "ig_warm_comment", False)):
            missing.append("like or comment")
        raise ValueError(f"IG warm incomplete: {', '.join(missing) or 'follow + like/comment'}")
    if skip_cap:
        return
    assert_ig_ready_wait_elapsed(db, b)
    quota = ig_outreach_quota(db)
    if quota["remaining"] <= 0:
        raise ValueError(
            f"IG soft DM daily cap reached ({quota['used']}/{quota['cap']} in {quota['timezone']})"
        )


def maybe_log_ig_warm_complete(db, b: Business, *, actor_id: Optional[int] = None, before: dict) -> None:
    """When required warm ticks flip complete, log one sales Activity (not outreach_sent)."""
    follow_only = ig_warm_follow_only(b)
    if follow_only:
        was = bool(before.get("ig_warm_follow"))
        outcome = "follow"
    else:
        was_follow = bool(before.get("ig_warm_follow"))
        was_extra = bool(before.get("ig_warm_like") or before.get("ig_warm_comment"))
        was = was_follow and was_extra
        outcome = "follow+" + ("like" if getattr(b, "ig_warm_like", False) else "comment")
    if was or not ig_warm_complete(b):
        return
    db.add(
        Activity(
            business_id=b.id,
            activity_type="instagram",
            channel="instagram",
            summary="IG warm complete",
            outcome=outcome,
            created_by=actor_id,
        )
    )


def remap_legacy_follow_up_stages(db, *, product_line: Optional[str] = None, limit: int = 2000) -> int:
    """Move prospects still on inactive legacy follow_up → follow_up_3 / follow_up_7."""
    stages = list(db.scalars(select(PipelineStage).where(PipelineStage.slug == "follow_up")).all())
    if product_line:
        stages = [s for s in stages if s.product_line == product_line.upper()]
    if not stages:
        return 0
    stage_ids = [s.id for s in stages]
    rows = db.scalars(
        select(Business)
        .where(Business.pipeline_stage_id.in_(stage_ids))
        .limit(max(1, min(int(limit), 5000)))
    ).all()
    moved = 0
    for b in rows:
        target_slug = follow_up_target_slug(b)
        pl = (b.product_line or "STANDALONE").upper()
        target = get_stage(db, pl, target_slug)
        if not target:
            continue
        move_stage(
            db,
            b,
            target.id,
            note=f"Auto: legacy follow_up → {target_slug}",
            force=True,
        )
        moved += 1
    return moved


def promote_due_follow_ups(db, *, product_line: Optional[str] = None, actor_id: Optional[int] = None, limit: int = 500) -> int:
    """Batch promote contacted → follow_up_3/7 when scheduled due is due/past."""
    remap_legacy_follow_up_stages(db, product_line=product_line, limit=limit)
    q = select(Business).where(
        Business.contacted.is_(True),
        or_(Business.lost.is_(False), Business.lost.is_(None)),
        Business.next_follow_up_at.is_not(None),
    )
    if product_line:
        q = q.where(Business.product_line == product_line.upper())
    rows = db.scalars(q.limit(max(1, min(int(limit), 2000)))).all()
    moved = 0
    for b in rows:
        if maybe_promote_follow_up(db, b, actor_id=actor_id):
            moved += 1
    return moved


def on_outreach_confirmed(
    db,
    b: Business,
    *,
    channel: str,
    actor_id: Optional[int] = None,
    follow_up_at: Optional[datetime] = None,
    outcome: str = "",
    notes: str = "",
    message: str = "",
    record_send: bool = True,
) -> dict:
    """Email/WA/IG confirm-sent: activity + follow-up task + auto stage advance.

    record_send=False skips a new outreach_sent row (re-confirm / note-only) so IG
    daily cap is not burned twice for the same lead.
    """
    biz_name = (b.name or f"#{b.id}").strip()
    msg = (message or "").strip()
    note = (notes or "").strip()
    # Prefer full sent body for history; keep operator notes on the activity outcome line.
    stored_message = msg or note
    outcome_bits = [x for x in (outcome.strip() if outcome else "", f"note: {note}" if msg and note else "") if x]
    if record_send:
        db.add(
            Activity(
                business_id=b.id,
                activity_type="outreach_sent",
                channel=channel,
                summary=f"{biz_name} · Confirmed {channel} send",
                outcome=" · ".join(outcome_bits),
                next_action=stored_message,
                created_by=actor_id,
            )
        )
    elif note or outcome or msg:
        db.add(
            Activity(
                business_id=b.id,
                activity_type="note",
                channel=channel,
                summary=f"{biz_name} · Updated after {channel} confirm",
                outcome=" · ".join(outcome_bits) or note,
                next_action=stored_message,
                created_by=actor_id,
            )
        )
    # Same-day call nudge when lead has a phone and operator did not set a follow-up.
    # Stays in Contacted with Call-today badge — does NOT auto-jump to Follow-up.
    same_day_call = False
    if follow_up_at is None:
        facts = _channel_facts(b)
        if facts.get("has_phone") and channel in ("instagram", "whatsapp"):
            follow_up_at = _utcnow()
            same_day_call = True
            if not getattr(b, "next_follow_up_at", None):
                b.next_follow_up_at = follow_up_at
        else:
            follow_up_at = _utcnow() + timedelta(days=3)
            if not getattr(b, "next_follow_up_at", None):
                b.next_follow_up_at = follow_up_at
    else:
        b.next_follow_up_at = follow_up_at
    b.follow_up_kind = classify_follow_up_kind(follow_up_at, same_day_call=same_day_call)
    due = follow_up_at
    title = (
        f"{biz_name} · Call today after {channel}"
        if same_day_call
        else f"{biz_name} · Follow up after {channel}"
    )
    description = msg or note or (
        "Optional same-day call — confirm-sent created this reminder"
        if same_day_call
        else "Auto-created when outreach was confirmed sent"
    )
    existing = db.scalar(
        select(Task).where(
            Task.business_id == b.id,
            Task.status == "open",
            Task.title.ilike("%follow%"),
        )
    )
    if not existing and same_day_call:
        existing = db.scalar(
            select(Task).where(
                Task.business_id == b.id,
                Task.status == "open",
                Task.title.ilike("%call today%"),
            )
        )
    if existing:
        existing.title = title
        existing.description = description
        existing.due_at = due
        existing.priority = existing.priority or "high"
        existing.updated_at = _utcnow()
    else:
        db.add(
            Task(
                business_id=b.id,
                title=title,
                description=description,
                priority="high",
                status="open",
                due_at=due,
                owner_id=actor_id,
            )
        )
    # Confirm-sent lands in Contacted only. Follow-up waits for scheduled due.
    b.ready_for_contact = False
    clear_ig_ready_at(b)
    advance_at_least(db, b, "contacted", actor_id=actor_id, note=f"Auto after {channel} confirm-sent")
    return {"ok": True, "channel": channel, "same_day_call": same_day_call}


def _is_contacted_queue_state(b: Business) -> bool:
    return bool(b.contacted or b.email_sent or b.whatsapp_sent or b.instagram_sent)


def revert_to_to_contact(
    db,
    b: Business,
    *,
    actor_id: Optional[int] = None,
    note: str = "",
    move_pipeline: bool = True,
) -> dict:
    """Undo confirm-sent: back to Ready (keep outreach_channel + warm ticks)."""
    was_contacted = _is_contacted_queue_state(b)
    b.contacted = False
    b.email_sent = False
    b.whatsapp_sent = False
    b.instagram_sent = False
    b.marked_for_contact = True
    b.ready_for_contact = True
    stamp_ig_ready_at(b)
    clear_cannot_contact(b, rematch=True)
    b.last_contacted_at = None
    b.next_follow_up_at = None
    b.follow_up_kind = ""
    b.updated_at = _utcnow()
    db.add(
        Activity(
            business_id=b.id,
            activity_type="reverted_to_contact",
            summary="Reverted from Contacted to Ready to contact",
            outcome=(note or "")[:500],
            created_by=actor_id,
        )
    )
    moved = False
    if move_pipeline:
        pl = (b.product_line or "STANDALONE").upper()
        target = get_stage(db, pl, "ready_to_contact")
        current = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        terminal = ("won", "lost", "nurture", "subscribed", "churned")
        early = (
            "identified",
            "to_contact",
            "ready_to_contact",
            "contacted",
            "follow_up",
            "follow_up_3",
            "follow_up_7",
        )
        if target and (not current or current.slug in early) and not (current and current.slug in terminal):
            if not current or current.slug != "ready_to_contact":
                move_stage(
                    db,
                    b,
                    target.id,
                    actor_id=actor_id,
                    note=note or "Reverted to Ready to contact",
                    force=True,
                )
                moved = True
                return {"ok": True, "reverted": True, "was_contacted": was_contacted, "stage_moved": moved}
    db.commit()
    db.refresh(b)
    return {"ok": True, "reverted": True, "was_contacted": was_contacted, "stage_moved": moved}


def on_marked_for_contact(db, b: Business, *, actor_id: Optional[int] = None) -> None:
    b.ready_for_contact = False
    clear_ig_ready_at(b)
    advance_at_least(db, b, "to_contact", actor_id=actor_id, note="Auto: marked for contact")


def on_discovery_saved(db, b: Business, *, actor_id: Optional[int] = None, painful_issue: str = "") -> None:
    # Discovery notes no longer auto-jump to Engaged (skips Contacted / +3/+7).
    # Pain still can nudge Qualified once already Engaged+.
    if painful_issue and len(painful_issue.strip()) > 20:
        stage = None
        if b.pipeline_stage_id:
            stage = db.get(PipelineStage, b.pipeline_stage_id)
        slug = (stage.slug if stage else "") or ""
        order = SAAS_ORDER if (b.product_line or "").upper() == "SAAS" else STANDALONE_ORDER
        if slug in order and order.index(slug) >= order.index("engaged"):
            advance_at_least(db, b, "qualified", actor_id=actor_id, note="Auto: discovery pain captured")


def on_demo_saved(db, b: Business, *, actor_id: Optional[int] = None, outcome: str = "") -> None:
    advance_at_least(db, b, "demo_completed", actor_id=actor_id, note="Auto: demo logged")
    oc = (outcome or "").lower()
    if any(k in oc for k in ("positive", "interested", "booked", "yes")):
        advance_at_least(db, b, "proposal_sent", actor_id=actor_id, note="Auto: positive demo")


def on_score_saved(db, b: Business, *, actor_id: Optional[int] = None) -> None:
    # Scores no longer auto-advance to Engaged — use mark_engaged explicitly.
    score = b.score_override if b.score_override is not None else b.qualification_score
    if score is not None and float(score) >= 7:
        stage = None
        if b.pipeline_stage_id:
            stage = db.get(PipelineStage, b.pipeline_stage_id)
        slug = (stage.slug if stage else "") or ""
        order = SAAS_ORDER if (b.product_line or "").upper() == "SAAS" else STANDALONE_ORDER
        if slug in order and order.index(slug) >= order.index("engaged"):
            advance_at_least(db, b, "qualified", actor_id=actor_id, note=f"Auto: score {score} ≥ 7")


def mark_engaged(db, b: Business, *, actor_id: Optional[int] = None, note: str = "") -> dict:
    """Explicit Engaged transition (reply / human signal) — not auto from discovery/score."""
    if is_already_connected(b):
        raise ValueError("Already connected park — clear before Engaged")
    clear_cannot_contact(b, rematch=False)
    b.ig_awaiting_follow_back = False
    b.updated_at = _utcnow()
    moved = advance_at_least(
        db,
        b,
        "engaged",
        actor_id=actor_id,
        note=(note or "Marked Engaged").strip()[:500] or "Marked Engaged",
    )
    db.add(
        Activity(
            business_id=b.id,
            activity_type="stage",
            summary="Marked Engaged",
            outcome=(note or "")[:500],
            created_by=actor_id,
        )
    )
    db.commit()
    db.refresh(b)
    return {"ok": True, "stage_moved": moved, "engaged": True}


def on_proposal_sent(db, b: Business, *, actor_id: Optional[int] = None) -> None:
    advance_at_least(db, b, "proposal_sent", actor_id=actor_id, note="Auto: proposal/invoice created")


def _discovery_boost_from_notes(notes: list) -> float:
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


def _discovery_boost(db, business_id: int) -> float:
    notes = db.scalars(
        select(DiscoveryNote).where(DiscoveryNote.business_id == business_id).order_by(DiscoveryNote.id.desc()).limit(3)
    ).all()
    return _discovery_boost_from_notes(list(notes))


def _discovery_boost_map(db, business_ids: list[int]) -> dict[int, float]:
    """One query for discovery-note boosts (avoids N+1 in contact-queue ranking)."""
    ids = [int(i) for i in business_ids if i is not None]
    if not ids:
        return {}
    # Newest notes first; keep ≤3 per business in Python.
    rows = db.scalars(
        select(DiscoveryNote)
        .where(DiscoveryNote.business_id.in_(ids))
        .order_by(DiscoveryNote.id.desc())
    ).all()
    by_biz: dict[int, list] = {}
    for note in rows:
        bid = int(note.business_id)
        bucket = by_biz.setdefault(bid, [])
        if len(bucket) < 3:
            bucket.append(note)
    return {bid: _discovery_boost_from_notes(notes) for bid, notes in by_biz.items()}


_IG_BLOCKED_PATHS = frozenset(
    {
        "p",
        "reel",
        "reels",
        "stories",
        "direct",
        "explore",
        "accounts",
        "account",
        "about",
        "legal",
        "developer",
        "directory",
        "nametag",
        "login",
        "signup",
    }
)


def normalize_instagram_profile(value: Any) -> str:
    """
    Canonical https://www.instagram.com/{handle}/ or '' when empty.
    Raises ValueError for non-empty invalid input (reel/post/login/garbage).
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        raw = "https:" + raw
    if re.match(r"^(www\.)?instagram\.com([/?#]|$)", raw, flags=re.I):
        raw = "https://" + raw
    handle = ""
    if not re.match(r"^https?://", raw, flags=re.I):
        handle = raw.lstrip("@").strip().split("/")[0].split("?")[0].split("#")[0].strip()
    else:
        try:
            from urllib.parse import unquote, urlparse

            u = urlparse(raw)
            host = (u.hostname or "").lower()
            if host and "instagram.com" not in host:
                raise ValueError(
                    "Enter a valid Instagram @handle or profile URL (not a reel/post/login link)"
                )
            path = unquote(u.path or "").strip("/")
            part = (path.split("/")[0] if path else "").lstrip("@").strip()
            handle = part
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(
                "Enter a valid Instagram @handle or profile URL (not a reel/post/login link)"
            ) from e
    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", handle or ""):
        raise ValueError(
            "Enter a valid Instagram @handle or profile URL (not a reel/post/login link)"
        )
    if handle.lower() in _IG_BLOCKED_PATHS:
        raise ValueError(
            "Enter a valid Instagram @handle or profile URL (not a reel/post/login link)"
        )
    return f"https://www.instagram.com/{handle}/"


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
    disc_map = _discovery_boost_map(db, [b.id for b in rows])
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
            "ready_for_contact": bool(getattr(b, "ready_for_contact", False)),
            "contacted": bool(b.contacted),
        }
        # Always keep can't-contact / Has booking / Ready / already-connected so tabs stay
        # populated even when the active ranking mode filters to marked-only, etc.
        has_booking = bool((getattr(b, "current_booking_software", None) or "").strip())
        keep_for_tab = (
            is_cannot_contact(b)
            or has_booking
            or is_already_connected(b)
            or bool(flags.get("ready_for_contact"))
        )
        if not rm.match_filter(filt, ch=ch, b_flags=flags) and not keep_for_tab:
            continue
        base = float(b.score_override if b.score_override is not None else (b.qualification_score or 0) or 0)
        disc = float(disc_map.get(int(b.id), 0.0))
        marked = 2.0 if b.marked_for_contact else 0.0
        contacted_penalty = -1.5 if b.contacted and not b.next_follow_up_at else 0.0
        stage_bonus = 0.0
        if stage:
            stage_bonus = {
                "ready_to_contact": 3.5,
                "to_contact": 3.0,
                "identified": 1.0,
                "contacted": 0.5,
                "follow_up": 2.5,
                "follow_up_3": 2.5,
                "follow_up_7": 2.6,
                "engaged": 2.0,
                "qualified": 1.5,
            }.get(stage.slug, 0.0)
        mode_pts, mode_tags = rm.mode_boost_points(boost_cfg)
        if active == "default":
            # Max-yield To contact order: phone+IG → IG only → phone only → rest
            if ch["has_phone"] and ch["has_instagram"]:
                mode_pts += 2.0
                mode_tags.append("phone + IG")
            elif ch["has_instagram"]:
                mode_pts += 1.2
                mode_tags.append("IG only")
            elif ch["has_phone"]:
                mode_pts += 0.8
                mode_tags.append("phone only")
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
        gate = messaging_gate_payload(db, b)
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
                "ready_for_contact": bool(getattr(b, "ready_for_contact", False)),
                "outreach_channel": (getattr(b, "outreach_channel", None) or "").strip(),
                "follow_up_kind": (getattr(b, "follow_up_kind", None) or "").strip(),
                "follow_up_badge": follow_up_badge(b),
                "contacted": bool(b.contacted),
                "opt_out": bool(b.opt_out),
                "outcome": b.outcome or "",
                **cannot_contact_payload(b),
                **already_connected_payload(b),
                "ig_awaiting_follow_back": bool(getattr(b, "ig_awaiting_follow_back", False)),
                "email": ch["email"],
                "phone": ch["phone"],
                "international_phone": ch["international_phone"],
                "website": ch["website"],
                "social_links": ch["social_links"],
                "email_sent": bool(b.email_sent),
                "whatsapp_sent": bool(b.whatsapp_sent),
                "instagram_sent": bool(b.instagram_sent),
                **ig_warm_payload(b),
                "ig_ready_at": b.ig_ready_at.isoformat() if getattr(b, "ig_ready_at", None) else "",
                "ig_ready_wait": gate.get("ig_ready_wait") or {},
                "can_message": gate.get("can_message"),
                "can_message_reason": gate.get("can_message_reason") or "",
                "last_contacted_at": b.last_contacted_at.isoformat() if getattr(b, "last_contacted_at", None) else "",
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
