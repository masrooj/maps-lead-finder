"""Invoice + payment orchestration across providers."""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import requests
from fastapi import HTTPException
from sqlalchemy import select

from app.config import (
    PAYONEER_API_KEY,
    PAYONEER_PAYEE_EMAIL,
    PAYONEER_WEBHOOK_SECRET,
    PAYPAL_CLIENT_ID,
    PAYPAL_CLIENT_SECRET,
    PAYPAL_MODE,
    PUBLIC_BASE_URL,
    SKRILL_MERCHANT_ID,
    SKRILL_PAY_TO_EMAIL,
    SKRILL_WEBHOOK_SECRET,
    STRIPE_MODE,
    STRIPE_SECRET_KEY,
)

log = logging.getLogger(__name__)
from app.models import (
    Activity,
    AppSetting,
    BillingEntity,
    Business,
    Invoice,
    Payment,
    Proposal,
    Task,
    WebhookEvent,
)
from app.services.pipeline import get_stage, move_stage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _money_to_minor_units(amount: float, currency: str) -> int:
    """Stripe expects the smallest currency unit (cents for AUD/USD)."""
    zero_decimal = {"jpy", "krw", "vnd"}
    cur = (currency or "usd").lower()
    if cur in zero_decimal:
        return int(round(float(amount)))
    return int(round(float(amount) * 100))


def create_stripe_checkout_session(
    *,
    amount: float,
    currency: str,
    description: str,
    reference: str,
    customer_email: str = "",
) -> Tuple[str, str, str]:
    """
    Create a Stripe Checkout Session via REST.
    Returns (session_id, hosted_url, instructions).
    Raises HTTPException on hard config errors; returns placeholder on API failure.
    """
    from app.services import secrets as sec

    stripe_key = sec.stripe_secret_key()
    base_url = sec.public_base_url() or PUBLIC_BASE_URL
    if not stripe_key:
        raise HTTPException(400, "Stripe is not configured")
    success = f"{base_url}/?paid=1&ref={reference}"
    cancel = f"{base_url}/?paid=0&ref={reference}"
    data = {
        "mode": "payment",
        "success_url": success,
        "cancel_url": cancel,
        "client_reference_id": reference[:200],
        "line_items[0][quantity]": "1",
        "line_items[0][price_data][currency]": (currency or "usd").lower(),
        "line_items[0][price_data][unit_amount]": str(_money_to_minor_units(amount, currency)),
        "line_items[0][price_data][product_data][name]": (description or "AppointHive")[:120],
        "metadata[proposal_ref]": reference[:500],
    }
    if customer_email:
        data["customer_email"] = customer_email[:200]
    try:
        resp = requests.post(
            "https://api.stripe.com/v1/checkout/sessions",
            data=data,
            auth=(stripe_key, ""),
            timeout=20,
        )
        payload = resp.json() if resp.content else {}
        if resp.ok and payload.get("url") and payload.get("id"):
            return (
                str(payload["id"]),
                str(payload["url"]),
                "Pay with Stripe Checkout (card). Session created via API.",
            )
        err = (payload.get("error") or {}).get("message") or resp.text[:300] or f"HTTP {resp.status_code}"
        log.warning("Stripe Checkout session failed: %s", err)
        return (
            f"stripe_pending_{reference}",
            f"https://checkout.stripe.com/pay/placeholder/{reference}",
            f"Stripe API did not create a live session ({err}). Confirm paid manually or fix STRIPE_SECRET_KEY.",
        )
    except requests.RequestException as exc:
        log.warning("Stripe Checkout request error: %s", exc)
        return (
            f"stripe_pending_{reference}",
            f"https://checkout.stripe.com/pay/placeholder/{reference}",
            f"Stripe unreachable ({exc}). Use manual confirm or retry when network/API is available.",
        )


