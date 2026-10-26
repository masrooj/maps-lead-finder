"""Shared pytest fixtures — one temp DB and auth for the whole suite."""
from __future__ import annotations

import os
import tempfile

# Must run before any app imports in test modules that rely on this package init order.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"
os.environ["LOGIN_PASSWORD"] = "test-password-sales-os"
os.environ["SESSION_SECRET"] = "test-session-secret-sales-os-32chars"
os.environ["DISCOVER_SOCIAL_LINKS"] = "false"
os.environ["CRON_SECRET"] = "cron-test-secret"

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    return TestClient(app)


@pytest.fixture
def auth_client(client):
    client.post("/api/logout")
    client.cookies.clear()
    r = client.post(
        "/api/login",
        json={"username": "hello@appointhive.com", "password": "test-password-sales-os"},
    )
    assert r.status_code == 200, r.text
    return client
