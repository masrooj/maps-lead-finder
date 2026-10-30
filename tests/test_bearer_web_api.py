"""Web UI Bearer path: login → token → validate → service → response (cookie cleared)."""
from __future__ import annotations

from pathlib import Path

from app.config import LOGIN_USERNAME
from tests.conftest import frontend_source


def _login_web(client, password="test-password-sales-os"):
    client.post("/api/logout")
    client.cookies.clear()
    r = client.post(
        "/api/login",
        json={"username": LOGIN_USERNAME, "password": password, "client": "web"},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_web_login_issues_bearer_and_sales_routes_work(client):
    data = _login_web(client)
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["token_type"] == "bearer"

    # Simulate frontend: drop cookie, call APIs with Bearer only
    client.cookies.clear()
    h = {"Authorization": f"Bearer {data['access_token']}"}

    me = client.get("/api/me", headers=h)
    assert me.status_code == 200, me.text
    assert me.json()["username"].lower() == LOGIN_USERNAME.lower()

    for path in (
        "/api/sales/dashboard",
        "/api/sales/settings",
        "/api/sales/email-signature",
        "/api/sales/prospects?limit=5",
        "/api/sales/contact-queue?limit=5",
    ):
        r = client.get(path, headers=h)
        assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:240]}"


def test_logout_with_refresh_revokes(client):
    data = _login_web(client)
    refresh = data["refresh_token"]
    client.cookies.clear()

    out = client.post("/api/logout", json={"refresh_token": refresh})
    assert out.status_code == 200, out.text

    bad = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert bad.status_code == 401


def test_email_signature_put_via_bearer_service_layer(client):
    data = _login_web(client)
    client.cookies.clear()
    h = {"Authorization": f"Bearer {data['access_token']}"}

    put = client.put(
        "/api/sales/email-signature",
        headers=h,
        json={"text": "Bearer Sig QA", "include_logo": False},
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body.get("ok") is True
    assert body["text"] == "Bearer Sig QA"
    assert "Bearer Sig QA" in body["plain"]

    got = client.get("/api/sales/email-signature", headers=h)
    assert got.status_code == 200
    assert got.json()["text"] == "Bearer Sig QA"


def test_frontend_bearer_auth_wiring(auth_client):
    html = auth_client.get("/").text
    src = frontend_source(html)
    auth_js = (Path(__file__).resolve().parents[1] / "app/static/js/core/auth.js").read_text()
    api_js = (Path(__file__).resolve().parents[1] / "app/static/js/core/api.js").read_text()

    assert "mlf_access_token" in auth_js
    assert "Authorization" in auth_js
    assert "refreshAccessToken" in auth_js
    assert "authHeaders" in api_js
    assert "Bearer" in api_js or "authHeaders" in api_js
    assert "setAuthTokens" in src
    assert "logoutAuth" in src
    assert "client:'web'" in src or 'client:"web"' in src or "client: 'web'" in src


def test_auth_401_message_not_hijacked_as_bridge(auth_client):
    """Regression: Sales OS 401 must not show AppointHive bridge copy."""
    api_js = (Path(__file__).resolve().parents[1] / "app/static/js/core/api.js").read_text()
    assert "never hijack Sales OS" in api_js or "beHint" in api_js
    # Bridge message still present for real BE failures
    assert "AppointHive bridge is not authorized" in api_js
