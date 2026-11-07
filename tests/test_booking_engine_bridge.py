"""Plan 2 — AppointHive Booking Engine bridge: connect gate, pricing, handover, webhooks."""
from __future__ import annotations

import json
import uuid
from unittest.mock import patch

from sqlalchemy import select

from app.db import SessionLocal
from app.models import BeDelivery, BeMilestoneSnapshot, Business
from app.services.booking_engine.connect import (
    be_connection_public,
    crm_lead_id_for,
    save_manual_connection,
)
from app.services.secrets import store_setting_value as store_setting


def _force_connected(db, *, base="http://127.0.0.1:3010", secret="test-be-shared-secret"):
    store_setting(db, "be.base_url", base, secret=False)
    store_setting(db, "be.shared_secret", secret, secret=True)
    store_setting(db, "be.bridge_enabled", "true", secret=False)
    store_setting(db, "be.connection_status", "connected", secret=False)
    db.commit()
    # Guard against settings-session races after disconnect / long suites.
    from app.services.booking_engine.connect import get_be_shared_secret

    db.expire_all()
    assert get_be_shared_secret(db=db) == secret


def _force_disconnected(db):
    store_setting(db, "be.shared_secret", "", secret=True)
    store_setting(db, "be.bridge_enabled", "false", secret=False)
    store_setting(db, "be.connection_status", "disconnected", secret=False)
    db.commit()


def test_crm_lead_id_format():
    assert crm_lead_id_for(42) == "business:42"


def test_rewrite_be_base_url_for_docker(monkeypatch):
    from app.services.booking_engine import connect as be_connect

    monkeypatch.setattr(be_connect, "_running_in_docker", lambda: True)
    assert be_connect.rewrite_be_base_url_for_runtime("http://127.0.0.1:3010") == "http://host.docker.internal:3010"
    assert be_connect.rewrite_be_base_url_for_runtime("http://localhost:3010/") == "http://host.docker.internal:3010"
    assert be_connect.rewrite_be_base_url_for_runtime("http://be.example:3010") == "http://be.example:3010"
    monkeypatch.setattr(be_connect, "_running_in_docker", lambda: False)
    assert be_connect.rewrite_be_base_url_for_runtime("http://127.0.0.1:3010") == "http://127.0.0.1:3010"
    assert (
        be_connect.rewrite_be_base_url_for_runtime("http://host.docker.internal:3010")
        == "http://127.0.0.1:3010"
    )


def test_connect_gate_blocks_catalog(auth_client):
    db = SessionLocal()
    try:
        _force_disconnected(db)
    finally:
        db.close()
    r = auth_client.get("/api/sales/be/catalog?product_line=STANDALONE")
    assert r.status_code == 409
    body = r.json()
    detail = body.get("detail") or body
    if isinstance(detail, dict):
        assert "not connected" in json.dumps(detail).lower()
    else:
        assert "not connected" in str(detail).lower()


