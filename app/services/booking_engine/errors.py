"""Map AppointHive / bridge failures to operator-facing messages."""
from __future__ import annotations

from typing import Any, Optional

from app.services.booking_engine.client import BeClientError

_FIELD_LABELS = {
    "buyerEmail": "buyer email",
    "contactEmail": "contact email",
    "productRevisionId": "package revision",
    "provider": "payment provider",
    "crmLeadId": "CRM lead id",
    "inboundRequestId": "inbound request id",
    "contactPhone": "phone",
    "contactName": "contact name",
    "buyerName": "buyer name",
    "crmProspect": "prospect details",
    "amountCents": "amount",
    "purchaseId": "purchase id",
    "toRevisionId": "target revision",
    "installationId": "installation id",
}


def _field_label(key: str) -> str:
    return _FIELD_LABELS.get(key, key.replace("_", " "))


def _detail_fields(details: Any) -> list[str]:
    if not isinstance(details, dict):
        return []
    out: list[str] = []
    for key, vals in details.items():
        label = _field_label(str(key))
        if isinstance(vals, list) and vals:
            joined = ", ".join(str(v) for v in vals if v is not None)
            out.append(f"{label} ({joined})" if joined else label)
        elif vals is not None and vals != "":
            out.append(f"{label} ({vals})")
        else:
            out.append(label)
    return out


def friendly_be_message(
    *,
    raw: str = "",
    status: int = 0,
    body: Any = None,
) -> str:
    """Return a short, actionable message for Sales OS toasts."""
    body = body if isinstance(body, dict) else {}
    details = body.get("details")
    fields = _detail_fields(details)
    text = (raw or str(body.get("error") or body.get("detail") or "")).strip()
    lower = text.lower()
    field_keys = {str(k) for k in (details or {})} if isinstance(details, dict) else set()

    if status in (401, 403) or "unauthorized" in lower:
        return "AppointHive bridge is not authorized. Open AppointHive → Connect and pair again."
    if "crm bridge disabled" in lower or (status == 403 and "disabled" in lower):
        return "AppointHive CRM bridge is disabled. Enable it in AppointHive Super Admin, then retry."
    if "crm not connected" in lower or "not connected" in lower:
        return "AppointHive is not connected. Open AppointHive → Connect, then retry."
    if "unreachable" in lower or status in (502, 503, 504):
        return "AppointHive is unreachable. Check that AppointHive is running and the base URL is correct."

    if "no payment provider configured" in lower:
        return "No card payment provider is configured on AppointHive. Add Stripe or PayPal there, then retry."

    if "validation failed" in lower or "invalid input" in lower:
        if "buyerEmail" in field_keys or "contactEmail" in field_keys:
            return (
                "This prospect needs one valid buyer email before Send to delivery. "
                "Open the Contact tab, set a real email (not comma-separated or placeholder), then retry."
            )
        if "productRevisionId" in field_keys or "toRevisionId" in field_keys:
            return "Choose a finalized AppointHive package revision, then retry Send to delivery."
        if "provider" in field_keys:
            return "Payment provider is invalid. Use Stripe or PayPal from AppointHive, or leave provider blank."
        if fields:
            return f"AppointHive rejected the handover — check {', '.join(fields)}."
        return (
            "AppointHive rejected the handover (validation). "
            "Check buyer email and package revision, then retry."
        )

    if "immutable" in lower:
        return (
            "That AppointHive package already has a purchase. "
            "Pick another finalized revision, or open the existing delivery."
        )
    if "purchase not found" in lower:
        return "AppointHive purchase was not found. Refresh Delivery / pay and try again."
    if "not found" in lower and status == 404:
        return "AppointHive could not find that record. Refresh and try again."
    if "internal server error" in lower or status >= 500:
        return "AppointHive had an internal error. Try again in a moment; if it persists, check AppointHive logs."

    # Prefer a known BE message when it is already readable.
    if text and text.lower() not in {"validation failed", "unauthorized", "forbidden", "error"}:
        if fields and "validation" not in lower:
            return f"{text} — check {', '.join(fields)}."
        return text

    if fields:
        return f"AppointHive rejected the handover — check {', '.join(fields)}."
    if status:
        return f"AppointHive request failed (HTTP {status})."
    return "AppointHive request failed. Try again."


def be_http_detail(exc: BeClientError) -> dict:
    """FastAPI `detail=` payload with a friendly `error` string for toasts."""
    body = exc.body if isinstance(exc.body, dict) else {}
    msg = friendly_be_message(raw=str(exc), status=exc.status or 0, body=body)
    return {"error": msg, "be": body or exc.body, "code": "APPOINTHIVE_BRIDGE"}
