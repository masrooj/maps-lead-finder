"""Already-connected park — same brand/other location sharing IG / WhatsApp / email."""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from sqlalchemy import or_, select

from app.models import Business

_IG_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?instagram\.com/([A-Za-z0-9._]{2,60})/?",
    re.I,
)
_IG_HANDLE_RE = re.compile(r"^@?([A-Za-z0-9._]{2,60})/?$")
_PHONE_DIGITS_RE = re.compile(r"\D+")


def is_already_connected(b: Business) -> bool:
    return bool(getattr(b, "already_connected", False))


def already_connected_payload(b: Business) -> dict:
    flag = is_already_connected(b)
    primary = getattr(b, "already_connected_primary_id", None) if flag else None
    return {
        "already_connected": flag,
        "already_connected_primary_id": int(primary) if primary else None,
    }


def apply_already_connected(
    b: Business,
    *,
    primary_id: int,
    notes: str = "",
) -> None:
    pid = int(primary_id)
    if pid == int(b.id):
        raise ValueError("primary_id must be a different lead")
    b.already_connected = True
    b.already_connected_primary_id = pid
    # Stay marked so CQ Connected tab still receives the row.
    b.marked_for_contact = True
    b.ready_for_contact = False
    b.ig_ready_at = None
    b.ig_awaiting_follow_back = False
    note = str(notes or "").strip()
    if note:
        existing = (b.notes or "").strip()
        prefix = f"[Already connected · primary #{pid}] {note}"
        b.notes = (f"{prefix}\n{existing}" if existing else prefix)[:5000]


def clear_already_connected(b: Business, *, rematch: bool = True) -> None:
    b.already_connected = False
    b.already_connected_primary_id = None
    if rematch:
        b.marked_for_contact = True
        b.ready_for_contact = False
        b.contacted = False


def normalize_ig_handle(raw: Any) -> str:
    s = str(raw or "").strip()
    if not s:
        return ""
    m = _IG_URL_RE.search(s)
    if m:
        return m.group(1).lower().rstrip(".")
    m2 = _IG_HANDLE_RE.match(s)
    if m2:
        return m2.group(1).lower().rstrip(".")
    if s.startswith("@"):
        return s[1:].lower().rstrip("/")
    return s.lower().rstrip("/")


def normalize_phone(raw: Any) -> str:
    digits = _PHONE_DIGITS_RE.sub("", str(raw or ""))
    if len(digits) >= 8:
        return digits[-10:] if len(digits) > 10 else digits
    return ""


def normalize_email(raw: Any) -> str:
    return str(raw or "").strip().lower()


def _social_ig(b: Business) -> str:
    try:
        links = json.loads(b.social_links_json or "{}")
    except Exception:
        links = {}
    if not isinstance(links, dict):
        return ""
    return normalize_ig_handle(links.get("instagram") or links.get("ig") or "")


def contact_keys(b: Business) -> dict[str, str]:
    phone = normalize_phone(getattr(b, "international_phone", None) or getattr(b, "phone", None))
    if not phone:
        phone = normalize_phone(getattr(b, "phone", None))
    return {
        "instagram": _social_ig(b),
        "phone": phone,
        "email": normalize_email(getattr(b, "email", None)),
    }


def find_same_contact_siblings(db, b: Business, *, limit: int = 25) -> list[dict]:
    """Other non-archived businesses sharing IG handle, phone, or email."""
    keys = contact_keys(b)
    if not any(keys.values()):
        return []

    clauses = [
        Business.id != b.id,
        or_(Business.archived.is_(False), Business.archived.is_(None)),
    ]
    # Broad fetch then normalize in Python (JSON socials are not easy to SQL-match).
    rows = db.scalars(select(Business).where(*clauses).limit(5000)).all()
    out: list[dict] = []
    for other in rows:
        if getattr(other, "lost", False):
            continue
        ok = contact_keys(other)
        shared: list[str] = []
        if keys["instagram"] and keys["instagram"] == ok["instagram"]:
            shared.append("instagram")
        if keys["phone"] and keys["phone"] == ok["phone"]:
            shared.append("whatsapp")
        if keys["email"] and keys["email"] == ok["email"]:
            shared.append("email")
        if not shared:
            continue
        out.append(
            {
                "id": other.id,
                "name": other.name or "",
                "city": other.city or "",
                "country": other.country or "",
                "shared_channels": shared,
                "contacted": bool(other.contacted),
                "ready_for_contact": bool(getattr(other, "ready_for_contact", False)),
                "already_connected": is_already_connected(other),
                "marked_for_contact": bool(other.marked_for_contact),
            }
        )
        if len(out) >= max(1, min(int(limit), 100)):
            break
    # Prefer already-contacted / ready primaries first.
    out.sort(
        key=lambda r: (
            0 if r.get("contacted") else 1,
            0 if r.get("ready_for_contact") else 1,
            (r.get("name") or "").lower(),
        )
    )
    return out
