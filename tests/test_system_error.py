"""System error page + degraded health when Postgres is unavailable."""
from __future__ import annotations

from app.system_status import (
    BootState,
    classify_db_error,
    render_system_error_html,
    sanitize_error_text,
)


def test_classify_neon_quota():
    exc = RuntimeError(
        'connection to server at "ep-snowy-base-b81pmwn5-pooler.c-14.us-east-1.aws.neon.tech" '
        "failed: ERROR: Your account or project has exceeded the quota. "
        "Upgrade your plan to increase limits."
    )
    issue = classify_db_error(exc)
    assert issue.code == "db_quota_exceeded"
    assert issue.status == 503
    assert "quota" in issue.title.lower()
    assert "plan limits" in issue.message.lower()
    assert "Neon" in issue.hint or "Billing" in issue.hint
    assert "exceeded the quota" in issue.detail.lower()


def test_sanitize_strips_password_from_urlish_text():
    raw = "postgresql://user:super-secret@db.example/app sslmode=require password=also-secret"
    cleaned = sanitize_error_text(raw)
    assert "super-secret" not in cleaned
    assert "also-secret" not in cleaned
    assert "***" in cleaned


def test_system_error_html_escapes_and_includes_detail():
    issue = classify_db_error(RuntimeError('quota <script>alert(1)</script> exceeded the quota. Upgrade your plan'))
    html = render_system_error_html(
        "<h1>{{title}}</h1><p>{{message}}</p><pre>{{detail}}</pre><span>{{code}}</span>",
        issue,
    )
    assert issue.title in html
    assert "<script>" not in html
    assert "db_quota_exceeded" in html
    assert "&#" in html or "&lt;" in html


def test_home_and_health_when_db_boot_failed(client, monkeypatch):
    from app import system_status

    issue = classify_db_error(
        RuntimeError("ERROR: Your account or project has exceeded the quota. Upgrade your plan to increase limits.")
    )
    previous = BootState(db_ready=system_status.boot_state.db_ready, issue=system_status.boot_state.issue)
    system_status.boot_state.db_ready = False
    system_status.boot_state.issue = issue

    def _restore():
        system_status.boot_state.db_ready = previous.db_ready
        system_status.boot_state.issue = previous.issue

    monkeypatch.setattr(system_status, "boot_state", system_status.boot_state)
    try:
        health = client.get("/api/health")
        assert health.status_code == 503
        body = health.json()
        assert body["ok"] is False
        assert body["db_ready"] is False
        assert body["issue"]["code"] == "db_quota_exceeded"

        home = client.get("/", headers={"Accept": "text/html"})
        assert home.status_code == 503
        assert "text/html" in home.headers.get("content-type", "")
        assert "Database quota exceeded" in home.text
        assert "Technical detail" in home.text
        assert "exceeded the quota" in home.text

        api = client.get("/api/businesses", headers={"Accept": "application/json"})
        assert api.status_code == 503
        assert api.json()["error"]["code"] == "db_quota_exceeded"
    finally:
        _restore()
