"""Database engine, session, and numbered SQL migrations."""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL

_engine_options: dict = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    _engine_options["connect_args"] = {"check_same_thread": False}
else:
    _engine_options.update({"pool_size": 5, "max_overflow": 5, "pool_recycle": 300})

engine = create_engine(DATABASE_URL, **_engine_options)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


class Base(DeclarativeBase):
    pass


def sql_statements(script: str) -> list[str]:
    lines = [line for line in script.splitlines() if not line.strip().startswith("--")]
    return [part.strip() for part in "\n".join(lines).split(";") if part.strip()]


def adapt_migration_sql(statement: str) -> str:
    if engine.dialect.name != "sqlite":
        return statement
    return re.sub(r"(?i)\bADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\b", "ADD COLUMN", statement)


def apply_migrations() -> None:
    MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version VARCHAR(255) PRIMARY KEY, "
                "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        applied = {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            for statement in sql_statements(path.read_text(encoding="utf-8")):
                sql = adapt_migration_sql(statement)
                try:
                    conn.execute(text(sql))
                except Exception as e:
                    msg = str(e).lower()
                    if (
                        "duplicate column" in msg
                        or "already exists" in msg
                        or "no such table" in msg
                        or "does not exist" in msg
                    ):
                        continue
                    raise
            conn.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                {"version": path.name},
            )


def migration_status() -> dict:
    files = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version, applied_at FROM schema_migrations ORDER BY version")).all()
    applied = []
    for version, applied_at in rows:
        if applied_at is None:
            stamp = ""
        elif hasattr(applied_at, "isoformat"):
            stamp = applied_at.isoformat()
        else:
            stamp = str(applied_at)
        applied.append({"version": version, "applied_at": stamp})
    applied_names = {item["version"] for item in applied}
    return {"applied": applied, "pending": [name for name in files if name not in applied_names]}
