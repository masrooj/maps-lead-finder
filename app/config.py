"""Application configuration — env names only; never log secret values."""
from __future__ import annotations

import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./local-dev.db")
LOGIN_USERNAME = "hello@appointhive.com"
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
SESSION_COOKIE = "mlf_session"
SESSION_DAYS = 14

# Commercial defaults (overridable via app_settings table)
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "USD").strip().upper() or "USD"
DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "Australia/Sydney").strip() or "Australia/Sydney"
WON_ON = os.getenv("WON_ON", "first_payment").strip() or "first_payment"  # first_payment | acceptance
SAAS_ENABLED = os.getenv("SAAS_ENABLED", "false").lower() in ("1", "true", "yes")
DEMO_DATA_ENABLED = os.getenv("DEMO_DATA_ENABLED", "false").lower() in ("1", "true", "yes")
CRON_SECRET = os.getenv("CRON_SECRET", "").strip()

# Public app URL for payment success/cancel redirects (no trailing slash)
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("APP_BASE_URL") or "http://localhost:8000").strip().rstrip("/")

# Payment providers (optional until configured)
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
STRIPE_MODE = os.getenv("STRIPE_MODE", "test").strip().lower() or "test"
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "").strip()
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
PAYPAL_WEBHOOK_ID = os.getenv("PAYPAL_WEBHOOK_ID", "").strip()
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox").strip().lower() or "sandbox"
PAYONEER_API_KEY = os.getenv("PAYONEER_API_KEY", "").strip()
PAYONEER_PAYEE_EMAIL = os.getenv("PAYONEER_PAYEE_EMAIL", "").strip()
PAYONEER_WEBHOOK_SECRET = os.getenv("PAYONEER_WEBHOOK_SECRET", "").strip()
SKRILL_MERCHANT_ID = os.getenv("SKRILL_MERCHANT_ID", "").strip()
SKRILL_SECRET = os.getenv("SKRILL_SECRET", "").strip()
SKRILL_PAY_TO_EMAIL = os.getenv("SKRILL_PAY_TO_EMAIL", "").strip()
SKRILL_WEBHOOK_SECRET = os.getenv("SKRILL_WEBHOOK_SECRET", "").strip() or SKRILL_SECRET

PRODUCT_LINES = ("STANDALONE", "SAAS")
USER_ROLES = ("owner", "sales", "viewer")

STANDALONE_STAGES = [
    ("identified", "Identified", 0.05, False),
    ("ready_to_contact", "Ready to Contact", 0.10, False),
    ("contacted", "Contacted", 0.15, False),
    ("follow_up", "Follow-up", 0.20, False),
    ("engaged", "Engaged", 0.30, False),
    ("qualified", "Qualified", 0.40, False),
    ("demo_scheduled", "Demo Scheduled", 0.50, False),
    ("demo_completed", "Demo Completed", 0.60, False),
    ("proposal_sent", "Proposal Sent", 0.70, False),
    ("negotiation", "Negotiation", 0.80, False),
    ("won", "Won", 1.00, True),
    ("lost", "Lost", 0.00, True),
    ("nurture", "Nurture", 0.10, False),
]

SAAS_STAGES = [
    ("identified", "Identified", 0.05, False),
    ("ready_to_contact", "Ready to Contact", 0.10, False),
    ("contacted", "Contacted", 0.15, False),
    ("follow_up", "Follow-up", 0.20, False),
    ("engaged", "Engaged", 0.30, False),
    ("qualified", "Qualified", 0.40, False),
    ("demo_scheduled", "Demo Scheduled", 0.50, False),
    ("demo_completed", "Demo Completed", 0.60, False),
    ("trial_offered", "Trial Offered", 0.65, False),
    ("trial_active", "Trial Active", 0.75, False),
    ("subscribed", "Subscribed", 1.00, True),
    ("past_due", "Past Due", 0.40, False),
    ("churned", "Churned", 0.00, True),
    ("lost", "Lost", 0.00, True),
    ("nurture", "Nurture", 0.10, False),
]

CURRENCY_BY_COUNTRY = {
    "AU": "AUD",
    "NZ": "NZD",
    "US": "USD",
    "CA": "CAD",
    "GB": "GBP",
    "IE": "EUR",
    "DE": "EUR",
    "FR": "EUR",
    "NL": "EUR",
    "AE": "AED",
    "SA": "SAR",
}
