"""Shared pytest fixtures — Postgres mapsleads_test with fast, correct isolation.

Session: full truncate + seed (clean slate each process).
Per test: truncate mutable data only; keep catalog (stages/plans/settings) and re-seed
idempotent owner/ranking — matches ephemeral-SQLite isolation without ~13min TRUNCATE tax.
"""
from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

# Tables tests mutate heavily. Catalog (pipeline_stages, plans, …) kept across tests.
_MUTABLE_TABLES = (
    "payments",
    "invoices",
    "proposals",
    "be_deliveries",
    "activities",
    "activity_log",
    "stage_history",
    "tasks",
    "contacts",
    "discovery_notes",
    "demo_notes",
    "demo_assets",
    "webhook_events",
    "jobs",
    "audit_events",
    "suppressions",
    "saved_views",
    "message_templates",
    "businesses",
    "sales_territories",
    "contact_queue_modes",
)


def _load_dotenv() -> None:
    env_path = _REPO_ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip())


def _ensure_postgres_test_db() -> str:
    """Create mapsleads_test if needed; return its DATABASE_URL."""
    _load_dotenv()
    pw = (os.getenv("POSTGRES_PASSWORD") or "change-this-password").strip()
    port = (os.getenv("DB_PORT") or "5433").strip() or "5433"
    host = (os.getenv("POSTGRES_HOST") or "127.0.0.1").strip()
    user = (os.getenv("POSTGRES_USER") or "mapsleads").strip()
    test_db = (os.getenv("POSTGRES_TEST_DB") or "mapsleads_test").strip()
    test_url = f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{test_db}"

    explicit = (os.getenv("TEST_DATABASE_URL") or "").strip()
    if explicit:
        if explicit.lower().startswith("sqlite:"):
            raise RuntimeError("SQLite is disabled for tests — set TEST_DATABASE_URL to Postgres")
        return explicit

    from sqlalchemy import create_engine, text

    admin_url = f"postgresql+psycopg://{user}:{pw}@{host}:{port}/mapsleads"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": test_db},
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{test_db}"'))
    finally:
        admin.dispose()
    return test_url


# Must run before any app imports.
os.environ["DATABASE_URL"] = _ensure_postgres_test_db()
os.environ["LOGIN_PASSWORD"] = "test-password-sales-os"
os.environ["SESSION_SECRET"] = "test-session-secret-sales-os-32chars"
os.environ["DISCOVER_SOCIAL_LINKS"] = "false"
os.environ["CRON_SECRET"] = "cron-test-secret"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import LOGIN_USERNAME
from app.main import app
from app.db import SessionLocal, engine
from app.services.seed import seed_owner, seed_sales_os
from app.services.pipeline import backfill_prospect_stages


def frontend_source(html: str = "") -> str:
    """HTML shell + static JS/CSS — markers moved out of index.html in SOLID extract."""
    parts = [html or ""]
    static = _REPO_ROOT / "app" / "static"
    for pattern in ("js/**/*.js", "css/**/*.css"):
        for path in sorted(static.glob(pattern)):
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def _truncate_all_except_migrations() -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                DO $$
                DECLARE r RECORD;
                BEGIN
                  FOR r IN (
                    SELECT tablename FROM pg_tables
                    WHERE schemaname = 'public'
                      AND tablename <> 'schema_migrations'
                  ) LOOP
                    EXECUTE 'TRUNCATE TABLE ' || quote_ident(r.tablename) || ' CASCADE';
                  END LOOP;
                END $$;
                """
            )
        )


def _truncate_mutable() -> None:
    """Clear prospect/money/job rows; keep stages/plans/settings for speed."""
    with engine.begin() as conn:
        present = []
        for name in _MUTABLE_TABLES:
            reg = conn.execute(
                text("SELECT to_regclass(:q)"),
                {"q": f"public.{name}"},
            ).scalar()
            if reg:
                present.append(name)
        if present:
            conn.execute(
                text(
                    "TRUNCATE TABLE "
                    + ", ".join(present)
                    + " RESTART IDENTITY CASCADE"
                )
            )
        conn.execute(
            text("DELETE FROM users WHERE lower(email) <> lower(:owner)"),
            {"owner": LOGIN_USERNAME},
        )


def _reseed_full() -> None:
    with SessionLocal() as db:
        seed_sales_os(db)
        try:
            backfill_prospect_stages(db)
        except Exception:
            pass


def _reseed_light() -> None:
    """Owner + ranking modes only (catalog already present from session seed)."""
    with SessionLocal() as db:
        seed_owner(db)
        try:
            from app.services.ranking_modes import seed_ranking_modes

            seed_ranking_modes(db)
        except Exception:
            pass
        db.commit()


def _reset_full() -> None:
    _truncate_all_except_migrations()
    _reseed_full()


def _reset_fast() -> None:
    _truncate_mutable()
    _reseed_light()


@pytest.fixture(scope="session")
def client():
    _reset_full()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_db(client):
    """Per-test data isolation (mutable truncate + light reseed).

    Catalog tables (stages/plans/settings) stay warm from the session seed so we
    avoid the full-catalog TRUNCATE that made the suite ~13–16 minutes on a
    nearly-full disk. Correctness target: same as ephemeral SQLite per process.
    """
    _reset_fast()
    yield


@pytest.fixture
def auth_client(client):
    client.post("/api/logout")
    client.cookies.clear()
    r = client.post(
        "/api/login",
        json={"username": "hello@appointhive.com", "password": "test-password-sales-os"},
    )
    assert r.status_code == 200, r.text
    return client
