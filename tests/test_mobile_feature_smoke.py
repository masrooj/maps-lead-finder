"""Bearer smoke across Sales OS feature groups (web-app API parity)."""
from __future__ import annotations

from app.config import LOGIN_USERNAME


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


def test_bearer_feature_groups(client):
    h = _bearer(client)

    checks = [
        ("GET", "/api/me"),
        ("GET", "/api/sales/me"),
        ("GET", "/api/locations"),
        ("GET", "/api/businesses?limit=5"),
        ("GET", "/api/templates"),
        ("GET", "/api/sales/dashboard"),
        ("GET", "/api/sales/pipeline"),
        ("GET", "/api/sales/prospects?limit=5"),
        ("GET", "/api/sales/contact-queue?limit=5"),
        ("GET", "/api/sales/tasks"),
        ("GET", "/api/sales/activities?limit=10"),
        ("GET", "/api/sales/invoices"),
        ("GET", "/api/sales/payments"),
        ("GET", "/api/sales/settings"),
        ("GET", "/api/sales/email-signature"),
        ("GET", "/api/sales/users"),
        ("GET", "/api/sales/be/connection"),
        ("GET", "/api/sales/ranking-modes"),
    ]
    for method, path in checks:
        if method == "GET":
            r = client.get(path, headers=h)
        else:
            r = client.request(method, path, headers=h)
        assert r.status_code == 200, f"{method} {path} -> {r.status_code} {r.text[:300]}"

    cq = client.get("/api/sales/contact-queue?product_line=STANDALONE&limit=5", headers=h)
    assert cq.status_code == 200, cq.text
    leads = cq.json().get("leads") or []
    if leads:
        sample = leads[0]
        for key in (
            "already_connected",
            "already_connected_primary_id",
            "ig_awaiting_follow_back",
            "can_message",
            "can_message_reason",
        ):
            assert key in sample, f"CQ lead missing {key}"