def create_paypal_order(
    *,
    amount: float,
    currency: str,
    description: str,
    reference: str,
) -> Tuple[str, str, str]:
    """
    Create a PayPal order and return (order_id, approve_url, instructions).
    Falls back to placeholder if credentials incomplete or API fails.
    """
    from app.services import secrets as sec

    paypal_id = sec.paypal_client_id()
    paypal_secret = sec.paypal_client_secret()
    base_url = sec.public_base_url() or PUBLIC_BASE_URL
    if not paypal_id:
        raise HTTPException(400, "PayPal is not configured")
    if not paypal_secret:
        return (
            f"paypal_pending_{reference}",
            f"https://www.paypal.com/invoice/p/#{reference}",
            "Set PayPal client secret (Settings → Payment providers) to create live approve links; until then use Owner confirm.",
        )
    base = "https://api-m.paypal.com" if sec.paypal_mode() == "live" else "https://api-m.sandbox.paypal.com"
    try:
        token_resp = requests.post(
            f"{base}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(paypal_id, paypal_secret),
            timeout=20,
        )
        token_payload = token_resp.json() if token_resp.content else {}
        access = token_payload.get("access_token")
        if not token_resp.ok or not access:
            err = token_payload.get("error_description") or token_resp.text[:200]
            return (
                f"paypal_pending_{reference}",
                f"https://www.paypal.com/invoice/p/#{reference}",
                f"PayPal OAuth failed ({err}). Use Owner confirm or fix credentials.",
            )
        order_body = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": reference[:127],
                    "description": (description or "AppointHive")[:127],
                    "amount": {
                        "currency_code": (currency or "USD").upper(),
                        "value": f"{float(amount):.2f}",
                    },
                }
            ],
            "application_context": {
                "return_url": f"{base_url}/?paypal=1&ref={reference}",
                "cancel_url": f"{base_url}/?paypal=0&ref={reference}",
                "brand_name": "AppointHive",
                "user_action": "PAY_NOW",
            },
        }
        order_resp = requests.post(
            f"{base}/v2/checkout/orders",
            json=order_body,
            headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json"},
            timeout=20,
        )
        order = order_resp.json() if order_resp.content else {}
        if order_resp.ok and order.get("id"):
            approve = ""
            for link in order.get("links") or []:
                if link.get("rel") == "approve":
                    approve = link.get("href") or ""
                    break
            if approve:
                return (
                    str(order["id"]),
                    approve,
                    "Pay with PayPal (approve link created via Orders API).",
                )
        err = order.get("message") or order_resp.text[:300] or f"HTTP {order_resp.status_code}"
        return (
            f"paypal_pending_{reference}",
            f"https://www.paypal.com/invoice/p/#{reference}",
            f"PayPal order create failed ({err}). Use Owner confirm.",
        )
    except requests.RequestException as exc:
        return (
            f"paypal_pending_{reference}",
            f"https://www.paypal.com/invoice/p/#{reference}",
            f"PayPal unreachable ({exc}). Use Owner confirm.",
        )


