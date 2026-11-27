"""Optional local Maps Reserve/Book click-verify (Playwright).

Not used on Vercel. Live booking detect uses Scan booking + Places reservable
via GOOGLE_MAPS_API_KEY — no separate enable flag.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Callable, Optional

VerifyRunner = Callable[[str], dict[str, Any]]

VALID_STATUSES = frozenset({"has_booking", "no_booking", "uncertain", "error"})


def maps_verify_enabled() -> bool:
    """True only when Playwright click-verify can run (local/Docker), never on Vercel."""
    if os.getenv("VERCEL", "").strip() or os.getenv("AWS_LAMBDA_FUNCTION_NAME", "").strip():
        return False
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


def normalize_verify_result(raw: Optional[dict]) -> dict[str, Any]:
    raw = raw or {}
    status = str(raw.get("status") or "error").strip().lower()
    if status not in VALID_STATUSES:
        status = "error"
    return {
        "status": status,
        "vendor_hint": (raw.get("vendor_hint") or "") or None,
        "evidence": str(raw.get("evidence") or "")[:500],
        "checked_at": raw.get("checked_at")
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def verify_maps_booking(
    google_maps_url: str,
    *,
    runner: Optional[VerifyRunner] = None,
    park_only_on_confirmed: bool = True,
) -> dict[str, Any]:
    """Run Maps click-verify. Returns normalized result + park/to_contact hints."""
    url = (google_maps_url or "").strip()
    if not url:
        result = normalize_verify_result(
            {"status": "uncertain", "evidence": "missing google_maps_url"}
        )
    else:
        if runner is None:
            from app.workers.maps_browser import run_maps_booking_check

            runner = run_maps_booking_check
        try:
            result = normalize_verify_result(runner(url))
        except Exception as exc:
            result = normalize_verify_result(
                {"status": "error", "evidence": f"runner_exception: {exc}"}
            )

    status = result["status"]
    should_park = status == "has_booking"
    should_to_contact = status == "no_booking" or (
        not park_only_on_confirmed and status in ("uncertain", "error")
    )
    return {
        **result,
        "should_park": should_park,
        "should_to_contact": should_to_contact and not should_park,
        "park_only_on_confirmed": park_only_on_confirmed,
    }


def _log(db, business_id: int, note: str) -> None:
    from app.models import ActivityLog

    db.add(
        ActivityLog(
            business_id=business_id,
            kind="maps_booking_verify",
            channel="",
            note=(note or "")[:2000],
        )
    )


def apply_maps_verify_to_business(
    db,
    b,
    verify: dict[str, Any],
    *,
    actor_id: Optional[int] = None,
) -> str:
    """Apply verify result onto a Business. Returns action: parked|to_contact|skipped."""
    from app.services.booking_detect import park_has_booking
    from app.services.automation import mark_for_contact

    if verify.get("should_park"):
        label = park_has_booking(b, verify.get("vendor_hint") or "Has booking")
        _log(db, b.id, f"maps_verify: {label} ({verify.get('evidence') or ''})")
        return "parked"

    if verify.get("should_to_contact"):
        mark_for_contact(db, b, actor_id=actor_id, marked=True)
        _log(db, b.id, "maps_verify: no booking → to contact")
        return "to_contact"

    _log(
        db,
        b.id,
        f"maps_verify: {verify.get('status')} — left for manual "
        f"({verify.get('evidence') or ''})",
    )
    return "skipped"
