"""Gaps coverage: per-user login, contact queue, undo stage, stripe sig, enrich job."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

from app.services.passwords import hash_password, verify_password


def test_password_hash_roundtrip():
    h = hash_password("secret-pass-99")
    assert verify_password("secret-pass-99", h)
    assert not verify_password("wrong", h)


def test_per_user_login(client, auth_client):
    # Owner creates sales user with password
    r = auth_client.post(
        "/api/sales/users",
        json={
            "email": "rep.gap@example.com",
            "name": "Rep Gap",
            "role": "sales",
            "password": "sales-pass-88",
        },
    )
    assert r.status_code == 200, r.text
    uid = r.json()["id"]
    assert r.json().get("has_password") is True

    client.post("/api/logout")
    client.cookies.clear()
    bad = client.post("/api/login", json={"username": "rep.gap@example.com", "password": "wrong"})
    assert bad.status_code == 401

    ok = client.post("/api/login", json={"username": "rep.gap@example.com", "password": "sales-pass-88"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["username"] == "rep.gap@example.com"
    assert ok.json()["role"] == "sales"

    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["username"] == "rep.gap@example.com"
    assert me.json()["role"] == "sales"

    # Owner can reset password
    client.post("/api/logout")
    client.cookies.clear()
    auth = client.post(
        "/api/login",
        json={"username": "hello@appointhive.com", "password": "test-password-sales-os"},
    )
    assert auth.status_code == 200
    pw = client.post(f"/api/sales/users/{uid}/password", json={"password": "new-pass-123"})
    assert pw.status_code == 200


def test_contact_queue_endpoint(auth_client):
    r = auth_client.get("/api/sales/contact-queue?limit=10")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "leads" in data
    assert "definition" in data
    assert "sample_size" in data
    assert "modes" in data
    assert data.get("mode")
    ids = {m["id"] for m in data["modes"]}
    assert "default" in ids
    assert "no_website_instagram" in ids
    assert "website_no_contact" in ids
    # Switch mode via settings + filtered queue
    put = auth_client.put("/api/sales/settings/contact_queue_mode?value=missing_contacts")
    assert put.status_code == 200, put.text
    r2 = auth_client.get("/api/sales/contact-queue?limit=10")
    assert r2.status_code == 200
    assert r2.json()["mode"] == "missing_contacts"
    # Explicit query override
    r3 = auth_client.get("/api/sales/contact-queue?limit=10&mode=default")
    assert r3.status_code == 200
    assert r3.json()["mode"] == "default"


def test_contact_queue_mark_for_selected_tab(auth_client):
    """Marking a ranked lead sets marked_for_contact so UI Selected tab can list it."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Business

    with SessionLocal() as db:
        b = Business(
            place_id="qa-cq-mark-1",
            name="CQ Mark Salon",
            email="cqmark@example.com",
            city="Sydney",
            country="AU",
            product_line="STANDALONE",
            marked_for_contact=False,
            contacted=False,
            created_at=datetime.now(timezone.utc),
        )
        db.add(b)
        db.commit()
        bid = b.id

    q = auth_client.get("/api/sales/contact-queue?limit=100&mode=default")
    assert q.status_code == 200, q.text
    ids = {int(x["id"]) for x in (q.json().get("leads") or [])}
    assert bid in ids

    bulk = auth_client.post("/api/businesses/bulk", json={"ids": [bid], "marked_for_contact": True})
    assert bulk.status_code == 200, bulk.text

    q2 = auth_client.get("/api/sales/contact-queue?limit=100&mode=default")
    assert q2.status_code == 200
    row = next(x for x in q2.json()["leads"] if int(x["id"]) == bid)
    assert row.get("marked_for_contact") is True

    with SessionLocal() as db:
        rowb = db.get(Business, bid)
        if rowb:
            db.delete(rowb)
            db.commit()


def test_undo_stage_and_link(auth_client):
    # Create a prospect via Places-less path: use search seed? Use CSV-less business via pipeline list
    pipe = auth_client.get("/api/sales/pipeline?product_line=STANDALONE")
    assert pipe.status_code == 200
    cols = pipe.json().get("columns") or []
    prospect = None
    for c in cols:
        for p in c.get("prospects") or []:
            prospect = p
            break
        if prospect:
            break
    if not prospect:
        # create via import API not needed — skip if empty DB
        return
    bid = prospect["id"]
    stages = auth_client.get("/api/sales/stages?product_line=STANDALONE").json()["stages"]
    contacted = next(s for s in stages if s["slug"] == "contacted")
    move = auth_client.post(
        f"/api/sales/prospects/{bid}/stage",
        json={"stage_id": contacted["id"], "force": True, "note": "test move"},
    )
    assert move.status_code == 200, move.text
    undo = auth_client.post(f"/api/sales/prospects/{bid}/undo-stage")
    assert undo.status_code == 200, undo.text

    # Link to self fails
    bad = auth_client.post(f"/api/sales/prospects/{bid}/link", json={"linked_prospect_id": bid})
    assert bad.status_code == 422


