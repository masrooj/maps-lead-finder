"""Markets glance: country × city contact counts + UI wiring."""
from __future__ import annotations

import time
from pathlib import Path

from app.config import LOGIN_USERNAME
from app.db import SessionLocal
from app.models import Business


def _bearer(client):
    client.post("/api/logout")
    client.cookies.clear()
    r = client.post(
        "/api/login",
        json={"username": LOGIN_USERNAME, "password": "test-password-sales-os"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    client.cookies.clear()
    return {"Authorization": f"Bearer {token}"}


def _lead(**overrides):
    data = dict(
        place_id=f"qa/geo-{time.time_ns()}",
        name="Geo Glance Shop",
        city="Dubai",
        country="UAE",
        email="geo@example.com",
        phone="+971500000001",
        international_phone="+971500000001",
        marked_for_contact=False,
        ready_for_contact=False,
        contacted=False,
        product_line="STANDALONE",
    )
    data.update(overrides)
    with SessionLocal() as db:
        b = Business(**data)
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


def test_geo_counts_buckets_by_city(client):
    h = _bearer(client)
    _lead(name="Fresh Dubai", marked_for_contact=False, city="Dubai", country="UAE")
    _lead(
        name="To Contact Dubai",
        marked_for_contact=True,
        ready_for_contact=False,
        city="Dubai",
        country="UAE",
    )
    _lead(
        name="Fresh Abu Dhabi",
        marked_for_contact=False,
        city="Abu Dhabi",
        country="UAE",
    )
    _lead(
        name="Sydney Ready",
        marked_for_contact=True,
        ready_for_contact=True,
        city="Sydney",
        country="Australia",
        ig_ready_at=None,
    )

    r = client.get("/api/sales/contact-queue/geo-counts", headers=h, params={"limit": 5000})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "rows" in data and "totals" in data and "buckets" in data
    assert "discovered" in data["buckets"]
    assert "selected" in data["buckets"]

    by_key = {(row["country"], row["city"]): row for row in data["rows"]}
    dubai = by_key.get(("UAE", "Dubai"))
    assert dubai is not None
    assert dubai["discovered"] >= 1
    assert dubai["selected"] >= 1

    abu = by_key.get(("UAE", "Abu Dhabi"))
    assert abu is not None
    assert abu["discovered"] >= 1

    syd = by_key.get(("Australia", "Sydney"))
    assert syd is not None
    assert syd["ready"] >= 1 or syd["lined_up"] >= 1

    assert data["totals"]["discovered"] >= 2
    assert data["totals"]["selected"] >= 1


def test_geo_counts_match_contact_queue_tab_filters(client):
    """Glance cell for a market must equal CQ ?tab=&country=&city= lead count."""
    h = _bearer(client)
    stamp = time.time_ns()
    country = "GeoQALand"
    city = f"City-{stamp % 100000}"
    _lead(
        name=f"Fresh {stamp}",
        country=country,
        city=city,
        marked_for_contact=False,
    )
    _lead(
        name=f"Selected {stamp}",
        country=country,
        city=city,
        marked_for_contact=True,
        ready_for_contact=False,
    )
    _lead(
        name=f"Ready {stamp}",
        country=country,
        city=city,
        marked_for_contact=True,
        ready_for_contact=True,
        ig_ready_at=None,
        outreach_channel="email",
    )
    _lead(
        name=f"Booking {stamp}",
        country=country,
        city=city,
        marked_for_contact=False,
        current_booking_software="Has booking",
    )

    geo = client.get("/api/sales/contact-queue/geo-counts", headers=h, params={"limit": 5000})
    assert geo.status_code == 200, geo.text
    row = next(
        (r for r in (geo.json().get("rows") or []) if r.get("country") == country and r.get("city") == city),
        None,
    )
    assert row is not None, geo.json()

    for tab, key in (
        ("selected", "selected"),
        ("ready", "ready"),
        ("booking", "booking"),
    ):
        cq = client.get(
            "/api/sales/contact-queue",
            headers=h,
            params={"tab": tab, "country": country, "city": city, "limit": 5000},
        )
        assert cq.status_code == 200, cq.text
        n = len(cq.json().get("leads") or [])
        assert row[key] == n, f"{key}: glance={row[key]} cq_tab={n}"

    # Discovered is Discover fresh — not a CQ tab; assert at least our fresh lead is counted
    assert row["discovered"] >= 1
    assert row["selected"] >= 1
    assert row["booking"] >= 1


def test_geo_counts_requires_auth(client):
    r = client.get("/api/sales/contact-queue/geo-counts")
    assert r.status_code in (401, 403)


def test_geo_counts_openapi_and_ui():
    root = Path(__file__).resolve().parents[1]
    html = (root / "app/templates/index.html").read_text(encoding="utf-8")
    js = (root / "app/static/js/panels/geo-glance.js").read_text(encoding="utf-8")
    index_js = (root / "app/static/js/panels/index.js").read_text(encoding="utf-8")
    topbar = (root / "app/static/js/shell/topbar.js").read_text(encoding="utf-8")
    assert 'data-app-panel="geoGlance"' in html
    assert 'id="geoGlancePanel"' in html
    assert 'id="geoGlanceTable"' in html
    assert "loadGeoGlance" in js
    assert "/api/sales/contact-queue/geo-counts" in js
    assert "geo-glance.js" in index_js
    assert "geoGlance:" in topbar
