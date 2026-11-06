"""Cannot-contact API: mark, clear, queue payload, legacy outcome."""
from __future__ import annotations

from app.db import SessionLocal
from app.models import Business
from app.services.cannot_contact import cannot_contact_payload


def _seed(place_suffix: str, **extra) -> int:
    with SessionLocal() as db:
        b = Business(
            place_id=f"gap/cannot-contact-{place_suffix}",
            name=f"Cannot Co {place_suffix}",
            city="Dubai",
            country="United Arab Emirates",
            phone="+971500000001",
            website="https://example.com",
            marked_for_contact=True,
            contacted=False,
            product_line="STANDALONE",
            **extra,
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


def test_mark_and_clear_cannot_contact(auth_client):
    bid = _seed("mark-1")
    r = auth_client.post(
        f"/api/businesses/{bid}/cannot-contact",
        json={"via": "whatsapp", "notes": "Number invalid"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["cannot_contact"] is True
    assert data["cannot_contact_via"] == "whatsapp"
    # Stay marked so ranking mode still returns the lead for Can't contact tab
    assert data["marked_for_contact"] is True
    assert data["contacted"] is False
    assert data["outcome"] == "cannot_contact_whatsapp"
    assert "Number invalid" in (data.get("notes") or "")

    queue = auth_client.get("/api/sales/contact-queue?limit=0&mode=default")
    assert queue.status_code == 200, queue.text
    lead = next((x for x in queue.json().get("leads") or [] if x["id"] == bid), None)
    assert lead is not None
    assert lead["cannot_contact"] is True
    assert lead["cannot_contact_via"] == "whatsapp"

    r2 = auth_client.post(f"/api/businesses/{bid}/clear-cannot-contact")
    assert r2.status_code == 200, r2.text
    cleared = r2.json()
    assert cleared["cannot_contact"] is False
    assert cleared["cannot_contact_via"] in ("", None)
    assert cleared["marked_for_contact"] is True


def test_cannot_contact_via_validation(auth_client):
    bid = _seed("via-bad")
    r = auth_client.post(
        f"/api/businesses/{bid}/cannot-contact",
        json={"via": "fax"},
    )
    assert r.status_code == 422


def test_legacy_outcome_payload():
    b = Business(
        place_id="gap/cannot-legacy",
        name="Legacy",
        outcome="cannot_contact_instagram",
        cannot_contact=False,
        cannot_contact_via="",
    )
    payload = cannot_contact_payload(b)
    assert payload["cannot_contact"] is True
    assert payload["cannot_contact_via"] == "instagram"


def test_confirm_sent_clears_cannot_contact(auth_client):
    bid = _seed(
        "confirm-clear",
        email="c@example.com",
        ready_for_contact=True,
        outreach_channel="email",
    )
    auth_client.post(
        f"/api/businesses/{bid}/cannot-contact",
        json={"via": "both"},
    )
    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["contacted"] is True
    assert data["cannot_contact"] is False
