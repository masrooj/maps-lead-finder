"""Contacts Instagram field: validate before save; reject reel/login; unlock IG channel."""
from __future__ import annotations

import pytest

from app.db import SessionLocal
from app.models import Business
from app.services.automation import normalize_instagram_profile


def _lead(**kwargs):
    with SessionLocal() as db:
        b = Business(
            place_id=kwargs.pop("place_id", "gap/manual-ig-1"),
            name=kwargs.pop("name", "Manual IG Co"),
            city="Sydney",
            country="Australia",
            product_line="STANDALONE",
            marked_for_contact=True,
            ready_for_contact=False,
            social_links_json="{}",
            **kwargs,
        )
        db.add(b)
        db.commit()
        db.refresh(b)
        return b.id


@pytest.mark.parametrize(
    "raw,ok_substr",
    [
        ("@manualigco", "manualigco"),
        ("https://www.instagram.com/manualigco/", "manualigco"),
        ("instagram.com/manualigco", "manualigco"),
    ],
)
def test_normalize_instagram_profile_ok(raw, ok_substr):
    href = normalize_instagram_profile(raw)
    assert "instagram.com" in href.lower()
    assert ok_substr in href.lower()


@pytest.mark.parametrize(
    "raw",
    [
        "https://www.instagram.com/reel/ABC123/",
        "https://www.instagram.com/accounts/login/",
        "https://facebook.com/notig",
        "@@@",
        "not a handle!!",
    ],
)
def test_normalize_instagram_profile_rejects(raw):
    with pytest.raises(ValueError):
        normalize_instagram_profile(raw)


def test_patch_instagram_sets_social_and_outreach(auth_client):
    bid = _lead(place_id="gap/manual-ig-patch")
    r = auth_client.patch(
        f"/api/businesses/{bid}",
        json={
            "instagram": "@manualigco",
            "outreach_channel": "instagram",
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("outreach_channel") == "instagram"
    ig = (data.get("social_links") or {}).get("instagram") or ""
    assert ig == "https://www.instagram.com/manualigco/"


def test_patch_instagram_rejects_reel(auth_client):
    bid = _lead(place_id="gap/manual-ig-reel")
    r = auth_client.patch(
        f"/api/businesses/{bid}",
        json={"instagram": "https://www.instagram.com/reel/XYZ/"},
    )
    assert r.status_code == 422, r.text
    assert "valid Instagram" in (r.json().get("detail") or "")


def test_patch_instagram_handle_form(auth_client):
    bid = _lead(place_id="gap/manual-ig-handle")
    r = auth_client.patch(
        f"/api/businesses/{bid}",
        json={"instagram": "https://www.instagram.com/barehandleqa/"},
    )
    assert r.status_code == 200, r.text
    ig = (r.json().get("social_links") or {}).get("instagram") or ""
    assert ig == "https://www.instagram.com/barehandleqa/"
