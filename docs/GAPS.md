# Product gaps & fixes (Sales OS)

## Shipped

| Area | Status |
|------|--------|
| Ranking modes CRUD (DB) | Done — Settings filter builder (Yes/No/Any) |
| Intl money/date formatting | Done |
| Demo library (URL + upload) | Done — local volume or S3 when configured |
| Payment API keys UI | Done — encrypted at rest |
| Invoice view / PDF / compose / **SMTP send + PDF** | Done |
| Invoice templates | Done — Templates → Invoice |
| Webhook secrets from Settings | Done — resolved live via `app.services.secrets` |
| Multi-currency PDF amounts | Done — locale-aware grouping + ASCII-safe symbols |
| Mail / SMTP settings UI | Done — Money → Mail / SMTP |
| Local pay defaults button | Done — Pay setup → **Apply local defaults** (`POST …/fill-local-defaults`) |
| Fernet secrets (`cryptography`) | Done — in `requirements.txt`; falls back to XOR+HMAC if missing |
| Compose bind-mount `./app` | Done — code changes without image rebuild |
| Runtime helper | Done — `scripts/ah-runtime.sh` (avoids hung `docker compose ps`) |

## Remaining (ops / product — intentional or needs your keys)

1. **Live Stripe / PayPal API keys** — paste `sk_test_…` / client id+secret in Pay setup. App will not invent them. See `docs/PAYMENT_SETUP_STATUS.md`.
2. **S3 on Vercel** — set `STORAGE_BACKEND=s3` + bucket/keys for durable demos.
3. **SMTP deliverability** — SPF/DKIM/DMARC are provider-side; fill Mail tab to send invoice PDF.
4. **SaaS subscription billing** — out of scope until `SAAS_ENABLED` product line; MRR fields only today.
5. **Email digests** — unpaid cron creates in-app tasks only.
6. **Multi-tenant CRM** — single company by design.
7. **Disk / Docker Desktop** — keep ≥8Gi free before `scripts/ah-runtime.sh rebuild`; compose CLI can hang — use the script.

## Configure

- **Payments / webhooks:** Money → Pay setup (or `.env`). Prefer **Apply local defaults**, then paste Stripe/PayPal keys.
- **SMTP:** Pay setup → Mail.
- **Uploads:** Docker volume `uploads_data`, or S3 env vars.
- **Runtime:** `bash scripts/ah-runtime.sh status|up|restart-app|rebuild`
