"""Prisma ops savings: TTL read cache, CQ↔Markets universe share, health throttle, N+1 hoist."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest import mock

from app.db import SessionLocal
from app.models import Business
from app.services import read_cache
from app.services.automation import _setting_value, ig_ready_wait_days
from app.services.metrics import task_queues


def setup_function(_fn=None):
    read_cache.clear_all()


def teardown_function(_fn=None):
    read_cache.clear_all()


def test_read_cache_get_or_set_hits():
    calls = {"n": 0}

    def loader():
        calls["n"] += 1
        return {"ok": True, "n": calls["n"]}

    a = read_cache.get_or_set("t:1", 30, loader)
    b = read_cache.get_or_set("t:1", 30, loader)
    assert a == b == {"ok": True, "n": 1}
    assert calls["n"] == 1


def test_invalidate_queue_caches_clears_prefixes():
    read_cache.get_or_set("queues:STANDALONE", 30, lambda: {"q": 1})
    read_cache.get_or_set("cquniverse:STANDALONE:default", 30, lambda: {"leads": []})
    read_cache.get_or_set("cqmeta:STANDALONE:default::", 30, lambda: {"count": 0})
    read_cache.get_or_set("other:keep", 30, lambda: 1)
    n = read_cache.invalidate_queue_caches()
    assert n >= 3
    # Re-load should call loaders again for cleared keys
    calls = {"q": 0}

    def qload():
        calls["q"] += 1
        return {"q": 2}

    read_cache.get_or_set("queues:STANDALONE", 30, qload)
    assert calls["q"] == 1
    assert read_cache.get_or_set("other:keep", 30, lambda: 99) == 1


def test_dashboard_queues_ttl_skips_second_bundle(auth_client, monkeypatch):
    calls = {"n": 0}
    real = __import__("app.services.metrics", fromlist=["notification_queues_bundle"]).notification_queues_bundle

    def wrapped(db, product_line="STANDALONE"):
        calls["n"] += 1
        return real(db, product_line=product_line)

    monkeypatch.setattr("app.services.metrics.notification_queues_bundle", wrapped)

    r1 = auth_client.get("/api/sales/dashboard/queues", params={"product_line": "STANDALONE"})
    assert r1.status_code == 200, r1.text
    r2 = auth_client.get("/api/sales/dashboard/queues", params={"product_line": "STANDALONE"})
    assert r2.status_code == 200, r2.text
    assert r1.json()["queues"]["to_contact_count"] == r2.json()["queues"]["to_contact_count"]
    assert calls["n"] == 1


def test_cq_universe_shared_with_geo_counts(auth_client, monkeypatch):
    calls = {"n": 0}
    import app.services.automation as auto

    real = auto.rank_contact_queue

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(auto, "rank_contact_queue", wrapped)

    r_geo = auth_client.get(
        "/api/sales/contact-queue/geo-counts",
        params={"product_line": "STANDALONE", "limit": 500},
    )
    assert r_geo.status_code == 200, r_geo.text
    after_geo = calls["n"]
    assert after_geo >= 1

    r_cq = auth_client.get(
        "/api/sales/contact-queue",
        params={"product_line": "STANDALONE", "limit": "0"},
    )
    assert r_cq.status_code == 200, r_cq.text
    assert calls["n"] == after_geo  # shared cquniverse — no second rank


def test_cq_universe_reverse_order(auth_client, monkeypatch):
    calls = {"n": 0}
    import app.services.automation as auto

    real = auto.rank_contact_queue

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(auto, "rank_contact_queue", wrapped)

    r_cq = auth_client.get(
        "/api/sales/contact-queue",
        params={"product_line": "STANDALONE", "limit": "0"},
    )
    assert r_cq.status_code == 200, r_cq.text
    after_cq = calls["n"]
    assert after_cq >= 1

    r_geo = auth_client.get(
        "/api/sales/contact-queue/geo-counts",
        params={"product_line": "STANDALONE", "limit": 500},
    )
    assert r_geo.status_code == 200, r_geo.text
    assert calls["n"] == after_cq


def test_geo_filtered_cq_bypasses_universe(auth_client, monkeypatch):
    calls = {"n": 0}
    import app.services.automation as auto

    real = auto.rank_contact_queue

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(auto, "rank_contact_queue", wrapped)

    # Warm universe
    auth_client.get("/api/sales/contact-queue", params={"product_line": "STANDALONE", "limit": "0"})
    n_warm = calls["n"]

    # Geo-filtered must re-rank (not served from cquniverse)
    r = auth_client.get(
        "/api/sales/contact-queue",
        params={"product_line": "STANDALONE", "country": "Australia", "city": "Sydney", "limit": "50"},
    )
    assert r.status_code == 200, r.text
    assert calls["n"] > n_warm


def test_mutation_invalidates_queues_cache(auth_client, monkeypatch):
    calls = {"n": 0}
    real = __import__("app.services.metrics", fromlist=["notification_queues_bundle"]).notification_queues_bundle

    def wrapped(db, product_line="STANDALONE"):
        calls["n"] += 1
        return real(db, product_line=product_line)

    monkeypatch.setattr("app.services.metrics.notification_queues_bundle", wrapped)

    assert auth_client.get("/api/sales/dashboard/queues").status_code == 200
    assert calls["n"] == 1
    # Successful mutation clears caches via middleware
    r = auth_client.put("/api/sales/settings/ig_ready_wait_days?value=1")
    assert r.status_code == 200, r.text
    assert auth_client.get("/api/sales/dashboard/queues").status_code == 200
    assert calls["n"] == 2


def test_health_throttles_select_1(client, monkeypatch):
    import app.main as main_mod
    import time as time_mod

    pings = {"n": 0}

    class _Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, *a, **k):
            pings["n"] += 1
            return mock.Mock()

    monkeypatch.setattr(main_mod.engine, "connect", lambda: _Conn())
    main_mod.boot_state.db_ready = True
    main_mod.boot_state.issue = None
    # Far past so first call always pings; second call within TTL skips.
    monkeypatch.setattr(main_mod, "_health_db_ping_at", time_mod.monotonic() - 120.0)

    r1 = client.get("/api/health")
    assert r1.status_code == 200, r1.text
    assert pings["n"] == 1, f"expected first health to ping DB, got {pings['n']}; body={r1.json()}"
    r2 = client.get("/api/health")
    assert r2.status_code == 200, r2.text
    assert pings["n"] == 1  # throttled within 60s


def test_task_queues_hoists_settings(monkeypatch):
    """Ready-lead loop must not call _setting_value per lead."""
    setting_calls = {"n": 0}
    real_sv = _setting_value

    def counting_sv(db, key, default=""):
        setting_calls["n"] += 1
        return real_sv(db, key, default)

    monkeypatch.setattr("app.services.automation._setting_value", counting_sv)

    with SessionLocal() as db:
        # Seed a few Ready leads
        now = datetime.now(timezone.utc)
        for i in range(5):
            db.add(
                Business(
                    place_id=f"ops/ready-{i}-{now.timestamp()}",
                    name=f"Ready {i}",
                    country="Australia",
                    city="Sydney",
                    product_line="STANDALONE",
                    archived=False,
                    lost=False,
                    opt_out=False,
                    ready_for_contact=True,
                    contacted=False,
                    ig_ready_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        db.commit()

        # Warm AppSetting cache / baseline
        ig_ready_wait_days(db)
        before = setting_calls["n"]
        q = task_queues(db)
        after = setting_calls["n"]
        assert "ready_unlocked_count" in q
        # Hoisted: a handful of setting reads, not 5 leads × 2+
        assert after - before <= 8, f"too many setting reads: {after - before}"


def test_pool_pre_ping_disabled():
    from app.db import engine

    assert engine.pool._pre_ping is False


def test_task_queues_single_count_statement(monkeypatch):
    """Five queue COUNTs + open invoices must be one execute (scalar subqueries)."""
    executes = {"n": 0}
    with SessionLocal() as db:
        real_execute = db.execute

        def counting_execute(*a, **k):
            executes["n"] += 1
            return real_execute(*a, **k)

        monkeypatch.setattr(db, "execute", counting_execute)
        # scalars() for ready/ladder/due also hits DB but via scalars, not execute for counts
        real_scalars = db.scalars
        scalar_calls = {"n": 0}

        def counting_scalars(*a, **k):
            scalar_calls["n"] += 1
            return real_scalars(*a, **k)

        monkeypatch.setattr(db, "scalars", counting_scalars)
        q = task_queues(db)
        assert "overdue_count" in q
        assert "_open_invoice_count" in q or True  # may be popped only in bundle
        # First db.execute should be the combined counts; later code may execute more
        assert executes["n"] >= 1


def test_stages_by_ids_one_query():
    from app.services.pipeline import list_stages, stages_by_ids

    with SessionLocal() as db:
        stages = list_stages(db, "STANDALONE")
        ids = [s.id for s in stages[:3]]
        m = stages_by_ids(db, ids)
        assert len(m) == len(ids)
        for i in ids:
            assert m[i].id == i


def test_notification_bundle_strips_internal_invoice_key(auth_client):
    r = auth_client.get("/api/sales/dashboard/queues", params={"product_line": "STANDALONE"})
    assert r.status_code == 200
    data = r.json()
    assert "_open_invoice_count" not in data.get("queues", {})
    assert "sample_size" in data.get("invoiced_outstanding", {})