def test_stripe_webhook_rejects_bad_signature(auth_client):
    secret = "whsec_test_gap"
    auth_client.put(
        "/api/sales/payment-settings",
        json={"key": "pay.stripe_webhook_secret", "value": secret},
    )

    body = json.dumps({"id": "evt_gap_1", "type": "checkout.session.completed", "data": {"object": {}}}).encode()
    r = auth_client.post("/api/webhooks/stripe", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 400

    ts = str(int(time.time()))
    signed = f"{ts}.".encode() + body
    sig = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={sig}"
    r2 = auth_client.post(
        "/api/webhooks/stripe",
        content=body,
        headers={"Content-Type": "application/json", "stripe-signature": header},
    )
    assert r2.status_code == 200, r2.text


def test_enrich_batch_job(auth_client):
    pipe = auth_client.get("/api/sales/pipeline?product_line=STANDALONE").json()
    ids = []
    for c in pipe.get("columns") or []:
        for p in c.get("prospects") or []:
            ids.append(p["id"])
            if len(ids) >= 2:
                break
        if len(ids) >= 2:
            break
    if not ids:
        return
    job = auth_client.post("/api/sales/jobs", json={"job_type": "enrich_batch", "payload": {"business_ids": ids}})
    assert job.status_code == 200, job.text
    jid = job.json()["id"]
    tick = auth_client.post(f"/api/jobs/{jid}/tick")
    assert tick.status_code == 200, tick.text
    assert tick.json().get("done") is True


def test_duplicates_resolve_ignore(auth_client):
    pipe = auth_client.get("/api/sales/pipeline?product_line=STANDALONE").json()
    ids = []
    for c in pipe.get("columns") or []:
        for p in c.get("prospects") or []:
            ids.append(p["id"])
            if len(ids) >= 2:
                break
        if len(ids) >= 2:
            break
    if len(ids) < 2:
        return
    r = auth_client.post(
        "/api/sales/duplicates/resolve",
        json={"action": "ignore", "primary_id": ids[0], "secondary_id": ids[1]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "ignore"


def test_payoneer_skrill_invoice_instructions(auth_client):
    from datetime import datetime, timezone

    from app.db import SessionLocal
    from app.models import Business
    from app.services.billing.service import create_invoice_from_proposal, create_proposal
    from app.services.pipeline import init_new_prospect

    with SessionLocal() as db:
        b = Business(place_id="ext/pay-alt-1", name="Pay Alt Co", email="a@b.com", currency="AUD")
        db.add(b)
        db.flush()
        init_new_prospect(db, b)
        prop = create_proposal(
            db, b, title="Alt pay", currency="AUD", line_items=[{"name": "Setup", "qty": 1, "amount": 50}]
        )
        inv_po = create_invoice_from_proposal(db, prop, provider="payoneer")
        assert "Pay via Payoneer" in (inv_po.pay_instructions or "")
        assert inv_po.provider_ref.startswith("payoneer_")
        inv_sk = create_invoice_from_proposal(db, prop, provider="skrill")
        assert "Pay via Skrill" in (inv_sk.pay_instructions or "")
        db.commit()
        ref = inv_po.provider_ref
        number = inv_po.number

    # Webhook gating uses app.services.secrets (env/DB), not ops module constants.
    monkey = __import__("pytest").MonkeyPatch()
    try:
        monkey.setattr("app.services.secrets.payoneer_webhook_secret", lambda db=None: "")
        r = auth_client.post(
            "/api/webhooks/payoneer", json={"id": "po1", "status": "completed", "provider_ref": ref}
        )
        assert r.status_code == 503

        monkey.setattr("app.services.secrets.payoneer_webhook_secret", lambda db=None: "po-secret-test")
        bad = auth_client.post(
            "/api/webhooks/payoneer",
            json={"id": "po2", "status": "completed", "provider_ref": ref},
            headers={"X-Webhook-Secret": "wrong"},
        )
        assert bad.status_code == 401
        ok = auth_client.post(
            "/api/webhooks/payoneer",
            json={"id": "po3", "status": "completed", "provider_ref": ref},
            headers={"X-Webhook-Secret": "po-secret-test"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json().get("invoice") == number
        dup = auth_client.post(
            "/api/webhooks/payoneer",
            json={"id": "po3", "status": "completed", "provider_ref": ref},
            headers={"X-Webhook-Secret": "po-secret-test"},
        )
        assert dup.status_code == 200
        assert dup.json().get("duplicate") is True
    finally:
        monkey.undo()


def test_unified_activity_shape(auth_client):
    pipe = auth_client.get("/api/sales/pipeline?product_line=STANDALONE").json()
    bid = None
    for c in pipe.get("columns") or []:
        for p in c.get("prospects") or []:
            bid = p["id"]
            break
        if bid:
            break
    if not bid:
        return
    r = auth_client.get(f"/api/businesses/{bid}/activity")
    assert r.status_code == 200
    data = r.json()
    assert "results" in data
    for row in data["results"]:
        assert "source" in row
