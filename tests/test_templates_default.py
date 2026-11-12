"""Set-default for message templates must stick per channel and drive compose pick."""
from __future__ import annotations

from tests.conftest import frontend_source


def test_set_default_template_switches_channel_flag(auth_client):
    store = auth_client.get("/api/templates")
    assert store.status_code == 200, store.text
    email = store.json().get("email") or []
    assert email, "expected seeded email templates"

    created = auth_client.post(
        "/api/templates",
        json={
            "channel": "email",
            "name": "Alt email default candidate",
            "subject": "Alt",
            "body": "Hi {{name}}",
            "is_default": False,
        },
    )
    assert created.status_code == 200, created.text
    alt_id = created.json()["id"]

    r = auth_client.post(f"/api/templates/{alt_id}/default")
    assert r.status_code == 200, r.text
    assert r.json().get("isDefault") is True
    assert r.json().get("id") == alt_id

    after = auth_client.get("/api/templates").json().get("email") or []
    defaults = [t for t in after if t.get("isDefault")]
    assert len(defaults) == 1
    assert defaults[0]["id"] == alt_id


def test_set_default_whatsapp_overrides_soft_opener_in_ui_helper(auth_client):
    """UI pickDefaultTemplate must prefer isDefault over the first soft_opener."""
    store = auth_client.get("/api/templates")
    assert store.status_code == 200, store.text
    wa = store.json().get("whatsapp") or []
    assert len(wa) >= 2

    long_form = next((t for t in wa if (t.get("purpose") or "") != "soft_opener"), None)
    soft = next((t for t in wa if (t.get("purpose") or "") == "soft_opener"), None)
    assert long_form and soft

    r = auth_client.post(f"/api/templates/{long_form['id']}/default")
    assert r.status_code == 200, r.text

    after = auth_client.get("/api/templates").json().get("whatsapp") or []
    marked = next(t for t in after if t.get("isDefault"))
    assert marked["id"] == long_form["id"]

    src = frontend_source()
    assert "pickDefaultTemplate" in src
    assert "if (marked) return marked" in src
    assert "preferFollowUp" in src
    # Soft must not win before isDefault (old bug).
    assert "Prefer soft_opener for IG/WA cold ask; else channel default." not in src
    # Compose dropdown must sort marked default ahead of soft openers.
    assert "a&&a.isDefault?0:1" in src or "a.isDefault?0:1" in src
    assert "refreshComposeTemplates" in src


def test_set_default_instagram_and_invoice(auth_client):
    for channel in ("instagram", "invoice"):
        created = auth_client.post(
            "/api/templates",
            json={
                "channel": channel,
                "name": f"Alt {channel}",
                "subject": "Invoice {{invoice_number}}" if channel == "invoice" else "",
                "body": "Body for {{name}}",
                "is_default": False,
            },
        )
        assert created.status_code == 200, created.text
        alt_id = created.json()["id"]
        r = auth_client.post(f"/api/templates/{alt_id}/default")
        assert r.status_code == 200, r.text
        rows = auth_client.get("/api/templates").json().get(channel) or []
        defaults = [t for t in rows if t.get("isDefault")]
        assert len(defaults) == 1
        assert defaults[0]["id"] == alt_id
