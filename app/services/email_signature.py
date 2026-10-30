"""Email signature settings — AppSetting-backed, used by compose + Settings UI."""
from __future__ import annotations

import html
from typing import Any

from sqlalchemy import select

from app.models import AppSetting

KEY_TEXT = "email_signature_text"
KEY_INCLUDE_LOGO = "email_signature_include_logo"
KEY_LOGO = "brand_logo_url"

DEFAULT_TEXT = "AppointHive"


def _get(db, key: str) -> str:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    return (row.value or "").strip() if row else ""


def _set(db, key: str, value: str) -> None:
    row = db.scalar(select(AppSetting).where(AppSetting.key == key))
    if not row:
        db.add(AppSetting(key=key, value=value))
    else:
        row.value = value


def get_brand_logo_url(db) -> str:
    return _get(db, KEY_LOGO)


def get_signature_text(db) -> str:
    text = _get(db, KEY_TEXT)
    return text if text else DEFAULT_TEXT


def get_include_logo(db) -> bool:
    raw = _get(db, KEY_INCLUDE_LOGO)
    if not raw:
        # Default: include logo when one is uploaded
        return bool(get_brand_logo_url(db))
    return raw.lower() in ("1", "true", "yes", "on")


def build_plain(text: str, *, logo_url: str = "", include_logo: bool = False, absolute_logo: str = "") -> str:
    body = (text or "").rstrip()
    parts = ["", "", "--", body] if body else ["", "", "--"]
    logo = (absolute_logo or logo_url or "").strip()
    if include_logo and logo:
        parts.append(f"Logo: {logo}")
    return "\r\n".join(parts)


def build_html(text: str, *, logo_url: str = "", include_logo: bool = False, absolute_logo: str = "") -> str:
    body = (text or "").rstrip()
    safe = html.escape(body).replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br>")
    chunks = ["<br><br>--<br>", safe]
    logo = (absolute_logo or logo_url or "").strip()
    if include_logo and logo:
        esc_logo = html.escape(logo, quote=True)
        chunks.append(
            f'<br><img src="{esc_logo}" alt="Logo" height="48" '
            'style="max-height:48px;width:auto;display:block;margin:8px 0"><br>'
        )
    return "".join(chunks)


def signature_payload(db, *, absolute_logo: str = "") -> dict[str, Any]:
    text = get_signature_text(db)
    logo = get_brand_logo_url(db)
    include = get_include_logo(db)
    abs_logo = absolute_logo or logo
    return {
        "text": text,
        "include_logo": include,
        "brand_logo_url": logo,
        "plain": build_plain(text, logo_url=logo, include_logo=include, absolute_logo=abs_logo),
        "html": build_html(text, logo_url=logo, include_logo=include, absolute_logo=abs_logo),
    }


def update_signature(db, *, text: str, include_logo: bool) -> dict[str, Any]:
    cleaned = (text or "").strip()
    if len(cleaned) > 4000:
        raise ValueError("Signature text too long (max 4000 characters)")
    _set(db, KEY_TEXT, cleaned or DEFAULT_TEXT)
    _set(db, KEY_INCLUDE_LOGO, "true" if include_logo else "false")
    db.flush()
    return signature_payload(db)


def ensure_defaults(db) -> None:
    """Seed defaults without overwriting an existing custom signature."""
    if not _get(db, KEY_TEXT):
        _set(db, KEY_TEXT, DEFAULT_TEXT)
    if not _get(db, KEY_INCLUDE_LOGO):
        _set(db, KEY_INCLUDE_LOGO, "true")
