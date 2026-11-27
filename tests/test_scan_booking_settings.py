"""Settings: scan_booking_limit / chunk + API ceiling."""
from __future__ import annotations

from app.db import SessionLocal
from app.models import Business
from app.services.booking_detect import scan_booking_limits


def _reset_scan_caps(auth_client):
    auth_client.put("/api/sales/settings/scan_booking_limit?value=60")
    auth_client.put("/api/sales/settings/scan_booking_chunk?value=8")


def test_scan_booking_limits_defaults(auth_client):
    _reset_scan_caps(auth_client)
    with SessionLocal() as db:
        caps = scan_booking_limits(db)
    assert caps["limit"] == 60
    assert caps["chunk"] == 8


def test_put_scan_booking_limit_setting(auth_client):
    try:
        r = auth_client.put("/api/sales/settings/scan_booking_limit?value=120")
        assert r.status_code == 200, r.text
        assert r.json()["value"] == "120"
        bad = auth_client.put("/api/sales/settings/scan_booking_limit?value=999")
        assert bad.status_code == 422
        chunk = auth_client.put("/api/sales/settings/scan_booking_chunk?value=12")
        assert chunk.status_code == 200, chunk.text
        assert chunk.json()["value"] == "12"

        with SessionLocal() as db:
            caps = scan_booking_limits(db)
        assert caps["limit"] == 120
        assert caps["chunk"] == 12
    finally:
        _reset_scan_caps(auth_client)


def test_scan_api_respects_settings_ceiling(auth_client, monkeypatch):
    import app.services.booking_detect as bd

    auth_client.put("/api/sales/settings/scan_booking_limit?value=2")
    auth_client.put("/api/sales/settings/scan_booking_chunk?value=2")
    try:
        ids = []
        with SessionLocal() as db:
            for i in range(5):
                b = Business(
                    place_id=f"scanlim/{i}",
                    name=f"Lim {i}",
                    country="Australia",
                    city="Sydney",
                    category="barbers",
                    website="",
                )
                db.add(b)
                db.commit()
                db.refresh(b)
                ids.append(int(b.id))

        monkeypatch.setattr(
            bd,
            "detect_booking_for_business",
            lambda b, **kw: {"has_booking": False, "software_name": "", "source": "none"},
        )

        r = auth_client.post(
            "/api/businesses/scan-booking",
            json={"ids": ids, "limit": 99},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Ceiling from settings (2) wins over requested 99 / 5 ids
        assert body["scanned"] == 2
        assert body["limit"] == 2
        assert body["caps"]["limit"] == 2
    finally:
        _reset_scan_caps(auth_client)