def provider_status(db=None) -> dict:
    from app.services import secrets as sec

    stripe_key = sec.stripe_secret_key(db=db)
    paypal_id = sec.paypal_client_id(db=db)
    stripe_ok = bool(stripe_key)
    paypal_ok = bool(paypal_id)
    s_mode = sec.stripe_mode(db=db)
    p_mode = sec.paypal_mode(db=db)
    return {
        "stripe": {
            "configured": stripe_ok,
            "mode": s_mode if stripe_ok else None,
            "auto_link": stripe_ok,
            "auto_paid_webhook": bool(sec.stripe_webhook_secret(db=db)),
        },
        "paypal": {
            "configured": paypal_ok,
            "mode": p_mode if paypal_ok else None,
            "auto_link": paypal_ok,
            "auto_paid_webhook": bool(sec.paypal_webhook_id(db=db)),
        },
        "payoneer": {
            "configured": bool(sec.payoneer_api_key(db=db) or sec.payoneer_payee_email(db=db)),
            "payee_email_set": bool(sec.payoneer_payee_email(db=db)),
            "auto_link": False,
            "auto_paid_webhook": bool(sec.payoneer_webhook_secret(db=db)),
            "webhook_path": "/api/webhooks/payoneer",
            "note": "Copyable pay instructions with reference. Owner confirm, or POST webhook when webhook secret is set.",
        },
        "skrill": {
            "configured": bool(sec.skrill_merchant_id(db=db) or sec.skrill_pay_to_email(db=db)),
            "pay_to_set": bool(sec.skrill_pay_to_email(db=db) or sec.skrill_merchant_id(db=db)),
            "auto_link": False,
            "auto_paid_webhook": bool(sec.skrill_webhook_secret(db=db)),
            "webhook_path": "/api/webhooks/skrill",
            "note": "Copyable pay instructions with reference. Owner confirm, or POST webhook when webhook secret is set.",
        },
        "manual": {
            "configured": True,
            "auto_link": False,
            "auto_paid_webhook": False,
        },
        "test_mode_banner": (stripe_ok and s_mode == "test")
        or (paypal_ok and p_mode == "sandbox")
        or True,
    }


def _setting(db, key: str, default: str = "") -> str:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    return row.value if row else default


def _next_number(db, prefix: str) -> str:
    return f"{prefix}-{_utcnow().strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"


def create_invoice_from_proposal(
    db,
    proposal: Proposal,
    *,
    provider: str = "manual",
    actor_id: Optional[int] = None,
    expires_days: int = 14,
) -> Invoice:
    provider = (provider or "manual").lower()
    if provider not in ("stripe", "paypal", "payoneer", "skrill", "manual"):
        raise HTTPException(422, "Unsupported payment provider")

    # Idempotent: reuse open invoice for same proposal+provider
    existing = db.scalar(
        select(Invoice).where(
            Invoice.proposal_id == proposal.id,
            Invoice.provider == provider,
            Invoice.status.in_(("draft", "open")),
        )
    )
    if existing:
        return existing

    entity = db.scalar(select(BillingEntity).where(BillingEntity.active.is_(True)))
    is_test = False
    hosted = ""
    instructions = ""
    provider_ref = ""

    business = db.get(Business, proposal.business_id)
    customer_email = (business.email or "") if business else ""

    from app.services import secrets as sec

    if provider == "stripe":
        if not sec.stripe_secret_key():
            raise HTTPException(400, "Stripe is not configured")
        is_test = sec.stripe_mode() != "live"
        provider_ref, hosted, instructions = create_stripe_checkout_session(
            amount=float(proposal.total),
            currency=proposal.currency or "USD",
            description=f"AppointHive {proposal.reference}",
            reference=proposal.reference,
            customer_email=customer_email,
        )
    elif provider == "paypal":
        if not sec.paypal_client_id():
            raise HTTPException(400, "PayPal is not configured")
        is_test = sec.paypal_mode() != "live"
        provider_ref, hosted, instructions = create_paypal_order(
            amount=float(proposal.total),
            currency=proposal.currency or "USD",
            description=f"AppointHive {proposal.reference}",
            reference=proposal.reference,
        )
    elif provider == "payoneer":
        provider_ref = f"payoneer_{proposal.reference}"
        payee = sec.payoneer_payee_email() or "(set Payoneer payee email in Settings → Payment providers)"
        amount = float(proposal.total)
        cur = proposal.currency or "USD"
        instructions = (
            f"Pay via Payoneer\n"
            f"Pay to: {payee}\n"
            f"Amount: {cur} {amount:.2f}\n"
            f"Payment reference (required): {provider_ref}\n"
            f"Invoice: {proposal.reference}\n"
            f"After you pay, reply with the Payoneer confirmation — or we mark paid when the webhook fires."
        )
        hosted = ""
    elif provider == "skrill":
        provider_ref = f"skrill_{proposal.reference}"
        if sec.skrill_pay_to_email():
            pay_to = sec.skrill_pay_to_email()
        elif sec.skrill_merchant_id():
            pay_to = f"merchant {sec.skrill_merchant_id()}"
        else:
            pay_to = "(set Skrill pay-to email in Settings → Payment providers)"
        amount = float(proposal.total)
        cur = proposal.currency or "USD"
        instructions = (
            f"Pay via Skrill\n"
            f"Pay to: {pay_to}\n"
            f"Amount: {cur} {amount:.2f}\n"
            f"Payment reference (required): {provider_ref}\n"
            f"Invoice: {proposal.reference}\n"
            f"After you pay, reply with the Skrill confirmation — or we mark paid when the webhook fires."
        )
        hosted = ""
    else:
        provider_ref = f"manual_{proposal.reference}"
        instructions = f"Bank transfer. Quote reference {proposal.reference} on the payment."
        is_test = False

    inv = Invoice(
        business_id=proposal.business_id,
        proposal_id=proposal.id,
        billing_entity_id=entity.id if entity else None,
        number=_next_number(db, "INV"),
        status="open",
        currency=proposal.currency,
        subtotal=proposal.subtotal,
        tax_amount=proposal.tax_amount,
        total=proposal.total,
        provider=provider,
        provider_ref=provider_ref,
        hosted_url=hosted,
        pay_instructions=instructions,
        line_items_json=proposal.line_items_json,
        expires_at=_utcnow() + timedelta(days=expires_days),
        is_test_mode=is_test,
    )
    db.add(inv)
    db.add(
        Activity(
            business_id=proposal.business_id,
            activity_type="invoice_created",
            summary=f"Invoice {inv.number} via {provider}",
            outcome=f"total={inv.total} {inv.currency}",
            created_by=actor_id,
        )
    )
    db.add(
        Task(
            business_id=proposal.business_id,
            title=f"Follow up unpaid invoice {inv.number}",
            description="Auto-created when invoice was issued",
            priority="high",
            status="open",
            due_at=inv.expires_at,
            owner_id=actor_id,
        )
    )
    db.commit()
    db.refresh(inv)
    return inv


