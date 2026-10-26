"""Webhook signature verification helpers."""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

from fastapi import HTTPException


def verify_stripe_signature(payload: bytes, header: str, secret: str, *, tolerance_sec: int = 300) -> None:
    """Verify Stripe-Signature header (t=…,v1=…). Raises HTTPException on failure."""
    if not secret:
        return
    if not header:
        raise HTTPException(400, "Missing stripe-signature")
    parts = {}
    for item in header.split(","):
        item = item.strip()
        if "=" not in item:
            continue
        k, v = item.split("=", 1)
        parts.setdefault(k.strip(), []).append(v.strip())
    try:
        timestamp = int((parts.get("t") or [""])[0])
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "Invalid stripe-signature timestamp") from e
    if abs(int(time.time()) - timestamp) > tolerance_sec:
        raise HTTPException(400, "stripe-signature timestamp outside tolerance")
    signed = f"{timestamp}.".encode() + payload
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    candidates = parts.get("v1") or []
    if not any(hmac.compare_digest(expected, c) for c in candidates):
        raise HTTPException(400, "Invalid stripe-signature")


def verify_paypal_headers(
    *,
    webhook_id: str,
    transmission_id: Optional[str],
    transmission_sig: Optional[str],
    client_secret: str = "",
    body: bytes = b"",
) -> None:
    """
    When PAYPAL_WEBHOOK_ID is set, require transmission id + sig.
    If PAYPAL_CLIENT_SECRET is set, also require HMAC-SHA256(body) == transmission_sig
    (test-friendly; production cert verification can replace this later).
    """
    if not webhook_id:
        return
    if not transmission_id or not transmission_sig:
        raise HTTPException(400, "Missing PayPal transmission headers")
    if client_secret and body:
        digest = hmac.new(client_secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, transmission_sig.lower()):
            raise HTTPException(400, "Invalid PayPal transmission signature")
