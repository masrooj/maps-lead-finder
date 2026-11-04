"""discover-instagram endpoint persists IG from website HTML (mocked fetch)."""
from __future__ import annotations

from unittest.mock import patch

from app.db import SessionLocal
from app.models import Business


def test_discover_instagram_from_website(auth_client):
    with SessionLocal() as db:
        b = Business(
            place_id="gap/discover-ig-1",
            name="IG Site Co",
            city="Sydney",
            country="Australia",
            website="https://ig-site.example",
            social_links_json="{}",
            product_line="STANDALONE",
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        bid = b.id

    ig = "https://www.instagram.com/igsiteco/"
    with patch(
        "app.main.discover_public_contacts",
        return_value=({"instagram": ig}, ""),
    ):
        r = auth_client.post(f"/api/businesses/{bid}/discover-instagram")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["found"] is True
    assert "instagram.com" in data["instagram"].lower()
    assert data["persisted"] is True
    assert data["business"]["social_links"].get("instagram")

    # Second call without force returns existing without re-fetch persist
    r2 = auth_client.post(f"/api/businesses/{bid}/discover-instagram")
    assert r2.status_code == 200
    assert r2.json()["found"] is True
    assert r2.json()["persisted"] is False
