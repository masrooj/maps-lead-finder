# AppointHive Sales OS — Plan 2 agent prompt (copy/paste)

**Product:** AppointHive Sales OS (this repo = `maps-lead-finder`)  
**Not:** Lead CRM, salon tenant CRM, or Booking Engine  
**Companion:** AppointHive Booking Engine (product cash + ZIP) — Plan 1 already shipped  
**Plan file:** `/Users/mohamedmasrooj/.cursor/plans/maps-lead-finder-booking-engine-bridge.plan.md`

Paste everything below the line into a **new Cursor agent chat** opened on the **maps-lead-finder** workspace. Execute end-to-end. Do not wait for ordinary approvals.

---

## APPOINTHIVE SALES OS — PLAN 2 (BOOKING ENGINE BRIDGE)

### 0. Identity (do not confuse)

| Name | What it is |
|------|------------|
| **AppointHive Sales OS** | This app (`maps-lead-finder`) — leads, pipeline, handover, share pay link, mirror Won |
| **AppointHive** / Booking Engine | Other product — invoice, pay URL, cash attestation, ZIP |
| **Not Lead CRM** | Do not brand, title, or document this product as “Lead CRM” |

**Money rule:** Booking Engine owns product cash and ZIP. Sales OS never marks paid and never issues ZIP for STANDALONE product sales.

### 1. Prerequisites (verify; do not invent)

1. Booking Engine SaaS local: `http://127.0.0.1:3010` → `GET /api/health` ok  
2. AppointHive Super Admin → **AppointHive Sales OS** connection UI (legacy path `/book/super-admin/lead-crm` on BE) can pair to this app  
3. This Sales OS app running (typical local: check README / `.deploy` — often `:8000`)  
4. Plan 1 APIs live on BE under `{BE}/api/internal/crm/*` with Bearer shared secret

### 2. Skills / style

- Preserve WIP. No destructive git. No force-push. No real customer data. Mock OK on LOCAL.
- Fail-closed: until bridge status is **`connected`**, block catalog / revisions / send / upgrade.
- Prefer additive migrations and tests. Update `docs/BOOKING_ENGINE_BRIDGE.md` + `docs/PAYMENTS.md` when done.

### 3. Execute Plan 2 todos (in order)

Use the Cursor plan: `maps-lead-finder-booking-engine-bridge.plan.md`

1. **so-0** Connect-first: `POST /api/v1/public/product-pairing` `{ code, productBaseUrl }` → `{ secret, webhookUrl }` ending `…/api/webhooks/booking-engine`. Encrypted settings: `be.base_url`, `be.shared_secret`, `be.bridge_enabled`, `be.connection_status` (`disconnected|connected|error`), `be.last_health_at`. Owner UI: pair / paste / **Test connection** → `GET {BE}/api/internal/crm/health` with Bearer → `connected` only on success. Disconnect clears secret path immediately. UI copy = **AppointHive** / **Sales OS**, never “Lead CRM.”

2. **so-1** Schema + client: migration `008_booking_engine_bridge.sql` + `BeDelivery` model; `app/services/booking_engine/client.py` with `require_be_connected()` on every call (health, catalog, revisions, upgrade_quote, create_purchase+checkout, create_request, request_checkout, get purchase).

3. **so-2** Send + webhooks: send purchase/request/upgrade; inbound `POST /api/webhooks/booking-engine` verifies Bearer; on `standalone.payment.paid` → mirror Won only (never local PSP / create_invoice_from_proposal for STANDALONE product cash).

4. **so-3** UI: connected banner; live prices + revision picker; soft-request vs pay-ready labels; copy/open BE pay URL; block local STANDALONE PSP when connected; disable BE actions when disconnected.

5. **so-4** Docs + pytest: connect gate, pricing, handover.

6. **so-5** Gaps: `crmLeadId` = `externalCrmRef` = deal id string; store `installation_id` from delivery webhooks; refresh purchase/request status; idempotent send; handle inbound status webhooks with `payUrl`.

### 4. BE contract (consume exactly)

Base: `{APPOINTHIVE_BASE}/api/internal/crm` — Header `Authorization: Bearer {shared_secret}`

| Call | Use |
|------|-----|
| `GET /health` | Connection probe |
| `GET /catalog?productLine=STANDALONE` | Live edition/addon `priceCents` — never hardcode / never seed PlanPrice as truth |
| `GET /standalone/revisions` | FINALIZED packages + snapshot prices |
| `POST /standalone/purchases` | Body: `productRevisionId`, buyer, `crmLeadId`, `createCheckout: true` → share `checkoutUrl` |
| `GET /standalone/purchases/:id` | Refresh status / pay URL |
| `POST /standalone/requests` | Soft handover only — label **Awaiting AppointHive quote** (no pay URL yet) |
| `GET /standalone/requests/:id` | Quote / `payUrl` when ready |
| `GET /standalone/upgrade-quote` | Upgrade delta |
| `POST /standalone/requests/:id/checkout` | Upgrade pay URL |

Webhooks to Sales OS (Bearer): `standalone.purchase.recorded`, `standalone.payment.*`, `standalone.delivery.ready`, `installation.active` (store `installationId`), upgrade status (+ `payUrl`). Never expect license keys `SOS-…` or receipt bytes.

### 5. Acceptance

- No catalog/send/upgrade until connected + health success  
- Live STANDALONE prices from BE  
- Purchase handover returns BE pay URL; upgrade quote + checkout works  
- `payment.paid` → `BeDelivery.paid` + Won (mirror only)  
- Connected → local STANDALONE product PSP blocked; disconnected → BE actions blocked  
- Secrets encrypted/masked; no BE platform Stripe keys in Sales OS  
- Product naming in UI/docs: **AppointHive Sales OS** / **AppointHive** — not Lead CRM  

### 6. Start now

Begin with so-0 connect-first. Continue through so-5. End with a short report: what shipped, how to pair locally, smoke commands, open gaps.
