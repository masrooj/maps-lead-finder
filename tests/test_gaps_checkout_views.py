"""Gap coverage: Stripe/PayPal checkout helpers, saved views, refunds UI API."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.db import SessionLocal
from app.models import Business
from app.services.billing import service as billing
from app.services.billing.service import create_invoice_from_proposal, create_proposal
from app.services.pipeline import init_new_prospect


def test_stripe_checkout_session_success_mock(monkeypatch):
    monkeypatch.setattr("app.services.secrets.stripe_secret_key", lambda db=None: "sk_test_fake")
    monkeypatch.setattr("app.services.secrets.public_base_url", lambda db=None: "http://localhost:8000")
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.content = b"{}"
    mock_resp.json.return_value = {
        "id": "cs_test_123",
        "url": "https://checkout.stripe.com/c/pay/cs_test_123",
    }
    with patch("app.services.billing.service.requests.post", return_value=mock_resp) as post:
        sid, url, instructions = billing.create_stripe_checkout_session(
            amount=199.0,
            currency="AUD",
            description="AppointHive Starter",
            reference="PROP-1",
            customer_email="a@b.co",
        )
    assert sid == "cs_test_123"
    assert "checkout.stripe.com" in url
    assert "placeholder" not in url
    assert "Session created" in instructions
    assert post.called


def test_stripe_checkout_falls_back_on_api_error(monkeypatch):
    monkeypatch.setattr("app.services.secrets.stripe_secret_key", lambda db=None: "sk_test_bad")
    monkeypatch.setattr("app.services.secrets.public_base_url", lambda db=None: "http://localhost:8000")
    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 401
    mock_resp.content = b'{"error":{"message":"Invalid API Key"}}'
    mock_resp.text = "Invalid API Key"
    mock_resp.json.return_value = {"error": {"message": "Invalid API Key"}}
    with patch("app.services.billing.service.requests.post", return_value=mock_resp):
        sid, url, instructions = billing.create_stripe_checkout_session(
            amount=50, currency="AUD", description="x", reference="PROP-2"
        )
    assert sid.startswith("stripe_pending_")
    assert "placeholder" in url
    assert "Invalid API Key" in instructions


def test_create_invoice_uses_stripe_session(monkeypatch):
    monkeypatch.setattr("app.services.secrets.stripe_secret_key", lambda db=None: "sk_test_fake")
    monkeypatch.setattr("app.services.secrets.stripe_mode", lambda db=None: "test")
    monkeypatch.setattr("app.services.secrets.public_base_url", lambda db=None: "http://localhost:8000")
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.content = b"{}"
    mock_resp.json.return_value = {
        "id": "cs_live_abc",
        "url": "https://checkout.stripe.com/c/pay/cs_live_abc",
    }
    with SessionLocal() as db:
        b = Business(place_id="gap/inv-stripe", name="Gap Clinic", city="Sydney", country="Australia", email="a@b.co")
        db.add(b)
        db.flush()
        init_new_prospect(db, b)
        db.commit()
        prop = create_proposal(
            db,
            b,
            title="Starter",
            currency="AUD",
            line_items=[{"name": "Standalone Starter", "qty": 1, "amount": 1500}],
            tax_rate=0.1,
            tax_label="GST",
        )
        with patch("app.services.billing.service.requests.post", return_value=mock_resp):
            inv = create_invoice_from_proposal(db, prop, provider="stripe")
        assert inv.provider == "stripe"
        assert inv.provider_ref == "cs_live_abc"
        assert "cs_live_abc" in (inv.hosted_url or "")


def test_paypal_order_success_mock(monkeypatch):
    monkeypatch.setattr("app.services.secrets.paypal_client_id", lambda db=None: "client")
    monkeypatch.setattr("app.services.secrets.paypal_client_secret", lambda db=None: "secret")
    monkeypatch.setattr("app.services.secrets.paypal_mode", lambda db=None: "sandbox")
    monkeypatch.setattr("app.services.secrets.public_base_url", lambda db=None: "http://localhost:8000")

    token_resp = MagicMock()
    token_resp.ok = True
    token_resp.content = b"{}"
    token_resp.json.return_value = {"access_token": "tok_abc"}

    order_resp = MagicMock()
    order_resp.ok = True
    order_resp.content = b"{}"
    order_resp.json.return_value = {
        "id": "ORDER99",
        "links": [{"rel": "approve", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDER99"}],
    }

    with patch("app.services.billing.service.requests.post", side_effect=[token_resp, order_resp]):
        oid, url, instructions = billing.create_paypal_order(
            amount=99.0, currency="AUD", description="Plan", reference="PROP-P"
        )
    assert oid == "ORDER99"
    assert "paypal.com" in url
    assert "Orders API" in instructions


def test_saved_views_crud(auth_client):
    r = auth_client.post(
        "/api/sales/saved-views",
        json={"name": "AU beauty", "filters": {"country": "Australia", "product_line": "STANDALONE"}},
    )
    assert r.status_code == 200, r.text
    vid = r.json()["id"]
    listed = auth_client.get("/api/sales/saved-views")
    assert listed.status_code == 200
    names = [v["name"] for v in listed.json()["views"]]
    assert "AU beauty" in names
    deleted = auth_client.delete(f"/api/sales/saved-views/{vid}")
    assert deleted.status_code == 200
    assert deleted.json()["ok"] is True


def test_home_has_kanban_drag_and_refund_ui(auth_client):
    html = auth_client.get("/").text
    assert 'draggable="true"' in html or "dragstart" in html
    assert "data-refund" in html or "/refund" in html
    assert "saved-views" in html or "saveViewBtn" in html
    assert 'class="sidebar"' in html or "id=\"sidebar\"" in html
    assert 'data-goto="tasks"' in html
    assert 'data-goto="money"' in html
    assert 'data-goto="pipeline"' in html
    assert 'id="uiDialog"' in html
    assert "function uiConfirm" in html or "uiConfirm(" in html
    assert "window.confirm" not in html
    assert "prompt(" not in html
    assert 'id="topbar"' in html
    assert 'id="notifBtn"' in html
    assert 'id="profileMenu"' in html
    assert 'id="logoutBtn"' in html
