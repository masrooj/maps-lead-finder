#!/usr/bin/env python3
"""QA live evidence for bridge go-live cycle. Writes JSON artifacts under EVIDENCE_DIR."""
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
EV = Path(os.environ.get("EVIDENCE_DIR", _ROOT / "docs/qa/evidence/release-2026-10-26-bridge"))
EV.mkdir(parents=True, exist_ok=True)
BIZ_ID = int(os.environ.get("E2E_BIZ_ID", "553"))


def dump(name: str, obj) -> None:
    (EV / name).write_text(json.dumps(obj, indent=2, default=str)[:50000], encoding="utf-8")


def j(r):
    try:
        return r.json()
    except Exception:
        return {"_status": r.status_code, "_raw": (r.text or "")[:2000]}


def main() -> int:
    user = os.environ.get("LOGIN_USERNAME") or "hello@appointhive.com"
    password = os.environ.get("LOGIN_PASSWORD") or ""
    if not password:
        print("LOGIN_PASSWORD missing")
        return 1

    c = httpx.Client(base_url=BASE, timeout=90.0, follow_redirects=True)
    results = {}

    # Gate when we force-check connection status first
    r = c.post("/api/login", json={"username": user, "password": password})
    results["login"] = r.status_code
    if r.status_code != 200:
        dump("live-connect.json", {"error": "login failed", "body": j(r)})
        return 1

    r = c.post("/api/sales/be/connection/test")
    conn_test = j(r)
    dump("live-connect.json", {"status_code": r.status_code, **conn_test})
    results["connect"] = bool(conn_test.get("connected"))

    r = c.get("/api/sales/be/revisions")
    revs_body = j(r)
    revs = revs_body.get("revisions") or []
    dump(
        "live-revisions.json",
        {
            "status_code": r.status_code,
            "count": len(revs),
            "sample": revs[:3],
        },
    )
    results["revisions"] = r.status_code == 200 and len(revs) > 0
    if not revs:
        dump("live-summary.json", results)
        return 2
    revision_id = revs[0].get("productRevisionId") or revs[0].get("id")

    # Soft (omit install id path is server-side)
    r = c.post(
        "/api/sales/be/send-request",
        json={"business_id": BIZ_ID, "message": "QA cycle soft handover evidence"},
    )
    soft = j(r)
    dump("live-soft.json", {"status_code": r.status_code, "body": soft})
    results["soft"] = r.status_code == 200 and not soft.get("detail")
    be_soft = (soft.get("be") or {}) if isinstance(soft, dict) else {}
    results["soft_has_prospect"] = "crmProspect" in json.dumps(soft)

    # Purchase
    r = c.post(
        "/api/sales/be/send-to-delivery",
        json={
            "business_id": BIZ_ID,
            "product_revision_id": revision_id,
            "create_checkout": True,
        },
    )
    purch = j(r)
    dump("live-purchase.json", {"status_code": r.status_code, "body": purch})
    results["purchase"] = r.status_code == 200 and not purch.get("detail")
    delivery = (purch.get("delivery") or {}) if isinstance(purch, dict) else {}
    be_p = (purch.get("be") or {}) if isinstance(purch, dict) else {}
    purchase_id = delivery.get("purchase_id") or be_p.get("purchaseId")
    pay_url = purch.get("pay_url") or delivery.get("pay_url") or be_p.get("checkoutUrl")
    results["pay_url"] = bool(pay_url)
    results["purchase_id"] = purchase_id
    results["reused"] = be_p.get("reused")

    # Webhooks: paid + installation (use stored secret)
    from app.db import SessionLocal
    from app.services.booking_engine.connect import get_be_shared_secret
    from app.models import Business, BeDelivery

    db = SessionLocal()
    try:
        secret = get_be_shared_secret(db=db)
    finally:
        db.close()

    crm = f"business:{BIZ_ID}"
    wh = []
    if secret and purchase_id:
        pid = purchase_id
        for payload in (
            {
                "type": "standalone.payment.paid",
                "event": "standalone.payment.paid",
                "crmLeadId": crm,
                "externalCrmRef": crm,
                "purchaseId": pid,
                "status": "PAID",
                "amountCents": revs[0].get("priceCents") or 0,
            },
            {
                "type": "standalone.installation.active",
                "event": "standalone.installation.active",
                "crmLeadId": crm,
                "externalCrmRef": crm,
                "purchaseId": pid,
                "installationId": "inst_qa_bridge_001",
                "status": "ACTIVE",
            },
        ):
            rr = c.post(
                "/api/webhooks/booking-engine",
                headers={"Authorization": f"Bearer {secret}"},
                json=payload,
            )
            wh.append({"event": payload["type"], "status_code": rr.status_code, "body": j(rr)})
    dump("live-webhooks.json", wh)
    results["webhook_paid"] = any(x["event"].endswith("paid") and x["status_code"] == 200 for x in wh)
    results["webhook_install"] = any(
        x["event"].endswith("installation.active") and x["status_code"] == 200 for x in wh
    )

    db = SessionLocal()
    try:
        b = db.get(Business, BIZ_ID)
        drow = (
            db.query(BeDelivery)
            .filter(BeDelivery.business_id == BIZ_ID)
            .order_by(BeDelivery.id.desc())
            .first()
        )
        stage = None
        if b and b.pipeline_stage_id:
            from app.models import PipelineStage

            st = db.get(PipelineStage, b.pipeline_stage_id)
            stage = {"id": st.id, "slug": st.slug, "name": st.name, "is_won": st.is_won} if st else None
        snap = {
            "business_id": BIZ_ID,
            "be_installation_id": getattr(b, "be_installation_id", None) if b else None,
            "stage": stage,
            "delivery": {
                "id": drow.id if drow else None,
                "status": drow.status if drow else None,
                "purchase_id": drow.purchase_id if drow else None,
                "installation_id": drow.installation_id if drow else None,
                "pay_url": (drow.pay_url or "")[:120] if drow else None,
            },
        }
        dump("live-db-after.json", snap)
        results["won"] = bool(stage and (stage.get("is_won") or stage.get("slug") == "won"))
        results["install_stored"] = bool(
            (getattr(b, "be_installation_id", "") or "") == "inst_qa_bridge_001"
            or (drow and drow.installation_id == "inst_qa_bridge_001")
        )
    finally:
        db.close()

    # Fail-closed sample: only document current connected state (gate covered in pytest)
    dump("live-gate.txt", "Fail-closed covered by pytest test_connect_gate_blocks_catalog + disconnect.\n")
    dump("live-summary.json", results)
    print(json.dumps(results, indent=2))
    ok = all(
        results.get(k)
        for k in (
            "connect",
            "revisions",
            "soft",
            "purchase",
            "pay_url",
            "webhook_paid",
            "webhook_install",
            "won",
            "install_stored",
        )
    )
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
