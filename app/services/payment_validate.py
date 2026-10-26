"""Validate payment / mail credentials (Owner configure screen)."""
from __future__ import annotations

import re
from typing import Any

import requests

from app.services import secrets as sec
from app.services.mail import smtp_configured


def _ok(message: str, **extra: Any) -> dict:
    return {"ok": True, "valid": True, "message": message, **extra}


def _fail(message: str, **extra: Any) -> dict:
    return {"ok": True, "valid": False, "message": message, **extra}


def validate_provider(provider: str) -> dict:
    p = (provider or "").strip().lower()
    if p == "stripe":
        return _validate_stripe()
    if p == "paypal":
        return _validate_paypal()
    if p == "payoneer":
        return _validate_payoneer()
    if p == "skrill":
        return _validate_skrill()
    if p == "mail" or p == "smtp":
        return _validate_mail()
    if p == "manual":
        return _ok("Manual / bank needs no API keys — always available.")
    return _fail(f"Unknown provider: {provider}")


def _validate_stripe() -> dict:
    key = sec.stripe_secret_key()
    if not key:
        return _fail("Stripe secret key is empty. Paste sk_test_… or sk_live_… and Save.")
    if not key.startswith(("sk_test_", "sk_live_")):
        return _fail("Stripe secret key should start with sk_test_ or sk_live_.")
    try:
        r = requests.get(
            "https://api.stripe.com/v1/account",
            auth=(key, ""),
            timeout=12,
        )
    except requests.RequestException as exc:
        return _fail(f"Could not reach Stripe: {exc}")
    if r.status_code == 200:
        data = r.json() if r.content else {}
        mode = "test" if key.startswith("sk_test_") else "live"
        return _ok(
            f"Stripe connected ({mode}). Account {data.get('id') or 'ok'}.",
            mode=mode,
            account_id=data.get("id"),
        )
    try:
        err = (r.json().get("error") or {}).get("message") or r.text[:200]
    except Exception:
        err = r.text[:200]
    return _fail(f"Stripe rejected key (HTTP {r.status_code}): {err}")


def _validate_paypal() -> dict:
    cid = sec.paypal_client_id()
    secret = sec.paypal_client_secret()
    if not cid or not secret:
        return _fail("PayPal needs Client ID and Client Secret. Save both, then Validate.")
    mode = sec.paypal_mode()
    base = "https://api-m.sandbox.paypal.com" if mode == "sandbox" else "https://api-m.paypal.com"
    try:
        r = requests.post(
            f"{base}/v1/oauth2/token",
            auth=(cid, secret),
            data={"grant_type": "client_credentials"},
            timeout=12,
        )
    except requests.RequestException as exc:
        return _fail(f"Could not reach PayPal: {exc}")
    if r.status_code == 200 and (r.json() or {}).get("access_token"):
        return _ok(f"PayPal OAuth OK ({mode}).", mode=mode)
    return _fail(f"PayPal auth failed (HTTP {r.status_code}): {r.text[:200]}")


def _email_ok(val: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", (val or "").strip()))


def _validate_payoneer() -> dict:
    email = sec.payoneer_payee_email()
    if not email:
        return _fail("Set Payoneer payee email (where you receive payments).")
    if not _email_ok(email):
        return _fail("Payoneer payee email looks invalid.")
    wh = bool(sec.payoneer_webhook_secret())
    return _ok(
        f"Payoneer payee set ({email}). "
        + ("Webhook secret set — auto-paid enabled." if wh else "Optional: add webhook secret for auto-paid."),
        payee=email,
        webhook=wh,
    )


def _validate_skrill() -> dict:
    email = sec.skrill_pay_to_email()
    mid = sec.skrill_merchant_id()
    if not email and not mid:
        return _fail("Set Skrill pay-to email or merchant id.")
    if email and not _email_ok(email):
        return _fail("Skrill pay-to email looks invalid.")
    wh = bool(sec.skrill_webhook_secret())
    return _ok(
        "Skrill pay target set. "
        + ("Webhook secret set — auto-paid enabled." if wh else "Optional: add webhook secret for auto-paid."),
        webhook=wh,
    )


def _validate_mail() -> dict:
    if not smtp_configured():
        return _fail("Set SMTP host and From email, then Save.")
    host = sec.resolve_secret("mail.smtp_host", "SMTP_HOST")
    port_s = sec.resolve_secret("mail.smtp_port", "SMTP_PORT", "587") or "587"
    try:
        port = int(port_s)
    except ValueError:
        return _fail("SMTP port must be a number (587 or 465).")
    import smtplib
    import ssl

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=12) as smtp:
                smtp.ehlo()
        else:
            with smtplib.SMTP(host, port, timeout=12) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
                user = sec.resolve_secret("mail.smtp_user", "SMTP_USER")
                password = sec.resolve_secret("mail.smtp_password", "SMTP_PASSWORD")
                if user:
                    smtp.login(user, password)
        return _ok(f"SMTP reachable at {host}:{port}.")
    except Exception as exc:
        return _fail(f"SMTP connection failed: {exc}")
