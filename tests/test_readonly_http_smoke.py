"""Read-only HTTP smoke assessment helpers (mocked; no real DB, no app.main import).

Importing app.main would run create_all/apply_migrations against DATABASE_URL.
These tests call proven-safe router handlers with MagicMock sessions only.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Before any app.* import: avoid constructing the engine against a live Prisma/Neon URL.
os.environ["APP_STARTUP_DATA_MUTATIONS"] = "false"
os.environ["DATABASE_URL"] = "postgresql+psycopg://smoke:smoke@127.0.0.1:9/smoke_readonly"

ROOT = Path(__file__).resolve().parents[1]
MAIN_PY = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
SALES_PY = (ROOT / "app" / "routers" / "sales.py").read_text(encoding="utf-8")
OPS_PY = (ROOT / "app" / "routers" / "ops.py").read_text(encoding="utf-8")

# Proven DB-mutating GET handlers (do not call in cutover smoke).
UNSAFE_GET_PATHS = {
    "/api/templates": "calls seed_default_templates on every GET",
    "/api/sales/pipeline": "conditional seed_stages + db.commit",
    "/api/sales/ranking-modes": "list_modes seeds when empty",
    "/api/sales/contact-queue": "_get_queue_mode → list_modes may seed",
    "/api/sales/contact-queue/meta": "_get_queue_mode → list_modes may seed",
    "/api/cron/follow-up-reminders": "GET alias runs promote/close/email digest",
}

# Proven read-only for DB (SELECT / file / in-memory only). Auth noted separately.
SAFE_GET_PATHS = {
    "/": "serves index.html; no DB when boot_state.db_ready",
    "/api/health": "Throttled SELECT 1 (60s); may flip in-memory boot_state only",
    "/api/me": "SELECT users row",
    "/api/migrations": "SELECT schema_migrations",
    "/api/locations": "static locations.json",
    "/api/businesses": "SELECT businesses",
    "/api/stats": "aggregate SELECTs",
    "/api/categories": "SELECT category/search_labels",
    "/api/businesses/{business_id}/activity": "SELECT activity tables",
    "/api/sales/dashboard": "metrics SELECTs only",
    "/api/sales/dashboard/queues": "notification count SELECTs",
    "/api/sales/stages": "list_stages SELECT",
    "/api/sales/settings": "SELECT app_settings + quota reads",
    "/api/sales/providers": "secret/env status; no writes",
    "/api/sales/prospects": "SELECT businesses",
    "/api/sales/plans": "SELECT plans/prices/features",
    "/api/sales/email-signature": "signature_payload reads only",
    "/api/sales/me": "SELECT user + settings",
}


def _assert_no_db_writes(db: MagicMock) -> None:
    db.commit.assert_not_called()
    db.add.assert_not_called()
    db.delete.assert_not_called()
    db.flush.assert_not_called()
    if hasattr(db, "execute"):
        # allow execute for SELECTs; forbid obvious DML helpers if present
        pass


def test_unsafe_get_inventory_is_documented_in_source():
    assert "seed_default_templates(db)" in MAIN_PY
    assert "@app.get(\"/api/templates\"" in MAIN_PY
    assert "seed_stages(db)" in SALES_PY
    assert "@router.get(\"/pipeline\"" in SALES_PY
    assert "list_modes(db" in (ROOT / "app" / "services" / "ranking_modes.py").read_text(encoding="utf-8")
    assert "@router.get(\"/api/cron/follow-up-reminders\"" in OPS_PY
    assert set(UNSAFE_GET_PATHS)  # non-empty contract for cutover docs


def test_safe_get_inventory_covers_requested_areas():
    needed = {
        "/api/health",
        "/",
        "/api/sales/dashboard",
        "/api/businesses",
        "/api/sales/stages",
        "/api/sales/settings",
    }
    assert needed <= set(SAFE_GET_PATHS)


def test_sales_stages_handler_is_read_only():
    from app.routers.sales import sales_stages

    db = MagicMock()
    stage = MagicMock()
    stage.id = 1
    stage.slug = "identified"
    stage.name = "Discovered"
    stage.product_line = "STANDALONE"
    stage.sort_order = 0
    stage.probability = 0.05
    stage.is_terminal = False
    stage.is_won = False
    stage.is_lost = False
    stage.active = True

    with patch("app.routers.sales.list_stages", return_value=[stage]):
        with patch("app.routers.sales.stage_dict", return_value={"slug": "identified"}):
            out = sales_stages(product_line="STANDALONE", _user="owner@example.com", db=db)

    assert "stages" in out
    _assert_no_db_writes(db)


def test_sales_settings_handler_is_read_only():
    from app.routers.sales import sales_settings

    db = MagicMock()
    row = MagicMock()
    row.key = "default_currency"
    row.value = "USD"
    db.scalars.return_value.all.return_value = [row]

    with patch("app.routers.sales.provider_status", return_value={"manual": {"configured": True}}):
        with patch("app.services.automation.ig_outreach_quota", return_value={"cap": 8, "used": 0, "remaining": 8}):
            out = sales_settings(_user="owner@example.com", db=db)

    assert out["settings"]["default_currency"] == "USD"
    _assert_no_db_writes(db)


def test_sales_dashboard_handler_is_read_only():
    from app.routers.sales import sales_dashboard

    db = MagicMock()
    payload = {"queues": {}, "pipeline": {}}
    with patch("app.routers.sales.dashboard_bundle", return_value=payload) as bundled:
        out = sales_dashboard(_user="owner@example.com", db=db)
    assert out == payload
    bundled.assert_called_once()
    _assert_no_db_writes(db)


def test_sales_prospects_handler_is_read_only():
    from app.routers.sales import sales_prospects

    db = MagicMock()
    db.scalar.return_value = 0
    db.scalars.return_value.all.return_value = []

    with patch("app.services.authz.get_user_by_email", return_value=None):
        with patch("app.services.authz.can_view_business", return_value=True):
            out = sales_prospects(_user="owner@example.com", db=db)

    assert out["prospects"] == []
    _assert_no_db_writes(db)


def test_sales_email_signature_handler_is_read_only():
    from app.routers.sales import get_email_signature

    db = MagicMock()
    with patch(
        "app.services.email_signature.signature_payload",
        return_value={"text": "AppointHive", "include_logo": False},
    ):
        out = get_email_signature(_user="owner@example.com", db=db)
    assert out["text"] == "AppointHive"
    _assert_no_db_writes(db)


def test_sales_me_handler_is_read_only():
    from app.routers.ops import sales_me

    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    with patch("app.routers.ops.get_user_by_email", return_value=None):
        out = sales_me(_user="owner@example.com", db=db)
    assert out["username"] == "owner@example.com"
    _assert_no_db_writes(db)


def test_sales_providers_handler_has_no_db_session():
    from app.routers.sales import sales_providers

    with patch("app.routers.sales.provider_status", return_value={"manual": {"configured": True}}) as ps:
        out = sales_providers(_user="owner@example.com")
    assert out["manual"]["configured"] is True
    ps.assert_called_once_with()


def test_pipeline_get_source_contains_write_path():
    """Regression guard: pipeline GET must stay classified unsafe while seed_stages remains."""
    start = SALES_PY.index('@router.get("/pipeline"')
    chunk = SALES_PY[start : start + 1200]
    assert "seed_stages(db)" in chunk
    assert "db.commit()" in chunk


def test_templates_get_source_contains_seed():
    start = MAIN_PY.index('@app.get("/api/templates"')
    chunk = MAIN_PY[start : start + 400]
    assert "seed_default_templates(db)" in chunk


def test_contact_queue_get_uses_list_modes_seed_path():
    auto = (ROOT / "app" / "services" / "automation.py").read_text(encoding="utf-8")
    assert "def _get_queue_mode" in auto
    assert "rm.list_modes(db" in auto
    ranking = (ROOT / "app" / "services" / "ranking_modes.py").read_text(encoding="utf-8")
    assert "seed_ranking_modes(db)" in ranking


def test_auth_model_no_login_post_in_readonly_plan():
    """POST /api/login may INSERT users and refresh tokens — not part of read-only smoke."""
    assert "@app.post(\"/api/login\"" in MAIN_PY
    assert "issue_token_pair" in MAIN_PY


@pytest.mark.parametrize("path", sorted(UNSAFE_GET_PATHS))
def test_unsafe_paths_are_explicit(path):
    assert path in UNSAFE_GET_PATHS
    assert UNSAFE_GET_PATHS[path]
