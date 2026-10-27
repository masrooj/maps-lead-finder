"""UI/API gates for the 21-item contact-queue / compose / deals batch."""


def test_index_has_21_item_markers(auth_client):
    html = auth_client.get("/").text
    assert "window.confirm" not in html
    assert "prompt(" not in html
    assert 'id="contactQueueBookingCount"' in html
    assert 'id="contactQueueChannel"' in html
    assert "queue-tabs-sticky" in html
    assert 'id="composeTabs"' in html
    assert 'id="composeMarkBtn"' in html
    assert 'id="copyComposeIconBtn"' in html
    assert "normalizeEmailBodyBreaks" in html
    assert "channelTagsHtml" in html
    assert "Do not auto-open" in html
    assert 'id="brandLogoFile"' in html
    assert 'id="paylinkCreateBtn"' in html
    assert 'id="dealsPaylinksPane"' in html
    assert 'data-dtab="proposal"' in html
    assert 'data-dtab="appointhive"' in html
    assert "markBookingIds" in html
    assert "z-index:320" in html
    assert "dismissNotification" in html
    assert 'id="notifClearAll"' in html
    assert "data-notif-dismiss" in html
    assert "contactQueueIsParked" in html


def test_contact_queue_exposes_maps_and_booking(auth_client):
    from app.db import SessionLocal
    from app.models import Business

    with SessionLocal() as db:
        b = Business(
            place_id="places/ui21test",
            name="UI21 Maps Lead",
            country="Australia",
            city="Sydney",
            google_maps_url="https://maps.google.com/?cid=ui21",
            current_booking_software="Fresha",
            product_line="STANDALONE",
            currency="USD",
        )
        db.add(b)
        db.commit()
        bid = b.id
    r = auth_client.get("/api/sales/contact-queue?product_line=STANDALONE&limit=0")
    assert r.status_code == 200
    leads = r.json().get("leads") or []
    hit = next((x for x in leads if x.get("id") == bid or x.get("name") == "UI21 Maps Lead"), None)
    assert hit is not None
    assert hit.get("google_maps_url")
    assert hit.get("has_booking") is True
    assert "Fresha" in (hit.get("current_booking_software") or "")


def test_dashboard_queues_include_to_contact(auth_client):
    r = auth_client.get("/api/sales/dashboard?product_line=STANDALONE")
    assert r.status_code == 200
    q = r.json().get("queues") or {}
    assert "to_contact_count" in q
    assert "overdue_count" in q


def test_brand_logo_owner_roundtrip(auth_client, tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    files = {"file": ("logo.png", png, "image/png")}
    up = auth_client.post("/api/sales/brand-logo", files=files)
    assert up.status_code == 200, up.text
    assert up.json().get("url")
    settings = auth_client.get("/api/sales/settings")
    assert settings.status_code == 200
    assert (settings.json().get("settings") or {}).get("brand_logo_url")
    cleared = auth_client.delete("/api/sales/brand-logo")
    assert cleared.status_code == 200


def test_get_prospect_endpoint(auth_client):
    from app.db import SessionLocal
    from app.models import Business

    with SessionLocal() as db:
        b = Business(
            place_id="places/ui21get",
            name="UI21 Get Prospect",
            country="Australia",
            city="Melbourne",
            product_line="STANDALONE",
            currency="USD",
        )
        db.add(b)
        db.commit()
        bid = b.id
    r = auth_client.get(f"/api/sales/prospects/{bid}")
    assert r.status_code == 200
    assert r.json()["name"] == "UI21 Get Prospect"


def test_index_gap_closure_markers(auth_client):
    html = auth_client.get("/").text
    assert "mapsUrlFor" in html
    assert "copyEmailHtmlSignature" in html
    assert "emailSignatureHtml" in html
    assert "mergePaylinkRows" in html
    assert "Local proposal (Manual" in html
    assert "kept existing secret" in html
    assert 'data-booking="' in html
    assert "data-prospect-booking" in html


def test_be_manual_keeps_existing_secret(auth_client):
    from app.db import SessionLocal
    from app.services.booking_engine.connect import get_be_shared_secret, save_manual_connection

    with SessionLocal() as db:
        save_manual_connection(db, base_url="http://127.0.0.1:3010", shared_secret="initial-secret-value-32")
        first = get_be_shared_secret(db=db)
    r = auth_client.post(
        "/api/sales/be/connection/manual",
        json={"base_url": "http://127.0.0.1:3011", "shared_secret": ""},
    )
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        assert get_be_shared_secret(db=db) == first
        assert "3011" in (r.json().get("base_url") or "")
