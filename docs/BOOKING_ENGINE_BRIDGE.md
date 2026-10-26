# AppointHive Booking Engine bridge (Sales OS Plan 2)

**AppointHive Sales OS** (this app) finds leads, shows **live AppointHive prices**, handovers deals, shares the AppointHive pay URL, and mirrors **Won** when AppointHive attests payment.

**AppointHive Booking Engine** owns product cash, checkout, ZIP, and license fulfillment.

Never brand this product as “Lead CRM.”

## Money rule

- Bridged STANDALONE product sales: **do not** create local Stripe/PayPal/etc. product invoices.
- Sales OS **never** marks paid and **never** issues ZIP for those sales.
- On `standalone.payment.paid` webhook → update `BeDelivery` + pipeline **Won** (mirror only).

## Connect first (fail-closed)

1. Owner → **Workspace → AppointHive**
2. **Generate pairing code** (15 min TTL)
3. On AppointHive Super Admin → AppointHive Sales OS connection: paste this Sales OS base URL + code  
   (BE calls `POST /api/v1/public/product-pairing` with `{ code, productBaseUrl }` → `{ secret, webhookUrl }`)
4. Or paste AppointHive URL + shared secret manually
5. **Test connection** → `GET {BE}/api/internal/crm/health` with Bearer → status becomes `connected` only on success
6. **Disconnect** clears the secret path immediately

Until `connection_status=connected`: catalog / revisions / send / upgrade return **409** `{ error: "CRM not connected" }`.

Encrypted settings: `be.base_url`, `be.shared_secret`, `be.bridge_enabled`, `be.connection_status`, `be.last_health_at`.

## Sales OS APIs

| Route | Purpose |
|-------|---------|
| `GET /api/sales/be/connection` | Public status (secret masked) |
| `POST /api/sales/be/connection/pair-code` | Owner one-time code |
| `POST /api/sales/be/connection/manual` | Paste URL + secret |
| `POST /api/sales/be/connection/test` | Health probe → connected |
| `POST /api/sales/be/connection/disconnect` | Clear secret |
| `GET /api/sales/be/catalog` | Live BE catalog |
| `GET /api/sales/be/revisions` | FINALIZED packages + prices |
| `POST /api/sales/be/send-to-delivery` | Purchase + checkout → pay URL |
| `POST /api/sales/be/send-request` | Soft handover (Awaiting AppointHive quote) |
| `POST /api/sales/be/send-upgrade` | Quote → request → checkout |
| `GET /api/sales/be/deliveries` | Local handover rows |
| `POST /api/sales/be/deliveries/{id}/refresh` | Refetch purchase/request status |
| `POST /api/webhooks/booking-engine` | Inbound Bearer events |

Deal key on every call: `crmLeadId` / `externalCrmRef` = `business:{id}`.

### Prospect payload (business + contacts)

Every send includes `crmProspect` (structured) plus a human-readable `message` with a `CRM_PROSPECT_JSON` block for Super Admin inbox:

- business: name, placeId, email, phones, website, maps URL, address/city/state/postcode/country, category, tax/billing, notes, pain, staff/locations, currency/dealValue
- primaryContact + contacts[]: name, role, email, phone

Booking Engine (source) stores `crmProspect` on purchase `purchaseSnapshot` and folds it into inbound request `message`. **Redeploy BE** so runtime picks up those API fields.

### Booking Engine follow-ups (for maintainable CRM records)

| Need | Why |
|------|-----|
| Optional first-class columns on inbound / purchase | `contactPhone`, `contactName`, `address`, `city`, `state`, `postcode`, `country`, `website`, `placeId`, `timezone` — today folded into `message` + `crmProspect` JSON; columns make Super Admin search/filter durable |
| Super Admin UI: CRM prospect card | Parse `CRM_PROSPECT_JSON` / `purchaseSnapshot.crmProspect` on `/book/super-admin/standalone-requests` (and purchase detail) |
| Validate `installationId` before write | Unknown ids currently Prisma-FK **500**; return **400** `{ error: "Unknown installationId" }` so Sales OS can clear a stale id |
| Only accept real `StandaloneInstallation` ids on EXTERNAL_CRM create | Sales OS now omits `installationId` on new-sale soft/purchase intake; upgrade soft still sends it when `requestedEdition` is set |
| Payment provider on BE for `createCheckout: true` | Without a platform PSP, purchase may return 400 “No payment provider configured” (local pay URL path OK when provider configured) |

Live local E2E (2026-10-26): multi-buy purchase + checkout URL OK; `payment.paid` → Won; `installation.active` → `be_installation_id`; soft request OK when `installationId` omitted.

## Webhooks (Bearer)

Handled: `crm.bridge.connected`, `standalone.purchase.recorded`, `standalone.payment.*`, `standalone.delivery.ready`, `standalone.installation.active` (stores `installation_id`), `standalone.upgrade.*` (incl. `payUrl`).

Payloads must not contain `SOS-…` license keys or receipt bytes.

## Local smoke

```bash
# Sales OS
curl -s http://127.0.0.1:8000/api/health

# Pairing (after Owner generates code in UI)
curl -s -X POST http://127.0.0.1:8000/api/v1/public/product-pairing \
  -H 'Content-Type: application/json' \
  -d '{"code":"<code>","productBaseUrl":"http://127.0.0.1:3010"}'

# After Test connection
curl -s -b cookies.txt http://127.0.0.1:8000/api/sales/be/revisions
```
