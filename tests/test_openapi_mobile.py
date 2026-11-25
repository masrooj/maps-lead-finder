"""OpenAPI / Swagger contract for mobile clients."""
from __future__ import annotations


def test_openapi_has_bearer_and_feature_tags(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200, r.text
    spec = r.json()
    schemes = (spec.get("components") or {}).get("securitySchemes") or {}
    assert "BearerAuth" in schemes, schemes
    assert schemes["BearerAuth"].get("scheme") == "bearer"
    assert spec.get("security") == [{"BearerAuth": []}]

    tag_names = {t.get("name") for t in (spec.get("tags") or []) if isinstance(t, dict)}
    for needed in ("auth", "discover", "pipeline", "tasks", "money", "ops", "be-bridge"):
        assert needed in tag_names, f"missing tag {needed} in {tag_names}"

    paths = spec.get("paths") or {}
    assert "/api/login" in paths
    assert "/api/auth/refresh" in paths
    assert "/api/sales/contact-queue" in paths
    assert "/api/sales/contact-queue/meta" in paths
    assert "/api/sales/contact-queue/lined-up" in paths
    assert "/api/sales/contact-queue/ready" in paths
    assert "/api/sales/contact-queue/geo-counts" in paths
    assert "/api/sales/dashboard/queues" in paths
    assert "/api/businesses" in paths
    assert "/api/stats" in paths
    for needed in (
        "/api/sales/prospects/{business_id}/already-connected",
        "/api/sales/prospects/{business_id}/clear-already-connected",
        "/api/sales/prospects/{business_id}/cannot-contact",
        "/api/sales/prospects/{business_id}/clear-cannot-contact",
        "/api/sales/prospects/{business_id}/same-contact-siblings",
        "/api/sales/prospects/{business_id}/ig-awaiting-follow-back",
        "/api/sales/prospects/{business_id}/mark-engaged",
        "/api/sales/prospects/{business_id}/mark-for-contact",
        "/api/sales/prospects/bulk-mark-for-contact",
        "/api/sales/prospects/{business_id}/confirm-sent",
        "/api/sales/prospects/{business_id}/promote-ready",
        "/api/sales/prospects/{business_id}/ready-status",
        "/api/sales/prospects/{business_id}/demote-to-contact",
        "/api/sales/dashboard/queues",
        "/api/sales/prospects/backfill-due-plus3",
        "/api/sales/templates/ensure-defaults",
    ):
        assert needed in paths, f"missing OpenAPI path {needed}"

    cq_get = (paths.get("/api/sales/contact-queue") or {}).get("get") or {}
    cq_params = {p.get("name") for p in (cq_get.get("parameters") or []) if isinstance(p, dict)}
    assert "tab" in cq_params, cq_params
    assert "sent_channels" in cq_params, cq_params

    # Public login has empty security
    login_post = paths["/api/login"].get("post") or {}
    assert login_post.get("security") == []

    me = (paths.get("/api/me") or {}).get("get") or {}
    assert me.get("security") == [{"BearerAuth": []}]


def test_docs_ui_available(client):
    r = client.get("/docs")
    assert r.status_code == 200
