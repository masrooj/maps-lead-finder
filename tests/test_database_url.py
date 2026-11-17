"""DATABASE_URL scheme normalization for SQLAlchemy (no DB connection)."""
from __future__ import annotations

from app.config import normalize_database_url


def test_normalize_postgres_scheme_to_postgresql():
    raw = "postgres://user:secret@db.example:5432/app?sslmode=require"
    out = normalize_database_url(raw)
    assert out.startswith("postgresql://")
    assert out == "postgresql://user:secret@db.example:5432/app?sslmode=require"


def test_normalize_leaves_postgresql_unchanged():
    raw = "postgresql://user:secret@db.example/app"
    assert normalize_database_url(raw) == raw


def test_normalize_leaves_psycopg_driver_url_unchanged():
    raw = "postgresql+psycopg://mapsleads:x@127.0.0.1:5433/mapsleads"
    assert normalize_database_url(raw) == raw


def test_normalize_is_case_insensitive_on_scheme():
    raw = "Postgres://User:Secret@Host/Db"
    assert normalize_database_url(raw) == "postgresql://User:Secret@Host/Db"
