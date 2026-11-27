"""v2 Maps booking verify service + job (mocked browser runner)."""
from __future__ import annotations

from app.db import SessionLocal
from app.models import Business
from app.services.maps_booking_verify import normalize_verify_result, verify_maps_booking


def test_normalize_verify_result_defaults():
    out = normalize_verify_result({"status": "weird"})
    assert out["status"] == "error"
    assert "checked_at" in out


def test_verify_maps_booking_park_hint():
    def runner(url: str):
        return {"status": "has_booking", "vendor_hint": "Fresha", "evidence": "cta:Reserve"}

    out = verify_maps_booking("https://maps.google.com/?cid=1", runner=runner)
    assert out["should_park"] is True
    assert out["should_to_contact"] is False


def test_verify_uncertain_does_not_park_when_confirmed_only():
    def runner(url: str):
        return {"status": "uncertain", "evidence": "captcha"}

    out = verify_maps_booking("https://maps.google.com/?cid=1", runner=runner)
    assert out["should_park"] is False
    assert out["should_to_contact"] is False


def test_maps_booking_verify_job_with_mock(auth_client):
    with SessionLocal() as db:
        b = Business(
            place_id="maps/v2-1",
            name="Maps Shop",
            country="Australia",
            city="Sydney",
            category="barbers",
            google_maps_url="https://maps.google.com/?cid=maps-v2-1",
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        bid = int(b.id)

    job = auth_client.post(
        "/api/sales/jobs",
        json={
            "job_type": "maps_booking_verify",
            "payload": {
                "business_ids": [bid],
                "park_only_on_confirmed": True,
                "mock_results": {
                    str(bid): {
                        "status": "has_booking",
                        "vendor_hint": "OpenTable",
                        "evidence": "mock",
                    }
                },
            },
        },
    )
    assert job.status_code == 200, job.text
    jid = job.json()["id"]
    tick = auth_client.post(f"/api/jobs/{jid}/tick")
    assert tick.status_code == 200, tick.text
    assert tick.json().get("done") is True
    assert tick.json()["stats"]["parked"] == 1

    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert (b.current_booking_software or "").strip() == "OpenTable"
        assert b.marked_for_contact is False
