# Payment & webhook setup status

Last checked: Pay setup → **Apply local defaults** (`POST /api/sales/payment-settings/fill-local-defaults`) + pytest webhook E2E.

## Filled (local defaults in `.env`)

| Key | Status | Notes |
|-----|--------|--------|
| `PUBLIC_BASE_URL` | Filled | `http://localhost:8000` |
| `STRIPE_MODE` | Filled | `test` |
| `PAYPAL_MODE` | Filled | `sandbox` |
| `PAYONEER_PAYEE_EMAIL` | Filled | `hello@appointhive.com` (replace with real Payoneer payee) |
| `PAYONEER_WEBHOOK_SECRET` | Filled | local synthetic secret for notify tests |
| `SKRILL_PAY_TO_EMAIL` | Filled | `hello@appointhive.com` (replace with real Skrill) |
| `SKRILL_WEBHOOK_SECRET` | Filled | local synthetic secret for notify tests |
| `CRON_SECRET` | Filled | local cron shared secret |
| Manual / bank | Ready | Owner **Confirm paid** — no keys |

## Remaining (you must fill — no live keys in repo)

| Key | Where | Needed for |
|-----|--------|------------|
| `STRIPE_SECRET_KEY` (`sk_test_…`) | Workspace → Pay setup → Stripe | Hosted Checkout links + Validate |
| `STRIPE_PUBLISHABLE_KEY` | Pay setup → Stripe | Client-side (optional) |
| `STRIPE_WEBHOOK_SECRET` (`whsec_…`) | Pay setup → Stripe | Auto-paid from Stripe dashboard webhook |
| `PAYPAL_CLIENT_ID` | Pay setup → PayPal | Order links + Validate |
| `PAYPAL_CLIENT_SECRET` | Pay setup → PayPal | Order links + Validate |
| `PAYPAL_WEBHOOK_ID` | Pay setup → PayPal | Auto-paid from PayPal webhooks |
| SMTP host / from / password | Pay setup → Mail | Send invoice email + PDF |
| Real Payoneer/Skrill emails | Pay setup | If not using `hello@appointhive.com` |
| Provider dashboard webhook URL | Stripe/PayPal/etc. | Point to `https://<PUBLIC_HOST>/api/webhooks/<provider>` |

For local Stripe/PayPal webhooks from the internet, use a tunnel (ngrok) and set `PUBLIC_BASE_URL` to that HTTPS URL.

## Tested (pytest)

| Flow | Result |
|------|--------|
| Stripe webhook → invoice paid (+ idempotent) | Pass |
| PayPal webhook → invoice paid | Pass |
| Payoneer webhook → invoice paid | Pass |
| Skrill webhook → invoice paid | Pass |
| Bad webhook secret rejected | Pass |
| Owner confirm-paid (manual) | Pass |
| Validate payoneer / skrill / manual | Pass (valid) |
| Validate stripe / paypal without live keys | Pass (honestly **invalid**) |

## Callback URLs to register

```
POST {PUBLIC_BASE_URL}/api/webhooks/stripe
POST {PUBLIC_BASE_URL}/api/webhooks/paypal
POST {PUBLIC_BASE_URL}/api/webhooks/payoneer   # header X-Webhook-Secret
POST {PUBLIC_BASE_URL}/api/webhooks/skrill     # header X-Webhook-Secret
```

Stripe/PayPal also use browser return URLs built from `PUBLIC_BASE_URL` on checkout create (success/cancel) — not separate app routes beyond `/?…` query flags.
