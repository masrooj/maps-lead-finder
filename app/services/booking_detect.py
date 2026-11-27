"""Website + Places booking detector for Discover Scan booking (v1).

Matches known scheduler hosts / embeds in HTML. When the website misses,
optionally checks Google Places ``reservable`` using the existing
``GOOGLE_MAPS_API_KEY`` (same live key as Discover search — no extra flag).
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests

# (substring matched against hostname or full URL/html haystack, display name)
BOOKING_PROVIDERS: tuple[tuple[str, str], ...] = (
    ("fresha.com", "Fresha"),
    ("fresha.io", "Fresha"),
    ("booksy.com", "Booksy"),
    ("treatwell.", "Treatwell"),
    ("wahanda.com", "Treatwell"),
    ("setmore.com", "Setmore"),
    ("calendly.com", "Calendly"),
    ("squareup.com/appointments", "Square Appointments"),
    ("square.site", "Square Appointments"),
    ("booksy.", "Booksy"),
    ("simplybook.me", "SimplyBook"),
    ("simplybook.it", "SimplyBook"),
    ("gettimely.com", "Timely"),
    ("timely.fun", "Timely"),
    ("acuityscheduling.com", "Acuity"),
    ("squarespace.com/scheduling", "Acuity"),
    ("mindbodyonline.com", "Mindbody"),
    ("mindbody.io", "Mindbody"),
    ("vagaro.com", "Vagaro"),
    ("schedulicity.com", "Schedulicity"),
    ("styleseat.com", "StyleSeat"),
    ("booker.com", "Booker"),
    ("salonized.com", "Salonized"),
    ("phorest.com", "Phorest"),
    ("shedul.com", "Fresha"),
    ("google.com/maps/reserve", "Google Reserve"),
    ("reserve with google", "Google Reserve"),
    ("opentable.com", "OpenTable"),
    ("resy.com", "Resy"),
    ("wheniwork.com", "When I Work"),
    ("appointy.com", "Appointy"),
    ("bookingkoala.com", "BookingKoala"),
    ("bookonline.", "Book Online"),
    ("appointment-plus.com", "AppointmentPlus"),
    ("appointlet.com", "Appointlet"),
    ("youcanbook.me", "YouCanBook.me"),
    ("calendly.", "Calendly"),
)

# Path tokens that suggest a booking page (used only with provider host or iframe)
_BOOK_PATH_RE = re.compile(
    r"(?:^|/)(?:book(?:-?now)?|booking|appointments?|reserve|schedule)(?:/|$|\?|#)",
    re.I,
)

FetchHtmlFn = Callable[[str, float], tuple[str, str]]


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except Exception:
        return ""


def match_booking_in_text(haystack: str) -> Optional[str]:
    """Return provider display name if haystack contains a known booking signature."""
    if not haystack:
        return None
    low = haystack.lower()
    for needle, name in BOOKING_PROVIDERS:
        if needle.lower() in low:
            return name
    return None


def match_booking_in_html(html: str, *, page_url: str = "") -> Optional[str]:
    """Scan HTML (and optional page URL) for booking providers / embeds."""
    if page_url:
        hit = match_booking_in_text(page_url)
        if hit:
            return hit
        # Website itself is a known booking host
        hit = match_booking_in_text(_host(page_url))
        if hit:
            return hit

    if not html:
        return None

    hit = match_booking_in_text(html)
    if hit:
        return hit

    # Conservative: iframe/script src pointing at a book path on a third-party host
    for src in re.findall(
        r"""(?:src|href)\s*=\s*["']([^"']+)["']""",
        html,
        flags=re.I,
    ):
        s = src.strip()
        if not s or s.startswith("#") or s.lower().startswith("mailto:"):
            continue
        host = _host(s) if "://" in s or s.startswith("//") else ""
        if not host:
            continue
        # Same-site /book alone is not enough (too many false positives)
        provider = match_booking_in_text(host) or match_booking_in_text(s)
        if provider:
            return provider
        if _BOOK_PATH_RE.search(urlparse(s).path or "") and match_booking_in_text(s):
            return match_booking_in_text(s)

    return None


def detect_booking_from_html(html: str, *, page_url: str = "") -> dict[str, Any]:
    name = match_booking_in_html(html, page_url=page_url)
    if name:
        return {"has_booking": True, "software_name": name}
    return {"has_booking": False, "software_name": ""}


def detect_booking_on_website(
    url: str,
    *,
    fetch_html: Optional[FetchHtmlFn] = None,
    timeout: float = 5.0,
    crawl_book_pages: bool = True,
) -> dict[str, Any]:
    """Fetch website HTML and detect booking software.

    When ``fetch_html`` is omitted, uses ``app.main.fetch_html`` (lazy import).
    """
    website = (url or "").strip()
    if not website:
        return {"has_booking": False, "software_name": ""}

    # Direct provider URL (no fetch needed)
    direct = match_booking_in_text(website) or match_booking_in_text(_host(website))
    if direct:
        return {"has_booking": True, "software_name": direct}

    if fetch_html is None:
        from app.main import fetch_html as _fetch

        fetch_html = _fetch

    final_url, html = fetch_html(website, timeout)
    if not html:
        return {"has_booking": False, "software_name": ""}

    hit = match_booking_in_html(html, page_url=final_url or website)
    if hit:
        return {"has_booking": True, "software_name": hit}

    if crawl_book_pages:
        try:
            from app.main import candidate_contact_urls
        except Exception:
            candidate_contact_urls = None  # type: ignore
        if candidate_contact_urls:
            for page in candidate_contact_urls(final_url or website, html)[1:]:
                path = (urlparse(page).path or "").lower()
                if not any(tok in path for tok in ("book", "booking", "appoint", "reserve", "schedule")):
                    continue
                _, page_html = fetch_html(page, min(timeout, 4.0))
                if not page_html:
                    continue
                hit = match_booking_in_html(page_html, page_url=page)
                if hit:
                    return {"has_booking": True, "software_name": hit}

    return {"has_booking": False, "software_name": ""}


def park_has_booking(b, software_name: str) -> str:
    """Park lead as Has booking; clear To contact / Ready prep flags. Returns stored label."""
    label = (software_name or "").strip() or "Has booking"
    b.current_booking_software = label
    b.marked_for_contact = False
    b.ready_for_contact = False
    if hasattr(b, "updated_at"):
        from datetime import datetime, timezone

        b.updated_at = datetime.now(timezone.utc)
    return label


def is_fresh_discover_business(b) -> bool:
    """Same predicates as Discover status=fresh pool."""
    if bool(getattr(b, "marked_for_contact", False)):
        return False
    if bool(getattr(b, "contacted", False)):
        return False
    if bool(getattr(b, "lost", False)):
        return False
    if (getattr(b, "current_booking_software", None) or "").strip():
        return False
    if bool(getattr(b, "already_connected", False)):
        return False
    if bool(getattr(b, "cannot_contact", False)):
        return False
    if bool(getattr(b, "archived", False)):
        return False
    return True


def _places_resource_name(place_id: str) -> str:
    raw = (place_id or "").strip()
    if not raw:
        return ""
    if raw.startswith("places/"):
        return raw
    # Synthetic import ids are not Places resources
    if raw.startswith("import/") or raw.startswith("scan/") or raw.startswith("fresh/") or raw.startswith("qa/"):
        return ""
    return f"places/{raw}"


def places_reservable(place_id: str, *, api_key: Optional[str] = None, timeout: float = 8.0) -> Optional[bool]:
    """Return Places ``reservable`` using GOOGLE_MAPS_API_KEY, or None if unknown/unavailable."""
    name = _places_resource_name(place_id)
    if not name:
        return None
    key = (api_key if api_key is not None else os.getenv("GOOGLE_MAPS_API_KEY", "")).strip()
    if not key or key.startswith("replace_"):
        return None
    url = f"https://places.googleapis.com/v1/{name}"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "reservable,id,displayName",
    }
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException:
        return None
    if not r.ok:
        return None
    try:
        data = r.json() or {}
    except Exception:
        return None
    if "reservable" not in data:
        return None
    return bool(data.get("reservable"))


def scan_booking_limits(db=None) -> dict[str, int]:
    """Owner-configurable Discover Scan caps (AppSetting). Defaults: limit 60, chunk 8."""
    limit, chunk = 60, 8
    if db is not None:
        try:
            from sqlalchemy import select
            from app.models import AppSetting

            rows = {
                r.key: r.value
                for r in db.scalars(
                    select(AppSetting).where(
                        AppSetting.key.in_(("scan_booking_limit", "scan_booking_chunk"))
                    )
                ).all()
            }
            if rows.get("scan_booking_limit"):
                limit = int(str(rows["scan_booking_limit"]).strip())
            if rows.get("scan_booking_chunk"):
                chunk = int(str(rows["scan_booking_chunk"]).strip())
        except Exception:
            pass
    limit = max(1, min(200, int(limit or 60)))
    chunk = max(1, min(50, int(chunk or 8)))
    chunk = min(chunk, limit)
    return {"limit": limit, "chunk": chunk}


def detect_booking_for_business(
    b,
    *,
    fetch_html: Optional[FetchHtmlFn] = None,
    places_check: bool = True,
) -> dict[str, Any]:
    """Website first, then Places reservable (live GOOGLE_MAPS_API_KEY)."""
    website = (getattr(b, "website", None) or "").strip()
    if website:
        detected = detect_booking_on_website(website, fetch_html=fetch_html)
        if detected.get("has_booking"):
            return {**detected, "source": "website"}

    if places_check:
        reservable = places_reservable(getattr(b, "place_id", "") or "")
        if reservable is True:
            return {
                "has_booking": True,
                "software_name": "Google Reserve",
                "source": "places_reservable",
            }

    return {"has_booking": False, "software_name": "", "source": "none"}
