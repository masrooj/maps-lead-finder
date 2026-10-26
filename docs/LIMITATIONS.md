# Known limitations (Sales OS v2)

- **In-app follow-ups** by default. Digests still need an email provider (not wired); unpaid cron creates in-app tasks when `CRON_SECRET` (or Owner session) runs.
- **Stripe Checkout** via Checkout Sessions when `STRIPE_SECRET_KEY` is set. Invalid keys / API errors fall back to placeholder URL + Owner confirm. Webhooks verify `Stripe-Signature` when `STRIPE_WEBHOOK_SECRET` is set.
- **PayPal** Orders API when client id/secret set; webhook requires transmission headers (+ HMAC soft-check when secret set) if `PAYPAL_WEBHOOK_ID` is configured.
- **Payoneer/Skrill** are first-class: copyable pay instructions + Owner confirm; optional webhooks (`/api/webhooks/payoneer`, `/api/webhooks/skrill`) when secrets are set. Not hosted Checkout like Stripe. See `docs/PAYMENTS.md`. **No recurring SaaS subscription billing yet** — MRR is tracked on prospects and shown in Reports.
- **Kanban** HTML5 drag-and-drop; Move… remains accessible. **Undo last stage** available on prospect Sales tab.
- **Single company** — not multi-tenant. Roles: Owner / Sales / Viewer with **per-user passwords** (Owner can set). Shared `LOGIN_PASSWORD` still works for the owner email until/alongside personal hash.
- **Compose is copy & paste** for outreach channels. Invoices can **Send email + PDF** when SMTP is configured (Money → Mail).
- **Compliance packs** are operational checklists — not legal advice.
- **Vercel timeouts** on huge city scans: use **enrich_batch** jobs (`/api/sales/jobs`) for batched verification stamps; Places search itself is still request-bound.
- **local-dev.db vs Neon** do not sync automatically — use CSV export/import. CSV soft duplicates can be **ignore / link / merge** from the import prompt.
- **Territories** can auto-assign unowned prospects by country to the territory owner.
- **Contact queue ranking modes** are DB CRUD with Yes/No/Any filter builder — Settings → Ranking modes.
- **Invoice PDF** + compose + SMTP send (when configured). Secrets encrypted at rest in AppSettings. Demo uploads: local volume or S3 (`STORAGE_BACKEND=s3`) for durable/Vercel. See `docs/GAPS.md`.
- **Nav density** on mobile: many Sales OS tabs; Discover remains the primary outreach workspace.
