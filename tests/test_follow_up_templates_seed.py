"""Day 3 / Day 7 ladder templates seed on live-safe ensure + compose purpose pick."""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.models import MessageTemplate


def test_seed_inserts_follow_up_3_and_7_for_all_channels(auth_client):
    # Wipe ladder templates only, then re-seed (simulates live DB missing new rows)
    with SessionLocal() as db:
        rows = db.scalars(
            select(MessageTemplate).where(
                MessageTemplate.purpose.in_(("follow_up_3", "follow_up_7"))
            )
        ).all()
        for t in rows:
            db.delete(t)
        db.commit()

    r = auth_client.post("/api/sales/templates/ensure-defaults")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ok") is True
    assert body.get("ladder_templates_created", 0) >= 6

    store = auth_client.get("/api/templates")
    assert store.status_code == 200, store.text
    data = store.json()
    for channel in ("instagram", "whatsapp", "email"):
        purposes = {(t.get("purpose") or "") for t in (data.get(channel) or [])}
        assert "follow_up_3" in purposes, f"{channel} missing follow_up_3"
        assert "follow_up_7" in purposes, f"{channel} missing follow_up_7"


def test_seed_idempotent_second_run(auth_client):
    auth_client.post("/api/sales/templates/ensure-defaults")
    r2 = auth_client.post("/api/sales/templates/ensure-defaults")
    assert r2.status_code == 200, r2.text
    assert r2.json().get("ladder_templates_created", 0) == 0
    assert r2.json().get("created", 0) == 0


def test_backfill_due_plus3_also_seeds_templates(auth_client):
    with SessionLocal() as db:
        rows = db.scalars(
            select(MessageTemplate).where(MessageTemplate.purpose == "follow_up_3")
        ).all()
        for t in rows:
            db.delete(t)
        db.commit()

    r = auth_client.post("/api/sales/prospects/backfill-due-plus3")
    assert r.status_code == 200, r.text
    tpl = r.json().get("templates") or {}
    assert tpl.get("ladder_templates_created", 0) >= 3  # at least Day 3 across channels


def test_pick_default_prefers_follow_up_purpose():
    tpl = (
        Path(__file__).resolve().parents[1] / "app/static/js/panels/templates.js"
    ).read_text()
    assert "if (purpose)" in tpl
    assert "opts.purpose" in tpl
    assert "follow_up_3" in Path(
        Path(__file__).resolve().parents[1] / "app/main.py"
    ).read_text()
    assert '"follow_up_7"' in Path(
        Path(__file__).resolve().parents[1] / "app/main.py"
    ).read_text() or "'follow_up_7'" in Path(
        Path(__file__).resolve().parents[1] / "app/main.py"
    ).read_text()
