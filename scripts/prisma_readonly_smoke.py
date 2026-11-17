#!/usr/bin/env python3
"""LIVE READ-ONLY HTTP smoke against Prisma Postgres via FastAPI TestClient.

Safety contract (do not expand without re-audit):
- DATABASE_URL must be supplied explicitly in the process environment (no .env fallback).
- PRISMA_EXPECTED_HOST must be supplied; preflight verifies hostname + remote PG17/postgres.
- APP_STARTUP_DATA_MUTATIONS=false and APP_SKIP_SCHEMA_INIT=true before any app import.
- External HTTP guards (requests + urllib) install before application import and stay active
  through TestClient requests; install failure aborts before import.
- Identity + baseline are captured on a standalone engine before importing app.main.
- Only the allowlisted GET paths below are called (proven read-only in prior audit).
- No POST/PUT/PATCH/DELETE; no /api/login; no unsafe GETs (templates, pipeline,
  ranking-modes, contact-queue, cron, BE outbound).
- Auth is dependency-overridden (no credential login).
- External HTTP guards are mandatory; any attempted outbound HTTP fails the smoke.
- Before/after public table names, row counts, and migration history must match exactly.

Usage:
  APP_STARTUP_DATA_MUTATIONS=false \\
  APP_SKIP_SCHEMA_INIT=true \\
  DATABASE_URL=... \\
  PRISMA_EXPECTED_HOST=... \\
  .venv/bin/python scripts/prisma_readonly_smoke.py

Never prints DATABASE_URL, passwords, tokens, query parameters, or full business records.
Exit 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_DATABASE_NAME = "postgres"
EXPECTED_PG_MAJOR = 17
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})

UNSAFE_BLOCKLIST = frozenset(
    {
        "/api/templates",
        "/api/sales/pipeline",
        "/api/sales/ranking-modes",
        "/api/sales/contact-queue",
        "/api/sales/contact-queue/meta",
    }
)

# Populated after successful preflight; used so post-HTTP checks hit the same target.
_VERIFIED_DATABASE_URL: Optional[str] = None


@dataclass(frozen=True)
class SafeDbIdentity:
    """Safe-to-print identity fields only (no credentials / query string)."""

    hostname: str
    database: str
    server_version: str
    server_version_major: int
    inet_server_addr: str
    inet_server_port: int


@dataclass
class DbBaseline:
    table_names: tuple[str, ...]
    row_counts: dict[str, int]
    public_table_count: int
    migration_count: int
    migration_first: Optional[str]
    migration_last: Optional[str]
    migration_versions: tuple[str, ...] = field(default_factory=tuple)


def normalize_database_url(url: str) -> str:
    """Local equivalent of app.config.normalize_database_url (no app import)."""
    if url.lower().startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def require_explicit_database_url() -> str:
    """Fail closed unless DATABASE_URL is present in the process environment."""
    raw = (os.environ.get("DATABASE_URL") or "").strip()
    if not raw:
        raise RuntimeError(
            "DATABASE_URL must be set explicitly in the environment "
            "(no .env fallback for remote Prisma smoke)."
        )
    return normalize_database_url(raw)


def require_expected_prisma_host() -> str:
    host = (os.environ.get("PRISMA_EXPECTED_HOST") or "").strip()
    if not host:
        raise RuntimeError(
            "PRISMA_EXPECTED_HOST must be set to the exact expected Prisma hostname."
        )
    return host


def _parse_url_host_and_database(url: str) -> tuple[str, str]:
    """Extract hostname + database name without exposing userinfo or query."""
    # Normalize driver prefixes so urlparse yields a usable hostname.
    for prefix in (
        "postgresql+psycopg://",
        "postgresql+psycopg2://",
        "postgres+psycopg://",
    ):
        if url.lower().startswith(prefix):
            url = "postgresql://" + url[len(prefix) :]
            break
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme.startswith("sqlite"):
        raise RuntimeError("SQLite is rejected for remote Prisma smoke.")
    if scheme not in {"postgresql", "postgres"} and not scheme.startswith("postgresql"):
        raise RuntimeError(f"Unsupported database scheme for Prisma smoke: {scheme or '(empty)'}")

    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise RuntimeError("DATABASE_URL hostname is missing.")
    if hostname in LOCAL_HOSTS or hostname.endswith(".local"):
        raise RuntimeError(f"Local database host is rejected: {hostname}")

    database = unquote((parsed.path or "").lstrip("/").split("/")[0] or "").strip()
    if database != EXPECTED_DATABASE_NAME:
        raise RuntimeError(
            f"DATABASE_URL database name must be {EXPECTED_DATABASE_NAME!r}, got {database!r}."
        )
    return hostname, database


def assert_url_targets_remote_prisma(url: str, expected_host: str) -> tuple[str, str]:
    hostname, database = _parse_url_host_and_database(url)
    if hostname != expected_host.strip().lower():
        raise RuntimeError(
            f"DATABASE_URL hostname {hostname!r} does not match "
            f"PRISMA_EXPECTED_HOST {expected_host.strip().lower()!r}."
        )
    return hostname, database


def _is_local_addr(addr: Optional[str]) -> bool:
    if addr is None:
        return True
    cleaned = str(addr).strip().lower().strip("[]")
    if not cleaned:
        return True
    return cleaned in LOCAL_HOSTS


def verify_remote_identity(engine: Any, expected_host: str) -> SafeDbIdentity:
    """Query live server identity; never print credentials."""
    with engine.connect() as conn:
        database = conn.execute(text_literal("SELECT current_database()")).scalar()
        server_version = conn.execute(
            text_literal("SELECT current_setting('server_version')")
        ).scalar()
        inet_addr = conn.execute(text_literal("SELECT inet_server_addr()")).scalar()
        inet_port = conn.execute(text_literal("SELECT inet_server_port()")).scalar()

    db_name = str(database or "").strip()
    if db_name != EXPECTED_DATABASE_NAME:
        raise RuntimeError(
            f"current_database() must be {EXPECTED_DATABASE_NAME!r}, got {db_name!r}."
        )

    version_str = str(server_version or "").strip()
    major_s = version_str.split(".")[0].split(" ")[0]
    try:
        major = int(major_s)
    except ValueError as exc:
        raise RuntimeError(f"Unable to parse PostgreSQL major version from {version_str!r}.") from exc
    if major != EXPECTED_PG_MAJOR:
        raise RuntimeError(
            f"PostgreSQL major version must be {EXPECTED_PG_MAJOR}, got {major} ({version_str!r})."
        )

    addr_s = "" if inet_addr is None else str(inet_addr)
    if _is_local_addr(addr_s):
        raise RuntimeError(
            f"inet_server_addr() must be a non-local address, got {addr_s!r}."
        )

    try:
        port_i = int(inet_port)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"inet_server_port() invalid: {inet_port!r}") from exc

    host = expected_host.strip().lower()
    return SafeDbIdentity(
        hostname=host,
        database=db_name,
        server_version=version_str,
        server_version_major=major,
        inet_server_addr=addr_s,
        inet_server_port=port_i,
    )


def text_literal(sql: str):
    """Lazy sqlalchemy.text wrapper so module import does not require app packages."""
    from sqlalchemy import text

    return text(sql)


def create_standalone_engine(url: str):
    from sqlalchemy import create_engine

    return create_engine(url, pool_pre_ping=True)


def print_safe_identity(identity: SafeDbIdentity) -> None:
    print(
        "db_identity:"
        f" hostname={identity.hostname!r}"
        f" database={identity.database!r}"
        f" pg_major={identity.server_version_major}"
        f" server_version={identity.server_version!r}"
        f" inet_server_addr={identity.inet_server_addr!r}"
        f" inet_server_port={identity.inet_server_port}"
    )


def capture_baseline(engine: Any) -> DbBaseline:
    with engine.connect() as conn:
        rows = conn.execute(
            text_literal(
                "SELECT tablename FROM pg_catalog.pg_tables "
                "WHERE schemaname = 'public' ORDER BY tablename"
            )
        ).all()
        names = tuple(str(r[0]) for r in rows)
        counts: dict[str, int] = {}
        for name in names:
            n = conn.execute(text_literal(f'SELECT COUNT(*) FROM public."{name}"')).scalar()
            counts[name] = int(n or 0)

        mig_rows = conn.execute(
            text_literal("SELECT version FROM schema_migrations ORDER BY version")
        ).all()
        versions = tuple(str(r[0]) for r in mig_rows)

    return DbBaseline(
        table_names=names,
        row_counts=counts,
        public_table_count=len(names),
        migration_count=len(versions),
        migration_first=versions[0] if versions else None,
        migration_last=versions[-1] if versions else None,
        migration_versions=versions,
    )


def compare_baselines(before: DbBaseline, after: DbBaseline) -> list[str]:
    failures: list[str] = []
    if before.table_names != after.table_names:
        failures.append(
            "public table names changed: "
            f"before={list(before.table_names)!r} after={list(after.table_names)!r}"
        )
    if before.public_table_count != after.public_table_count:
        failures.append(
            f"public table count changed: {before.public_table_count} -> {after.public_table_count}"
        )
    changed = {
        name: (before.row_counts.get(name), after.row_counts.get(name))
        for name in sorted(set(before.row_counts) | set(after.row_counts))
        if before.row_counts.get(name) != after.row_counts.get(name)
    }
    if changed:
        failures.append(f"row counts changed: {json.dumps(changed)}")
    if before.migration_versions != after.migration_versions:
        failures.append(
            "migration history changed: "
            f"count {before.migration_count}->{after.migration_count}, "
            f"first {before.migration_first!r}->{after.migration_first!r}, "
            f"last {before.migration_last!r}->{after.migration_last!r}"
        )
    return failures


def print_baseline_summary(label: str, baseline: DbBaseline) -> None:
    print(
        f"{label}:"
        f" public_tables={baseline.public_table_count}"
        f" migrations={baseline.migration_count}"
        f" first={baseline.migration_first!r}"
        f" last={baseline.migration_last!r}"
    )


def mutations_flag_is_false() -> bool:
    raw = os.environ.get("APP_STARTUP_DATA_MUTATIONS")
    if raw is None or not str(raw).strip():
        return False
    return str(raw).strip().lower() in ("0", "false", "no", "off")


def schema_init_skip_flag_is_true() -> bool:
    raw = os.environ.get("APP_SKIP_SCHEMA_INIT")
    if raw is None or not str(raw).strip():
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def ensure_mutations_disabled_before_import() -> None:
    os.environ["APP_STARTUP_DATA_MUTATIONS"] = "false"
    if not mutations_flag_is_false():
        raise RuntimeError("APP_STARTUP_DATA_MUTATIONS must be false before application import.")


def ensure_schema_init_skipped_before_import() -> None:
    os.environ["APP_SKIP_SCHEMA_INIT"] = "true"
    if not schema_init_skip_flag_is_true():
        raise RuntimeError("APP_SKIP_SCHEMA_INIT must be true before application import.")


def verify_app_engine_identity(expected_host: str, expected_db: str) -> None:
    """Confirm app.db.engine targets the same remote Prisma host/db. No credential prints."""
    from app.db import engine as app_engine

    host = (app_engine.url.host or "").strip().lower()
    database = (app_engine.url.database or "").strip()
    if host != expected_host.strip().lower():
        raise RuntimeError(
            f"app.db.engine host {host!r} does not match expected Prisma host "
            f"{expected_host.strip().lower()!r}."
        )
    if database != expected_db:
        raise RuntimeError(
            f"app.db.engine database {database!r} does not match expected {expected_db!r}."
        )
    if host in LOCAL_HOSTS:
        raise RuntimeError(f"app.db.engine host is local and rejected: {host}")


def install_external_http_guard() -> tuple[list[str], Callable[[], None]]:
    """Patch requests + urllib; abort if either guard cannot be installed."""
    hits: list[str] = []
    restorers: list[Callable[[], None]] = []
    install_errors: list[str] = []

    try:
        import requests

        orig_request = requests.sessions.Session.request

        def blocked(self, method, url, *args, **kwargs):  # type: ignore[no-untyped-def]
            hits.append(f"{method} {url}")
            raise RuntimeError(f"external HTTP blocked during readonly smoke: {method} {url}")

        requests.sessions.Session.request = blocked  # type: ignore[method-assign]

        def restore_requests() -> None:
            requests.sessions.Session.request = orig_request  # type: ignore[method-assign]

        restorers.append(restore_requests)
    except Exception as exc:  # noqa: BLE001 — must surface install failure
        install_errors.append(f"requests:{type(exc).__name__}:{exc}")

    try:
        import urllib.request as urllib_request

        orig_urlopen = urllib_request.urlopen

        def blocked_urlopen(url, *args, **kwargs):  # type: ignore[no-untyped-def]
            hits.append(f"urlopen {url}")
            raise RuntimeError(f"external HTTP blocked during readonly smoke: urlopen {url}")

        urllib_request.urlopen = blocked_urlopen  # type: ignore[assignment]

        def restore_urlopen() -> None:
            urllib_request.urlopen = orig_urlopen  # type: ignore[assignment]

        restorers.append(restore_urlopen)
    except Exception as exc:  # noqa: BLE001 — must surface install failure
        install_errors.append(f"urllib:{type(exc).__name__}:{exc}")

    if install_errors or len(restorers) < 2:
        for fn in restorers:
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        raise RuntimeError(
            "external HTTP guard installation failed; aborting before application import: "
            + "; ".join(install_errors or ["incomplete guard set"])
        )

    def restore_all() -> None:
        for fn in restorers:
            fn()

    return hits, restore_all


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def _expect_status(r, codes: set[int], path: str) -> None:
    if r.status_code not in codes:
        _fail(f"{path}: unexpected status {r.status_code} (want {sorted(codes)})")


def _ctype(r) -> str:
    return (r.headers.get("content-type") or "").split(";")[0].strip().lower()


def validate_home(r, _bid: int) -> None:
    _expect_status(r, {200}, "/")
    if "text/html" not in _ctype(r):
        _fail(f"/: expected text/html, got {_ctype(r)!r}")
    if len(r.text or "") < 50:
        _fail("/: HTML body too short")


def validate_health(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/health")
    if "application/json" not in _ctype(r):
        _fail("/api/health: expected JSON")
    body = r.json()
    if body.get("db_ready") is not True:
        _fail(f"/api/health: db_ready={body.get('db_ready')!r}")
    if body.get("service") != "appointhive-sales-os":
        _fail("/api/health: missing service marker")


def validate_me(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/me")
    body = r.json()
    for key in ("username", "role"):
        if key not in body:
            _fail(f"/api/me: missing key {key}")
    if not str(body.get("username") or "").strip():
        _fail("/api/me: empty username")


def validate_migrations(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/migrations")
    body = r.json()
    if "applied" not in body or "pending" not in body:
        _fail("/api/migrations: expected applied/pending")
    applied = body.get("applied") or []
    if len(applied) < 19:
        _fail(f"/api/migrations: expected >=19 applied, got {len(applied)}")


def validate_locations(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/locations")
    body = r.json()
    if "countries" not in body:
        _fail("/api/locations: missing countries")


def validate_businesses(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/businesses")
    body = r.json()
    if "count" not in body or "results" not in body:
        _fail("/api/businesses: expected count/results")
    if not isinstance(body["results"], list):
        _fail("/api/businesses: results not a list")


def validate_stats(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/stats")
    body = r.json()
    if not isinstance(body, dict) or not body:
        _fail("/api/stats: empty/non-object")


def validate_categories(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/categories")
    body = r.json()
    if "categories" not in body or not isinstance(body["categories"], list):
        _fail("/api/categories: expected categories list")


def validate_dashboard(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/sales/dashboard")
    body = r.json()
    for key in ("queues", "pipeline"):
        if key not in body:
            _fail(f"/api/sales/dashboard: missing {key}")


def validate_dashboard_queues(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/sales/dashboard/queues")
    body = r.json()
    if "queues" not in body:
        _fail("/api/sales/dashboard/queues: missing queues")


def validate_stages(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/sales/stages")
    body = r.json()
    if "stages" not in body or not isinstance(body["stages"], list):
        _fail("/api/sales/stages: expected stages list")


def validate_settings(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/sales/settings")
    body = r.json()
    if "settings" not in body or not isinstance(body["settings"], dict):
        _fail("/api/sales/settings: expected settings object")


def validate_providers(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/sales/providers")
    body = r.json()
    if "manual" not in body:
        _fail("/api/sales/providers: expected manual provider entry")


def validate_prospects(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/sales/prospects")
    body = r.json()
    if "prospects" not in body or not isinstance(body["prospects"], list):
        _fail("/api/sales/prospects: expected prospects list")


def validate_plans(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/sales/plans")
    body = r.json()
    if "plans" not in body or not isinstance(body["plans"], list):
        _fail("/api/sales/plans: expected plans list")


def validate_email_signature(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/sales/email-signature")
    body = r.json()
    if "text" not in body:
        _fail("/api/sales/email-signature: missing text")


def validate_sales_me(r, _bid: int) -> None:
    _expect_status(r, {200}, "/api/sales/me")
    body = r.json()
    if "username" not in body:
        _fail("/api/sales/me: missing username")


def validate_activity(r, _bid: int) -> None:
    path = f"/api/businesses/{_bid}/activity"
    _expect_status(r, {200}, path)
    body = r.json()
    if not isinstance(body, dict) or "count" not in body or "results" not in body:
        _fail(f"{path}: expected {{count, results}} object")
    if not isinstance(body["results"], list):
        _fail(f"{path}: results must be a list")
    if int(body["count"]) != len(body["results"]):
        _fail(f"{path}: count/len(results) mismatch")


ALLOWLIST: list[tuple[str, Callable[[Any, int], None]]] = [
    ("/", validate_home),
    ("/api/health", validate_health),
    ("/api/me", validate_me),
    ("/api/migrations", validate_migrations),
    ("/api/locations", validate_locations),
    ("/api/businesses", validate_businesses),
    ("/api/stats", validate_stats),
    ("/api/categories", validate_categories),
    ("/api/sales/dashboard", validate_dashboard),
    ("/api/sales/dashboard/queues", validate_dashboard_queues),
    ("/api/sales/stages", validate_stages),
    ("/api/sales/settings", validate_settings),
    ("/api/sales/providers", validate_providers),
    ("/api/sales/prospects", validate_prospects),
    ("/api/sales/plans", validate_plans),
    ("/api/sales/email-signature", validate_email_signature),
    ("/api/sales/me", validate_sales_me),
]


def pick_identity_and_business(engine: Any) -> tuple[str, int]:
    with engine.connect() as conn:
        email = conn.execute(
            text_literal(
                "SELECT email FROM public.users "
                "WHERE active IS TRUE AND email IS NOT NULL AND email <> '' "
                "ORDER BY id ASC LIMIT 1"
            )
        ).scalar()
        if not email:
            email = conn.execute(
                text_literal(
                    "SELECT email FROM public.users "
                    "WHERE email IS NOT NULL AND email <> '' "
                    "ORDER BY id ASC LIMIT 1"
                )
            ).scalar()
        bid = conn.execute(
            text_literal("SELECT id FROM public.businesses ORDER BY id ASC LIMIT 1")
        ).scalar()
    if not email:
        raise RuntimeError("No users.email available for auth override identity")
    if bid is None:
        raise RuntimeError("No businesses.id available for activity smoke")
    return str(email).strip().lower(), int(bid)


def import_application():
    """Import FastAPI app only after preflight + baseline + HTTP guards.

    Mutations must already be false; schema init must already be skipped.
    """
    ensure_mutations_disabled_before_import()
    ensure_schema_init_skipped_before_import()
    from fastapi.testclient import TestClient  # noqa: WPS433

    from app.config import schema_init_skipped, startup_data_mutations_enabled  # noqa: WPS433
    from app.main import app, require_login  # noqa: WPS433
    from app.routers import ops as ops_router  # noqa: WPS433
    from app.routers import sales as sales_router  # noqa: WPS433

    if startup_data_mutations_enabled():
        raise RuntimeError("startup_data_mutations_enabled() is True after import; refuse to proceed.")
    if not schema_init_skipped():
        raise RuntimeError("schema_init_skipped() is False after import; refuse to proceed.")
    return TestClient, app, require_login, ops_router, sales_router


def run_preflight() -> tuple[Any, SafeDbIdentity, DbBaseline, str, str]:
    """
    Fail-closed identity + baseline capture BEFORE any application import.

    Returns (engine, identity, baseline, expected_host, database_url).
    """
    global _VERIFIED_DATABASE_URL

    ensure_mutations_disabled_before_import()
    ensure_schema_init_skipped_before_import()
    if not mutations_flag_is_false():
        raise RuntimeError("APP_STARTUP_DATA_MUTATIONS is not false before preflight.")
    if not schema_init_skip_flag_is_true():
        raise RuntimeError("APP_SKIP_SCHEMA_INIT is not true before preflight.")

    expected_host = require_expected_prisma_host()
    url = require_explicit_database_url()
    assert_url_targets_remote_prisma(url, expected_host)

    engine = create_standalone_engine(url)
    try:
        identity = verify_remote_identity(engine, expected_host)
        print_safe_identity(identity)
        baseline = capture_baseline(engine)
        print_baseline_summary("baseline_before_app_import", baseline)
    except Exception:
        engine.dispose()
        raise

    _VERIFIED_DATABASE_URL = url
    return engine, identity, baseline, expected_host, url


def main() -> int:
    sys.path.insert(0, str(ROOT))
    ensure_mutations_disabled_before_import()
    ensure_schema_init_skipped_before_import()
    print(
        "prisma_readonly_smoke: mutations="
        f"{os.environ.get('APP_STARTUP_DATA_MUTATIONS')!r} "
        f"skip_schema_init={os.environ.get('APP_SKIP_SCHEMA_INIT')!r} "
        "(DATABASE_URL/PRISMA_EXPECTED_HOST required; values not printed)"
    )

    for path, _ in ALLOWLIST:
        if path in UNSAFE_BLOCKLIST or path.startswith("/api/cron") or "/be/" in path:
            print(f"FAIL: allowlist contains blocked path {path}")
            return 1

    engine = None
    app_engine_ref = None
    client: Optional[Any] = None
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    external_hits: list[str] = []
    restore_http: Callable[[], None] = lambda: None
    before: Optional[DbBaseline] = None
    identity_email = ""
    business_id = 0
    app = None

    try:
        engine, identity, before, expected_host, _url = run_preflight()
        # Guards before any application import (covers import-time outbound HTTP).
        external_hits, restore_http = install_external_http_guard()
        # Application import happens only after identity + baseline + HTTP guards.
        TestClient, app, require_login, ops_router, sales_router = import_application()
        print(
            "check: APP_STARTUP_DATA_MUTATIONS=false "
            "APP_SKIP_SCHEMA_INIT=true after app import"
        )

        try:
            verify_app_engine_identity(expected_host, identity.database)
            print(
                f"check: app.db.engine host/db match "
                f"hostname={expected_host!r} database={identity.database!r}"
            )
        except Exception:
            try:
                from app.db import engine as app_engine

                app_engine.dispose()
            except Exception:  # noqa: BLE001
                pass
            engine.dispose()
            raise

        from app.db import engine as app_engine

        app_engine_ref = app_engine

        identity_email, business_id = pick_identity_and_business(engine)
        print(
            "auth override identity: email_present=true "
            f"role_lookup=users.active business_id={business_id}"
        )

        def override_auth() -> str:
            return identity_email

        app.dependency_overrides[require_login] = override_auth
        app.dependency_overrides[sales_router.require_user] = override_auth
        app.dependency_overrides[ops_router.require_user] = override_auth

        client = TestClient(app)
        client.raise_server_exceptions = True

        paths = list(ALLOWLIST)
        paths.append((f"/api/businesses/{business_id}/activity", validate_activity))

        for path, validator in paths:
            if any(path == b or path.startswith(b + "/") for b in UNSAFE_BLOCKLIST):
                failures.append(f"refused unsafe path {path}")
                continue
            if path.startswith("/api/cron") or "/be/" in path:
                failures.append(f"refused blocked family path {path}")
                continue
            try:
                resp = client.get(path)
                validator(resp, business_id)
                results.append(
                    {
                        "path": path,
                        "status": resp.status_code,
                        "content_type": _ctype(resp),
                        "ok": True,
                    }
                )
                print(f"OK  {resp.status_code} {_ctype(resp):20} {path}")
            except Exception as exc:  # noqa: BLE001 — collect per-path failures
                failures.append(f"{path}: {exc}")
                results.append(
                    {
                        "path": path,
                        "status": getattr(getattr(exc, "response", None), "status_code", None),
                        "content_type": "",
                        "ok": False,
                        "error": str(exc)[:200],
                    }
                )
                print(f"ERR {path}: {exc}")

        # Reconnect to the same verified target for post-HTTP comparison.
        after = capture_baseline(engine)
        print_baseline_summary("baseline_after_http", after)
        failures.extend(compare_baselines(before, after))

        if external_hits:
            failures.append(f"external HTTP attempted: {external_hits[:5]}")

    except Exception as exc:  # noqa: BLE001
        failures.append(f"fatal: {exc}")
        traceback.print_exc()
    finally:
        restore_http()
        if app is not None:
            try:
                app.dependency_overrides.clear()
            except Exception:  # noqa: BLE001
                pass
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        if engine is not None:
            try:
                engine.dispose()
            except Exception:  # noqa: BLE001
                pass
        if app_engine_ref is not None:
            try:
                app_engine_ref.dispose()
            except Exception:  # noqa: BLE001
                pass

    print("--- summary ---")
    print(f"endpoints_ok={sum(1 for r in results if r.get('ok'))}/{len(results)}")
    print(f"failures={len(failures)} external_hits={len(external_hits)}")
    for f in failures:
        print(f"FAIL: {f}")

    if failures:
        print("VERDICT: FAIL")
        return 1
    print("VERDICT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
