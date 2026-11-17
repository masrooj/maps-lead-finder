"""Unit tests for scripts/prisma_readonly_smoke.py (mocked; no DB or network)."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.no_db

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prisma_readonly_smoke.py"


def _load_smoke():
    """Load smoke script as a module without executing __main__."""
    name = "prisma_readonly_smoke_under_test"
    # Fresh load each time so env-side effects in helpers stay isolated.
    if name in sys.modules:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def smoke(monkeypatch):
    monkeypatch.setenv("APP_STARTUP_DATA_MUTATIONS", "false")
    monkeypatch.setenv("APP_SKIP_SCHEMA_INIT", "true")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PRISMA_EXPECTED_HOST", raising=False)
    return _load_smoke()


def _fake_preflight_tuple(smoke, url="postgresql://u:p@db.prisma.io:5432/postgres"):
    return (
        MagicMock(),
        smoke.SafeDbIdentity(
            hostname="db.prisma.io",
            database="postgres",
            server_version="17.0",
            server_version_major=17,
            inet_server_addr="203.0.113.10",
            inet_server_port=5432,
        ),
        smoke.DbBaseline(
            table_names=("users",),
            row_counts={"users": 1},
            public_table_count=1,
            migration_count=1,
            migration_first="001.sql",
            migration_last="001.sql",
            migration_versions=("001.sql",),
        ),
        "db.prisma.io",
        url,
    )


def test_missing_database_url_fails_closed(smoke, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL must be set explicitly"):
        smoke.require_explicit_database_url()


def test_localhost_database_url_is_rejected(smoke, monkeypatch):
    monkeypatch.setenv("PRISMA_EXPECTED_HOST", "db.prisma.io")
    for url in (
        "postgresql://u:p@localhost:5432/postgres",
        "postgresql://u:p@127.0.0.1:5432/postgres",
        "postgresql://u:p@[::1]:5432/postgres",
        "postgresql+psycopg://u:p@127.0.0.1:5432/postgres",
    ):
        with pytest.raises(RuntimeError, match="Local database host is rejected"):
            smoke.assert_url_targets_remote_prisma(url, "db.prisma.io")


def test_sqlite_url_is_rejected(smoke):
    with pytest.raises(RuntimeError, match="SQLite"):
        smoke._parse_url_host_and_database("sqlite:///tmp/x.db")


def test_wrong_database_name_is_rejected(smoke):
    with pytest.raises(RuntimeError, match="database name must be 'postgres'"):
        smoke.assert_url_targets_remote_prisma(
            "postgresql://u:p@db.prisma.io:5432/mapsleads",
            "db.prisma.io",
        )


def test_missing_prisma_expected_host_is_rejected(smoke, monkeypatch):
    monkeypatch.delenv("PRISMA_EXPECTED_HOST", raising=False)
    with pytest.raises(RuntimeError, match="PRISMA_EXPECTED_HOST must be set"):
        smoke.require_expected_prisma_host()


def test_mismatched_prisma_hostname_is_rejected(smoke):
    with pytest.raises(RuntimeError, match="does not match"):
        smoke.assert_url_targets_remote_prisma(
            "postgresql://u:p@other.example.com:5432/postgres",
            "db.prisma.io",
        )


def test_wrong_postgresql_major_version_is_rejected(smoke):
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = None

    def _scalar_seq():
        # current_database, server_version, inet_server_addr, inet_server_port
        yield "postgres"
        yield "16.4"
        yield "203.0.113.10"
        yield 5432

    seq = _scalar_seq()
    conn.execute.return_value.scalar.side_effect = lambda: next(seq)

    with pytest.raises(RuntimeError, match="PostgreSQL major version must be 17"):
        smoke.verify_remote_identity(engine, "db.prisma.io")


def test_local_inet_server_addr_is_rejected(smoke):
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = None

    values = iter(["postgres", "17.2", "127.0.0.1", 5432])
    conn.execute.return_value.scalar.side_effect = lambda: next(values)

    with pytest.raises(RuntimeError, match="non-local address"):
        smoke.verify_remote_identity(engine, "db.prisma.io")


def test_verify_remote_identity_accepts_pg17_remote(smoke):
    engine = MagicMock()
    conn = MagicMock()
    engine.connect.return_value.__enter__.return_value = conn
    engine.connect.return_value.__exit__.return_value = None

    values = iter(["postgres", "17.2 (Debian)", "203.0.113.10", 5432])
    conn.execute.return_value.scalar.side_effect = lambda: next(values)

    identity = smoke.verify_remote_identity(engine, "db.prisma.io")
    assert identity.database == "postgres"
    assert identity.server_version_major == 17
    assert identity.hostname == "db.prisma.io"
    assert identity.inet_server_addr == "203.0.113.10"


def test_identity_verification_happens_before_application_import(smoke, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.prisma.io:5432/postgres")
    monkeypatch.setenv("PRISMA_EXPECTED_HOST", "db.prisma.io")
    monkeypatch.setenv("APP_STARTUP_DATA_MUTATIONS", "false")
    monkeypatch.setenv("APP_SKIP_SCHEMA_INIT", "true")

    order: list[str] = []

    def fake_preflight():
        order.append("preflight")
        return _fake_preflight_tuple(smoke, os.environ["DATABASE_URL"])

    def fake_guard():
        order.append("http_guard")
        return [], lambda: None

    def fake_import_application():
        order.append("import_application")
        raise RuntimeError("stop-after-import-order-check")

    monkeypatch.setattr(smoke, "run_preflight", fake_preflight)
    monkeypatch.setattr(smoke, "install_external_http_guard", fake_guard)
    monkeypatch.setattr(smoke, "import_application", fake_import_application)

    code = smoke.main()
    assert code == 1
    assert order == ["preflight", "http_guard", "import_application"]


def test_startup_mutation_flag_false_before_application_import(smoke, monkeypatch):
    monkeypatch.delenv("APP_STARTUP_DATA_MUTATIONS", raising=False)
    called = {"import": False}

    def fake_import():
        called["import"] = True
        assert os.environ.get("APP_STARTUP_DATA_MUTATIONS") == "false"
        assert smoke.mutations_flag_is_false() is True
        raise RuntimeError("import-blocked-for-test")

    monkeypatch.setattr(smoke, "import_application", fake_import)
    monkeypatch.setattr(smoke, "install_external_http_guard", lambda: ([], lambda: None))
    monkeypatch.setattr(
        smoke,
        "run_preflight",
        lambda: _fake_preflight_tuple(smoke),
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.prisma.io:5432/postgres")
    monkeypatch.setenv("PRISMA_EXPECTED_HOST", "db.prisma.io")

    assert smoke.main() == 1
    assert called["import"] is True
    assert os.environ.get("APP_STARTUP_DATA_MUTATIONS") == "false"


def test_schema_init_skip_flag_true_before_application_import(smoke, monkeypatch):
    monkeypatch.delenv("APP_SKIP_SCHEMA_INIT", raising=False)
    called = {"import": False}

    def fake_import():
        called["import"] = True
        assert os.environ.get("APP_SKIP_SCHEMA_INIT") == "true"
        assert smoke.schema_init_skip_flag_is_true() is True
        raise RuntimeError("import-blocked-for-schema-flag-test")

    monkeypatch.setattr(smoke, "import_application", fake_import)
    monkeypatch.setattr(smoke, "install_external_http_guard", lambda: ([], lambda: None))
    monkeypatch.setattr(smoke, "run_preflight", lambda: _fake_preflight_tuple(smoke))
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.prisma.io:5432/postgres")
    monkeypatch.setenv("PRISMA_EXPECTED_HOST", "db.prisma.io")

    assert smoke.main() == 1
    assert called["import"] is True
    assert os.environ.get("APP_SKIP_SCHEMA_INIT") == "true"


def test_http_guards_active_during_application_import(smoke, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.prisma.io:5432/postgres")
    monkeypatch.setenv("PRISMA_EXPECTED_HOST", "db.prisma.io")
    seen = {"guarded": False}

    def fake_import():
        import requests

        with pytest.raises(RuntimeError, match="external HTTP blocked"):
            requests.sessions.Session().request("GET", "https://example.com/during-import")
        seen["guarded"] = True
        raise RuntimeError("stop-after-guard-during-import-check")

    monkeypatch.setattr(smoke, "run_preflight", lambda: _fake_preflight_tuple(smoke))
    monkeypatch.setattr(smoke, "import_application", fake_import)
    # Real install_external_http_guard — must wrap import.

    assert smoke.main() == 1
    assert seen["guarded"] is True


def test_external_http_guard_install_failure_aborts_before_import(smoke, monkeypatch):
    order: list[str] = []

    def boom_guard():
        order.append("http_guard_attempt")
        raise RuntimeError(
            "external HTTP guard installation failed; aborting before application import: "
            "requests:ImportError:unavailable"
        )

    def fake_import():
        order.append("import_application")
        raise AssertionError("import must not run when guard install fails")

    monkeypatch.setattr(smoke, "run_preflight", lambda: _fake_preflight_tuple(smoke))
    monkeypatch.setattr(smoke, "install_external_http_guard", boom_guard)
    monkeypatch.setattr(smoke, "import_application", fake_import)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db.prisma.io:5432/postgres")
    monkeypatch.setenv("PRISMA_EXPECTED_HOST", "db.prisma.io")

    assert smoke.main() == 1
    assert order == ["http_guard_attempt"]


def test_external_http_guard_install_failure_aborts(smoke, monkeypatch):
    real_import = __import__

    def boom(name, *args, **kwargs):
        if name == "requests" or name.startswith("requests."):
            raise ImportError("requests unavailable for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", boom)
    with pytest.raises(RuntimeError, match="external HTTP guard installation failed"):
        smoke.install_external_http_guard()


def test_external_http_guard_records_and_blocks(smoke):
    hits, restore = smoke.install_external_http_guard()
    try:
        import requests

        with pytest.raises(RuntimeError, match="external HTTP blocked"):
            requests.sessions.Session().request("GET", "https://example.com")
        assert hits
        assert "https://example.com" in hits[0]
    finally:
        restore()


def test_unsafe_get_routes_remain_excluded(smoke):
    allow_paths = {p for p, _ in smoke.ALLOWLIST}
    for path in smoke.UNSAFE_BLOCKLIST:
        assert path not in allow_paths
    assert "/api/templates" in smoke.UNSAFE_BLOCKLIST
    assert "/api/sales/pipeline" in smoke.UNSAFE_BLOCKLIST
    assert "/api/sales/ranking-modes" in smoke.UNSAFE_BLOCKLIST
    assert "/api/sales/contact-queue" in smoke.UNSAFE_BLOCKLIST
    assert "/api/sales/contact-queue/meta" in smoke.UNSAFE_BLOCKLIST
    for path in allow_paths:
        assert not path.startswith("/api/cron")
        assert "/be/" not in path


def test_no_dotenv_fallback_helper(smoke):
    assert not hasattr(smoke, "_load_dotenv_database_url")


def test_compare_baselines_fails_on_any_difference(smoke):
    before = smoke.DbBaseline(
        table_names=("a", "b"),
        row_counts={"a": 1, "b": 2},
        public_table_count=2,
        migration_count=2,
        migration_first="001.sql",
        migration_last="002.sql",
        migration_versions=("001.sql", "002.sql"),
    )
    after_rows = smoke.DbBaseline(
        table_names=("a", "b"),
        row_counts={"a": 1, "b": 3},
        public_table_count=2,
        migration_count=2,
        migration_first="001.sql",
        migration_last="002.sql",
        migration_versions=("001.sql", "002.sql"),
    )
    fails = smoke.compare_baselines(before, after_rows)
    assert fails and "row counts changed" in fails[0]

    after_schema = smoke.DbBaseline(
        table_names=("a", "b", "c"),
        row_counts={"a": 1, "b": 2, "c": 0},
        public_table_count=3,
        migration_count=2,
        migration_first="001.sql",
        migration_last="002.sql",
        migration_versions=("001.sql", "002.sql"),
    )
    fails2 = smoke.compare_baselines(before, after_schema)
    assert any("table names changed" in f or "table count changed" in f for f in fails2)


def test_run_preflight_requires_env_before_engine(smoke, monkeypatch):
    monkeypatch.setenv("APP_STARTUP_DATA_MUTATIONS", "false")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PRISMA_EXPECTED_HOST", "db.prisma.io")
    with pytest.raises(RuntimeError, match="DATABASE_URL must be set explicitly"):
        smoke.run_preflight()


def test_verify_app_engine_identity_mismatch(smoke, monkeypatch):
    fake_engine = SimpleNamespace(
        url=SimpleNamespace(host="wrong.example.com", database="postgres"),
        dispose=MagicMock(),
    )
    app_db = ModuleType("app.db")
    app_db.engine = fake_engine
    app_pkg = ModuleType("app")
    monkeypatch.setitem(sys.modules, "app", app_pkg)
    monkeypatch.setitem(sys.modules, "app.db", app_db)
    with pytest.raises(RuntimeError, match="does not match expected Prisma host"):
        smoke.verify_app_engine_identity("db.prisma.io", "postgres")


def test_normalize_database_url_postgres_scheme(smoke):
    assert (
        smoke.normalize_database_url("postgres://u:p@host/db")
        == "postgresql://u:p@host/db"
    )


def test_guard_restores_in_finally_contract(smoke):
    """install_external_http_guard returns a restore callable that unwraps patches."""
    import urllib.request as urllib_request

    import requests

    orig_req = requests.sessions.Session.request
    orig_urlopen = urllib_request.urlopen
    hits, restore = smoke.install_external_http_guard()
    assert requests.sessions.Session.request is not orig_req
    assert urllib_request.urlopen is not orig_urlopen
    restore()
    assert requests.sessions.Session.request is orig_req
    assert urllib_request.urlopen is orig_urlopen
    assert hits == []
