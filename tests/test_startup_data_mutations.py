"""APP_STARTUP_DATA_MUTATIONS / APP_SKIP_SCHEMA_INIT safety switches (no real database)."""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.no_db

import app.config as config
from app.boot_data import (
    run_boot_schema_init,
    run_post_schema_startup_mutations,
    run_startup_template_seed,
    startup_mutations_allowed,
)


def test_startup_data_mutations_enabled_defaults_true_when_unset(monkeypatch):
    monkeypatch.delenv("APP_STARTUP_DATA_MUTATIONS", raising=False)
    assert config.startup_data_mutations_enabled() is True


def test_startup_data_mutations_enabled_false_for_explicit_off(monkeypatch):
    for value in ("false", "False", "0", "no", "off", " OFF "):
        monkeypatch.setenv("APP_STARTUP_DATA_MUTATIONS", value)
        assert config.startup_data_mutations_enabled() is False, value


def test_startup_data_mutations_enabled_true_for_explicit_on(monkeypatch):
    for value in ("true", "1", "yes", "on"):
        monkeypatch.setenv("APP_STARTUP_DATA_MUTATIONS", value)
        assert config.startup_data_mutations_enabled() is True, value


def test_schema_init_skipped_defaults_false_when_unset(monkeypatch):
    monkeypatch.delenv("APP_SKIP_SCHEMA_INIT", raising=False)
    assert config.schema_init_skipped() is False


def test_schema_init_skipped_true_only_when_explicitly_requested(monkeypatch):
    for value in ("true", "True", "1", "yes", "on", " ON "):
        monkeypatch.setenv("APP_SKIP_SCHEMA_INIT", value)
        assert config.schema_init_skipped() is True, value
    for value in ("false", "0", "no", "off", ""):
        monkeypatch.setenv("APP_SKIP_SCHEMA_INIT", value)
        assert config.schema_init_skipped() is False, value


def test_boot_schema_init_runs_ddl_by_default(monkeypatch):
    monkeypatch.delenv("APP_SKIP_SCHEMA_INIT", raising=False)
    create_all = MagicMock()
    apply_migrations = MagicMock()
    mark_ok = MagicMock()
    probe = MagicMock()

    ran = run_boot_schema_init(
        create_all_fn=create_all,
        apply_migrations_fn=apply_migrations,
        mark_ok_fn=mark_ok,
        readonly_probe_fn=probe,
    )
    assert ran is True
    create_all.assert_called_once()
    apply_migrations.assert_called_once()
    mark_ok.assert_called_once()
    probe.assert_not_called()


def test_boot_schema_init_skips_ddl_when_explicitly_requested(monkeypatch):
    monkeypatch.setenv("APP_SKIP_SCHEMA_INIT", "true")
    create_all = MagicMock()
    apply_migrations = MagicMock()
    mark_ok = MagicMock()
    probe = MagicMock()

    ran = run_boot_schema_init(
        create_all_fn=create_all,
        apply_migrations_fn=apply_migrations,
        mark_ok_fn=mark_ok,
        readonly_probe_fn=probe,
    )
    assert ran is False
    create_all.assert_not_called()
    apply_migrations.assert_not_called()
    probe.assert_called_once()
    mark_ok.assert_called_once()


def test_startup_mutations_allowed_requires_db_ready_and_flag(monkeypatch):
    monkeypatch.setenv("APP_STARTUP_DATA_MUTATIONS", "true")
    assert startup_mutations_allowed(db_ready=True) is True
    assert startup_mutations_allowed(db_ready=False) is False
    monkeypatch.setenv("APP_STARTUP_DATA_MUTATIONS", "false")
    assert startup_mutations_allowed(db_ready=True) is False


def test_post_schema_mutations_skipped_when_safety_off(monkeypatch):
    monkeypatch.setenv("APP_STARTUP_DATA_MUTATIONS", "false")
    seed = MagicMock()
    backfill = MagicMock()
    promote = MagicMock()
    session_factory = MagicMock()

    ran = run_post_schema_startup_mutations(
        db_ready=True,
        session_factory=session_factory,
        seed_sales_os_fn=seed,
        backfill_fn=backfill,
        promote_fn=promote,
    )
    assert ran is False
    session_factory.assert_not_called()
    seed.assert_not_called()
    backfill.assert_not_called()
    promote.assert_not_called()


def test_post_schema_mutations_run_when_enabled(monkeypatch):
    monkeypatch.setenv("APP_STARTUP_DATA_MUTATIONS", "true")
    db = MagicMock()
    seed = MagicMock()
    backfill = MagicMock()
    promote = MagicMock()

    @contextmanager
    def session_factory():
        yield db

    ran = run_post_schema_startup_mutations(
        db_ready=True,
        session_factory=session_factory,
        seed_sales_os_fn=seed,
        backfill_fn=backfill,
        promote_fn=promote,
    )
    assert ran is True
    seed.assert_called_once_with(db)
    backfill.assert_called_once_with(db)
    promote.assert_called_once_with(db)
    db.commit.assert_called_once()


def test_post_schema_mutations_skip_when_db_not_ready(monkeypatch):
    monkeypatch.delenv("APP_STARTUP_DATA_MUTATIONS", raising=False)
    seed = MagicMock()
    ran = run_post_schema_startup_mutations(
        db_ready=False,
        session_factory=MagicMock(),
        seed_sales_os_fn=seed,
        backfill_fn=MagicMock(),
        promote_fn=MagicMock(),
    )
    assert ran is False
    seed.assert_not_called()


def test_template_seed_skipped_when_safety_off(monkeypatch):
    monkeypatch.setenv("APP_STARTUP_DATA_MUTATIONS", "false")
    seed_tpl = MagicMock()
    ran = run_startup_template_seed(
        db_ready=True,
        session_factory=MagicMock(),
        seed_templates_fn=seed_tpl,
    )
    assert ran is False
    seed_tpl.assert_not_called()


def test_template_seed_runs_when_enabled(monkeypatch):
    monkeypatch.delenv("APP_STARTUP_DATA_MUTATIONS", raising=False)
    db = MagicMock()
    seed_tpl = MagicMock()

    @contextmanager
    def session_factory():
        yield db

    ran = run_startup_template_seed(
        db_ready=True,
        session_factory=session_factory,
        seed_templates_fn=seed_tpl,
    )
    assert ran is True
    seed_tpl.assert_called_once_with(db, commit=True)
