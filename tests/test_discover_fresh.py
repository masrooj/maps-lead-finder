"""Discover list = fresh pool only (not To contact / Contacted / Has booking)."""
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


def test_fresh_status_excludes_queue_contacted_booking(auth_client):
    fresh_id = _seed(place_id="fresh/ok-1", name="Fresh Shop")
    marked_id = _seed(place_id="fresh/marked-1", name="Marked Shop", marked_for_contact=True)
    contacted_id = _seed(place_id="fresh/contacted-1", name="Contacted Shop", contacted=True)
    booking_id = _seed(
        place_id="fresh/booking-1",
        name="Booking Shop",
        current_booking_software="Has booking",
    )

    r = auth_client.get(
        "/api/businesses",
        params={"status": "fresh", "scope": "city", "country": "Australia", "city": "Sydney"},
    )
    assert r.status_code == 200
    ids = {row["id"] for row in r.json()["results"]}
    assert fresh_id in ids
    assert marked_id not in ids
    assert contacted_id not in ids
    assert booking_id not in ids


def test_mark_for_contact_leaves_fresh_discover(auth_client):
    bid = _seed(place_id="fresh/mark-1", name="Mark Me Out")

    before = auth_client.get(
        "/api/businesses",
        params={"status": "fresh", "scope": "city", "country": "Australia", "city": "Sydney"},
    )
    assert before.status_code == 200
    assert bid in {row["id"] for row in before.json()["results"]}

    patch = auth_client.patch(f"/api/businesses/{bid}", json={"marked_for_contact": True})
    assert patch.status_code == 200
    assert patch.json()["marked_for_contact"] is True

    after = auth_client.get(
        "/api/businesses",
        params={"status": "fresh", "scope": "city", "country": "Australia", "city": "Sydney"},
    )
    assert after.status_code == 200
    assert bid not in {row["id"] for row in after.json()["results"]}

    queue = auth_client.get(
        "/api/businesses",
        params={"status": "queue", "scope": "global"},
    )
    assert queue.status_code == 200
    assert bid in {row["id"] for row in queue.json()["results"]}


def test_unmark_returns_lead_to_fresh_discover(auth_client):
    bid = _seed(place_id="fresh/unmark-1", name="Unmark Me", marked_for_contact=True)

    before = auth_client.get(
        "/api/businesses",
        params={"status": "fresh", "scope": "city", "country": "Australia", "city": "Sydney"},
    )
    assert before.status_code == 200
    assert bid not in {row["id"] for row in before.json()["results"]}

    patch = auth_client.patch(f"/api/businesses/{bid}", json={"marked_for_contact": False})
    assert patch.status_code == 200
    assert patch.json()["marked_for_contact"] is False

    after = auth_client.get(
        "/api/businesses",
        params={"status": "fresh", "scope": "city", "country": "Australia", "city": "Sydney"},
    )
    assert after.status_code == 200
    assert bid in {row["id"] for row in after.json()["results"]}


def test_stats_include_fresh_count(auth_client):
    _seed(place_id="fresh/stats-1", name="Stats Fresh")
    _seed(place_id="fresh/stats-2", name="Stats Marked", marked_for_contact=True)

    r = auth_client.get(
        "/api/stats",
        params={"scope": "city", "country": "Australia", "city": "Sydney"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "fresh" in data
    assert data["fresh"] >= 1
    assert data["total"] >= data["fresh"]
