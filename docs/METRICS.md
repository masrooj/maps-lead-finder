# AppointHive Sales OS — Metric dictionary

All percentages should show sample size **n=**. Prefer counts when n &lt; 10.

| Metric | Definition |
|--------|------------|
| Open pipeline value | Sum of `deal_value` for prospects in non-won, non-lost stages (by currency) |
| Weighted forecast | Σ(deal_value × stage.probability) for open stages — **estimate only** |
| Signed / Won value | Prospects in Won (standalone) or Subscribed (SaaS) |
| Collected revenue | Sum of payments with status `succeeded` minus refunds/`partial_refund` amounts. **Excludes** proposals and unpaid invoices. Test-mode payments excluded from live totals |
| Invoiced outstanding | Sum of invoice `total` where status = `open` |
| Qualified count | Prospects with qualification score ≥ 5 (or override), not archived |
| Reply rate | Prospects with ≥1 reply activity ÷ contacted (define cohort + date range in report filters) |
| Demo conversion | Demo completed ÷ qualified eligible |
| Win rate | Won ÷ (Won + Lost) in period |

`won_on` setting: default `first_payment` (auto-move to Won/Subscribed when first succeeded payment). Alternative: `acceptance` (proposal accepted).
