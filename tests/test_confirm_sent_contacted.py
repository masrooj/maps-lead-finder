"""First cold confirm-sent marks the business contacted and records channel flags."""
from __future__ import annotations

from app.db import SessionLocal
from app.models import Business


def test_confirm_sent_marks_contacted(auth_client):
    with SessionLocal() as db:
        b = Business(
            place_id="gap/confirm-sent-1",
            name="Cold Send Co",
            city="Sydney",
            country="Australia",
            email="cold@example.com",
            phone="+61400000001",
            international_phone="+61400000001",
            marked_for_contact=True,
            ready_for_contact=True,
            outreach_channel="whatsapp",
            contacted=False,
            email_sent=False,
            whatsapp_sent=False,
            instagram_sent=False,
            social_links_json='{"instagram":"https://instagram.com/coldsendco"}',
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        bid = b.id

    r = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "whatsapp", "notes": "First cold WA"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["contacted"] is True
    assert data["whatsapp_sent"] is True
    assert data["email_sent"] is False
    assert data.get("last_contacted_at")

    # Second channel still keeps contacted; sets email_sent
    r2 = auth_client.post(
        f"/api/businesses/{bid}/confirm-sent",
        json={"channel": "email"},
    )
    assert r2.status_code == 200, r2.text
    data2 = r2.json()
    assert data2["contacted"] is True
    assert data2["email_sent"] is True
    assert data2["whatsapp_sent"] is True

    queue = auth_client.get("/api/sales/contact-queue?limit=0&mode=default")
    assert queue.status_code == 200, queue.text
    lead = next((x for x in queue.json().get("leads") or [] if x["id"] == bid), None)
    assert lead is not None
    assert lead["contacted"] is True
    assert lead["whatsapp_sent"] is True
    assert lead["email_sent"] is True
    assert "last_contacted_at" in lead
