#!/usr/bin/env python3
"""Senior QA confirm: fresh prospect → soft → purchase → paid→Won → soft Won-guard → install."""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

import httpx

BASE = os.environ.get("SO_BASE", "http://127.0.0.1:8000")
EV = Path(os.environ["EVIDENCE_DIR"])
EV.mkdir(parents=True, exist_ok=True)


def dump(name: str, obj) -> None:
    (EV / name).write_text(json.dumps(obj, indent=2, default=str)[:60000], encoding="utf-8")


def j(r):
    try:
        return r.json()
    except Exception:
        return {"_status": r.status_code, "_raw": (r.text or "")[:2000]}


def main() -> int:
    user = os.environ.get("LOGIN_USERNAME") or "hello@appointhive.com"
    password = os.environ["LOGIN_PASSWORD"]
    c = httpx.Client(base_url=BASE, timeout=90.0, follow_redirects=True)
    out: dict = {"checks": {}}

    assert c.post("/api/login", json={"username": user, "password": password}).status_code == 200
    r = c.post("/api/sales/be/connection/test")
    conn = j(r)
    dump("live-connect.json", conn)
    out["checks"]["connect"] = bool(conn.get("connected"))
    if not out["checks"]["connect"]:
        dump("live-confirm-summary.json", out)
        return 2

    revs = (c.get("/api/sales/be/revisions").json() or {}).get("revisions") or []
    dump("live-revisions.json", {"count": len(revs), "sample": revs[:2]})
    out["checks"]["revisions"] = len(revs) > 0
    revision_id = (revs[0].get("productRevisionId") or revs[0].get("id")) if revs else None

    # Fresh prospect via DB (no public create API)
    from app.db import SessionLocal
    from app.models import Business, Contact, PipelineStage, BeDelivery
    from app.services.pipeline import init_new_prospect, get_stage
    from app.services.booking_engine.connect import get_be_shared_secret

    place = f"qa/confirm-{uuid.uuid4().hex[:12]}"
    db = SessionLocal()
    try:
        biz = Business(
            place_id=place,
            name="QA Confirm Handover Salon",
            email="qa.confirm.owner@example.com",
            phone="+61400111222",
            international_phone="+61400111222",
            website="https://qa-confirm.example",
            address="99 Confirm St",
            city="Sydney",
            country="AU",
            category="Hair salon",
            product_line="STANDALONE",
            currency="AUD",
            deal_value=1990,
            lead_source="qa-confirm",
            main_pain="Need booking",
            notes="Senior QA confirm cycle",
        )
        db.add(biz)
        db.flush()
        init_new_prospect(db, biz)
        db.add(
            Contact(
                business_id=biz.id,
                name="Casey Owner",
                email="qa.confirm.owner@example.com",
                phone="+61400111222",
                role="Owner",
                preferred_channel="email",
                verified=True,
            )
        )
        db.commit()
        bid = biz.id
        secret = get_be_shared_secret(db=db)
    finally:
        db.close()
    out["business_id"] = bid
    out["crmLeadId"] = f"business:{bid}"

    r = c.post(
        "/api/sales/be/send-request",
        json={"business_id": bid, "message": "QA confirm soft handover"},
    )
    soft = j(r)
    dump("live-soft.json", {"status_code": r.status_code, "body": soft})
    out["checks"]["soft"] = r.status_code == 200 and not soft.get("detail")
    out["checks"]["soft_prospect"] = "crmProspect" in json.dumps(soft) or "CRM_PROSPECT" in json.dumps(soft)

    r = c.post(
        "/api/sales/be/send-to-delivery",
        json={"business_id": bid, "product_revision_id": revision_id, "create_checkout": True},
    )
    purch = j(r)
    dump("live-purchase.json", {"status_code": r.status_code, "body": purch})
    out["checks"]["purchase"] = r.status_code == 200 and not purch.get("detail")
    delivery = purch.get("delivery") or {}
    be = purch.get("be") or {}
    purchase_id = delivery.get("purchase_id") or be.get("purchaseId")
    pay_url = purch.get("pay_url") or delivery.get("pay_url") or be.get("checkoutUrl")
    out["checks"]["pay_url"] = bool(pay_url)
    out["purchase_id"] = purchase_id

    event_id = f"qa-confirm-paid-{uuid.uuid4().hex}"
    wh = []
    if secret and purchase_id:
        payload_paid = {
            "eventId": event_id,
            "event": "standalone.payment.paid",
            "type": "standalone.payment.paid",
            "crmLeadId": f"business:{bid}",
            "externalCrmRef": f"business:{bid}",
            "purchaseId": purchase_id,
            "status": "PAID",
            "amountCents": (revs[0].get("priceCents") if revs else 0) or 0,
        }
        rr = c.post(
            "/api/webhooks/booking-engine",
            headers={"Authorization": f"Bearer {secret}"},
            json=payload_paid,
        )
        wh.append({"event": "paid", "status_code": rr.status_code, "body": j(rr)})
    dump("live-webhook-paid.json", wh)
    out["checks"]["webhook_paid"] = bool(wh) and wh[0]["status_code"] == 200 and not wh[0]["body"].get("duplicate")

    db = SessionLocal()
    try:
        b = db.get(Business, bid)
        st = db.get(PipelineStage, b.pipeline_stage_id) if b and b.pipeline_stage_id else None
        out["after_paid"] = {
            "stage": st.slug if st else None,
            "is_won": bool(st and st.is_won),
        }
        out["checks"]["won"] = bool(st and (st.is_won or st.slug == "won"))
    finally:
        db.close()

    # Won-guard: soft again must stay Won
    r = c.post(
        "/api/sales/be/send-request",
        json={"business_id": bid, "message": "QA confirm soft after Won"},
    )
    dump("live-soft-after-won.json", {"status_code": r.status_code, "body": j(r)})
    db = SessionLocal()
    try:
        b = db.get(Business, bid)
        st = db.get(PipelineStage, b.pipeline_stage_id) if b and b.pipeline_stage_id else None
        out["after_soft_post_won"] = {"stage": st.slug if st else None, "is_won": bool(st and st.is_won)}
        out["checks"]["won_guard"] = bool(st and (st.is_won or st.slug == "won"))
    finally:
        db.close()

    # installation
    if secret and purchase_id:
        payload_inst = {
            "eventId": f"qa-confirm-inst-{uuid.uuid4().hex}",
            "event": "standalone.installation.active",
            "type": "standalone.installation.active",
            "crmLeadId": f"business:{bid}",
            "externalCrmRef": f"business:{bid}",
            "purchaseId": purchase_id,
            "installationId": f"inst_qa_confirm_{bid}",
            "status": "ACTIVE",
        }
        rr = c.post(
            "/api/webhooks/booking-engine",
            headers={"Authorization": f"Bearer {secret}"},
            json=payload_inst,
        )
        dump("live-webhook-install.json", {"status_code": rr.status_code, "body": j(rr)})
        out["checks"]["webhook_install"] = rr.status_code == 200

    db = SessionLocal()
    try:
        b = db.get(Business, bid)
        d = (
            db.query(BeDelivery)
            .filter(BeDelivery.business_id == bid)
            .order_by(BeDelivery.id.desc())
            .first()
        )
        out["final"] = {
            "be_installation_id": getattr(b, "be_installation_id", None),
            "delivery_status": d.status if d else None,
            "delivery_install": d.installation_id if d else None,
            "pay_url": (d.pay_url or "")[:100] if d else None,
        }
        out["checks"]["install_stored"] = (getattr(b, "be_installation_id", "") or "") == f"inst_qa_confirm_{bid}"
    finally:
        db.close()

    # Login + sales surface
    r = c.get("/api/sales/pipeline")
    out["checks"]["pipeline"] = r.status_code == 200
    r = c.get("/api/sales/be/connection")
    out["checks"]["connection_masked"] = r.status_code == 200 and "secret" not in json.dumps(j(r)).lower().replace(
        "secret_configured", ""
    ).replace("secret_hint", "")

    dump("live-confirm-summary.json", out)
    print(json.dumps({k: out["checks"].get(k) for k in out["checks"]}, indent=2))
    print("business_id", bid, "purchase_id", purchase_id)
    failed = [k for k, v in out["checks"].items() if not v]
    print("FAILED", failed or "none")
    return 0 if not failed else 3


if __name__ == "__main__":
    sys.exit(main())
