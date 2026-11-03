"""Contacted → To contact: explicit revert and delete-task undo."""
from __future__ import annotations

from app.db import SessionLocal
from app.models import Business, Task


def _seed_contacted(place_id: str) -> int:
    with SessionLocal() as db:
        b = Business(
            place_id=place_id,
            name="Revert Co",
            city="Melbourne",
            country="Australia",
            email="revert@example.com",
            phone="+61400000999",
            international_phone="+61400000999",
            marked_for_contact=True,
            contacted=True,
            whatsapp_sent=True,
            email_sent=False,
            instagram_sent=False,
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


def test_revert_to_contact_endpoint(auth_client):
    bid = _seed_contacted("gap/revert-to-contact-1")
    r = auth_client.post(f"/api/sales/prospects/{bid}/revert-to-contact")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("reverted") is True

    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b is not None
        assert b.contacted is False
        assert b.marked_for_contact is True
        assert b.whatsapp_sent is False

    queue = auth_client.get("/api/sales/contact-queue?limit=0&mode=default")
    assert queue.status_code == 200, queue.text
    lead = next((x for x in queue.json().get("leads") or [] if x["id"] == bid), None)
    assert lead is not None
    assert lead["contacted"] is False
    assert lead["marked_for_contact"] is True
    assert lead["whatsapp_sent"] is False


def test_delete_task_reverts_to_contact(auth_client):
    bid = _seed_contacted("gap/revert-to-contact-2")
    with SessionLocal() as db:
        t = Task(
            business_id=bid,
            title="Follow up after whatsapp",
            description="Auto-created when outreach was confirmed sent",
            priority="high",
            status="open",
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        tid = t.id

    r = auth_client.delete(f"/api/sales/tasks/{tid}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("deleted") == tid
    assert data.get("reverted_to_contact") is True
    assert data.get("business_id") == bid

    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b is not None
        assert b.contacted is False
        assert b.marked_for_contact is True
        assert b.whatsapp_sent is False
        assert db.get(Task, tid) is None
