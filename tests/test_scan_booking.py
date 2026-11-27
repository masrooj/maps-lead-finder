"""API tests for POST /api/businesses/scan-booking (Discover Scan booking v1)."""
from __future__ import annotations

from app.db import SessionLocal
from app.models import Business


def _seed(**kwargs) -> int:
    with SessionLocal() as db:
        b = Business(
            place_id=kwargs.pop("place_id"),
            name=kwargs.pop("name"),
            country=kwargs.pop("country", "Australia"),
            city=kwargs.pop("city", "Sydney"),
            category=kwargs.pop("category", "barbers"),
            **kwargs,
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        return int(b.id)


def test_scan_booking_hit_parks_has_booking(auth_client):
    bid = _seed(
        place_id="scan/book-1",
        name="Fresha Shop",
        website="https://booksy.com/en-us/123-cuts",
    )
    r = auth_client.post(
        "/api/businesses/scan-booking",
        json={"ids": [bid], "limit": 10},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scanned"] == 1
    assert body["has_booking"] == 1
    assert body["to_contact"] == 0

    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert (b.current_booking_software or "").strip() == "Booksy"
        assert b.marked_for_contact is False
        assert b.ready_for_contact is False

    fresh = auth_client.get(
        "/api/businesses",
        params={"status": "fresh", "scope": "city", "country": "Australia", "city": "Sydney"},
    )
    assert bid not in {row["id"] for row in fresh.json()["results"]}


def test_scan_booking_miss_marks_to_contact(auth_client, monkeypatch):
    bid = _seed(
        place_id="scan/miss-1",
        name="Plain Shop",
        website="https://plain-shop.example/",
    )

    import app.services.booking_detect as bd

    monkeypatch.setattr(
        bd,
        "detect_booking_for_business",
        lambda b, **kw: {"has_booking": False, "software_name": "", "source": "none"},
    )

    r = auth_client.post(
        "/api/businesses/scan-booking",
        json={"ids": [bid], "limit": 10},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scanned"] == 1
    assert body["has_booking"] == 0
    assert body["to_contact"] == 1

    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.marked_for_contact is True
        assert not (b.current_booking_software or "").strip()

    fresh = auth_client.get(
        "/api/businesses",
        params={"status": "fresh", "scope": "city", "country": "Australia", "city": "Sydney"},
    )
    assert bid not in {row["id"] for row in fresh.json()["results"]}


def test_scan_booking_no_website_to_contact(auth_client):
    bid = _seed(place_id="scan/noweb-1", name="No Web Shop", website="")
    r = auth_client.post(
        "/api/businesses/scan-booking",
        json={"ids": [bid], "limit": 10},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scanned"] == 1
    assert body["to_contact"] == 1
    assert body["has_booking"] == 0

    with SessionLocal() as db:
        b = db.get(Business, bid)
        assert b.marked_for_contact is True


def test_scan_booking_skips_non_fresh(auth_client):
    auth_client.put("/api/sales/settings/scan_booking_limit?value=60")
    marked = _seed(
        place_id="scan/marked-1",
        name="Already Marked",
        website="https://booksy.com/x",
        marked_for_contact=True,
    )
    contacted = _seed(
        place_id="scan/contacted-1",
        name="Already Contacted",
        website="https://booksy.com/y",
        contacted=True,
    )
    booking = _seed(
        place_id="scan/parked-1",
        name="Already Booking",
        website="https://booksy.com/z",
        current_booking_software="Has booking",
    )
    r = auth_client.post(
        "/api/businesses/scan-booking",
        json={"ids": [marked, contacted, booking], "limit": 10},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["scanned"] == 0
    assert body["skipped"] == 3


def test_scan_booking_country_city_scope(auth_client, monkeypatch):
    import app.services.booking_detect as bd

    monkeypatch.setattr(
        bd,
        "detect_booking_for_business",
        lambda b, **kw: {"has_booking": False, "software_name": "", "source": "none"},
    )

    sydney = _seed(
        place_id="scan/scope-syd",
        name="Sydney Shop",
        country="Australia",
        city="Sydney",
        website="",
    )
    melbourne = _seed(
        place_id="scan/scope-mel",
        name="Melbourne Shop",
        country="Australia",
        city="Melbourne",
        website="",
    )

    r = auth_client.post(
        "/api/businesses/scan-booking",
        json={"country": "Australia", "city": "Sydney", "limit": 20},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    result_ids = {row["id"] for row in body.get("results") or []}
    assert sydney in result_ids
    assert melbourne not in result_ids
    assert body["to_contact"] >= 1
