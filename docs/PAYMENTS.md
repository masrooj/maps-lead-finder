# Payments & webhooks (AppointHive Sales OS)

## Providers in product

| Provider | How lead pays | How you mark collected |
|----------|---------------|------------------------|
| **Stripe** | Hosted Checkout link | Auto via `/api/webhooks/stripe` (needs `STRIPE_WEBHOOK_SECRET`) or Owner **Confirm paid** |
| **PayPal** | Approve/order link | Auto via `/api/webhooks/paypal` (needs `PAYPAL_WEBHOOK_ID` + headers) or Owner confirm |
| **Payoneer** | Copy/paste pay instructions (payee + amount + `payoneer_<proposal_ref>`) | Owner confirm, or `POST /api/webhooks/payoneer` with `X-Webhook-Secret` |
| **Skrill** | Copy/paste pay instructions (pay-to + amount + `skrill_<proposal_ref>`) | Owner confirm, or `POST /api/webhooks/skrill` with `X-Webhook-Secret` |
| **Manual / bank** | Copy instructions with invoice reference | Owner **Confirm paid** only |

**Collected revenue** = succeeded payments − refunds. Proposals and open invoices are not cash.

## Env (names only) — or Money → Payment API keys (DB override, encrypted at rest)

```
# Stripe
STRIPE_SECRET_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_MODE=test

# PayPal
PAYPAL_CLIENT_ID=
PAYPAL_CLIENT_SECRET=
PAYPAL_WEBHOOK_ID=
PAYPAL_MODE=sandbox

# Payoneer
PAYONEER_PAYEE_EMAIL=
PAYONEER_API_KEY=          # optional
PAYONEER_WEBHOOK_SECRET=   # enables /api/webhooks/payoneer

# Skrill
SKRILL_PAY_TO_EMAIL=
SKRILL_MERCHANT_ID=        # optional if email set
SKRILL_WEBHOOK_SECRET=     # or SKRILL_SECRET

PUBLIC_BASE_URL=https://your-app.example
```

Webhook handlers read secrets **live** from AppSettings (then env) — no restart required after saving keys in Money.

## Webhook payloads (Payoneer / Skrill)

**Payoneer** `POST /api/webhooks/payoneer`  
Header: `X-Webhook-Secret: <PAYONEER_WEBHOOK_SECRET>`  
JSON example:

```json
{
  "id": "po_txn_123",
  "status": "completed",
  "provider_ref": "payoneer_PROP-REF"
}
```

**Skrill** `POST /api/webhooks/skrill`  
Header: `X-Webhook-Secret: <SKRILL_WEBHOOK_SECRET>`  
JSON or form: `transaction_id`, `status` (`2` / `processed` / `completed`), plus `provider_ref` or `invoice_number`.

Idempotent: same event id does not double-pay.

## Sales workflow

1. Deals → proposal + choose provider (Stripe/PayPal/Payoneer/Skrill/manual).  
2. Copy pay link / instructions, **or** Money → invoice **Send email + PDF** (SMTP).  
3. Lead pays.  
4. Webhook marks paid **or** Owner **Confirm close / paid** under Deals.

## AppointHive Booking Engine (STANDALONE product cash)

When **Workspace → AppointHive** shows **connected**, STANDALONE product payment uses AppointHive pay URLs (`/api/sales/be/send-to-delivery`), not local PSP invoice create. See `docs/BOOKING_ENGINE_BRIDGE.md`. Local provider webhooks above still apply to **non-bridged** / SaaS / merchant flows.
