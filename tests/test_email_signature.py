"""Email signature settings API — Bearer auth, service layer, owner gate."""
from __future__ import annotations

from app.config import LOGIN_USERNAME


def _bearer_login(client, username=LOGIN_USERNAME, password="test-password-sales-os"):
    client.post("/api/logout")
    client.cookies.clear()
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    client.cookies.clear()
    return {"Authorization": f"Bearer {token}"}


def test_email_signature_get_default_bearer(client):
    h = _bearer_login(client)
    r = client.get("/api/sales/email-signature", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "text" in data
    assert "plain" in data
    assert "html" in data
    assert "include_logo" in data
    assert data["text"]


def test_email_signature_put_roundtrip_bearer(client):
    h = _bearer_login(client)
    put = client.put(
        "/api/sales/email-signature",
        headers=h,
        json={"text": "Best regards,\nAppointHive Sales", "include_logo": False},
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body.get("ok") is True
    assert "Best regards" in body["text"]
    assert body["include_logo"] is False
    assert "Logo:" not in body["plain"]

    got = client.get("/api/sales/email-signature", headers=h)
    assert got.status_code == 200
    assert got.json()["text"] == body["text"]
    assert got.json()["include_logo"] is False


def test_email_signature_requires_auth(client):
    client.post("/api/logout")
    client.cookies.clear()
    r = client.get("/api/sales/email-signature")
    assert r.status_code == 401
    r2 = client.put("/api/sales/email-signature", json={"text": "x", "include_logo": True})
    assert r2.status_code == 401


def test_email_signature_put_rejects_sales_role(auth_client):
    # Create sales user as owner
    created = auth_client.post(
        "/api/sales/users",
        json={
            "email": "sig.sales@appointhive.com",
            "name": "Sig Sales",
            "role": "sales",
            "password": "sales-pass-99",
        },
    )
    assert created.status_code in (200, 201), created.text

    auth_client.post("/api/logout")
    auth_client.cookies.clear()
    login = auth_client.post(
        "/api/login",
        json={"username": "sig.sales@appointhive.com", "password": "sales-pass-99"},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]
    auth_client.cookies.clear()
    h = {"Authorization": f"Bearer {token}"}

    # Sales can read (compose needs it)
    get = auth_client.get("/api/sales/email-signature", headers=h)
    assert get.status_code == 200, get.text

    # Sales cannot write
    put = auth_client.put(
        "/api/sales/email-signature",
        headers=h,
        json={"text": "Hacked", "include_logo": True},
    )
    assert put.status_code == 403, put.text


def test_email_signature_validation(auth_client):
    r = auth_client.put(
        "/api/sales/email-signature",
        json={"text": "x" * 5000, "include_logo": True},
    )
    assert r.status_code == 422, r.text


def test_generic_settings_rejects_signature_keys(auth_client):
    r = auth_client.put("/api/sales/settings/email_signature_text?value=nope")
    assert r.status_code == 422, r.text
    r2 = auth_client.put("/api/sales/settings/pay.stripe_secret_key?value=sk_test_x")
    assert r2.status_code == 422, r2.text


def test_email_signature_service_builds_logo(auth_client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    up = auth_client.post("/api/sales/brand-logo", files={"file": ("logo.png", png, "image/png")})
    assert up.status_code == 200, up.text
    url = up.json()["url"]

    put = auth_client.put(
        "/api/sales/email-signature",
        json={"text": "Team AppointHive", "include_logo": True},
    )
    assert put.status_code == 200, put.text
    data = put.json()
    assert data["brand_logo_url"] == url
    assert "Team AppointHive" in data["plain"]
    assert "Logo:" in data["plain"]
    assert "<img" in data["html"]


def test_index_has_signature_settings_ui(auth_client):
    html = auth_client.get("/").text
    assert "emailSignatureText" in html
    assert "emailSignatureSave" in html
    assert "/api/sales/email-signature" in html or "email-signature" in html