def test_catalog_when_connected_returns_items(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
    finally:
        db.close()

    fake_catalog = {
        "productLine": "STANDALONE",
        "items": [
            {
                "id": "cat_growth",
                "productRevisionId": "rev_growth",
                "productName": "Growth",
                "edition": "GROWTH",
                "priceCents": 14900,
                "currency": "USD",
                "label": "Growth",
                "productLine": "STANDALONE",
            },
            {
                "id": "cat_pro",
                "productRevisionId": "rev_pro",
                "name": "Pro",
                "edition": "PRO",
                "priceCents": 24900,
                "currency": "USD",
            },
        ],
    }
    with patch(
        "app.services.booking_engine.client.BookingEngineClient.get_catalog",
        return_value=fake_catalog,
    ) as mocked:
        r = auth_client.get("/api/sales/be/catalog?product_line=STANDALONE")
    assert r.status_code == 200, r.text
    mocked.assert_called_once()
    body = r.json()
    assert body["productLine"] == "STANDALONE"
    assert len(body["items"]) == 2
    assert body["items"][0]["priceCents"] == 14900
    assert body["items"][0]["productRevisionId"] == "rev_growth"
    assert body["items"][1]["name"] == "Pro"


def test_pair_code_and_public_pairing(auth_client):
    r = auth_client.post("/api/sales/be/connection/pair-code", json={})
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert code

    # Wrong code
    bad = auth_client.post(
        "/api/v1/public/product-pairing",
        json={"code": "nope", "productBaseUrl": "http://127.0.0.1:3010"},
    )
    assert bad.status_code == 401

    ok = auth_client.post(
        "/api/v1/public/product-pairing",
        json={"code": code, "productBaseUrl": "http://127.0.0.1:3010"},
    )
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data.get("secret")
    assert str(data.get("webhookUrl") or "").endswith("/api/webhooks/booking-engine")
    assert "Lead CRM" not in json.dumps(data)

    # Code is one-time
    again = auth_client.post(
        "/api/v1/public/product-pairing",
        json={"code": code, "productBaseUrl": "http://127.0.0.1:3010"},
    )
    assert again.status_code == 401


def test_manual_connect_and_test_sets_connected(auth_client):
    db = SessionLocal()
    try:
        save_manual_connection(db, base_url="http://127.0.0.1:3010", shared_secret="bridge-secret-xyz")
        pub = be_connection_public(db=db)
        assert pub["connection_status"] == "disconnected"
        assert pub["secret_configured"] is True
    finally:
        db.close()

    fake_health = {
        "ok": True,
        "secretConfigured": True,
        "crmBridgeEnabled": True,
        "status": "connected",
    }
    with patch("app.services.booking_engine.client.BookingEngineClient.health", return_value=fake_health):
        r = auth_client.post("/api/sales/be/connection/test", json={})
    assert r.status_code == 200, r.text
    assert r.json()["connected"] is True

    st = auth_client.get("/api/sales/be/connection")
    assert st.status_code == 200
    assert st.json()["connected"] is True
    assert "Lead CRM" not in st.text


def test_revisions_and_send_when_connected(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        biz = Business(
            place_id="be/bridge-send-1",
            name="Bridge Salon",
            email="buyer@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.commit()
        db.refresh(biz)
        bid = biz.id
    finally:
        db.close()

    fake_revs = {
        "productLine": "STANDALONE",
        "revisions": [
            {
                "id": "rev_1",
                "productRevisionId": "rev_1",
                "productName": "Growth",
                "edition": "GROWTH",
                "priceCents": 9900,
                "currency": "AUD",
                "label": "Growth",
            }
        ],
    }
    fake_purchase = {
        "purchaseId": "sp_test_1",
        "inboundRequestId": "inbound_mirror_1",
        "revisionId": "rev_1",
        "priceCents": 9900,
        "currency": "AUD",
        "checkoutUrl": "https://pay.example/checkout/sp_test_1",
        "crmLeadId": crm_lead_id_for(bid),
        "reused": False,
    }
    with patch("app.services.booking_engine.client.BookingEngineClient.list_revisions", return_value=fake_revs):
        r = auth_client.get("/api/sales/be/revisions")
    assert r.status_code == 200, r.text
    assert r.json()["revisions"][0]["priceCents"] == 9900

    with (
        patch(
            "app.services.booking_engine.client.BookingEngineClient.create_request",
            return_value={"id": "inbound_pre_1"},
        ),
        patch(
            "app.services.booking_engine.client.BookingEngineClient.create_purchase",
            return_value=fake_purchase,
        ),
    ):
        send = auth_client.post(
            "/api/sales/be/send-to-delivery",
            json={"business_id": bid, "product_revision_id": "rev_1"},
        )
    assert send.status_code == 200, send.text
    body = send.json()
    assert body["pay_url"].startswith("https://pay.example/")
    assert body["delivery"]["crm_lead_id"] == crm_lead_id_for(bid)
    assert body["delivery"]["purchase_id"] == "sp_test_1"
    # Prefer BE-mirrored inbound hub id from createCheckout response
    assert body["delivery"]["inbound_request_id"] == "inbound_mirror_1"

    # Idempotent upsert on same purchase id
    with patch("app.services.booking_engine.client.BookingEngineClient.create_purchase", return_value={**fake_purchase, "reused": True}):
        send2 = auth_client.post(
            "/api/sales/be/send-to-delivery",
            json={"business_id": bid, "product_revision_id": "rev_1"},
        )
    assert send2.status_code == 200
    db = SessionLocal()
    try:
        rows = db.scalars(select(BeDelivery).where(BeDelivery.purchase_id == "sp_test_1")).all()
        assert len(rows) == 1
    finally:
        db.close()


def test_soft_request_awaits_quote(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        biz = Business(
            place_id="be/bridge-soft-1",
            name="Soft Request Co",
            email="buyer@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.commit()
        db.refresh(biz)
        bid = biz.id
    finally:
        db.close()

    with patch(
        "app.services.booking_engine.client.BookingEngineClient.create_request",
        return_value={"id": "req_soft_1", "status": "REQUESTED"},
    ):
        r = auth_client.post(
            "/api/sales/be/send-request",
            json={"business_id": bid, "message": "Need quote"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"] == "Awaiting AppointHive quote"
    assert body["delivery"]["inbound_request_id"] == "req_soft_1"
    assert not body.get("pay_url")


def test_upgrade_requires_installation_id(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        biz = Business(
            place_id="be/bridge-upg-1",
            name="Upgrade Gate Co",
            email="buyer@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
            be_installation_id="",
        )
        db.add(biz)
        db.commit()
        db.refresh(biz)
        bid = biz.id
    finally:
        db.close()

    r = auth_client.post(
        "/api/sales/be/send-upgrade",
        json={"business_id": bid, "to_revision_id": "rev_up"},
    )
    assert r.status_code == 422
    body = r.json().get("detail") or r.json()
    blob = json.dumps(body).lower() if isinstance(body, dict) else str(body).lower()
    assert "installation" in blob


def test_local_psp_blocked_when_connected(auth_client):
    """When AppointHive is connected: Stripe/PayPal local PSP blocked; Manual/Payoneer/Skrill allowed."""
    db = SessionLocal()
    try:
        _force_connected(db)
        biz = Business(
            place_id="be/bridge-psp-1",
            name="PSP Block Co",
            email="buyer@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.commit()
        db.refresh(biz)
        bid = biz.id
    finally:
        db.close()

    plans = auth_client.get("/api/sales/plans?product_line=STANDALONE")
    assert plans.status_code == 200
    plist = plans.json().get("plans") or []
    assert plist, "plans should be seeded"
    prop = auth_client.post(
        "/api/sales/proposals",
        json={
            "business_id": bid,
            "title": "Test",
            "currency": "AUD",
            "plan_id": plist[0]["id"],
            "tax_rate": 0.1,
            "tax_label": "GST",
            "terms_url": "",
        },
    )
    assert prop.status_code == 200, prop.text
    pid = prop.json()["id"]
    # Card PSP must stay on AppointHive delivery when connected
    inv_stripe = auth_client.post("/api/sales/invoices", json={"proposal_id": pid, "provider": "stripe"})
    assert inv_stripe.status_code == 409, inv_stripe.text
    inv_paypal = auth_client.post("/api/sales/invoices", json={"proposal_id": pid, "provider": "paypal"})
    assert inv_paypal.status_code == 409, inv_paypal.text
    # Bank-instruction providers remain available for multi pay-link / QR flows
    inv_manual = auth_client.post("/api/sales/invoices", json={"proposal_id": pid, "provider": "manual"})
    assert inv_manual.status_code == 200, inv_manual.text



def test_payment_paid_webhook_mirrors_won(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        store_setting(
            db,
            "be.won_policy",
            json.dumps(
                {
                    "auto_won_on_be_paid": True,
                    "require_agreement_accepted": False,
                    "require_initial_milestone_paid": False,
                    "require_fully_paid": True,
                }
            ),
            secret=False,
        )
        from app.services.pipeline import get_stage, init_new_prospect

        biz = Business(
            place_id="be/bridge-won-1",
            name="Won Mirror Co",
            email="buyer@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        nego = get_stage(db, "STANDALONE", "negotiation")
        if nego:
            biz.pipeline_stage_id = nego.id
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_paid_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="pending_payment",
                pay_url="https://pay.example/x",
            )
        )
        db.commit()
        bid = biz.id
        secret = "test-be-shared-secret"
    finally:
        db.close()

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "event": "standalone.payment.paid",
            "productLine": "STANDALONE",
            "purchaseId": "sp_paid_1",
            "crmLeadId": crm_lead_id_for(bid),
            "paymentStatus": "PAID",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json().get("status") == "paid"

    db = SessionLocal()
    try:
        from sqlalchemy import select
        from app.models import PipelineStage

        biz = db.get(Business, bid)
        stage = db.get(PipelineStage, biz.pipeline_stage_id) if biz and biz.pipeline_stage_id else None
        assert stage is not None and stage.slug == "won"
        d = db.scalar(select(BeDelivery).where(BeDelivery.purchase_id == "sp_paid_1"))
        assert d and d.status == "paid"
    finally:
        db.close()


def test_soft_after_won_does_not_demote(auth_client):
    """DEF-BRIDGE-001: re-send soft must not move Won → negotiation."""
    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import get_stage, init_new_prospect

        biz = Business(
            place_id=f"be/bridge-won-soft-{uuid.uuid4().hex[:8]}",
            name="Won Soft Guard Co",
            email="buyer@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        won = get_stage(db, "STANDALONE", "won")
        assert won is not None
        biz.pipeline_stage_id = won.id
        db.commit()
        bid = biz.id
        won_id = won.id
    finally:
        db.close()

    with patch(
        "app.services.booking_engine.client.BookingEngineClient.create_request",
        return_value={"id": "req_after_won", "status": "REQUESTED"},
    ):
        r = auth_client.post(
            "/api/sales/be/send-request",
            json={"business_id": bid, "message": "Re-send after paid"},
        )
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        biz = db.get(Business, bid)
        assert biz is not None
        assert biz.pipeline_stage_id == won_id
    finally:
        db.close()


def test_disconnect_clears_secret(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
    finally:
        db.close()
    r = auth_client.post("/api/sales/be/connection/disconnect", json={})
    assert r.status_code == 200
    assert r.json()["connected"] is False
    assert r.json()["secret_configured"] is False
    blocked = auth_client.get("/api/sales/be/revisions")
    assert blocked.status_code == 409


def test_soft_request_sends_crm_prospect(auth_client):
    from unittest.mock import patch

    db = SessionLocal()
    try:
        _force_connected(db)
        biz = Business(
            place_id=f"be/bridge-soft-{uuid.uuid4().hex[:8]}",
            name="Soft Request Salon",
            email="soft@mapsleads.test",
            phone="+61 400 000 001",
            address="1 Test St",
            city="Sydney",
            country="Australia",
            website="https://soft.example",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.commit()
        db.refresh(biz)
        bid = biz.id
    finally:
        db.close()

    captured = {}

    def fake_create_request(self, payload):
        captured["payload"] = payload
        return {"id": "req_soft_1", "status": "REQUESTED", "source": "EXTERNAL_CRM"}

    with patch(
        "app.services.booking_engine.client.BookingEngineClient.create_request",
        fake_create_request,
    ):
        r = auth_client.post("/api/sales/be/send-request", json={"business_id": bid, "message": "Please quote"})
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "Awaiting AppointHive quote"
    body = captured["payload"]
    assert body["externalCrmRef"] == crm_lead_id_for(bid)
    assert body["crmProspect"]["business"]["name"] == "Soft Request Salon"
    assert "soft@mapsleads.test" in (body["contactEmail"] or "")
    assert "CRM_PROSPECT_JSON" in (body["message"] or "")


def test_installation_active_webhook_stores_id(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        biz = Business(
            place_id=f"be/bridge-install-{uuid.uuid4().hex[:8]}",
            name="Install Co",
            email="inst@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_inst_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="paid",
            )
        )
        db.commit()
        bid = biz.id
        secret = "test-be-shared-secret"
    finally:
        db.close()

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "event": "standalone.installation.active",
            "productLine": "STANDALONE",
            "purchaseId": "sp_inst_1",
            "installationId": "inst_abc",
            "crmLeadId": crm_lead_id_for(bid),
        },
    )
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        biz = db.get(Business, bid)
        assert biz.be_installation_id == "inst_abc"
        d = db.scalar(select(BeDelivery).where(BeDelivery.purchase_id == "sp_inst_1"))
        assert d and d.installation_id == "inst_abc"
    finally:
        db.close()


def test_upgrade_status_webhook_sets_pay_url(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        biz = Business(
            place_id=f"be/bridge-upg-{uuid.uuid4().hex[:8]}",
            name="Upgrade Co",
            email="upg@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
            be_installation_id="inst_upg",
        )
        db.add(biz)
        db.flush()
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="request",
                inbound_request_id="req_upg_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="sent",
            )
        )
        db.commit()
        bid = biz.id
        secret = "test-be-shared-secret"
    finally:
        db.close()

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "event": "standalone.upgrade.status",
            "productLine": "STANDALONE",
            "requestId": "req_upg_1",
            "status": "AWAITING_PAYMENT",
            "payUrl": "https://pay.example/upgrade",
            "quoteCents": 5000,
            "externalCrmRef": crm_lead_id_for(bid),
            "crmLeadId": crm_lead_id_for(bid),
        },
    )
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        d = db.scalar(select(BeDelivery).where(BeDelivery.inbound_request_id == "req_upg_1"))
        assert d and d.pay_url == "https://pay.example/upgrade"
        assert d.status == "pending_payment"
        assert d.quoted_amount_cents == 5000
    finally:
        db.close()


def test_soft_omits_installation_id_on_new_sale(auth_client):
    """New-sale soft must not send be_installation_id (BE FK 500 on unknown ids)."""
    db = SessionLocal()
    try:
        _force_connected(db)
        biz = Business(
            place_id=f"be/bridge-soft-omit-{uuid.uuid4().hex[:8]}",
            name="Omit Install Soft Co",
            email="omit@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
            be_installation_id="inst_fake_should_not_send",
        )
        db.add(biz)
        db.commit()
        db.refresh(biz)
        bid = biz.id
    finally:
        db.close()

    captured: dict = {}

    def fake_create_request(self, payload):
        captured["payload"] = payload
        return {"id": "req_omit_1", "status": "REQUESTED"}

    with patch(
        "app.services.booking_engine.client.BookingEngineClient.create_request",
        fake_create_request,
    ):
        r = auth_client.post(
            "/api/sales/be/send-request",
            json={"business_id": bid, "message": "New sale soft"},
        )
    assert r.status_code == 200, r.text
    assert captured["payload"].get("installationId") in (None, "")


def test_upgrade_happy_path_mocked(auth_client):
    """Path C: quote → request → checkout with installation_id present."""
    db = SessionLocal()
    try:
        _force_connected(db)
        biz = Business(
            place_id=f"be/bridge-upg-happy-{uuid.uuid4().hex[:8]}",
            name="Upgrade Happy Co",
            email="uphappy@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
            be_installation_id="inst_real_test",
        )
        db.add(biz)
        db.commit()
        db.refresh(biz)
        bid = biz.id
    finally:
        db.close()

    captured: dict = {}

    def fake_quote(self, params):
        captured["quote_params"] = params
        return {"deltaCents": 9900, "currency": "USD", "quoteCents": 9900}

    def fake_request(self, payload):
        captured["request"] = payload
        return {"id": "req_up_happy", "status": "REQUESTED"}

    def fake_checkout(self, request_id, payload=None):
        captured["checkout_id"] = request_id
        return {"payUrl": "https://pay.example/up-happy", "checkoutUrl": "https://pay.example/up-happy"}

    with patch(
        "app.services.booking_engine.client.BookingEngineClient.upgrade_quote",
        fake_quote,
    ), patch(
        "app.services.booking_engine.client.BookingEngineClient.create_request",
        fake_request,
    ), patch(
        "app.services.booking_engine.client.BookingEngineClient.request_checkout",
        fake_checkout,
    ):
        r = auth_client.post(
            "/api/sales/be/send-upgrade",
            json={"business_id": bid, "to_revision_id": "rev_to_pro"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["label"] == "Pay-ready"
    assert body["pay_url"] == "https://pay.example/up-happy"
    assert body["delivery"]["purpose"] == "upgrade"
    assert body["delivery"]["installation_id"] == "inst_real_test"
    assert captured["quote_params"]["installationId"] == "inst_real_test"
    assert captured["request"]["installationId"] == "inst_real_test"
    assert "crmProspect" in captured["request"]
    assert captured["checkout_id"] == "req_up_happy"


def test_paid_webhook_idempotent_no_double_activity(auth_client):
    """Duplicate payment.paid returns duplicate and does not corrupt delivery."""
    db = SessionLocal()
    try:
        _force_connected(db)
        store_setting(
            db,
            "be.won_policy",
            json.dumps(
                {
                    "auto_won_on_be_paid": True,
                    "require_agreement_accepted": False,
                    "require_initial_milestone_paid": False,
                    "require_fully_paid": True,
                }
            ),
            secret=False,
        )
        from app.services.pipeline import get_stage, init_new_prospect

        biz = Business(
            place_id=f"be/bridge-idem-{uuid.uuid4().hex[:8]}",
            name="Idempotent Pay Co",
            email="idem@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        nego = get_stage(db, "STANDALONE", "negotiation")
        if nego:
            biz.pipeline_stage_id = nego.id
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_idem_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="pending_payment",
            )
        )
        db.commit()
        bid = biz.id
        secret = "test-be-shared-secret"
    finally:
        db.close()

    payload = {
        "eventId": "evt_idem_paid_1",
        "event": "standalone.payment.paid",
        "purchaseId": "sp_idem_1",
        "crmLeadId": crm_lead_id_for(bid),
        "paymentStatus": "PAID",
    }
    r1 = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json=payload,
    )
    assert r1.status_code == 200, r1.text
    assert r1.json().get("status") == "paid"
    r2 = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json=payload,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json().get("duplicate") is True

    db = SessionLocal()
    try:
        from app.models import PipelineStage

        biz = db.get(Business, bid)
        stage = db.get(PipelineStage, biz.pipeline_stage_id) if biz and biz.pipeline_stage_id else None
        assert stage and stage.slug == "won"
        d = db.scalar(select(BeDelivery).where(BeDelivery.purchase_id == "sp_idem_1"))
        assert d and d.status == "paid"
    finally:
        db.close()


def test_refresh_delivery_updates_pay_url(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        biz = Business(
            place_id=f"be/bridge-refresh-{uuid.uuid4().hex[:8]}",
            name="Refresh Co",
            email="ref@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        row = BeDelivery(
            business_id=biz.id,
            purpose="purchase",
            purchase_id="sp_refresh_1",
            crm_lead_id=crm_lead_id_for(biz.id),
            status="sent",
            pay_url="",
        )
        db.add(row)
        db.commit()
        did = row.id
    finally:
        db.close()

    with patch(
        "app.services.booking_engine.client.BookingEngineClient.get_purchase",
        return_value={
            "purchaseId": "sp_refresh_1",
            "status": {
                "checkoutUrl": "https://pay.example/refreshed",
                "payment": {"status": "PENDING"},
            },
        },
    ):
        r = auth_client.post(f"/api/sales/be/deliveries/{did}/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["delivery"]["pay_url"] == "https://pay.example/refreshed"


def test_webhook_rejects_bad_bearer(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
    finally:
        db.close()
    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": "Bearer wrong-secret"},
        json={"event": "standalone.payment.paid", "purchaseId": "x"},
    )
    assert r.status_code == 401


def test_so_only_full_flow_a_b_c_without_live_be(auth_client):
    """Sales OS Path B → A → paid/Won/install → C, fully mocked BE (no live Booking Engine)."""
    db = SessionLocal()
    try:
        _force_connected(db)
        store_setting(
            db,
            "be.won_policy",
            json.dumps(
                {
                    "auto_won_on_be_paid": True,
                    "require_agreement_accepted": False,
                    "require_initial_milestone_paid": False,
                    "require_fully_paid": True,
                }
            ),
            secret=False,
        )
        from app.services.pipeline import get_stage, init_new_prospect

        biz = Business(
            place_id=f"be/bridge-so-e2e-{uuid.uuid4().hex[:8]}",
            name="SO Only E2E Co",
            email="so.e2e@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
            be_installation_id="",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.commit()
        bid = biz.id
        secret = "test-be-shared-secret"
        lead = crm_lead_id_for(bid)
    finally:
        db.close()

    # Path B — soft
    with patch(
        "app.services.booking_engine.client.BookingEngineClient.create_request",
        return_value={"id": "req_so_e2e", "status": "REQUESTED"},
    ):
        soft = auth_client.post(
            "/api/sales/be/send-request",
            json={"business_id": bid, "message": "SO-only soft"},
        )
    assert soft.status_code == 200, soft.text
    assert soft.json()["label"] == "Awaiting AppointHive quote"

    # Path A — purchase (create_request may also be called; purchase is required)
    fake_purchase = {
        "purchaseId": "sp_so_e2e",
        "checkoutUrl": "https://pay.example/so-e2e",
        "hostedUrl": "https://pay.example/so-e2e",
        "priceCents": 19900,
        "currency": "AUD",
        "revisionId": "rev_so_e2e",
        "inboundRequestId": "req_so_e2e_buy",
        "crmLeadId": lead,
        "reused": False,
    }
    with patch(
        "app.services.booking_engine.client.BookingEngineClient.create_request",
        return_value={"id": "req_so_e2e_buy", "status": "REQUESTED"},
    ), patch(
        "app.services.booking_engine.client.BookingEngineClient.create_purchase",
        return_value=fake_purchase,
    ):
        purch = auth_client.post(
            "/api/sales/be/send-to-delivery",
            json={"business_id": bid, "product_revision_id": "rev_so_e2e", "create_checkout": True},
        )
    assert purch.status_code == 200, purch.text
    assert purch.json()["pay_url"] == "https://pay.example/so-e2e"
    assert purch.json()["label"] == "Pay-ready"

    # Paid → Won
    paid = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "eventId": f"so-e2e-paid-{uuid.uuid4().hex}",
            "event": "standalone.payment.paid",
            "purchaseId": "sp_so_e2e",
            "crmLeadId": lead,
            "paymentStatus": "PAID",
        },
    )
    assert paid.status_code == 200, paid.text
    assert paid.json().get("status") == "paid"

    # Install id
    inst = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "eventId": f"so-e2e-inst-{uuid.uuid4().hex}",
            "event": "standalone.installation.active",
            "purchaseId": "sp_so_e2e",
            "installationId": "inst_so_e2e",
            "crmLeadId": lead,
        },
    )
    assert inst.status_code == 200, inst.text

    db = SessionLocal()
    try:
        from app.models import PipelineStage

        biz = db.get(Business, bid)
        stage = db.get(PipelineStage, biz.pipeline_stage_id) if biz and biz.pipeline_stage_id else None
        assert stage and stage.slug == "won"
        assert biz.be_installation_id == "inst_so_e2e"
    finally:
        db.close()

    # Soft after Won must not demote
    with patch(
        "app.services.booking_engine.client.BookingEngineClient.create_request",
        return_value={"id": "req_so_e2e_2", "status": "REQUESTED"},
    ):
        soft2 = auth_client.post(
            "/api/sales/be/send-request",
            json={"business_id": bid, "message": "after won"},
        )
    assert soft2.status_code == 200, soft2.text
    db = SessionLocal()
    try:
        from app.models import PipelineStage

        biz = db.get(Business, bid)
        stage = db.get(PipelineStage, biz.pipeline_stage_id) if biz and biz.pipeline_stage_id else None
        assert stage and stage.slug == "won"
    finally:
        db.close()

    # Path C — upgrade with stored install id
    with patch(
        "app.services.booking_engine.client.BookingEngineClient.upgrade_quote",
        return_value={"deltaCents": 5000, "currency": "AUD", "quoteCents": 5000},
    ), patch(
        "app.services.booking_engine.client.BookingEngineClient.create_request",
        return_value={"id": "req_so_up", "status": "REQUESTED"},
    ), patch(
        "app.services.booking_engine.client.BookingEngineClient.request_checkout",
        return_value={"payUrl": "https://pay.example/so-upgrade"},
    ):
        upg = auth_client.post(
            "/api/sales/be/send-upgrade",
            json={"business_id": bid, "to_revision_id": "rev_so_up"},
        )
    assert upg.status_code == 200, upg.text
    assert upg.json()["pay_url"] == "https://pay.example/so-upgrade"
    assert upg.json()["delivery"]["purpose"] == "upgrade"
    assert upg.json()["delivery"]["installation_id"] == "inst_so_e2e"


def test_payment_paid_default_policy_does_not_auto_won(auth_client):
    """Default won_policy: payment updates financial snapshot only — not Won."""
    db = SessionLocal()
    try:
        _force_connected(db)
        store_setting(
            db,
            "be.won_policy",
            json.dumps(
                {
                    "auto_won_on_be_paid": False,
                    "require_agreement_accepted": False,
                    "require_initial_milestone_paid": False,
                    "require_fully_paid": True,
                }
            ),
            secret=False,
        )
        from app.services.pipeline import get_stage, init_new_prospect

        biz = Business(
            place_id=f"be/bridge-nowon-{uuid.uuid4().hex[:8]}",
            name="No Auto Won Co",
            email="nowon@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        nego = get_stage(db, "STANDALONE", "negotiation")
        assert nego is not None
        biz.pipeline_stage_id = nego.id
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_nowon_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="pending_payment",
                quoted_amount_cents=10000,
            )
        )
        db.commit()
        bid = biz.id
        nego_id = nego.id
        secret = "test-be-shared-secret"
    finally:
        db.close()

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "eventId": f"evt-nowon-{uuid.uuid4().hex}",
            "event": "standalone.payment.paid",
            "purchaseId": "sp_nowon_1",
            "crmLeadId": crm_lead_id_for(bid),
            "paymentStatus": "PAID",
            "paidAmountCents": 10000,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "paid"
    assert (body.get("won") or {}).get("won_mirrored") is False

    db = SessionLocal()
    try:
        biz = db.get(Business, bid)
        assert biz.pipeline_stage_id == nego_id
        d = db.scalar(select(BeDelivery).where(BeDelivery.purchase_id == "sp_nowon_1"))
        assert d and d.financial_status == "paid"
        assert d.paid_amount_cents == 10000
    finally:
        db.close()


def test_partial_payment_and_milestone_snapshots(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import init_new_prospect

        biz = Business(
            place_id=f"be/bridge-partial-{uuid.uuid4().hex[:8]}",
            name="Partial Pay Co",
            email="partial@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_partial_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="pending_payment",
                quoted_amount_cents=20000,
            )
        )
        db.commit()
        bid = biz.id
        secret = "test-be-shared-secret"
    finally:
        db.close()

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "eventId": f"evt-partial-{uuid.uuid4().hex}",
            "event": "payment.partially_paid",
            "purchaseId": "sp_partial_1",
            "crmLeadId": crm_lead_id_for(bid),
            "paidAmountCents": 5000,
            "outstandingCents": 15000,
            "milestones": [
                {
                    "id": "ms1",
                    "name": "Deposit",
                    "amountCents": 5000,
                    "status": "paid",
                    "currency": "AUD",
                },
                {
                    "id": "ms2",
                    "name": "Balance",
                    "amountCents": 15000,
                    "status": "pending",
                    "dueAt": "2099-01-01T00:00:00+00:00",
                    "currency": "AUD",
                },
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json().get("financial_status") == "partially_paid"

    commercial = auth_client.get(f"/api/sales/be/prospects/{bid}/commercial")
    assert commercial.status_code == 200, commercial.text
    data = commercial.json()
    assert data["payment_status"] == "partially_paid"
    assert data["paid_amount_cents"] == 5000
    assert data["outstanding_balance_cents"] == 15000
    assert data["milestone_summary"]["milestone_count"] == 2
    assert data["milestone_summary"]["paid_milestones"] == 1
    assert data["read_only_commercial"] is True



def test_agreement_quote_invoice_and_milestone_events(auth_client):
    """AH commercial OS emitters → SOS snapshot fields (no Won on these alone)."""
    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import init_new_prospect

        biz = Business(
            place_id=f"be/bridge-docs-{uuid.uuid4().hex[:8]}",
            name="Docs Event Co",
            email="docs@mapsleads.test",
            currency="USD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_docs_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="sent",
                quoted_amount_cents=30000,
            )
        )
        db.commit()
        bid = biz.id
        secret = "test-be-shared-secret"
        lead = crm_lead_id_for(bid)
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {secret}"}

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers=headers,
        json={
            "eventId": f"evt-quote-acc-{uuid.uuid4().hex}",
            "event": "quote.accepted",
            "purchaseId": "sp_docs_1",
            "crmLeadId": lead,
            "documentId": "doc-q1",
        },
    )
    assert r.status_code == 200, r.text

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers=headers,
        json={
            "eventId": f"evt-agr-acc-{uuid.uuid4().hex}",
            "event": "agreement.accepted",
            "purchaseId": "sp_docs_1",
            "crmLeadId": lead,
            "documentId": "doc-a1",
            "documentNumber": "AG-1",
        },
    )
    assert r.status_code == 200, r.text

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers=headers,
        json={
            "eventId": f"evt-inv-{uuid.uuid4().hex}",
            "event": "invoice.created",
            "purchaseId": "sp_docs_1",
            "crmLeadId": lead,
            "documentId": "doc-i1",
            "amountCents": 15000,
            "currency": "USD",
        },
    )
    assert r.status_code == 200, r.text

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers=headers,
        json={
            "eventId": f"evt-ms-created-{uuid.uuid4().hex}",
            "event": "milestone.created",
            "purchaseId": "sp_docs_1",
            "crmLeadId": lead,
            "milestones": [
                {
                    "id": "ms_dep",
                    "name": "Deposit",
                    "amountCents": 10000,
                    "status": "pending",
                    "currency": "USD",
                },
                {
                    "id": "ms_bal",
                    "name": "Balance",
                    "amountCents": 20000,
                    "status": "PARTIALLY_PAID",
                    "currency": "USD",
                },
            ],
        },
    )
    assert r.status_code == 200, r.text

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers=headers,
        json={
            "eventId": f"evt-ms-paid-{uuid.uuid4().hex}",
            "event": "milestone.paid",
            "purchaseId": "sp_docs_1",
            "crmLeadId": lead,
            "milestoneId": "ms_dep",
            "milestoneName": "Deposit",
            "amountCents": 10000,
            "currency": "USD",
        },
    )
    assert r.status_code == 200, r.text

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers=headers,
        json={
            "eventId": f"evt-ms-od-{uuid.uuid4().hex}",
            "event": "milestone.overdue",
            "purchaseId": "sp_docs_1",
            "crmLeadId": lead,
            "milestoneId": "ms_bal",
            "milestoneName": "Balance",
            "amountCents": 20000,
            "dueAt": "2020-01-01T00:00:00+00:00",
            "currency": "USD",
        },
    )
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        d = db.scalar(select(BeDelivery).where(BeDelivery.purchase_id == "sp_docs_1"))
        assert d is not None
        assert d.quote_status == "accepted"
        assert d.agreement_status == "accepted"
        assert d.invoice_status == "created"
        snaps = list(
            db.scalars(
                select(BeMilestoneSnapshot).where(BeMilestoneSnapshot.delivery_id == d.id)
            )
        )
        by_id = {s.ah_milestone_id: s for s in snaps}
        assert by_id["ms_dep"].status == "paid"
        assert by_id["ms_bal"].status == "overdue"
        # PARTIALLY_PAID from created payload maps to pending until paid/overdue event
    finally:
        db.close()



