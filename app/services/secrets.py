"""Resolve payment / ops secrets: AppSetting overrides env (never log values)."""
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import select

from app.db import SessionLocal
from app.models import AppSetting
from app.services.crypto_settings import decrypt_setting, encrypt_setting

# Public keys that may be shown (non-secret). Secrets are masked in API responses.
PAY_SETTING_KEYS = [
    ("pay.stripe_secret_key", "STRIPE_SECRET_KEY", True),
    ("pay.stripe_publishable_key", "STRIPE_PUBLISHABLE_KEY", False),
    ("pay.stripe_webhook_secret", "STRIPE_WEBHOOK_SECRET", True),
    ("pay.stripe_mode", "STRIPE_MODE", False),
    ("pay.paypal_client_id", "PAYPAL_CLIENT_ID", False),
    ("pay.paypal_client_secret", "PAYPAL_CLIENT_SECRET", True),
    ("pay.paypal_webhook_id", "PAYPAL_WEBHOOK_ID", False),
    ("pay.paypal_mode", "PAYPAL_MODE", False),
    ("pay.payoneer_payee_email", "PAYONEER_PAYEE_EMAIL", False),
    ("pay.payoneer_api_key", "PAYONEER_API_KEY", True),
    ("pay.payoneer_webhook_secret", "PAYONEER_WEBHOOK_SECRET", True),
    ("pay.skrill_pay_to_email", "SKRILL_PAY_TO_EMAIL", False),
    ("pay.skrill_merchant_id", "SKRILL_MERCHANT_ID", False),
    ("pay.skrill_secret", "SKRILL_SECRET", True),
    ("pay.skrill_webhook_secret", "SKRILL_WEBHOOK_SECRET", True),
    ("pay.public_base_url", "PUBLIC_BASE_URL", False),
]

SECRET_SETTING_KEYS = {k for k, _, secret in PAY_SETTING_KEYS if secret} | {
    "mail.smtp_password",
}


def _decrypt_raw(raw: str) -> str:
    try:
        return decrypt_setting(raw)
    except Exception:
        return raw


def _db_value(key: str, db=None) -> Optional[str]:
    """Read AppSetting. Prefer caller session to avoid pool deadlock (pool_size=1)."""
    try:
        if db is not None:
            row = db.scalar(select(AppSetting).where(AppSetting.key == key))
            if row and (row.value or "").strip():
                return _decrypt_raw(row.value.strip())
            return None
        with SessionLocal() as local:
            row = local.scalar(select(AppSetting).where(AppSetting.key == key))
            if row and (row.value or "").strip():
                return _decrypt_raw(row.value.strip())
    except Exception:
        return None
    return None


def resolve_secret(setting_key: str, env_name: str, default: str = "", db=None) -> str:
    db_val = _db_value(setting_key, db=db)
    if db_val:
        return db_val
    return (os.getenv(env_name, "") or default).strip()


def store_setting_value(db, key: str, value: str, *, secret: bool = False) -> None:
    """Persist AppSetting; encrypt when secret=True."""
    val = (value or "").strip()
    if secret and val:
        val = encrypt_setting(val)
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if not row:
        row = AppSetting(key=key, value=val)
        db.add(row)
    else:
        row.value = val


def mask_secret(value: str, *, secret: bool = True) -> dict:
    raw = (value or "").strip()
    if not raw:
        return {"configured": False, "masked": "", "hint": ""}
    if not secret:
        return {"configured": True, "masked": raw, "hint": raw}
    if len(raw) <= 4:
        return {"configured": True, "masked": "••••", "hint": "set · encrypted"}
    return {"configured": True, "masked": "••••" + raw[-4:], "hint": "set · encrypted · ends " + raw[-4:]}


def payment_settings_public(db=None) -> list[dict]:
    out = []
    for setting_key, env_name, is_secret in PAY_SETTING_KEYS:
        val = resolve_secret(setting_key, env_name, db=db)
        meta = mask_secret(val, secret=is_secret)
        out.append(
            {
                "key": setting_key,
                "env": env_name,
                "secret": is_secret,
                "label": env_name.replace("_", " ").title(),
                **meta,
            }
        )
    return out


def stripe_secret_key(db=None) -> str:
    return resolve_secret("pay.stripe_secret_key", "STRIPE_SECRET_KEY", db=db)


def stripe_webhook_secret(db=None) -> str:
    return resolve_secret("pay.stripe_webhook_secret", "STRIPE_WEBHOOK_SECRET", db=db)


def stripe_mode(db=None) -> str:
    return (resolve_secret("pay.stripe_mode", "STRIPE_MODE", "test", db=db) or "test").lower()


def paypal_client_id(db=None) -> str:
    return resolve_secret("pay.paypal_client_id", "PAYPAL_CLIENT_ID", db=db)


def paypal_client_secret(db=None) -> str:
    return resolve_secret("pay.paypal_client_secret", "PAYPAL_CLIENT_SECRET", db=db)


def paypal_webhook_id(db=None) -> str:
    return resolve_secret("pay.paypal_webhook_id", "PAYPAL_WEBHOOK_ID", db=db)


def paypal_mode(db=None) -> str:
    return (resolve_secret("pay.paypal_mode", "PAYPAL_MODE", "sandbox", db=db) or "sandbox").lower()


def payoneer_payee_email(db=None) -> str:
    return resolve_secret("pay.payoneer_payee_email", "PAYONEER_PAYEE_EMAIL", db=db)


def payoneer_api_key(db=None) -> str:
    return resolve_secret("pay.payoneer_api_key", "PAYONEER_API_KEY", db=db)


def payoneer_webhook_secret(db=None) -> str:
    return resolve_secret("pay.payoneer_webhook_secret", "PAYONEER_WEBHOOK_SECRET", db=db)


def skrill_pay_to_email(db=None) -> str:
    return resolve_secret("pay.skrill_pay_to_email", "SKRILL_PAY_TO_EMAIL", db=db)


def skrill_merchant_id(db=None) -> str:
    return resolve_secret("pay.skrill_merchant_id", "SKRILL_MERCHANT_ID", db=db)


def skrill_secret(db=None) -> str:
    return resolve_secret("pay.skrill_secret", "SKRILL_SECRET", db=db)


def skrill_webhook_secret(db=None) -> str:
    return resolve_secret("pay.skrill_webhook_secret", "SKRILL_WEBHOOK_SECRET", db=db) or skrill_secret(db=db)


def public_base_url(db=None) -> str:
    return (
        resolve_secret("pay.public_base_url", "PUBLIC_BASE_URL", "http://localhost:8000", db=db)
        or "http://localhost:8000"
    ).rstrip("/")
