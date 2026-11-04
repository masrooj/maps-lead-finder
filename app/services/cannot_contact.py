"""Cannot-contact workflow — proper columns + legacy outcome fallback."""
from __future__ import annotations

from typing import Optional

from app.models import Business

CANNOT_VIA = ("whatsapp", "instagram", "both")
OUTCOME_BY_VIA = {
    "whatsapp": "cannot_contact_whatsapp",
    "instagram": "cannot_contact_instagram",
    "both": "cannot_contact_both",
}
VIA_BY_OUTCOME = {v: k for k, v in OUTCOME_BY_VIA.items()}
VIA_BY_OUTCOME["cannot_contact"] = "both"


def normalize_via(raw: Optional[str]) -> str:
    v = str(raw or "").strip().lower()
    if v in ("wa", "whatsapp", "phone"):
        return "whatsapp"
    if v in ("ig", "instagram", "insta"):
        return "instagram"
    if v in ("both", "all", "wa_ig", "whatsapp_instagram"):
        return "both"
    return ""


def via_from_outcome(outcome: Optional[str]) -> str:
    o = str(outcome or "").strip().lower()
    if o in VIA_BY_OUTCOME:
        return VIA_BY_OUTCOME[o]
    if o.startswith("cannot_contact_"):
        rest = o[len("cannot_contact_") :]
        return normalize_via(rest) or "both"
    if o.startswith("cannot_contact"):
        return "both"
    return ""


def is_cannot_contact(b: Business) -> bool:
    if bool(getattr(b, "cannot_contact", False)):
        return True
    return bool(via_from_outcome(getattr(b, "outcome", None)))


def cannot_via(b: Business) -> str:
    via = normalize_via(getattr(b, "cannot_contact_via", None))
    if via:
        return via
    return via_from_outcome(getattr(b, "outcome", None)) or ""


def cannot_contact_payload(b: Business) -> dict:
    flag = is_cannot_contact(b)
    via = cannot_via(b) if flag else ""
    return {
        "cannot_contact": flag,
        "cannot_contact_via": via,
    }


def apply_cannot_contact(b: Business, *, via: str, notes: str = "") -> None:
    v = normalize_via(via) or "both"
    if v not in CANNOT_VIA:
        v = "both"
    b.cannot_contact = True
    b.cannot_contact_via = v
    # Stay marked so default contact-queue ranking (marked=true) still returns the lead
    # for the Can't contact tab; UI excludes these from To contact via cannot_contact.
    b.marked_for_contact = True
    b.contacted = False
    b.outcome = OUTCOME_BY_VIA[v]
    note = str(notes or "").strip()
    if note:
        existing = (b.notes or "").strip()
        prefix = f"[Cannot contact · {v}] {note}"
        b.notes = (f"{prefix}\n{existing}" if existing else prefix)[:5000]


def clear_cannot_contact(b: Business, *, rematch: bool = True) -> None:
    b.cannot_contact = False
    b.cannot_contact_via = ""
    if via_from_outcome(b.outcome):
        b.outcome = ""
    if rematch:
        b.marked_for_contact = True
        b.contacted = False
