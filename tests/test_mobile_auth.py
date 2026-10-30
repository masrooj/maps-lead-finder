"""Bearer access + refresh token auth for mobile clients."""
from __future__ import annotations

from app.config import LOGIN_USERNAME
from app.db import SessionLocal
from app.models import User
from app.services.passwords import hash_password


def _login(client, password="test-password-sales-os"):
    client.post("/api/logout")
    client.cookies.clear()
    r = client.post(
        "/api/login",
        json={"username": LOGIN_USERNAME, "password": password},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_login_returns_tokens_and_bearer_works(client):
    data = _login(client)
    assert data.get("access_token")
    assert data.get("refresh_token")
    assert data.get("token_type") == "bearer"
    assert data.get("expires_in", 0) > 0

    # Drop cookies — Bearer only
    client.cookies.clear()
    me = client.get("/api/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    assert me.status_code == 200, me.text
    assert me.json()["username"].lower() == LOGIN_USERNAME.lower()

    dash = client.get(
        "/api/sales/dashboard",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    assert dash.status_code == 200, dash.text


def test_cookie_login_still_works(auth_client):
    r = auth_client.get("/api/me")
    assert r.status_code == 200
    assert r.json()["username"].lower() == LOGIN_USERNAME.lower()


def test_refresh_rotates_and_reuse_revokes(client):
    data = _login(client)
    old_refresh = data["refresh_token"]
    client.cookies.clear()

    r1 = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r1.status_code == 200, r1.text
    pair = r1.json()
    assert pair["access_token"]
    assert pair["refresh_token"] != old_refresh

    # New refresh still works
    r_ok = client.post("/api/auth/refresh", json={"refresh_token": pair["refresh_token"]})
    assert r_ok.status_code == 200, r_ok.text
    newest = r_ok.json()["refresh_token"]

    # Old refresh reuse → 401 and family revoke
    r2 = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r2.status_code == 401

    r3 = client.post("/api/auth/refresh", json={"refresh_token": newest})
    assert r3.status_code == 401


def test_bad_access_token_401(client):
    client.cookies.clear()
    r = client.get("/api/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


def test_inactive_user_rejected(client):
    from sqlalchemy import select

    data = _login(client)
    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == LOGIN_USERNAME.lower()))
        assert u
        u.active = False
        db.commit()
        uid = u.id
    try:
        client.cookies.clear()
        r = client.get("/api/me", headers={"Authorization": f"Bearer {data['access_token']}"})
        assert r.status_code == 401
    finally:
        with SessionLocal() as db:
            u = db.get(User, uid)
            if u:
                u.active = True
                db.commit()


def test_login_rate_limit(client):
    client.post("/api/logout")
    client.cookies.clear()
    # Use a unique fake user key so we don't poison other tests' owner login
    for _ in range(10):
        r = client.post(
            "/api/login",
            json={"username": "rate-limit-mobile@example.com", "password": "wrong-password-xx"},
        )
        assert r.status_code == 401
    r = client.post(
        "/api/login",
        json={"username": "rate-limit-mobile@example.com", "password": "wrong-password-xx"},
    )
    assert r.status_code == 429


def test_password_change_revokes_refresh(auth_client, client):
    data = _login(client)
    refresh = data["refresh_token"]
    me = client.get("/api/me", headers={"Authorization": f"Bearer {data['access_token']}"})
    user_id = me.json()["user_id"]
    assert user_id

    with SessionLocal() as db:
        u = db.get(User, user_id)
        u.password_hash = hash_password("test-password-sales-os")
        db.commit()

    r = auth_client.post(
        f"/api/sales/users/{user_id}/password",
        json={"password": "test-password-sales-os"},
    )
    assert r.status_code == 200, r.text

    client.cookies.clear()
    bad = client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert bad.status_code == 401