def mark_invoice_paid(
    db,
    invoice: Invoice,
    *,
    provider_ref: str = "",
    actor_id: Optional[int] = None,
    amount: Optional[float] = None,
    method: str = "",
) -> Payment:
    if invoice.status == "paid":
        existing = db.scalar(select(Payment).where(Payment.invoice_id == invoice.id, Payment.status == "succeeded"))
        if existing:
            return existing

    pay = Payment(
        business_id=invoice.business_id,
        invoice_id=invoice.id,
        amount=float(amount if amount is not None else invoice.total),
        currency=invoice.currency,
        status="succeeded",
        provider=invoice.provider,
        method=method or invoice.provider,
        provider_ref=provider_ref or invoice.provider_ref,
        paid_at=_utcnow(),
        is_test_mode=bool(invoice.is_test_mode),
    )
    invoice.status = "paid"
    invoice.paid_at = pay.paid_at
    if provider_ref:
        invoice.provider_ref = provider_ref
    db.add(pay)
    db.add(
        Activity(
            business_id=invoice.business_id,
            activity_type="payment_received",
            summary=f"Payment for {invoice.number}",
            outcome=f"{pay.amount} {pay.currency}",
            created_by=actor_id,
        )
    )

    # Complete related unpaid follow-up tasks
    for t in db.scalars(
        select(Task).where(
            Task.business_id == invoice.business_id,
            Task.status == "open",
            Task.title.ilike(f"%{invoice.number}%"),
        )
    ).all():
        t.status = "done"
        t.completed_at = _utcnow()

    won_on = _setting(db, "won_on", "first_payment")
    business = db.get(Business, invoice.business_id)
    if business and won_on == "first_payment":
        won = get_stage(db, business.product_line or "STANDALONE", "won")
        if not won and business.product_line == "SAAS":
            won = get_stage(db, "SAAS", "subscribed")
        if won and business.pipeline_stage_id != won.id:
            move_stage(db, business, won.id, actor_id=actor_id, note="Auto-won on first payment", force=True)
            return pay

    db.commit()
    db.refresh(pay)
    return pay