def test_refund_dispute_and_delivery_stage_events(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import init_new_prospect

        biz = Business(
            place_id=f"be/bridge-rdf-{uuid.uuid4().hex[:8]}",
            name="Refund Dispute Co",
            email="rdf@mapsleads.test",
            currency="USD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_rdf_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="paid",
                financial_status="paid",
                quoted_amount_cents=10000,
                paid_amount_cents=10000,
            )
        )
        db.commit()
        bid = biz.id
        secret = "test-be-shared-secret"
        lead = crm_lead_id_for(bid)
    finally:
        db.close()

    headers = {"Authorization": f"Bearer {secret}"}

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers=headers,
        json={
            "eventId": f"evt-refund-{uuid.uuid4().hex}",
            "event": "payment.refunded",
            "purchaseId": "sp_rdf_1",
            "crmLeadId": lead,
            "paidAmountCents": 0,
            "outstandingCents": 10000,
        },
    )
    assert r.status_code == 200, r.text

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers=headers,
        json={
            "eventId": f"evt-disp-{uuid.uuid4().hex}",
            "event": "payment.disputed",
            "purchaseId": "sp_rdf_1",
            "crmLeadId": lead,
            "paidAmountCents": 5000,
            "outstandingCents": 5000,
        },
    )
    assert r.status_code == 200, r.text

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers=headers,
        json={
            "eventId": f"evt-dstart-{uuid.uuid4().hex}",
            "event": "delivery.started",
            "purchaseId": "sp_rdf_1",
            "crmLeadId": lead,
            "deliveryStatus": "READY_TO_FINALIZE",
        },
    )
    assert r.status_code == 200, r.text

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers=headers,
        json={
            "eventId": f"evt-dfin-{uuid.uuid4().hex}",
            "event": "delivery.finalized",
            "purchaseId": "sp_rdf_1",
            "crmLeadId": lead,
            "deliveryStatus": "FINALIZED",
        },
    )
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        d = db.scalar(select(BeDelivery).where(BeDelivery.purchase_id == "sp_rdf_1"))
        assert d is not None
        assert d.financial_status == "disputed"
        assert d.delivery_status in ("started", "finalized", "ready")
    finally:
        db.close()


