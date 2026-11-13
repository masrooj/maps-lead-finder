"""List-refresh DB savings: meta fingerprints, no promote-on-GET, light queues."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import Business, PipelineStage
from app.services.automation import promote_due_follow_ups
from app.services.pipeline import get_stage, list_stages, move_stage


def _biz(**kwargs) -> Business:
    now = datetime.now(timezone.utc)
    defaults = dict(
        place_id=kwargs.pop("place_id", f"test/{now.timestamp()}"),
        name=kwargs.pop("name", "Cache Test Lead"),
        country="Australia",
        city="Sydney",
        product_line="STANDALONE",
        archived=False,
        opt_out=False,
        lost=False,
        contacted=False,
        created_at=now,
        updated_at=now,
    )
    defaults.update(kwargs)
    return Business(**defaults)


def test_contact_queue_meta_shape(auth_client):
    with SessionLocal() as db:
        db.add(_biz(place_id="meta/cq-1", name="Meta CQ"))
        db.commit()

    r = auth_client.get(
        "/api/sales/contact-queue/meta",
        params={"product_line": "STANDALONE", "country": "Australia", "city": "Sydney"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "count" in data
    assert "max_updated_at" in data
    assert "mode" in data
    assert data["product_line"] == "STANDALONE"
    assert data["count"] >= 1

    full = auth_client.get(
        "/api/sales/contact-queue",
        params={"product_line": "STANDALONE", "limit": 0, "country": "Australia", "city": "Sydney"},
    )
    assert full.status_code == 200
    # Meta count is rank-eligible (pre mode-filter); mode is echoed for fingerprinting.
    assert data["mode"] == full.json().get("mode")


def test_contact_queue_get_does_not_promote_due(auth_client):
    due = datetime.now(timezone.utc) - timedelta(days=1)
    with SessionLocal() as db:
        stages = {s.slug: s for s in list_stages(db, "STANDALONE")}
        contacted = stages.get("contacted")
        assert contacted is not None
        b = _biz(
            place_id="meta/no-promote-cq",
            name="Due No Promote CQ",
            contacted=True,
            next_follow_up_at=due,
            pipeline_stage_id=contacted.id,
            follow_up_kind="scheduled",
        )
        db.add(b)
        db.commit()
        bid = b.id
        stage_before = b.pipeline_stage_id

    r = auth_client.get("/api/sales/contact-queue", params={"product_line": "STANDALONE", "limit": 5})
    assert r.status_code == 200, r.text

    with SessionLocal() as db:
        row = db.get(Business, bid)
        assert row.pipeline_stage_id == stage_before


def test_pipeline_get_does_not_promote_due(auth_client):
    due = datetime.now(timezone.utc) - timedelta(days=1)
    with SessionLocal() as db:
        stages = {s.slug: s for s in list_stages(db, "STANDALONE")}
        contacted = stages.get("contacted")
        assert contacted is not None
        b = _biz(
            place_id="meta/no-promote-pipe",
            name="Due No Promote Pipe",
            contacted=True,
            next_follow_up_at=due,
            pipeline_stage_id=contacted.id,
            follow_up_kind="scheduled",
        )
        db.add(b)
        db.commit()
        bid = b.id
        stage_before = b.pipeline_stage_id

    r = auth_client.get("/api/sales/pipeline", params={"product_line": "STANDALONE"})
    assert r.status_code == 200, r.text

    with SessionLocal() as db:
        row = db.get(Business, bid)
        assert row.pipeline_stage_id == stage_before


def test_stats_sql_aggregates_and_max_updated(auth_client):
    with SessionLocal() as db:
        db.add(_biz(place_id="meta/stats-fresh", name="Stats Fresh Lead"))
        db.add(
            _biz(
                place_id="meta/stats-marked",
                name="Stats Marked Lead",
                marked_for_contact=True,
            )
        )
        db.commit()

    r = auth_client.get(
        "/api/stats",
        params={"scope": "city", "country": "Australia", "city": "Sydney"},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["fresh"] >= 1
    assert data["total"] >= data["fresh"]
    assert data["marked"] >= 1
    assert "max_updated_at" in data
    assert data["max_updated_at"]


def test_dashboard_queues_light(auth_client):
    r = auth_client.get("/api/sales/dashboard/queues", params={"product_line": "STANDALONE"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "queues" in data
    q = data["queues"]
    for key in (
        "overdue_count",
        "due_today_count",
        "to_contact_count",
        "follow_up_due_count",
        "follow_up_push_reminders_enabled",
    ):
        assert key in q
    assert "invoiced_outstanding" in data
    assert "sample_size" in data["invoiced_outstanding"]
    # Light payload must not include heavy dashboard sections
    assert "pipeline" not in data
    assert "appointhive" not in data
    assert "collected" not in data


def test_promote_due_batch_commit_persists(auth_client):
    due = datetime.now(timezone.utc) - timedelta(days=4)
    with SessionLocal() as db:
        contacted = get_stage(db, "STANDALONE", "contacted")
        assert contacted is not None
        b = _biz(
            place_id="meta/batch-promote",
            name="Batch Promote",
            contacted=True,
            next_follow_up_at=due,
            last_contacted_at=due - timedelta(days=1),
            pipeline_stage_id=contacted.id,
            follow_up_kind="scheduled",
        )
        db.add(b)
        db.commit()
        bid = b.id

    with SessionLocal() as db:
        moved = promote_due_follow_ups(db, product_line="STANDALONE")
        assert moved >= 1
        row = db.get(Business, bid)
        stage = db.get(PipelineStage, row.pipeline_stage_id) if row.pipeline_stage_id else None
        assert stage is not None
        assert stage.slug in ("follow_up_3", "follow_up_7", "follow_up")


def test_move_stage_commit_false_needs_outer_commit():
    with SessionLocal() as db:
        contacted = get_stage(db, "STANDALONE", "contacted")
        ready = get_stage(db, "STANDALONE", "ready_to_contact")
        assert contacted and ready
        b = _biz(
            place_id="meta/commit-false",
            name="Commit False",
            marked_for_contact=True,
            ready_for_contact=True,
            pipeline_stage_id=ready.id,
        )
        db.add(b)
        db.commit()
        bid = b.id

    with SessionLocal() as db:
        b = db.get(Business, bid)
        move_stage(db, b, contacted.id, force=True, commit=False, note="test batch")
        db.rollback()

    with SessionLocal() as db:
        b = db.get(Business, bid)
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage and stage.slug == "ready_to_contact"

    with SessionLocal() as db:
        b = db.get(Business, bid)
        move_stage(db, b, contacted.id, force=True, commit=False, note="test batch")
        db.commit()

    with SessionLocal() as db:
        b = db.get(Business, bid)
        stage = db.get(PipelineStage, b.pipeline_stage_id)
        assert stage and stage.slug == "contacted"
