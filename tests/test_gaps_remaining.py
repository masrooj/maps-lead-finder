"""Remaining gaps: crypto secrets, mail settings, money format, webhook live secrets."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.crypto_settings import decrypt_setting, encrypt_setting
from app.services.invoices_pdf import format_money


def test_encrypt_decrypt_roundtrip(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-sales-os-32chars")
    enc = encrypt_setting("sk_live_secret_value")
    assert enc.startswith("enc:v1:")
    assert decrypt_setting(enc) == "sk_live_secret_value"
    assert decrypt_setting("legacy-plaintext") == "legacy-plaintext"


def test_format_money_currencies():
    assert "1,234.50" in format_money(1234.5, "USD") or "1234.50" in format_money(1234.5, "USD")
    assert "EUR" in format_money(1234.5, "EUR")
    assert "A$" in format_money(10, "AUD")


def test_payment_settings_encrypt_store(auth_client):
    r = auth_client.put(
        "/api/sales/payment-settings",
        json={"key": "pay.stripe_secret_key", "value": "sk_test_gap_secret"},
    )
    assert r.status_code == 200, r.text
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import AppSetting

    with SessionLocal() as db:
        row = db.scalar(select(AppSetting).where(AppSetting.key == "pay.stripe_secret_key"))
        assert row is not None
        assert row.value.startswith("enc:v1:")
        assert "sk_test_gap_secret" not in row.value
    pub = auth_client.get("/api/sales/payment-settings")
    assert pub.status_code == 200
    item = next(s for s in pub.json()["settings"] if s["key"] == "pay.stripe_secret_key")
    assert item["configured"] is True
    assert "sk_test" not in (item.get("masked") or "")


def test_mail_settings_list(auth_client):
    r = auth_client.get("/api/sales/mail-settings")
    assert r.status_code == 200, r.text
    keys = {s["key"] for s in r.json()["settings"] if s.get("key") and s["key"] != "_status"}
    assert "mail.smtp_host" in keys
    assert "storage" in r.json()


def test_invoice_send_email_mocked(auth_client):
    from datetime import datetime, timezone
    import json

    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import BillingEntity, Business, Invoice

    with SessionLocal() as db:
        b = Business(
            place_id="qa-smtp-place",
            name="SMTP Co",
            email="client@example.com",
            city="Sydney",
            country="AU",
        )
        db.add(b)
        db.flush()
        ent = db.scalar(select(BillingEntity).where(BillingEntity.active.is_(True)).limit(1))
        if not ent:
            ent = BillingEntity(legal_name="AH", active=True)
            db.add(ent)
            db.flush()
        inv = Invoice(
            business_id=b.id,
            billing_entity_id=ent.id,
            number="INV-SMTP-1",
            status="open",
            currency="AUD",
            total=50.0,
            subtotal=50.0,
            provider="manual",
            line_items_json=json.dumps([{"name": "Fee", "qty": 1, "amount": 50}]),
            created_at=datetime.now(timezone.utc),
        )
        db.add(inv)
        db.commit()
        iid = inv.id

    auth_client.put("/api/sales/mail-settings", json={"key": "mail.smtp_host", "value": "smtp.test"})
    auth_client.put("/api/sales/mail-settings", json={"key": "mail.from_email", "value": "from@test.com"})

    with patch("app.services.mail.smtplib.SMTP") as smtp_cls:
        smtp = MagicMock()
        smtp.__enter__ = MagicMock(return_value=smtp)
        smtp.__exit__ = MagicMock(return_value=False)
        smtp_cls.return_value = smtp
        r = auth_client.post(
            f"/api/sales/invoices/{iid}/send-email",
            json={"to": "client@example.com", "subject": "Inv", "body": "Hi"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert smtp.send_message.called


def test_payment_settings_bulk_and_validate_shape(auth_client):
    r = auth_client.put(
        "/api/sales/payment-settings/bulk",
        json={
            "settings": {
                "pay.stripe_mode": "test",
                "pay.public_base_url": "http://localhost:8000",
            }
        },
    )
    assert r.status_code == 200, r.text
    assert "pay.stripe_mode" in r.json().get("saved", [])
    v = auth_client.post("/api/sales/payment-settings/validate", json={"provider": "stripe"})
    assert v.status_code == 200, v.text
    assert "valid" in v.json()
    assert v.json()["valid"] is False  # no secret key yet


def test_stripe_webhook_uses_settings_secret(auth_client):
    """Webhook verifier reads live secret (Settings), not frozen env import."""
    auth_client.put(
        "/api/sales/payment-settings",
        json={"key": "pay.stripe_webhook_secret", "value": "whsec_gap_test"},
    )
    # Bad signature → 401/400 from verifier when secret is set
    r = auth_client.post(
        "/api/webhooks/stripe",
        data=b'{"id":"evt_x","type":"checkout.session.completed","data":{"object":{}}}',
        headers={"content-type": "application/json", "stripe-signature": "t=1,v1=bad"},
    )
    assert r.status_code in (400, 401, 403)


def test_storage_blob_preferred_over_s3(monkeypatch):
    from app.services import storage

    monkeypatch.delenv("STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_test_token")
    monkeypatch.setenv("S3_BUCKET", "should-not-win")
    assert storage.backend() == "blob"


def test_uploads_root_uses_packaged_dir(monkeypatch):
    """Packaged uploads/ + demos/ avoid mkdir on read-only Vercel FS."""
    from pathlib import Path

    from app.services import storage

    monkeypatch.delenv("UPLOADS_DIR", raising=False)
    root = storage.uploads_root()
    assert root == Path("uploads")
    assert root.is_dir()
    assert (root / "demos").is_dir()


def test_storage_blob_put_mocked(monkeypatch):
    from app.services import storage

    monkeypatch.setenv("STORAGE_BACKEND", "blob")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_test_token")

    class FakeResp:
        ok = True
        content = b'{"url":"https://blob.vercel-storage.com/demos/x.png"}'
        text = content.decode()

        def json(self):
            return {"url": "https://blob.vercel-storage.com/demos/x.png"}

    with patch("app.services.storage.requests.put", return_value=FakeResp()) as put:
        out = storage.save_bytes("demos/x.png", b"png", content_type="image/png")
    assert out["backend"] == "blob"
    assert out["url"].startswith("https://blob.vercel-storage.com/")
    assert put.called
    assert "Authorization" in put.call_args.kwargs["headers"]