def test_connection_exposes_sync_fields_without_secret(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
    finally:
        db.close()
    r = auth_client.get("/api/sales/be/connection")
    assert r.status_code == 200
    body = r.json()
    assert body.get("secret_configured") is True
    assert body.get("secret_hint") == "configured"
    assert "test-be-shared-secret" not in json.dumps(body)
    assert "pending_events" in body
    assert "failed_events" in body
    assert "permissions" in body
    assert "secret_rotation_status" in body
    assert "won_policy" in body


def test_event_alias_payment_paid(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        store_setting(
            db,
            "be.won_policy",
            json.dumps({"auto_won_on_be_paid": False, "require_fully_paid": True}),
            secret=False,
        )
        from app.services.pipeline import init_new_prospect

        biz = Business(
            place_id=f"be/bridge-alias-{uuid.uuid4().hex[:8]}",
            name="Alias Event Co",
            email="alias@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_alias_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="pending_payment",
            )
        )
        db.commit()
        bid = biz.id
        secret = "test-be-shared-secret"
    finally:
        db.close()

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "eventId": f"evt-alias-{uuid.uuid4().hex}",
            "event": "payment.paid",
            "purchaseId": "sp_alias_1",
            "crmLeadId": crm_lead_id_for(bid),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json().get("event") == "standalone.payment.paid"
    assert r.json().get("status") == "paid"

    events = auth_client.get("/api/sales/be/events")
    assert events.status_code == 200
    assert events.json()["counts"]["processed"] >= 1


def test_monitor_and_stage_history_endpoints(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import init_new_prospect

        biz = Business(
            place_id=f"be/bridge-mon-{uuid.uuid4().hex[:8]}",
            name="Monitor Co",
            email="mon@mapsleads.test",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.commit()
        bid = biz.id
    finally:
        db.close()

    mon = auth_client.get("/api/sales/be/monitor")
    assert mon.status_code == 200
    assert "connection" in mon.json()
    assert "funnel" in mon.json()
    assert "event_counts" in mon.json()

    hist = auth_client.get(f"/api/sales/prospects/{bid}/stage-history")
    assert hist.status_code == 200
    assert "history" in hist.json()

    dash = auth_client.get("/api/sales/dashboard?product_line=STANDALONE")
    assert dash.status_code == 200
    assert "appointhive" in dash.json()


def test_customer_ref_links_prospects(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import init_new_prospect

        a = Business(
            place_id=f"be/cust-a-{uuid.uuid4().hex[:8]}",
            name="Cust A",
            email="a@mapsleads.test",
            product_line="STANDALONE",
            be_customer_ref="cust_shared_1",
        )
        b = Business(
            place_id=f"be/cust-b-{uuid.uuid4().hex[:8]}",
            name="Cust B",
            email="b@mapsleads.test",
            product_line="STANDALONE",
            be_customer_ref="cust_shared_1",
        )
        db.add(a)
        db.add(b)
        db.flush()
        init_new_prospect(db, a)
        init_new_prospect(db, b)
        db.commit()
    finally:
        db.close()

    r = auth_client.get("/api/sales/be/customers/cust_shared_1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["customer_ref"] == "cust_shared_1"
    assert len(body["prospects"]) == 2


def test_pipeline_includes_financial_delivery_fields(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import init_new_prospect

        biz = Business(
            place_id=f"be/pipe-fin-{uuid.uuid4().hex[:8]}",
            name="Pipe Fin Co",
            email="pipe@mapsleads.test",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_pipe_1",
                status="pending_payment",
                financial_status="partially_paid",
                delivery_status="started",
                crm_lead_id=crm_lead_id_for(biz.id),
            )
        )
        db.commit()
        bid = biz.id
    finally:
        db.close()

    r = auth_client.get("/api/sales/pipeline?product_line=STANDALONE")
    assert r.status_code == 200, r.text
    found = None
    for col in r.json().get("columns") or []:
        for p in col.get("prospects") or []:
            if p.get("id") == bid:
                found = p
                break
    assert found is not None
    assert found.get("financial_status") == "partially_paid"
    assert found.get("delivery_status") == "started"


def test_dashboard_filters_and_won_policy_roundtrip(auth_client):
    r = auth_client.put(
        "/api/sales/be/won-policy",
        json={
            "auto_won_on_be_paid": False,
            "require_fully_paid": True,
            "require_agreement_accepted": True,
            "require_initial_milestone_paid": False,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["require_agreement_accepted"] is True

    g = auth_client.get("/api/sales/be/won-policy")
    assert g.status_code == 200
    assert g.json()["require_agreement_accepted"] is True

    d = auth_client.get("/api/sales/dashboard?product_line=STANDALONE&payment_status=paid")
    assert d.status_code == 200
    assert "filtered" in d.json()
    assert "filters" in d.json()


def test_webhook_stores_customer_ref_on_business(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import init_new_prospect

        biz = Business(
            place_id=f"be/cust-ref-{uuid.uuid4().hex[:8]}",
            name="Cust Ref Co",
            email="cref@mapsleads.test",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_cref_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="sent",
            )
        )
        db.commit()
        bid = biz.id
        secret = "test-be-shared-secret"
    finally:
        db.close()

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "eventId": f"evt-cref-{uuid.uuid4().hex}",
            "event": "standalone.purchase.recorded",
            "purchaseId": "sp_cref_1",
            "crmLeadId": crm_lead_id_for(bid),
            "customerRef": "ah_cust_99",
        },
    )
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        biz = db.get(Business, bid)
        assert biz.be_customer_ref == "ah_cust_99"
    finally:
        db.close()


def test_standalone_quote_created_alias_and_order_reference(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import init_new_prospect

        biz = Business(
            place_id=f"be/quote-alias-{uuid.uuid4().hex[:8]}",
            name="Quote Alias Co",
            email="quotealias@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_quote_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="sent",
            )
        )
        db.commit()
        bid = biz.id
        secret = "test-be-shared-secret"
    finally:
        db.close()

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "eventId": f"evt-quote-{uuid.uuid4().hex}",
            "event": "standalone.quote.created",
            "purchaseId": "sp_quote_1",
            "crmLeadId": crm_lead_id_for(bid),
            "orderReference": "AH-ORD-77",
            "quoteStatus": "CREATED",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json().get("event") == "quote.created"

    commercial = auth_client.get(f"/api/sales/be/prospects/{bid}/commercial")
    assert commercial.status_code == 200, commercial.text
    data = commercial.json()
    assert data["quotation_status"] == "created"
    assert data["order_reference"] == "AH-ORD-77"
    assert data["purchase_order_reference"] == "AH-ORD-77"

    db = SessionLocal()
    try:
        d = db.scalar(select(BeDelivery).where(BeDelivery.purchase_id == "sp_quote_1"))
        assert d and d.order_reference == "AH-ORD-77"
        assert d.quote_status == "created"
    finally:
        db.close()


def test_license_issued_and_upgrade_applied_by_installation(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import init_new_prospect

        biz = Business(
            place_id=f"be/upgrade-inst-{uuid.uuid4().hex[:8]}",
            name="Upgrade Install Co",
            email="upinst@mapsleads.test",
            product_line="STANDALONE",
            be_installation_id="inst_match_1",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_up_inst_1",
                installation_id="inst_match_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="delivery_ready",
            )
        )
        db.commit()
        bid = biz.id
        secret = "test-be-shared-secret"
    finally:
        db.close()

    lic = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "eventId": f"evt-lic-{uuid.uuid4().hex}",
            "event": "standalone.license.issued",
            "purchaseId": "sp_up_inst_1",
            "licenseId": "lic_abc",
            "crmLeadId": crm_lead_id_for(bid),
        },
    )
    assert lic.status_code == 200, lic.text

    # No crmLeadId / purchaseId — match via installationId only
    up = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "eventId": f"evt-up-inst-{uuid.uuid4().hex}",
            "event": "standalone.upgrade.applied",
            "installationId": "inst_match_1",
            "fromRevisionId": "rev_a",
            "toRevisionId": "rev_b",
        },
    )
    assert up.status_code == 200, up.text
    assert up.json().get("event") == "standalone.upgrade.applied"

    db = SessionLocal()
    try:
        d = db.scalar(select(BeDelivery).where(BeDelivery.business_id == bid).order_by(BeDelivery.id.desc()))
        assert d is not None
        assert d.release_status == "licensed"
        assert d.upgrade_status == "applied"
        assert d.revision_id == "rev_b"
        assert d.from_revision_id == "rev_a"
    finally:
        db.close()


def test_soft_intake_failure_logged_but_purchase_continues(auth_client):
    from app.models import BeOutboundFailure
    from app.services.booking_engine.client import BeClientError

    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import init_new_prospect

        biz = Business(
            place_id=f"be/soft-fail-{uuid.uuid4().hex[:8]}",
            name="Soft Fail Co",
            email="softfail@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.commit()
        bid = biz.id
    finally:
        db.close()

    fake_purchase = {
        "purchaseId": "sp_soft_fail_1",
        "inboundRequestId": "",
        "revisionId": "rev_sf",
        "priceCents": 5000,
        "currency": "AUD",
        "checkoutUrl": "https://pay.example/sf",
        "orderReference": "AH-ORD-SF",
        "crmLeadId": crm_lead_id_for(bid),
    }

    def boom_request(self, payload):
        raise BeClientError("soft intake down", status=502)

    with (
        patch(
            "app.services.booking_engine.client.BookingEngineClient.create_request",
            boom_request,
        ),
        patch(
            "app.services.booking_engine.client.BookingEngineClient.create_purchase",
            return_value=fake_purchase,
        ),
    ):
        r = auth_client.post(
            "/api/sales/be/send-to-delivery",
            json={"business_id": bid, "product_revision_id": "rev_sf"},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["delivery"]["purchase_id"] == "sp_soft_fail_1"
    assert body["delivery"]["order_reference"] == "AH-ORD-SF"
    assert "soft_intake_warning" in body

    db = SessionLocal()
    try:
        fails = list(
            db.scalars(
                select(BeOutboundFailure).where(
                    BeOutboundFailure.business_id == bid,
                    BeOutboundFailure.operation == "send_purchase_soft_intake",
                )
            ).all()
        )
        assert len(fails) >= 1
    finally:
        db.close()


def test_payment_refunded_and_disputed_webhooks(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import init_new_prospect

        biz = Business(
            place_id=f"be/refund-{uuid.uuid4().hex[:8]}",
            name="Refund Co",
            email="refund@mapsleads.test",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_refund_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="paid",
                financial_status="paid",
            )
        )
        db.commit()
        bid = biz.id
        secret = "test-be-shared-secret"
    finally:
        db.close()

    r = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "eventId": f"evt-refund-{uuid.uuid4().hex}",
            "event": "payment.refunded",
            "purchaseId": "sp_refund_1",
            "crmLeadId": crm_lead_id_for(bid),
        },
    )
    assert r.status_code == 200, r.text
    assert r.json().get("financial_status") == "refunded"

    d = auth_client.post(
        "/api/webhooks/booking-engine",
        headers={"Authorization": f"Bearer {secret}"},
        json={
            "eventId": f"evt-dispute-{uuid.uuid4().hex}",
            "event": "payment.disputed",
            "purchaseId": "sp_refund_1",
            "crmLeadId": crm_lead_id_for(bid),
        },
    )
    assert d.status_code == 200, d.text
    assert d.json().get("financial_status") == "disputed"


def test_inbox_auto_retry_batch(auth_client):
    from app.models import BeEventInbox
    from app.services.booking_engine.events import retry_failed_events_batch

    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import init_new_prospect

        biz = Business(
            place_id=f"be/retry-batch-{uuid.uuid4().hex[:8]}",
            name="Retry Batch Co",
            email="retrybatch@mapsleads.test",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_retry_batch_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="sent",
            )
        )
        row = BeEventInbox(
            event_id=f"evt-retry-batch-{uuid.uuid4().hex}",
            event_type="standalone.payment.paid",
            status="failed",
            business_id=biz.id,
            attempts=1,
            error="simulated",
            payload_json=json.dumps(
                {
                    "event": "standalone.payment.paid",
                    "purchaseId": "sp_retry_batch_1",
                    "crmLeadId": crm_lead_id_for(biz.id),
                    "paidAmountCents": 1000,
                }
            ),
        )
        db.add(row)
        db.commit()
        eid = row.id
        bid = biz.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        store_setting(
            db,
            "be.won_policy",
            json.dumps({"auto_won_on_be_paid": False, "require_fully_paid": True}),
            secret=False,
        )
        db.commit()
        out = retry_failed_events_batch(db, limit=5)
        assert out["retried"] >= 1
        assert out["succeeded"] >= 1
        row = db.get(BeEventInbox, eid)
        assert row and row.status == "processed"
        d = db.scalar(select(BeDelivery).where(BeDelivery.business_id == bid).order_by(BeDelivery.id.desc()))
        assert d and d.financial_status == "paid"
    finally:
        db.close()


