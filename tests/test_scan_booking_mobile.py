"""Mobile Bearer access for scan-booking + outreach_batch jobs."""
from __future__ import annotations

from app.config import LOGIN_USERNAME
from app.db import SessionLocal
from app.models import Business


def _bearer(client, password="test-password-sales-os"):
    client.post("/api/logout")
    client.cookies.clear()
    r = client.post(
        "/api/login",
        json={"username": LOGIN_USERNAME, "password": password},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    client.cookies.clear()
    return {"Authorization": f"Bearer {token}"}


def test_scan_booking_bearer_mobile(client, monkeypatch):
    import app.services.booking_detect as bd

    with SessionLocal() as db:
        b = Business(
            place_id="mobile/scan-1",
            name="Mobile Scan",
            country="Australia",
            city="Sydney",
            category="barbers",
            website="https://booksy.com/en-us/1",
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        bid = int(b.id)

    headers = _bearer(client)
    # Ensure Places path not required for this hit (direct provider URL)
    monkeypatch.setattr(bd, "places_reservable", lambda *a, **k: None)

    r = client.post(
        "/api/businesses/scan-booking",
        headers=headers,
        json={"ids": [bid], "limit": 5},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_booking"] == 1
    assert body["scanned"] == 1

    unauth = client.post(
        "/api/businesses/scan-booking",
        json={"ids": [bid], "limit": 5},
    )
    assert unauth.status_code in (401, 403)


def test_outreach_batch_job_bearer_mobile(client):
    with SessionLocal() as db:
        b = Business(
            place_id="mobile/ob-1",
            name="Mobile OB",
            country="Australia",
            city="Sydney",
            category="barbers",
            phone="+61400111222",
            marked_for_contact=True,
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        bid = int(b.id)

    headers = _bearer(client)
    job = client.post(
        "/api/sales/jobs",
        headers=headers,
        json={"job_type": "outreach_batch", "payload": {"business_ids": [bid]}},
    )
    assert job.status_code == 200, job.text
    jid = job.json()["id"]
    tick = client.post(f"/api/jobs/{jid}/tick", headers=headers)
    assert tick.status_code == 200, tick.text
    assert tick.json().get("done") is True


def test_health_capabilities_no_separate_maps_flag(client):
    r = client.get("/api/health")
    assert r.status_code == 200, r.text
    caps = r.json().get("capabilities") or {}
    assert caps.get("scan_booking") is True
    assert caps.get("outreach_batch") is True
    assert "maps_verify" in caps  # bool; false on CI without playwright/vercel
