"""Confirm-sent stores business-named follow-up task + full sent message history."""
from __future__ import annotations

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Activity, Business, Task


def test_confirm_sent_task_includes_business_and_message(auth_client):
    with SessionLocal() as db:
        b = Business(
            place_id="gap/outreach-msg-1",
            name="Barber House Sydney",
            city="Sydney",
            country="Australia",
            email="barber@example.com",
            phone="+61400001111",
            international_phone="+61400001111",
            marked_for_contact=True,
            ready_for_contact=True,
            outreach_channel="whatsapp",
            contacted=False,
            social_links_json='{"instagram":"https://www.instagram.com/accounts/login/"}',
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        bid = b.id

    body = {
        "channel": "whatsapp",
        "notes": "operator note",
        "message": "Hi Barber House,\n\nQuick idea for bookings in Sydney.\n\nThanks!",
        "outcome": "interested",
    }
    r = auth_client.post(f"/api/businesses/{bid}/confirm-sent", json=body)
    assert r.status_code == 200, r.text

    tasks = auth_client.get("/api/sales/tasks").json()["tasks"]
    mine = [t for t in tasks if t.get("business_id") == bid]
    assert mine, "expected follow-up task"
    t = mine[0]
    assert "Barber House Sydney" in (t.get("title") or "")
    assert "whatsapp" in (t.get("title") or "").lower()
    assert "Quick idea for bookings" in (t.get("description") or "")
    assert t.get("business_name") == "Barber House Sydney"
    assert "Quick idea for bookings" in (t.get("message") or "")

    acts = auth_client.get("/api/sales/activities?limit=50").json()["activities"]
    mine_a = [
        a
        for a in acts
        if a.get("business_id") == bid and a.get("activity_type") == "outreach_sent"
    ]
    assert mine_a, "expected outreach_sent activity"
    a = mine_a[0]
    assert "Barber House Sydney" in (a.get("summary") or "")
    assert "Quick idea for bookings" in (a.get("message") or a.get("next_action") or "")

    with SessionLocal() as db:
        task = db.scalars(select(Task).where(Task.business_id == bid)).first()
        assert task is not None
        assert "Barber House Sydney" in task.title
        act = db.scalars(
            select(Activity).where(Activity.business_id == bid, Activity.activity_type == "outreach_sent")
        ).first()
        assert act is not None
        assert "Quick idea for bookings" in (act.next_action or "")