def test_saas_proxy_routes_require_connection_and_forward(auth_client):
    db = SessionLocal()
    try:
        _force_disconnected(db)
    finally:
        db.close()
    blocked = auth_client.post(
        "/api/sales/be/saas/demos",
        json={"business_name": "Acme", "owner_email": "owner@mapsleads.test"},
    )
    assert blocked.status_code == 409

    db = SessionLocal()
    try:
        _force_connected(db)
    finally:
        db.close()

    with patch(
        "app.services.booking_engine.client.BookingEngineClient.create_demo",
        return_value={"clientId": "c1", "url": "https://demo.example/c1"},
    ):
        r = auth_client.post(
            "/api/sales/be/saas/demos",
            json={"business_name": "Acme", "owner_email": "owner@mapsleads.test"},
        )
    assert r.status_code == 200, r.text
    assert r.json()["clientId"] == "c1"

    with patch(
        "app.services.booking_engine.client.BookingEngineClient.create_trial",
        return_value={"clientId": "c2", "billingStatus": "TRIAL"},
    ):
        t = auth_client.post(
            "/api/sales/be/saas/trials",
            json={"business_name": "Acme", "owner_email": "owner@mapsleads.test"},
        )
    assert t.status_code == 200, t.text

    with patch(
        "app.services.booking_engine.client.BookingEngineClient.get_client",
        return_value={"clientId": "c2", "billingStatus": "TRIAL"},
    ):
        g = auth_client.get("/api/sales/be/saas/clients/c2")
    assert g.status_code == 200, g.text


