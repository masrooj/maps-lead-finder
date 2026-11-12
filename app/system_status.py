"""Boot / runtime system status for degraded DB and operator-facing errors."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlparse

from app.config import DATABASE_URL

_SECRET_IN_URL = re.compile(r"(://[^:/@\s]+:)([^@/\s]+)(@)", re.IGNORECASE)
_PASSWORD_KV = re.compile(r"(password|pwd|pass|secret|token)\s*[:=]\s*\S+", re.IGNORECASE)


@dataclass
class SystemIssue:
    code: str
    title: str
    message: str
    hint: str
    detail: str = ""
    status: int = 503
    db_ready: bool = False

    def as_public_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "message": self.message,
            "hint": self.hint,
            "detail": self.detail,
            "status": self.status,
            "db_ready": self.db_ready,
        }


@dataclass
class BootState:
    db_ready: bool = False
    issue: Optional[SystemIssue] = None

    def as_public_dict(self) -> dict[str, Any]:
        payload = {
            "ok": self.db_ready and self.issue is None,
            "db_ready": self.db_ready,
            "issue": self.issue.as_public_dict() if self.issue else None,
        }
        return payload


boot_state = BootState()


def database_host_label(url: str = DATABASE_URL) -> str:
    raw = (url or "").strip()
    if not raw:
        return "(unset)"
    try:
        parsed = urlparse(raw.replace("postgresql+psycopg2://", "postgresql://").replace("postgresql+psycopg://", "postgresql://"))
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        db = (parsed.path or "").lstrip("/") or ""
        if host and db:
            return f"{host}{port}/{db}"
        if host:
            return f"{host}{port}"
    except Exception:
        pass
    return "(configured)"


def sanitize_error_text(text: str, *, limit: int = 800) -> str:
    cleaned = _SECRET_IN_URL.sub(r"\1***\3", str(text or ""))
    cleaned = _PASSWORD_KV.sub(lambda m: f"{m.group(1)}=***", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > limit:
        return cleaned[: limit - 1] + "…"
    return cleaned


def classify_db_error(exc: BaseException) -> SystemIssue:
    detail = sanitize_error_text(f"{type(exc).__name__}: {exc}")
    low = detail.lower()
    host = database_host_label()

    if "exceeded the quota" in low or ("quota" in low and "upgrade your plan" in low):
        provider = "Neon" if "neon.tech" in host.lower() else "your Postgres provider"
        return SystemIssue(
            code="db_quota_exceeded",
            title="Database quota exceeded",
            message=(
                "The database provider rejected new connections because this account "
                "or project has exceeded its plan limits."
            ),
            hint=(
                f"Open the {provider} console (Billing / Usage), free capacity or upgrade the plan, "
                f"confirm {host} accepts connections, then redeploy."
            ),
            detail=detail,
            status=503,
        )

    if "password authentication failed" in low or "invalid password" in low:
        return SystemIssue(
            code="db_auth_failed",
            title="Database authentication failed",
            message="PostgreSQL rejected the credentials in DATABASE_URL.",
            hint="Update DATABASE_URL with a valid user/password for the project, then redeploy.",
            detail=detail,
            status=503,
        )

    if "does not exist" in low and ("database" in low or "role" in low):
        return SystemIssue(
            code="db_missing",
            title="Database not found",
            message="The configured database or role does not exist on the server.",
            hint=f"Create the database on {host}, or point DATABASE_URL at an existing database.",
            detail=detail,
            status=503,
        )

    if any(
        token in low
        for token in (
            "could not connect",
            "connection refused",
            "connection timed out",
            "timeout expired",
            "server closed the connection",
            "network is unreachable",
            "name or service not known",
            "temporary failure in name resolution",
            "ssl connection has been closed",
        )
    ):
        return SystemIssue(
            code="db_unreachable",
            title="Database unreachable",
            message=f"Could not open a PostgreSQL connection to {host}.",
            hint="Check the host is up, allowlist/network access, SSL settings, and DATABASE_URL.",
            detail=detail,
            status=503,
        )

    return SystemIssue(
        code="db_unavailable",
        title="Database unavailable",
        message="AppointHive Sales OS cannot use the database right now.",
        hint=f"Inspect provider status for {host}, then retry. Technical detail is below.",
        detail=detail,
        status=503,
    )


def set_boot_issue(exc: BaseException) -> SystemIssue:
    issue = classify_db_error(exc)
    boot_state.db_ready = False
    boot_state.issue = issue
    return issue


def mark_boot_ok() -> None:
    boot_state.db_ready = True
    boot_state.issue = None


def current_issue() -> Optional[SystemIssue]:
    return boot_state.issue


def wants_html(request_headers: Any) -> bool:
    accept = ""
    try:
        accept = (request_headers.get("accept") or "").lower()
    except Exception:
        accept = ""
    return "text/html" in accept and "application/json" not in accept.split(",")[0]


def render_system_error_html(template_text: str, issue: SystemIssue) -> str:
    data = {
        "title": issue.title,
        "message": issue.message,
        "hint": issue.hint,
        "code": issue.code,
        "status": str(issue.status),
        "detail": issue.detail or "No additional technical detail.",
        "db_host": database_host_label(),
        "db_ready": "no" if not issue.db_ready else "yes",
    }
    out = template_text
    for key, value in data.items():
        out = out.replace("{{" + key + "}}", html.escape(str(value)))
    return out

