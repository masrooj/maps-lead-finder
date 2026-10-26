"""CRUD + evaluation for contact-queue ranking modes (DB-backed)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.models import ContactQueueMode

DEFAULT_MODES: list[dict[str, Any]] = [
    {
        "slug": "default",
        "name": "Balanced (score + discovery + starred)",
        "description": "All open prospects. Priority = qualification score + discovery notes + starred + stage bias.",
        "filter_json": {},
        "boost_json": {"base_boost": 0, "tag": ""},
        "sort_order": 0,
    },
    {
        "slug": "no_website_instagram",
        "name": "No website · Instagram only",
        "description": "Instagram present and no website — IG-first outreach.",
        "filter_json": {"has_website": False, "has_instagram": True},
        "boost_json": {"base_boost": 8.0, "tag": "IG · no site"},
        "sort_order": 10,
    },
    {
        "slug": "website_no_contact",
        "name": "Has website · no contact details",
        "description": "Website present but missing email, phone, and Instagram.",
        "filter_json": {"has_website": True, "has_email": False, "has_phone": False, "has_instagram": False},
        "boost_json": {"base_boost": 7.0, "tag": "site · needs contacts"},
        "sort_order": 20,
    },
    {
        "slug": "instagram_only_contact",
        "name": "Instagram is the only channel",
        "description": "Has Instagram but no email and no phone.",
        "filter_json": {"has_instagram": True, "has_email": False, "has_phone": False},
        "boost_json": {"base_boost": 8.0, "tag": "IG-only channel"},
        "sort_order": 30,
    },
    {
        "slug": "email_ready_unsent",
        "name": "Has email · not emailed yet",
        "description": "Email on file and email_sent is false.",
        "filter_json": {"has_email": True, "email_sent": False},
        "boost_json": {"base_boost": 6.0, "tag": "email ready"},
        "sort_order": 40,
    },
    {
        "slug": "whatsapp_ready_unsent",
        "name": "Has phone · WhatsApp not sent",
        "description": "Phone on file and whatsapp_sent is false.",
        "filter_json": {"has_phone": True, "whatsapp_sent": False},
        "boost_json": {"base_boost": 6.0, "tag": "WA ready"},
        "sort_order": 50,
    },
    {
        "slug": "starred_uncontacted",
        "name": "Starred · not contacted",
        "description": "Marked for contact and not yet contacted.",
        "filter_json": {"marked_for_contact": True, "contacted": False},
        "boost_json": {"base_boost": 5.0, "tag": "starred queue"},
        "sort_order": 60,
    },
    {
        "slug": "missing_contacts",
        "name": "Missing all contacts",
        "description": "No email, phone, or Instagram — needs enrichment.",
        "filter_json": {"has_email": False, "has_phone": False, "has_instagram": False},
        "boost_json": {"base_boost": 4.0, "tag": "needs enrich"},
        "sort_order": 70,
    },
    {
        "slug": "website_and_email_unsent",
        "name": "Website + email · not emailed",
        "description": "Has website and email; email not sent yet.",
        "filter_json": {"has_website": True, "has_email": True, "email_sent": False},
        "boost_json": {"base_boost": 6.5, "tag": "site + email"},
        "sort_order": 80,
    },
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_json(raw: str, default=None):
    try:
        data = json.loads(raw or ("{}" if default is None else json.dumps(default)))
        return data if data is not None else (default if default is not None else {})
    except Exception:
        return default if default is not None else {}


def seed_ranking_modes(db) -> int:
    created = 0
    for spec in DEFAULT_MODES:
        exists = db.scalar(select(ContactQueueMode).where(ContactQueueMode.slug == spec["slug"]))
        if exists:
            continue
        db.add(
            ContactQueueMode(
                slug=spec["slug"],
                name=spec["name"],
                description=spec["description"],
                filter_json=json.dumps(spec["filter_json"]),
                boost_json=json.dumps(spec["boost_json"]),
                sort_order=spec["sort_order"],
                active=True,
                created_at=_utcnow(),
                updated_at=_utcnow(),
            )
        )
        created += 1
    return created


def mode_to_dict(m: ContactQueueMode) -> dict:
    return {
        "id": m.id,
        "slug": m.slug,
        "name": m.name,
        "description": m.description,
        "filter": _parse_json(m.filter_json, {}),
        "boost": _parse_json(m.boost_json, {}),
        "sort_order": m.sort_order,
        "active": bool(m.active),
        # Compat with UI that used id/label
        "label": m.name,
    }


def list_modes(db, *, active_only: bool = True) -> list[dict]:
    q = select(ContactQueueMode).order_by(ContactQueueMode.sort_order, ContactQueueMode.id)
    rows = db.scalars(q).all()
    if active_only:
        rows = [r for r in rows if r.active]
    if not rows:
        seed_ranking_modes(db)
        db.flush()
        rows = db.scalars(q).all()
        if active_only:
            rows = [r for r in rows if r.active]
    return [mode_to_dict(r) for r in rows]


def get_mode_by_slug(db, slug: str) -> Optional[ContactQueueMode]:
    return db.scalar(select(ContactQueueMode).where(ContactQueueMode.slug == slug))


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return (s or "mode")[:80]


def match_filter(filt: dict, *, ch: dict, b_flags: dict) -> bool:
    """filt keys: has_website/email/phone/instagram, email_sent, whatsapp_sent, instagram_sent, marked_for_contact, contacted — bool or omit."""
    if not filt:
        return True
    checks = {
        "has_website": ch.get("has_website"),
        "has_email": ch.get("has_email"),
        "has_phone": ch.get("has_phone"),
        "has_instagram": ch.get("has_instagram"),
        "email_sent": b_flags.get("email_sent"),
        "whatsapp_sent": b_flags.get("whatsapp_sent"),
        "instagram_sent": b_flags.get("instagram_sent"),
        "marked_for_contact": b_flags.get("marked_for_contact"),
        "contacted": b_flags.get("contacted"),
    }
    for key, expected in filt.items():
        if expected is None or key not in checks:
            continue
        if bool(checks[key]) != bool(expected):
            return False
    return True


def mode_boost_points(boost: dict) -> tuple[float, list[str]]:
    pts = float(boost.get("base_boost") or 0)
    tags = []
    tag = (boost.get("tag") or "").strip()
    if tag:
        tags.append(tag)
    return pts, tags
