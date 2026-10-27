"""CRM connect-first: pairing codes, encrypted BE settings, connection status."""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

from fastapi import HTTPException

from app.services.secrets import resolve_secret, store_setting_value

PAIRING_TTL_MIN = 15


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def crm_lead_id_for(business_id: int) -> str:
    return f"business:{int(business_id)}"


def _running_in_docker() -> bool:
    if (os.environ.get("RUNNING_IN_DOCKER") or "").strip() in ("1", "true", "yes"):
        return True
    try:
        return Path("/.dockerenv").exists()
    except Exception:
        return False


def rewrite_be_base_url_for_runtime(url: str) -> str:
    """Map BE URL to the current runtime network.

    - In Docker: 127.0.0.1/localhost → host.docker.internal (host-published BE).
    - On host: host.docker.internal → 127.0.0.1 (URL saved while Sales OS was in Docker).
    """
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return raw
    try:
        parsed = urlparse(raw)
    except Exception:
        return raw
    host = (parsed.hostname or "").lower()
    port = parsed.port
    scheme = parsed.scheme or "http"
    in_docker = _running_in_docker()
    if in_docker and host in ("127.0.0.1", "localhost", "0.0.0.0"):
        netloc = f"host.docker.internal:{port}" if port else "host.docker.internal"
        return urlunparse((scheme, netloc, parsed.path or "", "", "", "")).rstrip("/")
    if (not in_docker) and host == "host.docker.internal":
        netloc = f"127.0.0.1:{port}" if port else "127.0.0.1"
        return urlunparse((scheme, netloc, parsed.path or "", "", "", "")).rstrip("/")
    return raw


def get_be_base_url(db=None) -> str:
    return rewrite_be_base_url_for_runtime(
        resolve_secret("be.base_url", "APPOINTHIVE_BASE_URL", db=db).rstrip("/")
    )


def get_be_shared_secret(db=None) -> str:
    return resolve_secret("be.shared_secret", "APPOINTHIVE_CRM_SHARED_SECRET", db=db)


def get_connection_status(db=None) -> str:
    return (resolve_secret("be.connection_status", "", "disconnected", db=db) or "disconnected").lower()


def is_bridge_enabled(db=None) -> bool:
    raw = resolve_secret("be.bridge_enabled", "APPOINTHIVE_BRIDGE_ENABLED", "true", db=db)
    return (raw or "true").lower() in ("1", "true", "yes")


def require_be_connected(db=None) -> None:
    if get_connection_status(db=db) != "connected" or not is_bridge_enabled(db=db):
        raise HTTPException(409, detail={"error": "CRM not connected"})
    if not get_be_base_url(db=db) or not get_be_shared_secret(db=db):
        raise HTTPException(409, detail={"error": "CRM not connected"})


def be_connection_public(db=None) -> dict:
    status = get_connection_status(db=db)
    secret = get_be_shared_secret(db=db)
    base = get_be_base_url(db=db)
    pairing = resolve_secret("be.pairing_code", "", db=db)
    expires = resolve_secret("be.pairing_expires_at", "", db=db)
    pairing_live = False
    if pairing and expires:
        try:
            pairing_live = datetime.fromisoformat(expires) > _utcnow()
        except Exception:
            pairing_live = False
    return {
        "base_url": base,
        "bridge_enabled": is_bridge_enabled(db=db),
        "connection_status": status,
        "connected": status == "connected" and bool(base) and bool(secret),
        "secret_configured": bool(secret),
        "secret_hint": ("••••" + secret[-4:]) if secret and len(secret) > 4 else ("••••" if secret else ""),
        "last_health_at": resolve_secret("be.last_health_at", "", db=db),
        "pairing_code_active": pairing_live,
        "pairing_expires_at": expires if pairing_live else "",
        "webhook_path": "/api/webhooks/booking-engine",
        "product_name": "AppointHive Sales OS",
    }


def generate_pairing_code(db) -> dict:
    code = secrets.token_urlsafe(12)
    expires = (_utcnow() + timedelta(minutes=PAIRING_TTL_MIN)).isoformat()
    store_setting_value(db, "be.pairing_code", code, secret=True)
    store_setting_value(db, "be.pairing_expires_at", expires, secret=False)
    db.commit()
    return {"code": code, "expires_at": expires, "ttl_minutes": PAIRING_TTL_MIN}


def consume_pairing_code(db, code: str) -> bool:
    expected = resolve_secret("be.pairing_code", "", db=db)
    expires = resolve_secret("be.pairing_expires_at", "", db=db)
    if not expected or not code or code.strip() != expected:
        return False
    try:
        if datetime.fromisoformat(expires) < _utcnow():
            return False
    except Exception:
        return False
    # one-time
    store_setting_value(db, "be.pairing_code", "", secret=True)
    store_setting_value(db, "be.pairing_expires_at", "", secret=False)
    return True


