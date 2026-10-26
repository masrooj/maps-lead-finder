"""HTTP client for AppointHive Booking Engine /api/internal/crm/*."""
from __future__ import annotations

from typing import Any, Optional

import httpx

from app.services.booking_engine.connect import (
    get_be_base_url,
    get_be_shared_secret,
    require_be_connected,
)


class BeClientError(Exception):
    def __init__(self, message: str, *, status: int = 0, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class BeNotConnected(BeClientError):
    pass


class BookingEngineClient:
    def __init__(self, db=None, *, skip_connected_gate: bool = False, timeout: float = 20.0):
        self.db = db
        self.skip_connected_gate = skip_connected_gate
        self.timeout = timeout

    def _ensure(self) -> None:
        if not self.skip_connected_gate:
            require_be_connected(self.db)

    def _headers(self) -> dict[str, str]:
        secret = get_be_shared_secret(db=self.db)
        if not secret:
            raise BeClientError("Shared secret not configured", status=409)
        return {
            "Authorization": f"Bearer {secret}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _url(self, path: str) -> str:
        base = get_be_base_url(db=self.db).rstrip("/")
        if not base:
            raise BeClientError("AppointHive base URL not configured", status=409)
        if not path.startswith("/"):
            path = "/" + path
        return f"{base}/api/internal/crm{path}"

    def _request(self, method: str, path: str, *, params: Optional[dict] = None, json_body: Any = None) -> Any:
        self._ensure()
        url = self._url(path)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                res = client.request(method, url, headers=self._headers(), params=params, json=json_body)
        except httpx.HTTPError as exc:
            raise BeClientError(f"AppointHive unreachable: {exc}") from exc
        body: Any
        try:
            body = res.json()
        except Exception:
            body = {"raw": (res.text or "")[:500]}
        if res.status_code >= 400:
            err = ""
            if isinstance(body, dict):
                err = str(body.get("error") or body.get("detail") or "")
            raise BeClientError(err or f"AppointHive HTTP {res.status_code}", status=res.status_code, body=body)
        return body

    def health(self) -> dict:
        # Health allows unpaired on BE; still need secret when BE has one.
        # Skip require_be_connected so Test connection can promote disconnected → connected.
        prev = self.skip_connected_gate
        self.skip_connected_gate = True
        try:
            return self._request("GET", "/health")
        finally:
            self.skip_connected_gate = prev

    def get_catalog(self, product_line: str = "STANDALONE") -> dict:
        return self._request("GET", "/catalog", params={"productLine": product_line})

    def list_revisions(self) -> dict:
        return self._request("GET", "/standalone/revisions")

    def create_purchase(self, payload: dict) -> dict:
        return self._request("POST", "/standalone/purchases", json_body=payload)

    def get_purchase(self, purchase_id: str) -> dict:
        return self._request("GET", f"/standalone/purchases/{purchase_id}")

    def create_request(self, payload: dict) -> dict:
        return self._request("POST", "/standalone/requests", json_body=payload)

    def get_request(self, request_id: str) -> dict:
        return self._request("GET", f"/standalone/requests/{request_id}")

    def request_checkout(self, request_id: str, payload: Optional[dict] = None) -> dict:
        return self._request("POST", f"/standalone/requests/{request_id}/checkout", json_body=payload or {})

    def upgrade_quote(self, params: dict) -> dict:
        return self._request("GET", "/standalone/upgrade-quote", params=params)
