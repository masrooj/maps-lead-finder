"""Outreach compliance checks — opt-out, suppression, packs."""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import urlparse

from sqlalchemy import select

from app.models import Business, CompliancePack, MessageTemplate, Suppression


def _domain_from_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email:
        return ""
    return email.split("@", 1)[1]


def _domain_from_website(website: str) -> str:
    try:
        host = (urlparse(website or "").hostname or "").lower().removeprefix("www.")
        return host
    except Exception:
        return ""


def is_suppressed(db, *, email: str = "", phone: str = "", website: str = "") -> Optional[str]:
    values = []
    if email:
        values.append(("email", email.strip().lower()))
        dom = _domain_from_email(email)
        if dom:
            values.append(("domain", dom))
    if phone:
        digits = "".join(c for c in phone if c.isdigit())
        if digits:
            values.append(("phone", digits))
    dom_w = _domain_from_website(website)
    if dom_w:
        values.append(("domain", dom_w))
    if not values:
        return None
    rows = db.scalars(select(Suppression)).all()
    for s in rows:
        for kind, val in values:
            if s.kind == kind and s.value == val:
                return f"Suppressed ({kind}: {val})" + (f" — {s.reason}" if s.reason else "")
    return None


def outreach_block_reason(db, business: Business, channel: str = "") -> Optional[str]:
    if getattr(business, "opt_out", False):
        channels = (business.opt_out_channels or "").lower()
        if not channels or not channel or channel.lower() in channels.split(",") or channels.strip() in ("*", "all"):
            return "Prospect is opted out" + (f" ({business.opt_out_channels})" if business.opt_out_channels else "")
    reason = is_suppressed(
        db,
        email=business.email or "",
        phone=business.phone or business.international_phone or "",
        website=business.website or "",
    )
    if reason:
        return reason
    # Country pack enforce
    country = (business.country or "").strip().upper()
    code = country if len(country) <= 3 else ""
    # Map common names
    name_map = {
        "AUSTRALIA": "AU",
        "NEW ZEALAND": "NZ",
        "UNITED STATES": "US",
        "USA": "US",
        "CANADA": "CA",
        "UNITED KINGDOM": "GB",
        "UK": "GB",
        "UAE": "AE",
    }
    if not code:
        code = name_map.get(country, "")
    if len(country) == 2:
        code = country
    if code:
        pack = db.scalar(select(CompliancePack).where(CompliancePack.country_code == code))
        if pack and pack.enforce:
            try:
                required = json.loads(pack.required_fields_json or "[]")
            except Exception:
                required = []
            for field in required:
                val = getattr(business, field, None)
                if field == "permission_status_json":
                    try:
                        data = json.loads(val or "{}")
                    except Exception:
                        data = {}
                    if not data:
                        return f"Compliance pack {code} requires permission status before outreach"
                elif not val:
                    return f"Compliance pack {code} requires field: {field}"
            if not pack.counsel_reviewed_at:
                return f"Compliance pack {code} is in enforce mode but not counsel-reviewed"
    return None


def template_copy_allowed(db, business: Business, template: Optional[MessageTemplate], channel: str) -> Optional[str]:
    block = outreach_block_reason(db, business, channel)
    if block:
        return block
    if template is not None and getattr(template, "governance_ok", True) is False:
        return "Template failed content governance (claims not verified). Fix template before copy/send."
    return None