def confirm_manual_payment(db, invoice_id: int, *, actor_id: Optional[int] = None, provider_ref: str = "") -> Payment:
    invoice = db.get(Invoice, invoice_id)
    if not invoice:
        raise HTTPException(404, "Invoice not found")
    return mark_invoice_paid(db, invoice, provider_ref=provider_ref, actor_id=actor_id, method="manual_confirm")


def record_refund(
    db,
    payment_id: int,
    *,
    amount: Optional[float] = None,
    actor_id: Optional[int] = None,
    reason: str = "",
) -> Payment:
    original = db.get(Payment, payment_id)
    if not original or original.status != "succeeded":
        raise HTTPException(404, "Succeeded payment not found")
    refund_amt = float(amount if amount is not None else original.amount)
    if refund_amt <= 0 or refund_amt > float(original.amount) + 0.001:
        raise HTTPException(422, "Invalid refund amount")
    refund = Payment(
        business_id=original.business_id,
        invoice_id=original.invoice_id,
        amount=refund_amt,
        currency=original.currency,
        status="refunded" if abs(refund_amt - float(original.amount)) < 0.01 else "partial_refund",
        provider=original.provider,
        method="refund",
        provider_ref=f"refund_of_{original.id}",
        paid_at=_utcnow(),
        is_test_mode=bool(original.is_test_mode),
        notes=(reason or "")[:2000],
    )
    if refund.status == "refunded":
        original.status = "refunded"
    db.add(refund)
    db.add(
        Activity(
            business_id=original.business_id,
            activity_type="payment_refunded",
            summary=f"Refund {refund_amt} {original.currency}",
            outcome=reason or "",
            created_by=actor_id,
        )
    )
    db.commit()
    db.refresh(refund)
    return refund


def record_webhook_once(db, provider: str, event_id: str, payload: dict) -> bool:
    """Return True if this is a new event (should process)."""
    existing = db.scalar(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
    if existing:
        return False
    db.add(
        WebhookEvent(
            provider=provider,
            event_id=event_id,
            payload_json=json.dumps(payload)[:50000],
        )
    )
    db.commit()
    return True


def create_proposal(
    db,
    business: Business,
    *,
    title: str,
    currency: str,
    line_items: list[dict],
    discount_amount: float = 0.0,
    discount_reason: str = "",
    tax_rate: float = 0.0,
    tax_label: str = "",
    plan_id: Optional[int] = None,
    terms_url: str = "",
    expires_days: int = 30,
) -> Proposal:
    subtotal = sum(float(i.get("amount") or 0) * float(i.get("qty") or 1) for i in line_items)
    discount_amount = float(discount_amount or 0)
    taxable = max(0.0, subtotal - discount_amount)
    tax_amount = round(taxable * float(tax_rate or 0), 2)
    total = round(taxable + tax_amount, 2)
    ref = _next_number(db, "PR")
    prop = Proposal(
        business_id=business.id,
        title=title or f"Proposal for {business.name}",
        reference=ref,
        status="draft",
        currency=(currency or business.currency or "USD").upper(),
        subtotal=round(subtotal, 2),
        discount_amount=discount_amount,
        discount_reason=discount_reason or "",
        tax_rate=float(tax_rate or 0),
        tax_label=tax_label or "",
        tax_amount=tax_amount,
        total=total,
        plan_id=plan_id,
        line_items_json=json.dumps(line_items),
        expires_at=_utcnow() + timedelta(days=expires_days),
        terms_url=terms_url or "",
    )
    if business.deal_value is None:
        business.deal_value = total
        business.currency = prop.currency
    db.add(prop)
    db.commit()
    db.refresh(prop)
    return prop