def test_payment_evidence_forces_pending(auth_client):
    db = SessionLocal()
    try:
        _force_connected(db)
        from app.services.pipeline import init_new_prospect

        biz = Business(
            place_id=f"be/evidence-{uuid.uuid4().hex[:8]}",
            name="Evidence Co",
            email="evidence@mapsleads.test",
            currency="AUD",
            product_line="STANDALONE",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.add(
            BeDelivery(
                business_id=biz.id,
                purpose="purchase",
                purchase_id="sp_ev_1",
                crm_lead_id=crm_lead_id_for(biz.id),
                status="pending_payment",
                currency="AUD",
            )
        )
        db.commit()
        bid = biz.id
    finally:
        db.close()

    captured = {}

    def fake_submit(self, payload):
        captured["payload"] = dict(payload)
        return {
            "paymentId": "pay_ev_1",
            "paymentStatus": "PENDING",
            "verificationRequired": True,
        }

    with patch(
        "app.services.booking_engine.client.BookingEngineClient.submit_payment_evidence",
        fake_submit,
    ):
        r = auth_client.post(
            "/api/sales/be/payment-evidence",
            json={
                "business_id": bid,
                "method": "BANK_TRANSFER",
                "amount_cents": 2500,
                "reference_note": "Transfer ref ABC",
            },
        )
    assert r.status_code == 200, r.text
    assert r.json().get("verification_required") is True
    assert captured["payload"]["status"] == "PENDING"
    assert captured["payload"]["purchaseId"] == "sp_ev_1"
    assert "finalize" not in captured["payload"]
