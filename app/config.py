"""Application configuration — env names only; never log secret values."""
from __future__ import annotations

import os

def _database_url() -> str:
    """Postgres only. Never use SQLite (avoids dual-DB confusion)."""
    explicit = (os.getenv("DATABASE_URL") or "").strip()
    if explicit:
        if explicit.lower().startswith("sqlite:"):
            raise RuntimeError(
                "SQLite is disabled. Set DATABASE_URL to Postgres "
                "(Docker: postgresql+psycopg://mapsleads:…@127.0.0.1:5433/mapsleads)."
            )
        return explicit
    pw = (os.getenv("POSTGRES_PASSWORD") or "change-this-password").strip()
    port = (os.getenv("DB_PORT") or "5433").strip() or "5433"
    return f"postgresql+psycopg://mapsleads:{pw}@127.0.0.1:{port}/mapsleads"


DATABASE_URL = _database_url()
LOGIN_USERNAME = "hello@appointhive.com"
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
SESSION_COOKIE = "mlf_session"
SESSION_DAYS = 14
# Mobile / Bearer tokens (ACCESS_TOKEN_SECRET falls back to SESSION_SECRET)
ACCESS_TOKEN_SECRET = (os.getenv("ACCESS_TOKEN_SECRET") or SESSION_SECRET).strip()
ACCESS_TOKEN_MINUTES = int((os.getenv("ACCESS_TOKEN_MINUTES") or "30").strip() or "30")
REFRESH_TOKEN_DAYS = int((os.getenv("REFRESH_TOKEN_DAYS") or "30").strip() or "30")
ENABLE_API_DOCS = (os.getenv("ENABLE_API_DOCS") or "true").strip().lower() in ("1", "true", "yes")

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

# Early stages mirror Contact queue tabs:
#   to_contact ↔ To contact
#   ready_to_contact ↔ Ready to contact
#   contacted ↔ Contacted
# Unmarked discovers stay on identified ("Discovered") and are Discover-only (hidden on board).
STANDALONE_STAGES = [
    ("identified", "Discovered", 0.05, False),
    ("to_contact", "To contact", 0.08, False),
    ("ready_to_contact", "Ready to contact", 0.10, False),
    ("contacted", "Contacted", 0.15, False),
    ("follow_up_3", "Follow-up +3", 0.20, False),
    ("follow_up_7", "Follow-up +7", 0.22, False),
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
    ("identified", "Discovered", 0.05, False),
    ("to_contact", "To contact", 0.08, False),
    ("ready_to_contact", "Ready to contact", 0.10, False),
    ("contacted", "Contacted", 0.15, False),
    ("follow_up_3", "Follow-up +3", 0.20, False),
    ("follow_up_7", "Follow-up +7", 0.22, False),
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

# Legacy slug kept inactive after seed migration (prospects remapped to follow_up_3).
LEGACY_FOLLOW_UP_SLUG = "follow_up"

# Pipeline board shows all active stages including Discovered (identified)
# so every column can render business cards. Empty columns still appear.
PIPELINE_BOARD_HIDDEN_SLUGS = frozenset()

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