def issue_pairing_secret(db, *, product_base_url: str, public_base: str) -> dict:
    """Called by public product-pairing after code check."""
    secret = secrets.token_urlsafe(32)
    base = (product_base_url or "").strip().rstrip("/")
    if not base:
        raise HTTPException(422, "productBaseUrl required")
    store_setting_value(db, "be.base_url", base, secret=False)
    store_setting_value(db, "be.shared_secret", secret, secret=True)
    store_setting_value(db, "be.bridge_enabled", "true", secret=False)
    store_setting_value(db, "be.connection_status", "disconnected", secret=False)
    db.commit()
    webhook = f"{public_base.rstrip('/')}/api/webhooks/booking-engine"
    return {"secret": secret, "webhookUrl": webhook, "sharedSecret": secret}


def save_manual_connection(db, *, base_url: str, shared_secret: str = "") -> dict:
    """Save BE URL. If shared_secret is blank, keep the existing secret (URL-only update)."""
    base = (base_url or "").strip().rstrip("/")
    secret = (shared_secret or "").strip()
    if not base:
        raise HTTPException(422, "AppointHive base URL required")
    existing = get_be_shared_secret(db=db)
    if not secret:
        if not existing:
            raise HTTPException(422, "Shared secret required")
        secret = existing
    store_setting_value(db, "be.base_url", base, secret=False)
    store_setting_value(db, "be.shared_secret", secret, secret=True)
    store_setting_value(db, "be.bridge_enabled", "true", secret=False)
    store_setting_value(db, "be.connection_status", "disconnected", secret=False)
    db.commit()
    return be_connection_public(db=db)


def disconnect_be(db) -> dict:
    store_setting_value(db, "be.shared_secret", "", secret=True)
    store_setting_value(db, "be.bridge_enabled", "false", secret=False)
    store_setting_value(db, "be.connection_status", "disconnected", secret=False)
    store_setting_value(db, "be.pairing_code", "", secret=True)
    store_setting_value(db, "be.pairing_expires_at", "", secret=False)
    db.commit()
    return be_connection_public(db=db)


def set_connection_status(db, status: str, *, health_at: Optional[str] = None) -> None:
    store_setting_value(db, "be.connection_status", status, secret=False)
    if health_at is not None:
        store_setting_value(db, "be.last_health_at", health_at, secret=False)
    db.commit()


def test_be_connection(db) -> dict:
    """Probe BE health with Bearer; set connected only on success."""
    from app.services.booking_engine.client import BookingEngineClient, BeClientError

    base = get_be_base_url(db=db)
    secret = get_be_shared_secret(db=db)
    if not base or not secret:
        set_connection_status(db, "error", health_at=_utcnow().isoformat())
        return {"ok": False, "connected": False, "message": "Set AppointHive URL and shared secret first", "connection": be_connection_public(db=db)}
    client = BookingEngineClient(db=db, skip_connected_gate=True)
    try:
        health = client.health()
    except BeClientError as exc:
        set_connection_status(db, "error", health_at=_utcnow().isoformat())
        msg = str(exc)
        # Sales OS in Docker: 127.0.0.1 is the container, not the host where BE runs.
        low = msg.lower()
        hostish = "127.0.0.1" in base or "localhost" in base.lower()
        if hostish and ("connection refused" in low or "unreachable" in low or "connecterror" in low):
            msg = (
                f"{msg}. Sales OS is likely in Docker — use http://host.docker.internal:3010 "
                "(Mac/Windows) instead of http://127.0.0.1:3010, then Save and Test again. "
                "BE ‘Connect’ success only proves BE→Sales OS; this test is Sales OS→BE."
            )
        return {"ok": False, "connected": False, "message": msg, "connection": be_connection_public(db=db)}
    status = (health.get("status") or "").lower()
    enabled = health.get("crmBridgeEnabled")
    secret_ok = health.get("secretConfigured")
    ok = bool(health.get("ok")) and status == "connected" and (enabled is True or enabled is None) and (secret_ok is True or secret_ok is None)
    # BE may return connected only when both sides configured; also accept ok+secretConfigured+enabled
    if not ok and bool(health.get("ok")) and secret_ok and enabled:
        ok = True
    if ok:
        set_connection_status(db, "connected", health_at=_utcnow().isoformat())
        store_setting_value(db, "be.bridge_enabled", "true", secret=False)
        db.commit()
        return {"ok": True, "connected": True, "message": "Connected to AppointHive", "health": health, "connection": be_connection_public(db=db)}
    set_connection_status(db, "error", health_at=_utcnow().isoformat())
    return {
        "ok": False,
        "connected": False,
        "message": f"AppointHive health status={status or health} — enable bridge on AppointHive Super Admin",
        "health": health,
        "connection": be_connection_public(db=db),
    }
