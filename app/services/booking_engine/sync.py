"""AppointHive sync / heartbeat markers (app_settings backed)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.services.booking_engine.events import event_counts
from app.services.secrets import resolve_secret, store_setting_value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def mark_sync_ok(db, *, note: str = "", commit: bool = False) -> None:
    now = _utcnow().isoformat()
    store_setting_value(db, "be.last_sync_ok_at", now, secret=False)
    if note:
        store_setting_value(db, "be.last_sync_ok_note", note[:500], secret=False)
    if commit:
        db.commit()


def mark_sync_fail(db, *, note: str = "", commit: bool = False) -> None:
    now = _utcnow().isoformat()
    store_setting_value(db, "be.last_sync_fail_at", now, secret=False)
    if note:
        store_setting_value(db, "be.last_sync_fail_note", note[:500], secret=False)
    if commit:
        db.commit()


def mark_heartbeat(db, *, ok: bool, note: str = "", commit: bool = True) -> None:
    now = _utcnow().isoformat()
    store_setting_value(db, "be.last_heartbeat_at", now, secret=False)
    store_setting_value(db, "be.last_heartbeat_ok", "true" if ok else "false", secret=False)
    if note:
        store_setting_value(db, "be.last_heartbeat_note", note[:500], secret=False)
    if ok:
        store_setting_value(db, "be.last_sync_ok_at", now, secret=False)
    else:
        store_setting_value(db, "be.last_sync_fail_at", now, secret=False)
        if note:
            store_setting_value(db, "be.last_sync_fail_note", note[:500], secret=False)
    if commit:
        db.commit()


def get_pairing_id(db=None) -> str:
    return resolve_secret("be.pairing_id", "", db=db) or ""


def ensure_pairing_id(db, *, commit: bool = False) -> str:
    import secrets

    existing = get_pairing_id(db=db)
    if existing:
        return existing
    pid = "pair_" + secrets.token_hex(8)
    store_setting_value(db, "be.pairing_id", pid, secret=False)
    if commit:
        db.commit()
    return pid


def secret_rotation_status(db=None) -> str:
    """configured | missing | rotated_unknown — Sales OS does not store prior secrets."""
    secret = resolve_secret("be.shared_secret", "APPOINTHIVE_CRM_SHARED_SECRET", db=db)
    if not secret:
        return "missing"
    rotated_at = resolve_secret("be.secret_rotated_at", "", db=db)
    if rotated_at:
        return f"configured · last set {rotated_at}"
    return "configured"


def sync_public_fields(db=None) -> dict:
    counts = event_counts(db) if db is not None else {
        "pending": 0, "processed": 0, "failed": 0, "duplicate": 0, "total": 0
    }
    return {
        "pairing_id": get_pairing_id(db=db),
        "last_sync_ok_at": resolve_secret("be.last_sync_ok_at", "", db=db) or "",
        "last_sync_fail_at": resolve_secret("be.last_sync_fail_at", "", db=db) or "",
        "last_sync_fail_note": resolve_secret("be.last_sync_fail_note", "", db=db) or "",
        "last_heartbeat_at": resolve_secret("be.last_heartbeat_at", "", db=db) or "",
        "last_heartbeat_ok": (resolve_secret("be.last_heartbeat_ok", "", db=db) or "") == "true",
        "pending_events": counts.get("pending", 0),
        "failed_events": counts.get("failed", 0),
        "processed_events": counts.get("processed", 0),
        "permissions": [
            "catalog.read",
            "revisions.read",
            "purchase.create",
            "request.create",
            "upgrade.create",
            "webhook.receive",
        ],
        "secret_rotation_status": secret_rotation_status(db=db),
    }


def run_heartbeat(db) -> dict:
    """Owner/cron: probe BE health, record heartbeat, and auto-retry failed inbox."""
    from app.services.booking_engine.connect import test_be_connection

    result = test_be_connection(db)
    ok = bool(result.get("ok") and result.get("connected"))
    mark_heartbeat(db, ok=ok, note=str(result.get("message") or ""))
    from app.services.booking_engine.connect import be_connection_public
    from app.services.booking_engine.events import retry_failed_events_batch

    inbox_retry = {"retried": 0, "succeeded": 0, "failed": 0}
    try:
        inbox_retry = retry_failed_events_batch(db, limit=10, max_attempts=8)
    except Exception as exc:
        inbox_retry = {"retried": 0, "succeeded": 0, "failed": 0, "error": str(exc)[:200]}

    return {
        "ok": ok,
        "message": result.get("message"),
        "connection": be_connection_public(db=db),
        "inbox_retry": inbox_retry,
    }
