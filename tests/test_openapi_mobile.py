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
    assert "/api/businesses" in paths

    # Public login has empty security
    login_post = paths["/api/login"].get("post") or {}
    assert login_post.get("security") == []

    me = (paths.get("/api/me") or {}).get("get") or {}
    assert me.get("security") == [{"BearerAuth": []}]


def test_docs_ui_available(client):
    r = client.get("/docs")
    assert r.status_code == 200
