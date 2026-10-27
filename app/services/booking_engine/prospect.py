"""Build full prospect / contact payload for AppointHive handover."""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy import select

from app.models import Business, Contact
from app.services.booking_engine.connect import crm_lead_id_for


def _social(business: Business) -> dict:
    try:
        return json.loads(business.social_links_json or "{}") or {}
    except Exception:
        return {}


def primary_contact(db, business_id: int) -> Optional[Contact]:
    return db.scalar(
        select(Contact).where(Contact.business_id == business_id).order_by(Contact.id.asc())
    )


def build_crm_prospect(db, business: Business) -> dict[str, Any]:
    """Canonical Sales OS → AppointHive business + contact snapshot."""
    contact = primary_contact(db, business.id)
    social = _social(business)
    contacts = [
        {
            "name": c.name or "",
            "role": c.role or "",
            "email": c.email or "",
            "phone": c.phone or "",
            "preferred_channel": c.preferred_channel or "",
            "verified": bool(c.verified),
        }
        for c in db.scalars(select(Contact).where(Contact.business_id == business.id).order_by(Contact.id.asc())).all()
    ]
    email = (business.email or "").strip() or ((contact.email or "").strip() if contact else "")
    phone = (business.international_phone or business.phone or "").strip()
    if not phone and contact:
        phone = (contact.phone or "").strip()
    contact_name = (contact.name if contact and contact.name else "") or (business.name or "")
    return {
        "crmLeadId": crm_lead_id_for(business.id),
        "externalCrmRef": crm_lead_id_for(business.id),
        "source": "AppointHive Sales OS",
        "business": {
            "id": business.id,
            "name": business.name or "",
            "placeId": business.place_id or "",
            "category": business.category or business.primary_type or "",
            "primaryType": business.primary_type or "",
            "email": email,
            "phone": (business.phone or "").strip(),
            "internationalPhone": (business.international_phone or "").strip() or phone,
            "website": business.website or "",
            "googleMapsUrl": business.google_maps_url or "",
            "address": business.address or "",
            "city": business.city or "",
            "state": getattr(business, "state", "") or "",
            "postcode": getattr(business, "postcode", "") or "",
            "country": business.country or "",
            "timezone": getattr(business, "timezone", "") or "",
            "rating": business.rating,
            "reviewsCount": business.reviews_count,
            "businessStatus": business.business_status or "",
            "editorialSummary": (business.editorial_summary or "")[:500],
            "social": social,
            "taxId": getattr(business, "tax_id", "") or "",
            "billingEntityName": getattr(business, "billing_entity_name", "") or "",
            "billingAddress": getattr(business, "billing_address", "") or "",
            "estimatedStaff": business.estimated_staff,
            "locationsCount": business.locations_count,
            "currentBookingSoftware": business.current_booking_software or "",
            "mainPain": (business.main_pain or "")[:1000],
            "notes": (business.notes or "")[:1000],
            "productLine": business.product_line or "STANDALONE",
            "currency": business.currency or "USD",
            "dealValue": business.deal_value,
            "leadSource": business.lead_source or "",
            "installationId": getattr(business, "be_installation_id", "") or "",
        },
        "primaryContact": {
            "name": contact_name,
            "email": email,
            "phone": phone,
            "role": (contact.role if contact else "") or "",
        },
        "contacts": contacts,
    }


def format_crm_prospect_message(prospect: dict[str, Any], *, intro: str = "") -> str:
    """Human-readable + JSON block for Super Admin inbox / request.message."""
    b = prospect.get("business") or {}
    c = prospect.get("primaryContact") or {}
    lines = [
        intro.strip() or "AppointHive Sales OS handover",
        "",
        f"Business: {b.get('name') or '—'}",
        f"CRM ref: {prospect.get('crmLeadId') or '—'}",
        f"Email: {c.get('email') or b.get('email') or '—'}",
        f"Phone: {c.get('phone') or b.get('internationalPhone') or b.get('phone') or '—'}",
        f"Contact: {c.get('name') or '—'}" + (f" ({c.get('role')})" if c.get("role") else ""),
        f"Website: {b.get('website') or '—'}",
        f"Maps: {b.get('googleMapsUrl') or '—'}",
        f"Address: {b.get('address') or '—'}",
        f"City/State/Post: {b.get('city') or '—'} / {b.get('state') or '—'} / {b.get('postcode') or '—'}",
        f"Country: {b.get('country') or '—'}",
        f"Category: {b.get('category') or '—'}",
        f"Tax / billing: {b.get('taxId') or '—'} · {b.get('billingEntityName') or '—'}",
        "",
        "--- CRM_PROSPECT_JSON ---",
        json.dumps(prospect, default=str)[:6000],
    ]
    return "\n".join(lines)


def buyer_fields_from_prospect(prospect: dict[str, Any]) -> tuple[str, str, str]:
    """email, buyerName, phone for BE purchase APIs."""
    b = prospect.get("business") or {}
    c = prospect.get("primaryContact") or {}
    email = (c.get("email") or b.get("email") or "").strip()
    name = (c.get("name") or b.get("name") or "").strip()
    phone = (c.get("phone") or b.get("internationalPhone") or b.get("phone") or "").strip()
    return email, name, phone
