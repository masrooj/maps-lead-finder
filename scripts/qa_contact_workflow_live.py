#!/usr/bin/env python3
"""Live QA walk for contact workflow split. Exit 1 on any failure."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import http.cookiejar
from pathlib import Path

from app.db import SessionLocal
from app.models import Business, PipelineStage
from app.services.pipeline import backfill_prospect_stages
from app.services.seed import seed_stages

ROOT = Path(__file__).resolve().parents[1]
env: dict[str, str] = {}
for line in (ROOT / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")

BASE = "http://127.0.0.1:8000"
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
failures: list[tuple[str, str]] = []
passes: list[str] = []


def call(method, path, body=None, expect=200):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with opener.open(req, timeout=60) as r:
            raw = r.read().decode() or "{}"
            code = r.status
            try:
                payload = json.loads(raw)
            except Exception:
                payload = raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        code = e.code
        try:
            payload = json.loads(raw)
        except Exception:
            payload = raw
    ok = code == expect if isinstance(expect, int) else code in expect
    return code, payload, ok


def check(name, cond, detail=""):
    if cond:
        passes.append(name)
        print("PASS", name)
    else:
        failures.append((name, str(detail)[:300]))
        print("FAIL", name, str(detail)[:300])


def main() -> int:
    user = env.get("LOGIN_USERNAME") or "hello@appointhive.com"
    pw = env.get("LOGIN_PASSWORD") or ""
    code, data, ok = call("POST", "/api/login", {"username": user, "password": pw})
    check("login", ok, data)

    ns = time.time_ns()
    with SessionLocal() as db:
        seed_stages(db)
        backfill_prospect_stages(db)
        db.commit()
        leads = {}
        specs = {
            "email": dict(name="QA Email Only", email="qa-email@example.com", phone="", social="{}"),
            "wa": dict(name="QA WA Only", email="", phone="+61411110001", social="{}"),
            "ig": dict(
                name="QA IG Only",
                email="",
                phone="",
                social='{"instagram":"https://instagram.com/qaliveig"}',
            ),
            "none": dict(name="QA No Channel", email="", phone="", social="{}"),
            "all": dict(
                name="QA All Channels",
                email="qa-all@example.com",
                phone="+61411110002",
                social='{"instagram":"https://instagram.com/qaliveall"}',
            ),
        }
        for tag, s in specs.items():
            b = Business(
                place_id=f"qa/live-walk3-{ns}-{tag}",
                name=s["name"],
                city="Sydney",
                country="Australia",
                email=s["email"],
                phone=s["phone"],
                international_phone=s["phone"],
                social_links_json=s["social"],
                product_line="STANDALONE",
                marked_for_contact=False,
                ready_for_contact=False,
            )
            db.add(b)
            db.flush()
            leads[tag] = b.id
        db.commit()
    print("created", leads)

    for tag in leads:
        code, data, ok = call("PATCH", f"/api/businesses/{leads[tag]}", {"marked_for_contact": True})
        check(
            f"mark_{tag}",
            ok and data.get("marked_for_contact") is True and data.get("ready_for_contact") is False,
            data,
        )

    code, q, ok = call("GET", "/api/sales/contact-queue?product_line=STANDALONE&limit=0")
    check("queue_load", ok, q if not ok else "")
    by_id = {x["id"]: x for x in (q.get("leads") or [])}
    for tag in leads:
        row = by_id.get(leads[tag])
        check(f"queue_has_{tag}", row is not None, "missing")
        if row:
            check(
                f"prep_not_ready_{tag}",
                row.get("marked_for_contact") and not row.get("ready_for_contact"),
                row,
            )

    code, data, ok = call("POST", f"/api/sales/prospects/{leads['email']}/promote-ready", {}, expect=422)
    check("promote_no_channel_422", ok, data)

    code, data, ok = call("PATCH", f"/api/businesses/{leads['email']}", {"outreach_channel": "email"})
    check("set_email_channel", ok and data.get("outreach_channel") == "email", data)
    code, data, ok = call("POST", f"/api/sales/prospects/{leads['email']}/promote-ready", {})
    check("promote_email", ok and data.get("ready_for_contact") is True, data)

    call("PATCH", f"/api/businesses/{leads['wa']}", {"outreach_channel": "whatsapp"})
    code, data, ok = call("POST", f"/api/sales/prospects/{leads['wa']}/promote-ready", {})
    check("promote_wa", ok and data.get("ready_for_contact") is True, data)

    call("PATCH", f"/api/businesses/{leads['ig']}", {"outreach_channel": "instagram"})
    code, data, ok = call("POST", f"/api/sales/prospects/{leads['ig']}/promote-ready", {}, expect=422)
    check("promote_ig_no_warm_422", ok, data)
    call("PATCH", f"/api/businesses/{leads['ig']}", {"ig_warm_follow": True})
    code, data, ok = call("POST", f"/api/sales/prospects/{leads['ig']}/promote-ready", {}, expect=422)
    check("promote_ig_follow_only_422", ok, data)
    call("PATCH", f"/api/businesses/{leads['ig']}", {"ig_warm_like": True})
    code, data, ok = call("POST", f"/api/sales/prospects/{leads['ig']}/promote-ready", {})
    check("promote_ig_warm_ok", ok and data.get("ready_for_contact") is True, data)

    code, data, ok = call(
        "PATCH", f"/api/businesses/{leads['none']}", {"outreach_channel": "email"}, expect=422
    )
    check("none_email_channel_422", ok, data)

    call("PATCH", f"/api/businesses/{leads['all']}", {"outreach_channel": "email"})
    call("POST", f"/api/sales/prospects/{leads['all']}/promote-ready", {})
    code, data, ok = call("PATCH", f"/api/businesses/{leads['all']}", {"outreach_channel": "instagram"})
    check("switch_to_ig_demotes", ok and data.get("ready_for_contact") is False, data)
    call(
        "PATCH",
        f"/api/businesses/{leads['all']}",
        {"ig_warm_follow": True, "ig_warm_comment": True},
    )
    code, data, ok = call("POST", f"/api/sales/prospects/{leads['all']}/promote-ready", {})
    check("repromote_after_ig_warm", ok, data)
    code, data, ok = call("PATCH", f"/api/businesses/{leads['all']}", {"outreach_channel": "whatsapp"})
    check("switch_ig_to_wa_stays_ready", ok and data.get("ready_for_contact") is True, data)

    code, data, ok = call("POST", f"/api/sales/prospects/{leads['wa']}/demote-to-contact", {})
    check("demote_wa", ok)
    with SessionLocal() as db:
        b = db.get(Business, leads["wa"])
        check(
            "demote_keeps_channel",
            (b.outreach_channel or "") == "whatsapp" and b.ready_for_contact is False,
            f"{b.outreach_channel} {b.ready_for_contact}",
        )
    call("POST", f"/api/sales/prospects/{leads['wa']}/promote-ready", {})

    code, data, ok = call(
        "POST", f"/api/businesses/{leads['email']}/confirm-sent", {"channel": "email", "message": "hi"}
    )
    check("confirm_email", ok and data.get("contacted") is True, data)
    with SessionLocal() as db:
        b = db.get(Business, leads["email"])
        stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        check("confirm_email_kind_scheduled", (b.follow_up_kind or "") == "scheduled", b.follow_up_kind)
        check("confirm_clears_ready", b.ready_for_contact is False, b.ready_for_contact)
        check(
            "confirm_email_stage",
            stage and stage.slug in ("contacted", "follow_up"),
            stage.slug if stage else None,
        )

    code, data, ok = call(
        "POST",
        f"/api/businesses/{leads['wa']}/confirm-sent",
        {"channel": "whatsapp", "message": "hi"},
    )
    check("confirm_wa", ok, data)
    with SessionLocal() as db:
        b = db.get(Business, leads["wa"])
        stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        check("confirm_wa_call_today_kind", (b.follow_up_kind or "") == "call_today", b.follow_up_kind)
        check(
            "confirm_wa_stays_contacted",
            stage and stage.slug == "contacted",
            stage.slug if stage else None,
        )

    code, data, ok = call(
        "POST",
        f"/api/businesses/{leads['ig']}/confirm-sent",
        {"channel": "instagram", "message": "soft"},
    )
    check("confirm_ig", ok and data.get("instagram_sent") is True, data)

    code, data, ok = call("POST", f"/api/sales/prospects/{leads['email']}/revert-to-contact", {})
    check("revert_ok", ok)
    with SessionLocal() as db:
        b = db.get(Business, leads["email"])
        stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        check(
            "revert_ready_flag",
            b.ready_for_contact is True and b.contacted is False,
            f"{b.ready_for_contact} {b.contacted}",
        )
        check("revert_keeps_channel", (b.outreach_channel or "") == "email", b.outreach_channel)
        check(
            "revert_stage_ready",
            stage and stage.slug == "ready_to_contact",
            stage.slug if stage else None,
        )

    code, data, ok = call(
        "POST",
        f"/api/businesses/{leads['all']}/confirm-sent",
        {"channel": "whatsapp", "message": "x", "next_follow_up_at": "2020-01-02"},
    )
    check("confirm_all_past_due", ok, data)
    with SessionLocal() as db:
        b = db.get(Business, leads["all"])
        stage = db.get(PipelineStage, b.pipeline_stage_id) if b.pipeline_stage_id else None
        check("past_due_auto_follow_up", stage and stage.slug == "follow_up", stage.slug if stage else None)

    code, pipe, ok = call("GET", "/api/sales/pipeline?product_line=STANDALONE")
    check("pipeline_ok", ok)
    slugs = [(c.get("stage") or {}).get("slug") for c in (pipe.get("columns") or [])]
    check("pipeline_has_to_contact", "to_contact" in slugs, slugs[:10])
    check("pipeline_has_ready", "ready_to_contact" in slugs, slugs[:10])
    ready_id = next(
        (c["stage"]["id"] for c in pipe["columns"] if c["stage"]["slug"] == "ready_to_contact"),
        None,
    )
    code, data, ok = call(
        "POST",
        f"/api/sales/prospects/{leads['none']}/stage",
        {"stage_id": ready_id, "note": "qa", "force": False},
        expect=422,
    )
    check("kanban_ready_gate_422", ok, data)

    code, data, ok = call("PATCH", f"/api/businesses/{leads['email']}", {"marked_for_contact": False})
    check("unmark", ok and data.get("marked_for_contact") is False, data)
    check(
        "unmark_clears_ready_channel",
        data.get("ready_for_contact") is False and not (data.get("outreach_channel") or ""),
        data,
    )

    code, data, ok = call("PATCH", f"/api/businesses/{leads['none']}", {"email": "none-now@example.com"})
    check("none_add_email", ok and "none-now" in (data.get("email") or ""), data)
    code, data, ok = call("PATCH", f"/api/businesses/{leads['none']}", {"outreach_channel": "email"})
    check("none_set_channel_after_email", ok, data)
    code, data, ok = call("POST", f"/api/sales/prospects/{leads['none']}/promote-ready", {})
    check("none_promote_after_enrich", ok, data)

    print("\n==== SUMMARY ====")
    print("PASS", len(passes), "FAIL", len(failures))
    for n, d in failures:
        print(" -", n, "::", d)
    out = ROOT / "support/docs/qa/evidence/contact-workflow-2026-11-06"
    out.mkdir(parents=True, exist_ok=True)
    (out / "live_walk.json").write_text(
        json.dumps(
            {"pass": len(passes), "fail": [{"name": n, "detail": d} for n, d in failures]},
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
