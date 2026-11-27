"""Smoke: fresh leads → Scan booking → leave Discover (Has booking or To contact)."""
from __future__ import annotations

from app.db import SessionLocal
from app.models import Business


def test_smoke_scan_booking_search_pool_flow(auth_client, monkeypatch):
    import app.services.booking_detect as bd

    with SessionLocal() as db:
        hit = Business(
            place_id="smoke/hit",
            name="Smoke Fresha",
            country="Australia",
            city="Sydney",
            category="barbers",
            website="https://example.com/salon",
        )
        miss = Business(
            place_id="smoke/miss",
            name="Smoke Plain",
            country="Australia",
            city="Sydney",
            category="barbers",
            website="https://example.com/plain",
        )
        noweb = Business(
            place_id="smoke/noweb",
            name="Smoke NoWeb",
            country="Australia",
            city="Sydney",
            category="barbers",
            website="",
        )
        db.add_all([hit, miss, noweb])
        db.commit()
        db.refresh(hit)
        db.refresh(miss)
        db.refresh(noweb)
        hit_id, miss_id, noweb_id = int(hit.id), int(miss.id), int(noweb.id)

    def fake_detect(b, **kwargs):
        website = (getattr(b, "website", None) or "")
        if "salon" in website:
            return {"has_booking": True, "software_name": "Fresha", "source": "website"}
        return {"has_booking": False, "software_name": "", "source": "none"}

    monkeypatch.setattr(bd, "detect_booking_for_business", fake_detect)

    before = auth_client.get(
        "/api/businesses",
        params={"status": "fresh", "scope": "city", "country": "Australia", "city": "Sydney"},
    )
    assert before.status_code == 200
    before_ids = {row["id"] for row in before.json()["results"]}
    assert {hit_id, miss_id, noweb_id} <= before_ids

    scan = auth_client.post(
        "/api/businesses/scan-booking",
        json={"ids": [hit_id, miss_id, noweb_id], "limit": 10},
    )
    assert scan.status_code == 200, scan.text
    body = scan.json()
    assert body["has_booking"] == 1
    assert body["to_contact"] == 2
    assert body["scanned"] == 3

    after = auth_client.get(
        "/api/businesses",
        params={"status": "fresh", "scope": "city", "country": "Australia", "city": "Sydney"},
    )
    after_ids = {row["id"] for row in after.json()["results"]}
    assert hit_id not in after_ids
    assert miss_id not in after_ids
    assert noweb_id not in after_ids

    with SessionLocal() as db:
        assert (db.get(Business, hit_id).current_booking_software or "") == "Fresha"
        assert db.get(Business, miss_id).marked_for_contact is True
        assert db.get(Business, noweb_id).marked_for_contact is True
