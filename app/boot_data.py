"""Import-time catalog/pipeline/template mutations — gated by APP_STARTUP_DATA_MUTATIONS."""
from __future__ import annotations

from typing import Any, Callable, Optional

from app.config import schema_init_skipped, startup_data_mutations_enabled


def startup_mutations_allowed(*, db_ready: bool) -> bool:
    return bool(db_ready) and startup_data_mutations_enabled()


def run_boot_schema_init(
    *,
    create_all_fn: Callable[[], Any],
    apply_migrations_fn: Callable[[], Any],
    mark_ok_fn: Callable[[], Any],
    readonly_probe_fn: Optional[Callable[[], Any]] = None,
) -> bool:
    """Create tables + apply migrations unless APP_SKIP_SCHEMA_INIT.

    When skipped, optionally runs a read-only connectivity probe, then marks boot OK.
    Returns True if create_all/migrations ran, False if skipped.
    """
    if schema_init_skipped():
        if readonly_probe_fn is not None:
            readonly_probe_fn()
        mark_ok_fn()
        return False
    create_all_fn()
    apply_migrations_fn()
    mark_ok_fn()
    return True


def run_post_schema_startup_mutations(
    *,
    db_ready: bool,
    session_factory: Callable[[], Any],
    seed_sales_os_fn: Callable[[Any], Any],
    backfill_fn: Callable[[Any], Any],
    promote_fn: Callable[[Any], Any],
) -> bool:
    """Seed catalog + backfill/promote prospects. Returns True if work ran."""
    if not startup_mutations_allowed(db_ready=db_ready):
        return False
    with session_factory() as db:
        seed_sales_os_fn(db)
        backfill_fn(db)
        promote_fn(db)
        db.commit()
    return True


def run_startup_template_seed(
    *,
    db_ready: bool,
    session_factory: Callable[[], Any],
    seed_templates_fn: Callable[..., Any],
) -> bool:
    """Insert missing default/soft/ladder templates. Returns True if work ran."""
    if not startup_mutations_allowed(db_ready=db_ready):
        return False
    with session_factory() as db:
        seed_templates_fn(db, commit=True)
    return True
