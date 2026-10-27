import csv
import hashlib
import hmac
import io
import json
import os
import re
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone, date
from typing import Optional, Any
from urllib.parse import unquote, urlparse

import requests
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, and_

from app.config import LOGIN_PASSWORD, LOGIN_USERNAME, SESSION_COOKIE, SESSION_DAYS, SESSION_SECRET
from app.db import Base, SessionLocal, apply_migrations, engine, migration_status
from app.models import ActivityLog, Business, MessageTemplate
from app.routers import ops as ops_router
from app.routers import sales as sales_router
from app.services.pipeline import backfill_prospect_stages, init_new_prospect
from app.services.seed import seed_sales_os
from app.services.storage import uploads_root

CHANNELS = ("email", "whatsapp", "instagram", "invoice")
DEFAULT_TEMPLATES = {
    "email": {
        "name": "Default email",
        "subject": "Quick idea for {{name}}",
        "body": "Hi {{name}},\n\nI came across your business in {{city}} and thought you might find AppointHive useful for bookings and client outreach.\n\nWould you be open to a quick chat?\n\nThanks!",
    },
    "whatsapp": {
        "name": "Default WhatsApp",
        "subject": "",
        "body": "Hi {{name}}! I saw your {{category}} business in {{city}}. Would you be open to a quick WhatsApp chat about AppointHive for bookings?",
    },
    "instagram": {
        "name": "Default Instagram",
        "subject": "",
        "body": "Hi {{name}} — loved your page. I help {{category}} businesses in {{city}} with AppointHive bookings. Mind if I share a quick idea?",
    },
    "invoice": {
        "name": "Invoice email (with PDF)",
        "subject": "Invoice {{invoice_number}} — {{total_fmt}}",
        "body": (
            "Hi {{name}},\n\n"
            "Please find invoice {{invoice_number}} for {{total_fmt}}.\n\n"
            "{{pay_block}}\n\n"
            "A PDF is attached (download from AppointHive if your mail client cannot attach automatically).\n\n"
            "Thanks,\nAppointHive"
        ),
    },
}

Base.metadata.create_all(engine)
apply_migrations()

# Seed Sales OS catalog/stages/owner (idempotent)
with SessionLocal() as _seed_db:
    try:
        seed_sales_os(_seed_db)
        backfill_prospect_stages(_seed_db)
    except Exception:
        # Do not block app boot if seed races; APIs can re-run backfill
        pass

app = FastAPI(title="AppointHive Sales OS", version="2.0.0")
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / "app" / "templates"
app.mount("/static", StaticFiles(directory=str(TEMPLATE_DIR)), name="static")


class LoginRequest(BaseModel):
    username: str = Field(default="", max_length=320)
    password: str = Field(default="", max_length=500)


def auth_configured() -> bool:
    return bool(SESSION_SECRET)


def secure_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(left.encode()).digest(),
        hashlib.sha256(right.encode()).digest(),
    )


def _cookie_email_encode(email: str) -> str:
    # base64url — emails contain '.' which would break cookie.split(".")
    import base64

    raw = email.strip().lower().encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cookie_email_decode(token: str) -> str:
    import base64

    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode((token + pad).encode("ascii")).decode("utf-8").strip().lower()


def sign_session(username: str, expires_at: int) -> str:
    """Cookie value: expires.encodedEmail.signature"""
    email = username.strip().lower()
    enc = _cookie_email_encode(email)
    payload = f"{email}:{expires_at}"
    signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{expires_at}.{enc}.{signature}"


def verify_session(cookie: Optional[str]) -> Optional[str]:
    if not cookie or not SESSION_SECRET:
        return None
    try:
        parts = cookie.split(".")
        if len(parts) == 3:
            expires_str, enc, signature = parts
            expires_at = int(expires_str)
            email = _cookie_email_decode(enc)
            expected = sign_session(email, expires_at)
            if expires_at < int(datetime.now(timezone.utc).timestamp()):
                return None
            if not hmac.compare_digest(cookie, expected):
                return None
            return email
        # Legacy cookie: expires.signature bound to LOGIN_USERNAME
        if len(parts) == 2 and LOGIN_PASSWORD:
            expires_str, signature = parts
            expires_at = int(expires_str)
            if expires_at < int(datetime.now(timezone.utc).timestamp()):
                return None
            payload = f"{LOGIN_USERNAME.lower()}:{expires_at}"
            expected_sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected_sig):
                return None
            return LOGIN_USERNAME.lower()
    except (ValueError, AttributeError):
        return None
    return None


def set_session_cookie(response: Response, request: Request, username: str = ""):
    email = (username or LOGIN_USERNAME).strip().lower()
    expires_at = int((datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).timestamp())
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
    response.set_cookie(
        key=SESSION_COOKIE,
        value=sign_session(email, expires_at),
        httponly=True,
        samesite="lax",
        secure=secure,
        max_age=SESSION_DAYS * 24 * 60 * 60,
        path="/",
    )


def clear_session_cookie(response: Response):
    response.delete_cookie(key=SESSION_COOKIE, path="/")


def require_login(request: Request) -> str:
    username = verify_session(request.cookies.get(SESSION_COOKIE))
    if not username:
        raise HTTPException(401, "Authentication required")
    return username


sales_router.bind_auth(require_login)
ops_router.bind_auth(require_login)
app.include_router(sales_router.router)
app.include_router(ops_router.router)

# Demo uploads StaticFiles mount.
# Local/Docker: ./uploads. Vercel: /tmp (read-only deploy FS) — prefer Blob for real demos.
_uploads = uploads_root()
app.mount("/uploads", StaticFiles(directory=str(_uploads)), name="uploads")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_day_start(value: Optional[str] = None) -> datetime:
    if value:
        try:
            d = date.fromisoformat(value[:10])
        except ValueError:
            d = utcnow().date()
    else:
        d = utcnow().date()
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def ensure_aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def append_search_label(existing: str, label: str) -> str:
    label = (label or "").strip()
    if not label:
        return existing or ""
    parts = [p.strip() for p in (existing or "").split("|") if p.strip()]
    if label not in parts:
        parts.append(label)
    return " | ".join(parts)[:500]


def merge_socials(existing_json: str, discovered: dict) -> str:
    try:
        current = json.loads(existing_json or "{}")
    except Exception:
        current = {}
    if not isinstance(current, dict):
        current = {}
    for key, value in (discovered or {}).items():
        if value and not current.get(key):
            current[key] = value
    return json.dumps(current)


SOCIAL_DOMAINS = {
    "instagram.com": "instagram", "facebook.com": "facebook", "tiktok.com": "tiktok",
    "linkedin.com": "linkedin", "youtube.com": "youtube", "x.com": "x", "twitter.com": "x"
}
EMAIL_RE = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.I)
SKIP_EMAIL_DOMAINS = {
    "example.com", "example.org", "example.net", "sentry.io", "wixpress.com", "schema.org",
    "google.com", "gstatic.com", "facebook.com", "instagram.com", "twitter.com", "x.com",
    "youtube.com", "tiktok.com", "linkedin.com", "cloudflare.com", "w3.org", "github.com",
    "gravatar.com", "sentry-next.wixpress.com",
}
SKIP_EMAIL_LOCALS = {"noreply", "no-reply", "donotreply", "do-not-reply"}


def is_public_web_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return host not in ("localhost", "127.0.0.1", "0.0.0.0") and not host.endswith(".local")


def extract_emails(html: str) -> str:
    found, seen = [], set()

    def add(raw: str):
        email = unquote(str(raw)).split("?", 1)[0].strip().strip(".,;:<>\"'").lower()
        if email.startswith("mailto:"):
            email = email[7:]
        if not EMAIL_RE.fullmatch(email):
            return
        local, _, domain = email.partition("@")
        if local in SKIP_EMAIL_LOCALS or any(domain == d or domain.endswith("." + d) for d in SKIP_EMAIL_DOMAINS):
            return
        if email in seen:
            return
        seen.add(email)
        found.append(email)

    for raw in re.findall(r"mailto:([^\"'\s>]+)", html, re.I):
        add(raw)
    for raw in EMAIL_RE.findall(html):
        add(raw)
    return ", ".join(found[:3])


CONTACT_PATH_HINTS = (
    "contact", "contacts", "contact-us", "contactus", "get-in-touch", "getintouch",
    "about", "about-us", "aboutus", "connect", "enquiry", "inquiry", "book", "booking",
)
USER_AGENT = {"User-Agent": "MapsLeadFinder/1.1 (+public business website link discovery)"}


def fetch_html(url: str, timeout: float = 5.0) -> tuple[str, str]:
    """Return (final_url, html) or ("", "")."""
    if not is_public_web_url(url):
        return "", ""
    try:
        r = requests.get(url, timeout=timeout, headers=USER_AGENT, allow_redirects=True)
        if not is_public_web_url(r.url) or "text/html" not in r.headers.get("content-type", "").lower():
            return "", ""
        return r.url, r.text[:1_500_000]
    except Exception:
        return "", ""


def extract_socials(html: str) -> dict:
    links = {}
    for href in re.findall(r'''href\s*=\s*["']([^"']+)["']''', html, re.I):
        h = href.strip()
        if h.startswith("//"):
            h = "https:" + h
        elif h.startswith("/") or not h.lower().startswith("http"):
            continue
        domain = (urlparse(h).hostname or "").lower().removeprefix("www.")
        for d, key in SOCIAL_DOMAINS.items():
            if domain == d or domain.endswith("." + d):
                links.setdefault(key, h)
    return links


def candidate_contact_urls(base_url: str, html: str) -> list[str]:
    """Homepage + likely contact/about pages on the same host."""
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    found = [base_url]
    seen = {base_url.rstrip("/").lower()}

    def same_host(host: str) -> bool:
        base = (parsed.hostname or "").lower().removeprefix("www.")
        other = (host or "").lower().removeprefix("www.")
        return bool(base) and other == base

    def add(url: str):
        clean = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        key = clean.lower()
        if key in seen or not is_public_web_url(clean):
            return
        host = (urlparse(clean).hostname or "").lower()
        if not same_host(host):
            return
        seen.add(key)
        found.append(clean)

    for path in ("/contact", "/contact-us", "/contacts", "/about", "/about-us", "/get-in-touch", "/book"):
        add(origin + path)

    for href in re.findall(r'''href\s*=\s*["']([^"']+)["']''', html, re.I):
        h = href.strip()
        if not h or h.startswith("#") or h.lower().startswith("mailto:") or h.lower().startswith("tel:"):
            continue
        if h.startswith("//"):
            h = f"{parsed.scheme}:{h}"
        elif h.startswith("/"):
            h = origin + h
        elif not h.lower().startswith("http"):
            h = origin + "/" + h.lstrip("./")
        path = (urlparse(h).path or "").lower()
        if any(hint in path for hint in CONTACT_PATH_HINTS):
            add(h)
        if len(found) >= 5:
            break
    return found[:5]


def discover_public_contacts(url: str) -> tuple[dict, str]:
    if not url or os.getenv("DISCOVER_SOCIAL_LINKS", "true").lower() != "true":
        return {}, ""
    if not is_public_web_url(url):
        return {}, ""
    final_url, html = fetch_html(url, timeout=5.0)
    if not html:
        return {}, ""
    links = extract_socials(html)
    emails = extract_emails(html)
    email_list = [e.strip() for e in emails.split(",") if e.strip()] if emails else []
    # Crawl a few contact/about pages if homepage is thin
    if len(email_list) < 1 or len(links) < 1:
        for page in candidate_contact_urls(final_url or url, html)[1:]:
            _, page_html = fetch_html(page, timeout=4.0)
            if not page_html:
                continue
            for k, v in extract_socials(page_html).items():
                links.setdefault(k, v)
            more = extract_emails(page_html)
            for e in [x.strip() for x in more.split(",") if x.strip()]:
                if e not in email_list:
                    email_list.append(e)
            if len(email_list) >= 2 and links:
                break
    return links, ", ".join(email_list[:3])


# Google Text Search (New) returns at most ~60 places per query (3×20 pages).
# To go beyond that we tile the city viewport and search each rectangle.
PLACES_QUERY_CAP = 60
PLACES_PAGE_SIZE = 20
SEARCH_HARD_CEILING = 500  # safety stop for "All" city scans


def _places_api_key() -> str:
    key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not key or key.startswith("replace_"):
        raise HTTPException(400, "Set GOOGLE_MAPS_API_KEY in your .env file first.")
    return key


def _viewport_from_latlng(lat: float, lng: float, pad: float = 0.12) -> dict:
    """Build a rectangle around a point (pad ≈ degrees, ~0.12 ≈ 13km)."""
    return {
        "low": {"latitude": lat - pad, "longitude": lng - pad},
        "high": {"latitude": lat + pad, "longitude": lng + pad},
    }


def _normalize_viewport(vp: dict) -> Optional[dict]:
    """Accept Places {low,high} or Geocoding {southwest,northeast} → Places rectangle."""
    if not isinstance(vp, dict):
        return None
    low = vp.get("low") or vp.get("southwest") or {}
    high = vp.get("high") or vp.get("northeast") or {}
    # Places uses latitude/longitude; Geocoding uses lat/lng
    def coord(obj, *keys):
        for k in keys:
            if k in obj and obj[k] is not None:
                return float(obj[k])
        return None
    lat0 = coord(low, "latitude", "lat")
    lng0 = coord(low, "longitude", "lng")
    lat1 = coord(high, "latitude", "lat")
    lng1 = coord(high, "longitude", "lng")
    if None in (lat0, lng0, lat1, lng1):
        return None
    if lat1 < lat0:
        lat0, lat1 = lat1, lat0
    if lng1 < lng0:
        lng0, lng1 = lng1, lng0
    # Slight pad so edge suburbs aren't clipped by tile borders
    pad_lat = max(0.01, (lat1 - lat0) * 0.05)
    pad_lng = max(0.01, (lng1 - lng0) * 0.05)
    return {
        "low": {"latitude": lat0 - pad_lat, "longitude": lng0 - pad_lng},
        "high": {"latitude": lat1 + pad_lat, "longitude": lng1 + pad_lng},
    }


def places_city_viewport(city: str, country: str) -> Optional[dict]:
    """Resolve city bounds via Places Text Search (same API already enabled for lead search)."""
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": _places_api_key(),
        "X-Goog-FieldMask": "places.id,places.displayName,places.location,places.viewport,places.types",
    }
    body = {"textQuery": f"{city}, {country}", "pageSize": 5}
    try:
        response = requests.post(url, headers=headers, json=body, timeout=30)
    except requests.RequestException:
        return None
    if not response.ok:
        return None
    places = response.json().get("places") or []
    if not places:
        return None
    city_l = city.strip().lower()
    # Prefer a named match for the city; else first result with a viewport/location
    ranked = sorted(
        places,
        key=lambda p: (
            0 if city_l and city_l in ((p.get("displayName") or {}).get("text") or "").lower() else 1,
            0 if p.get("viewport") else 1,
        ),
    )
    for p in ranked:
        vp = _normalize_viewport(p.get("viewport") or {})
        if vp:
            return vp
        loc = p.get("location") or {}
        lat, lng = loc.get("latitude"), loc.get("longitude")
        if lat is not None and lng is not None:
            return _viewport_from_latlng(float(lat), float(lng))
    return None


def geocode_api_viewport(city: str, country: str) -> Optional[dict]:
    """Optional Geocoding API fallback (only if that API is enabled on the project)."""
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params={"address": f"{city}, {country}", "key": _places_api_key()},
            timeout=20,
        )
        r.raise_for_status()
        data = r.json()
    except requests.RequestException:
        return None
    if data.get("status") != "OK" or not data.get("results"):
        return None
    result = data["results"][0]
    geometry = result.get("geometry") or {}
    vp = _normalize_viewport(geometry.get("viewport") or {})
    if vp:
        return vp
    loc = geometry.get("location") or {}
    if "lat" in loc and "lng" in loc:
        return _viewport_from_latlng(float(loc["lat"]), float(loc["lng"]))
    return None


def geocode_city_viewport(city: str, country: str) -> Optional[dict]:
    """Return city rectangle for tiled search. Prefer Places (already enabled); Geocoding optional."""
    return places_city_viewport(city, country) or geocode_api_viewport(city, country)


def city_grid_rectangles(viewport: dict, max_results: int) -> list[dict]:
    """Split city viewport into rectangles so each tile can yield up to ~60 Places results."""
    low, high = viewport["low"], viewport["high"]
    lat0, lat1 = float(low["latitude"]), float(high["latitude"])
    lng0, lng1 = float(low["longitude"]), float(high["longitude"])
    if lat1 < lat0:
        lat0, lat1 = lat1, lat0
    if lng1 < lng0:
        lng0, lng1 = lng1, lng0
    # Aim for enough tiles that PLACES_QUERY_CAP * tiles >= max_results
    target_tiles = max(1, min(25, (max_results + PLACES_QUERY_CAP - 1) // PLACES_QUERY_CAP))
    cols = max(1, int(target_tiles ** 0.5 + 0.999))
    rows = max(1, (target_tiles + cols - 1) // cols)
    # Prefer a bit finer grid for large cities (more coverage, fewer missed denser areas)
    if max_results >= SEARCH_HARD_CEILING:
        cols = max(cols, 4)
        rows = max(rows, 4)
    lat_step = (lat1 - lat0) / rows if rows else (lat1 - lat0)
    lng_step = (lng1 - lng0) / cols if cols else (lng1 - lng0)
    tiles = []
    for r in range(rows):
        for c in range(cols):
            tiles.append({
                "low": {
                    "latitude": lat0 + r * lat_step,
                    "longitude": lng0 + c * lng_step,
                },
                "high": {
                    "latitude": lat0 + (r + 1) * lat_step if r < rows - 1 else lat1,
                    "longitude": lng0 + (c + 1) * lng_step if c < cols - 1 else lng1,
                },
            })
    return tiles


def places_text_search_pages(
    query: str,
    max_results: int,
    location_restriction: Optional[dict] = None,
) -> list[dict]:
    """Paginate Text Search until no nextPageToken or Google's ~60/query cap."""
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": _places_api_key(),
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,"
            "places.internationalPhoneNumber,places.websiteUri,places.googleMapsUri,places.rating,"
            "places.userRatingCount,places.types,places.primaryTypeDisplayName,places.businessStatus,"
            "places.editorialSummary,nextPageToken"
        ),
    }
    limit = max(1, min(int(max_results), PLACES_QUERY_CAP))
    all_places: list[dict] = []
    page_token = None
    pages = 0
    while len(all_places) < limit and pages < 5:
        page_size = min(PLACES_PAGE_SIZE, limit - len(all_places))
        if page_size <= 0:
            break
        body: dict[str, Any] = {
            "textQuery": query,
            "pageSize": page_size,
        }
        if page_token:
            body["pageToken"] = page_token
        if location_restriction:
            body["locationRestriction"] = {"rectangle": location_restriction}
        try:
            response = requests.post(url, headers=headers, json=body, timeout=45)
        except requests.RequestException as e:
            raise HTTPException(502, f"Google Places API request failed: {e}") from e
        # nextPageToken can be briefly invalid — retry once after a short wait
        if not response.ok and page_token and response.status_code in (400, 429):
            time.sleep(2)
            try:
                response = requests.post(url, headers=headers, json=body, timeout=45)
            except requests.RequestException as e:
                raise HTTPException(502, f"Google Places API request failed: {e}") from e
        if not response.ok:
            try:
                detail = response.json().get("error", {}).get("message", response.text)
            except Exception:
                detail = response.text
            # A bad tile shouldn't kill the whole city scan
            if location_restriction:
                break
            raise HTTPException(response.status_code, f"Google Places API: {detail}")
        data = response.json()
        batch = data.get("places") or []
        all_places.extend(batch)
        pages += 1
        page_token = data.get("nextPageToken")
        if not page_token or not batch:
            break
    return all_places[:limit]


def google_search(country: str, city: str, category: str, max_results: int):
    """
    Fetch Places for category+city.
    - max_results <= 60: single query, full pagination (Google's per-query max).
    - max_results > 60 or "all": tile the city and merge unique place_ids.
    """
    query = f"{category} in {city}, {country}"
    want = max(1, min(int(max_results), SEARCH_HARD_CEILING))

    if want <= PLACES_QUERY_CAP:
        places = places_text_search_pages(query, want)
        return query, places, {"tiles": 1, "mode": "single"}

    viewport = geocode_city_viewport(city, country)
    if not viewport:
        # Can't tile without bounds — take Google's full 60 from one query
        places = places_text_search_pages(query, PLACES_QUERY_CAP)
        return query, places, {
            "tiles": 1,
            "mode": "single_fallback",
            "note": "Could not resolve city map bounds — got Google’s 60-result cap only",
        }

    tiles = city_grid_rectangles(viewport, want)
    # Unrestricted city query first (best ranking), then tile the viewport.
    # Tile queries use category-only text — city is already enforced by locationRestriction.
    # (Keeping "in City" in the tile query over-filters and often returns empty tiles.)
    seen: dict[str, dict] = {}
    for p in places_text_search_pages(query, PLACES_QUERY_CAP):
        pid = p.get("id")
        if pid:
            seen[pid] = p

    tiles_used = 1
    tiles_with_hits = 1 if seen else 0
    tile_query = category.strip() or query
    for rect in tiles:
        if len(seen) >= want:
            break
        remaining = want - len(seen)
        if remaining <= 0:
            break
        batch = places_text_search_pages(
            tile_query,
            min(PLACES_QUERY_CAP, remaining),
            location_restriction=rect,
        )
        tiles_used += 1
        before = len(seen)
        for p in batch:
            pid = p.get("id")
            if pid and pid not in seen:
                seen[pid] = p
        if len(seen) > before:
            tiles_with_hits += 1
        if len(seen) >= want:
            break

    places = list(seen.values())[:want]
    return query, places, {
        "tiles": tiles_used,
        "tiles_with_hits": tiles_with_hits,
        "mode": "city_scan",
        "unique": len(places),
    }


def iso_or_empty(dt: Optional[datetime]) -> str:
    dt = ensure_aware(dt)
    return dt.isoformat() if dt else ""


def _score_criteria_dict(b: Business) -> dict:
    try:
        data = json.loads(getattr(b, "score_criteria_json", None) or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def business_dict(b: Business) -> dict:
    try:
        socials = json.loads(b.social_links_json or "{}")
    except Exception:
        socials = {}
    return {
        "id": b.id,
        "place_id": b.place_id,
        "name": b.name,
        "country": b.country,
        "city": b.city,
        "category": b.category,
        "search_labels": b.search_labels or "",
        "address": b.address,
        "phone": b.phone,
        "international_phone": b.international_phone,
        "website": b.website,
        "email": b.email,
        "google_maps_url": b.google_maps_url,
        "rating": b.rating,
        "reviews_count": b.reviews_count,
        "social_links": socials,
        "business_status": b.business_status,
        "primary_type": b.primary_type,
        "editorial_summary": b.editorial_summary,
        "contacted": bool(b.contacted),
        "lost": bool(b.lost),
        "email_sent": bool(getattr(b, "email_sent", False)),
        "whatsapp_sent": bool(getattr(b, "whatsapp_sent", False)),
        "instagram_sent": bool(getattr(b, "instagram_sent", False)),
        "marked_for_contact": bool(getattr(b, "marked_for_contact", False)),
        "archived": bool(getattr(b, "archived", False)),
        "next_follow_up_at": iso_or_empty(getattr(b, "next_follow_up_at", None)),
        "last_contacted_at": iso_or_empty(getattr(b, "last_contacted_at", None)),
        "outcome": getattr(b, "outcome", "") or "",
        "notes": b.notes or "",
        "created_at": iso_or_empty(b.created_at),
        "updated_at": iso_or_empty(b.updated_at),
        "product_line": getattr(b, "product_line", None) or "STANDALONE",
        "pipeline_stage_id": getattr(b, "pipeline_stage_id", None),
        "deal_value": getattr(b, "deal_value", None),
        "currency": getattr(b, "currency", None) or "USD",
        "next_action": getattr(b, "next_action", None) or "",
        "qualification_score": getattr(b, "qualification_score", None),
        "opt_out": bool(getattr(b, "opt_out", False)),
        "lost_reason": getattr(b, "lost_reason", None) or "",
        "owner_id": getattr(b, "owner_id", None),
        "state": getattr(b, "state", None) or "",
        "postcode": getattr(b, "postcode", None) or "",
        "lead_source": getattr(b, "lead_source", None) or "",
        "score_criteria": _score_criteria_dict(b),
        "mrr": getattr(b, "mrr", None),
        "linked_prospect_id": getattr(b, "linked_prospect_id", None),
        "be_installation_id": getattr(b, "be_installation_id", None) or "",
    }


def template_dict(t: MessageTemplate) -> dict:
    return {
        "id": t.id,
        "channel": t.channel,
        "name": t.name,
        "subject": t.subject or "",
        "body": t.body or "",
        "isDefault": bool(t.is_default),
        "product_line": getattr(t, "product_line", "") or "",
        "country": getattr(t, "country", "") or "",
        "purpose": getattr(t, "purpose", "") or "",
        "governance_ok": bool(getattr(t, "governance_ok", True)),
        "updated_at": iso_or_empty(t.updated_at),
    }


def activity_dict(a: ActivityLog) -> dict:
    return {
        "id": a.id,
        "business_id": a.business_id,
        "kind": a.kind,
        "channel": a.channel or "",
        "note": a.note or "",
        "created_at": iso_or_empty(a.created_at),
    }


def log_activity(db, business_id: int, kind: str, channel: str = "", note: str = ""):
    db.add(ActivityLog(business_id=business_id, kind=kind, channel=channel or "", note=note or ""))


def seed_default_templates(db):
    existing = db.scalars(select(MessageTemplate)).all()
    have = {t.channel for t in existing}
    created = False
    for channel, data in DEFAULT_TEMPLATES.items():
        if channel in have:
            continue
        db.add(MessageTemplate(
            channel=channel,
            name=data["name"],
            subject=data["subject"],
            body=data["body"],
            is_default=True,
        ))
        created = True
    if created or not existing:
        db.commit()


def parse_follow_up(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return ensure_aware(value)
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        if len(text_value) == 10:
            d = date.fromisoformat(text_value)
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        return ensure_aware(datetime.fromisoformat(text_value.replace("Z", "+00:00")))
    except ValueError as e:
        raise HTTPException(422, f"Invalid follow-up date: {e}") from e


def apply_business_updates(b: Business, data: dict, db=None):
    bool_fields = (
        "contacted", "lost", "email_sent", "whatsapp_sent", "instagram_sent",
        "marked_for_contact", "archived",
    )
    for field in bool_fields:
        if field in data and data[field] is not None:
            setattr(b, field, bool(data[field]))
    str_fields = {
        "notes": 5000,
        "outcome": 80,
        "email": 320,
        "phone": 100,
        "international_phone": 100,
        "website": 2000,
        "address": 2000,
    }
    for field, maxlen in str_fields.items():
        if field in data and data[field] is not None:
            setattr(b, field, str(data[field])[:maxlen])
    if "instagram" in data and data["instagram"] is not None:
        try:
            socials = json.loads(b.social_links_json or "{}")
        except Exception:
            socials = {}
        if not isinstance(socials, dict):
            socials = {}
        ig = str(data["instagram"] or "").strip()
        if ig:
            socials["instagram"] = ig
        else:
            socials.pop("instagram", None)
        b.social_links_json = json.dumps(socials)
    if "social_links" in data and isinstance(data["social_links"], dict):
        try:
            socials = json.loads(b.social_links_json or "{}")
        except Exception:
            socials = {}
        if not isinstance(socials, dict):
            socials = {}
        for key, value in data["social_links"].items():
            if value:
                socials[key] = str(value)
            elif key in socials:
                del socials[key]
        b.social_links_json = json.dumps(socials)
    if "next_follow_up_at" in data:
        b.next_follow_up_at = parse_follow_up(data["next_follow_up_at"])
    if b.lost:
        b.contacted = True
    if data.get("contacted") is True or data.get("email_sent") is True or data.get("whatsapp_sent") is True or data.get("instagram_sent") is True:
        if not b.last_contacted_at:
            b.last_contacted_at = utcnow()
    b.updated_at = utcnow()


def businesses_query(
    db,
    q: str = "",
    country: str = "",
    city: str = "",
    category: str = "",
    status: str = "all",
    scope: str = "city",
    contact: str = "any",
    include_archived: bool = False,
    ids: Optional[list[int]] = None,
    day: str = "",
):
    stmt = select(Business)
    if ids:
        stmt = stmt.where(Business.id.in_(ids))
        return stmt
    if not include_archived and status != "archived":
        stmt = stmt.where(or_(Business.archived.is_(False), Business.archived.is_(None)))
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(
            Business.name.ilike(like),
            Business.address.ilike(like),
            Business.phone.ilike(like),
            Business.email.ilike(like),
            Business.notes.ilike(like),
            Business.category.ilike(like),
            Business.search_labels.ilike(like),
        ))
    if scope != "global":
        if country:
            stmt = stmt.where(Business.country == country)
        if city:
            stmt = stmt.where(Business.city == city)
    if category:
        stmt = stmt.where(or_(Business.category == category, Business.search_labels.ilike(f"%{category}%")))

    # Prefer client-local calendar day when provided (YYYY-MM-DD) to avoid UTC off-by-one.
    today = parse_day_start(day or None)
    tomorrow = today + timedelta(days=1)

    if status == "contacted":
        stmt = stmt.where(Business.contacted.is_(True), Business.lost.is_(False))
    elif status == "lost":
        stmt = stmt.where(Business.lost.is_(True))
    elif status == "uncontacted":
        stmt = stmt.where(Business.contacted.is_(False), Business.lost.is_(False))
    elif status == "marked":
        stmt = stmt.where(Business.marked_for_contact.is_(True))
    elif status in ("queue", "marked_uncontacted"):
        stmt = stmt.where(
            Business.marked_for_contact.is_(True),
            Business.contacted.is_(False),
            Business.lost.is_(False),
        )
    elif status == "due":
        stmt = stmt.where(
            Business.next_follow_up_at.is_not(None),
            Business.next_follow_up_at < tomorrow,
            Business.lost.is_(False),
            Business.archived.is_(False),
        )
    elif status == "overdue":
        stmt = stmt.where(
            Business.next_follow_up_at.is_not(None),
            Business.next_follow_up_at < today,
            Business.lost.is_(False),
            Business.archived.is_(False),
        )
    elif status == "archived":
        stmt = stmt.where(Business.archived.is_(True))

    if contact == "email":
        stmt = stmt.where(Business.email != "", Business.email.is_not(None))
    elif contact == "phone":
        stmt = stmt.where(or_(
            and_(Business.phone != "", Business.phone.is_not(None)),
            and_(Business.international_phone != "", Business.international_phone.is_not(None)),
        ))
    elif contact == "instagram":
        stmt = stmt.where(Business.social_links_json.ilike("%instagram%"))
    elif contact == "any_channel":
        stmt = stmt.where(or_(
            and_(Business.email != "", Business.email.is_not(None)),
            and_(Business.phone != "", Business.phone.is_not(None)),
            and_(Business.international_phone != "", Business.international_phone.is_not(None)),
            Business.social_links_json.ilike("%instagram%"),
        ))
    elif contact == "email_unsent":
        stmt = stmt.where(
            Business.email != "", Business.email.is_not(None),
            or_(Business.email_sent.is_(False), Business.email_sent.is_(None)),
        )
    elif contact == "whatsapp_unsent":
        stmt = stmt.where(
            or_(
                and_(Business.phone != "", Business.phone.is_not(None)),
                and_(Business.international_phone != "", Business.international_phone.is_not(None)),
            ),
            or_(Business.whatsapp_sent.is_(False), Business.whatsapp_sent.is_(None)),
        )
    elif contact == "instagram_unsent":
        stmt = stmt.where(
            Business.social_links_json.ilike("%instagram%"),
            or_(Business.instagram_sent.is_(False), Business.instagram_sent.is_(None)),
        )

    return stmt


@app.get("/", response_class=HTMLResponse)
def home():
    with open(TEMPLATE_DIR / "index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/login")
def login(payload: LoginRequest, request: Request, response: Response):
    if not SESSION_SECRET:
        raise HTTPException(503, "Login is not configured. Set SESSION_SECRET.")
    username = str(payload.username or "").strip().lower()
    password = str(payload.password or "")
    if not username or not password:
        raise HTTPException(401, "Invalid username or password")

    from app.services.passwords import verify_password
    from app.models import User

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == username))
        ok = False
        role = "owner"
        if user and user.active and user.password_hash:
            ok = verify_password(password, user.password_hash)
            role = user.role or "sales"
        elif user and user.active and not user.password_hash and LOGIN_PASSWORD:
            # Legacy directory user without personal password: shared LOGIN_PASSWORD only for owner email
            if username == LOGIN_USERNAME.lower() and secure_equal(password, LOGIN_PASSWORD):
                ok = True
                role = user.role or "owner"
        elif not user and username == LOGIN_USERNAME.lower() and LOGIN_PASSWORD and secure_equal(password, LOGIN_PASSWORD):
            ok = True
            role = "owner"
        if not ok:
            raise HTTPException(401, "Invalid username or password")
        set_session_cookie(response, request, username)
        return {"username": username, "role": role, "has_password": bool(user and user.password_hash)}


@app.post("/api/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    username = verify_session(request.cookies.get(SESSION_COOKIE))
    if not username:
        raise HTTPException(401, "Authentication required")
    from app.models import User

    with SessionLocal() as db:
        u = db.scalar(select(User).where(User.email == username.lower()))
        return {
            "username": username,
            "role": (u.role if u else "owner"),
            "name": (u.name if u else ""),
            "user_id": (u.id if u else None),
        }


@app.get("/api/migrations")
def migrations(_user: str = Depends(require_login)):
    return migration_status()


LOCATIONS_PATH = Path(__file__).resolve().parent / "data" / "locations.json"
try:
    with open(LOCATIONS_PATH, encoding="utf-8") as f:
        LOCATIONS = json.load(f)
except OSError:
    LOCATIONS = {"countries": {}}


@app.get("/api/locations")
def locations(_user: str = Depends(require_login)):
    return LOCATIONS


@app.post("/api/search")
def search(payload: dict, _user: str = Depends(require_login)):
    country = str(payload.get("country", "")).strip()
    city = str(payload.get("city", "")).strip()
    category = str(payload.get("category", "")).strip()
    raw_max = payload.get("max_results", 0)
    try:
        # 0 / "all" = scan whole city (tiled) up to SEARCH_HARD_CEILING
        if raw_max in (None, "", "all", "All"):
            max_results = SEARCH_HARD_CEILING
        else:
            max_results = int(raw_max)
            if max_results <= 0:
                max_results = SEARCH_HARD_CEILING
            else:
                max_results = max(1, min(SEARCH_HARD_CEILING, max_results))
    except Exception:
        max_results = SEARCH_HARD_CEILING
    if not country or not city or not category:
        raise HTTPException(422, "Country, city, and business category are required.")
    query, places, search_meta = google_search(country, city, category, max_results)
    inserted, updated = 0, 0
    enrich_ids = []
    with SessionLocal() as db:
        for p in places:
            place_id = p.get("id")
            if not place_id:
                continue
            name = (p.get("displayName") or {}).get("text", "Unnamed business")
            website = p.get("websiteUri", "") or ""
            primary_type = (p.get("primaryTypeDisplayName") or {}).get("text", "")
            if not primary_type:
                types = p.get("types") or []
                primary_type = types[0].replace("_", " ") if types else ""
            editorial = ((p.get("editorialSummary") or {}).get("text") or "")[:2000]
            status = str(p.get("businessStatus") or "").replace("_", " ").strip()
            existing = db.scalar(select(Business).where(Business.place_id == place_id))
            # Website email/social discovery is deferred (POST /api/businesses/enrich)
            # so Places search stays fast and avoids serverless timeouts.
            if existing:
                b = existing
                updated += 1
                b.name = name or b.name
                if country and not b.country:
                    b.country = country
                if city and not b.city:
                    b.city = city
                if not b.category:
                    b.category = category
                b.search_labels = append_search_label(b.search_labels or b.category, category)
                if p.get("formattedAddress"):
                    b.address = p.get("formattedAddress", "")
                if p.get("nationalPhoneNumber"):
                    b.phone = p.get("nationalPhoneNumber", "")
                if p.get("internationalPhoneNumber"):
                    b.international_phone = p.get("internationalPhoneNumber", "")
                if website:
                    b.website = website
                if p.get("googleMapsUri"):
                    b.google_maps_url = p.get("googleMapsUri", "")
                if p.get("rating") is not None:
                    b.rating = p.get("rating")
                if p.get("userRatingCount") is not None:
                    b.reviews_count = p.get("userRatingCount")
                if status:
                    b.business_status = status
                if primary_type:
                    b.primary_type = primary_type
                if editorial:
                    b.editorial_summary = editorial
                b.updated_at = utcnow()
            else:
                b = Business(place_id=place_id)
                db.add(b)
                inserted += 1
                b.name = name
                b.country = country
                b.city = city
                b.category = category
                b.search_labels = category
                b.address = p.get("formattedAddress", "")
                b.phone = p.get("nationalPhoneNumber", "")
                b.international_phone = p.get("internationalPhoneNumber", "")
                b.website = website
                b.email = ""
                b.google_maps_url = p.get("googleMapsUri", "")
                b.rating = p.get("rating")
                b.reviews_count = p.get("userRatingCount")
                b.social_links_json = "{}"
                b.business_status = status
                b.primary_type = primary_type
                b.editorial_summary = editorial
                init_new_prospect(db, b, lead_source="google_places")
            db.flush()
            if website and (not b.email or b.social_links_json in ("", "{}")):
                enrich_ids.append(b.id)
        db.commit()
        rows = db.scalars(
            select(Business).where(
                Business.city == city,
                Business.country == country,
                or_(Business.category == category, Business.search_labels.ilike(f"%{category}%")),
                or_(Business.archived.is_(False), Business.archived.is_(None)),
            ).order_by(Business.name)
        ).all()
        return {
            "query": query,
            "found": len(places),
            "inserted": inserted,
            "updated": updated,
            "enrich_ids": enrich_ids,
            "search_meta": search_meta,
            "results": [business_dict(b) for b in rows],
        }


@app.get("/api/businesses")
def list_businesses(
    q: str = "",
    country: str = "",
    city: str = "",
    category: str = "",
    status: str = "all",
    scope: str = "city",
    contact: str = "any",
    include_archived: bool = False,
    day: str = "",
    _user: str = Depends(require_login),
):
    with SessionLocal() as db:
        stmt = businesses_query(
            db, q=q, country=country, city=city, category=category,
            status=status, scope=scope, contact=contact, include_archived=include_archived,
            day=day,
        )
        if status in ("due", "overdue", "queue", "marked_uncontacted"):
            rows = db.scalars(stmt.order_by(Business.next_follow_up_at.asc(), Business.name)).all()
        else:
            rows = db.scalars(stmt.order_by(Business.name)).all()
        return {"count": len(rows), "results": [business_dict(b) for b in rows]}


@app.get("/api/stats")
def stats(
    country: str = "",
    city: str = "",
    category: str = "",
    scope: str = "city",
    day: str = "",
    _user: str = Depends(require_login),
):
    with SessionLocal() as db:
        stmt = businesses_query(db, country=country, city=city, category=category, scope=scope, status="all", day=day)
        rows = db.scalars(stmt).all()
        today = parse_day_start(day or None)
        tomorrow = today + timedelta(days=1)
        total = len(rows)
        uncontacted = sum(1 for b in rows if not b.contacted and not b.lost)
        contacted = sum(1 for b in rows if b.contacted and not b.lost)
        lost = sum(1 for b in rows if b.lost)
        marked = sum(1 for b in rows if b.marked_for_contact and not b.archived)
        queue = sum(1 for b in rows if b.marked_for_contact and not b.contacted and not b.lost and not b.archived)
        due = sum(
            1 for b in rows
            if b.next_follow_up_at and ensure_aware(b.next_follow_up_at) < tomorrow and not b.lost and not b.archived
        )
        overdue = sum(
            1 for b in rows
            if b.next_follow_up_at and ensure_aware(b.next_follow_up_at) < today and not b.lost and not b.archived
        )
        # Global queue ignores city scope for the dedicated counter
        global_queue_stmt = businesses_query(db, status="queue", scope="global")
        global_queue = len(db.scalars(global_queue_stmt).all())
        return {
            "total": total,
            "uncontacted": uncontacted,
            "contacted": contacted,
            "lost": lost,
            "marked": marked,
            "queue": queue,
            "due": due,
            "overdue": overdue,
            "global_queue": global_queue,
        }


@app.get("/api/categories")
def categories(country: str = "", city: str = "", scope: str = "city", _user: str = Depends(require_login)):
    with SessionLocal() as db:
        stmt = select(Business.category, Business.search_labels)
        if scope != "global":
            if country:
                stmt = stmt.where(Business.country == country)
            if city:
                stmt = stmt.where(Business.city == city)
        stmt = stmt.where(or_(Business.archived.is_(False), Business.archived.is_(None)))
        values = set()
        for category, labels in db.execute(stmt).all():
            if category and str(category).strip():
                values.add(str(category).strip())
            for part in str(labels or "").split("|"):
                label = part.strip()
                if label:
                    values.add(label)
        return {"categories": sorted(values, key=lambda s: s.lower())}


class EnrichRequest(BaseModel):
    ids: list[int] = Field(default_factory=list)
    limit: int = Field(default=60, ge=1, le=60)
    force: bool = False


def enrich_business_row(b: Business, force: bool = False) -> bool:
    """Discover email/social from public website. Returns True if anything changed."""
    website = (b.website or "").strip()
    if not website:
        return False
    try:
        socials = json.loads(b.social_links_json or "{}")
    except Exception:
        socials = {}
    needs_email = force or not (b.email or "").strip()
    needs_social = force or not socials
    if not needs_email and not needs_social:
        return False
    discovered, email = discover_public_contacts(website)
    changed = False
    if needs_email and email and not (b.email or "").strip():
        b.email = email
        changed = True
    elif force and email:
        b.email = email
        changed = True
    before = b.social_links_json or "{}"
    b.social_links_json = merge_socials(b.social_links_json, discovered if discovered else {})
    if force and discovered:
        try:
            current = json.loads(b.social_links_json or "{}")
        except Exception:
            current = {}
        current.update({k: v for k, v in discovered.items() if v})
        b.social_links_json = json.dumps(current)
    if b.social_links_json != before:
        changed = True
    if changed:
        b.updated_at = utcnow()
    return changed


@app.post("/api/businesses/enrich")
def enrich_businesses(payload: EnrichRequest, _user: str = Depends(require_login)):
    ids = [int(i) for i in payload.ids if i is not None][: payload.limit]
    if not ids:
        raise HTTPException(422, "Provide ids to enrich.")
    enriched, skipped, failed = 0, 0, 0
    results = []
    with SessionLocal() as db:
        rows = db.scalars(select(Business).where(Business.id.in_(ids))).all()
        by_id = {b.id: b for b in rows}
        for bid in ids:
            b = by_id.get(bid)
            if not b:
                skipped += 1
                continue
            try:
                if enrich_business_row(b, force=payload.force):
                    enriched += 1
                    log_activity(db, b.id, "enriched", note=b.website or "")
                else:
                    skipped += 1
                results.append(business_dict(b))
            except Exception:
                failed += 1
        db.commit()
    return {"enriched": enriched, "skipped": skipped, "failed": failed, "results": results}


@app.post("/api/businesses/{business_id}/enrich")
def enrich_one(business_id: int, force: bool = False, _user: str = Depends(require_login)):
    with SessionLocal() as db:
        b = db.get(Business, business_id)
        if not b:
            raise HTTPException(404, "Business not found")
        changed = enrich_business_row(b, force=force)
        if changed:
            log_activity(db, b.id, "enriched", note=b.website or "")
        db.commit()
        db.refresh(b)
        return {"changed": changed, "business": business_dict(b)}


class UpdateBusiness(BaseModel):
    contacted: Optional[bool] = None
    lost: Optional[bool] = None
    email_sent: Optional[bool] = None
    whatsapp_sent: Optional[bool] = None
    instagram_sent: Optional[bool] = None
    marked_for_contact: Optional[bool] = None
    archived: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=5000)
    outcome: Optional[str] = Field(default=None, max_length=80)
    next_follow_up_at: Optional[str] = None
    email: Optional[str] = Field(default=None, max_length=320)
    phone: Optional[str] = Field(default=None, max_length=100)
    international_phone: Optional[str] = Field(default=None, max_length=100)
    website: Optional[str] = Field(default=None, max_length=2000)
    address: Optional[str] = Field(default=None, max_length=2000)
    instagram: Optional[str] = Field(default=None, max_length=500)


@app.patch("/api/businesses/{business_id}")
def update_business(business_id: int, payload: UpdateBusiness, _user: str = Depends(require_login)):
    with SessionLocal() as db:
        b = db.get(Business, business_id)
        if not b:
            raise HTTPException(404, "Business not found")
        data = payload.model_dump(exclude_unset=True)
        before_contacted = b.contacted
        apply_business_updates(b, data, db)
        if data.get("marked_for_contact") is True:
            log_activity(db, b.id, "marked")
        if data.get("marked_for_contact") is False:
            log_activity(db, b.id, "unmarked")
        if data.get("archived") is True:
            log_activity(db, b.id, "archived")
        if data.get("contacted") is True and not before_contacted:
            log_activity(db, b.id, "contacted")
        if "next_follow_up_at" in data:
            log_activity(db, b.id, "follow_up_set", note=str(data.get("next_follow_up_at") or ""))
        if data.get("outcome"):
            log_activity(db, b.id, "outcome", note=str(data.get("outcome")))
        db.commit()
        db.refresh(b)
        return business_dict(b)


class BulkUpdate(BaseModel):
    ids: list[int] = Field(default_factory=list)
    contacted: Optional[bool] = None
    lost: Optional[bool] = None
    marked_for_contact: Optional[bool] = None
    archived: Optional[bool] = None
    email_sent: Optional[bool] = None
    whatsapp_sent: Optional[bool] = None
    instagram_sent: Optional[bool] = None
    outcome: Optional[str] = Field(default=None, max_length=80)
    next_follow_up_at: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=5000)


@app.post("/api/businesses/bulk")
def bulk_update(payload: BulkUpdate, _user: str = Depends(require_login)):
    ids = [int(i) for i in payload.ids if i is not None]
    if not ids:
        raise HTTPException(422, "Select at least one lead.")
    data = payload.model_dump(exclude_unset=True)
    data.pop("ids", None)
    if not data:
        raise HTTPException(422, "No updates provided.")
    with SessionLocal() as db:
        rows = db.scalars(select(Business).where(Business.id.in_(ids))).all()
        for b in rows:
            apply_business_updates(b, data, db)
            log_activity(db, b.id, "bulk_update", note=json.dumps({k: data[k] for k in data}))
        db.commit()
        return {"updated": len(rows), "results": [business_dict(b) for b in rows]}


class ConfirmSent(BaseModel):
    channel: str
    next_follow_up_at: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=5000)
    outcome: Optional[str] = Field(default=None, max_length=80)


@app.post("/api/businesses/{business_id}/confirm-sent")
def confirm_sent(business_id: int, payload: ConfirmSent, _user: str = Depends(require_login)):
    channel = str(payload.channel or "").strip().lower()
    if channel not in CHANNELS:
        raise HTTPException(422, "channel must be email, whatsapp, or instagram")
    field = f"{channel}_sent"
    with SessionLocal() as db:
        from app.services.authz import get_user_by_email
        from app.services.automation import on_outreach_confirmed

        b = db.get(Business, business_id)
        if not b:
            raise HTTPException(404, "Business not found")
        setattr(b, field, True)
        b.contacted = True
        b.last_contacted_at = utcnow()
        follow = None
        if payload.next_follow_up_at is not None:
            follow = parse_follow_up(payload.next_follow_up_at)
            b.next_follow_up_at = follow
        if payload.notes is not None and str(payload.notes).strip():
            note = str(payload.notes).strip()
            existing = (b.notes or "").strip()
            b.notes = (f"{note}\n{existing}" if existing else note)[:5000]
        if payload.outcome is not None:
            b.outcome = str(payload.outcome)[:80]
        b.updated_at = utcnow()
        log_activity(db, b.id, "confirmed_sent", channel=channel, note=payload.outcome or "")
        actor = get_user_by_email(db, _user)
        on_outreach_confirmed(
            db,
            b,
            channel=channel,
            actor_id=actor.id if actor else None,
            follow_up_at=follow,
            outcome=str(payload.outcome or ""),
            notes=str(payload.notes or ""),
        )
        db.commit()
        db.refresh(b)
        return business_dict(b)


@app.get("/api/businesses/{business_id}/activity")
def business_activity(business_id: int, _user: str = Depends(require_login)):
    """Unified timeline: Discover activity_log + Sales OS activities."""
    from app.models import Activity as SalesActivity

    with SessionLocal() as db:
        b = db.get(Business, business_id)
        if not b:
            raise HTTPException(404, "Business not found")
        legacy = db.scalars(
            select(ActivityLog).where(ActivityLog.business_id == business_id).order_by(ActivityLog.created_at.desc())
        ).all()
        sales = db.scalars(
            select(SalesActivity).where(SalesActivity.business_id == business_id).order_by(SalesActivity.created_at.desc())
        ).all()
        merged = []
        for a in legacy:
            merged.append(
                {
                    "source": "discover",
                    **activity_dict(a),
                    "sort_at": a.created_at.isoformat() if a.created_at else "",
                }
            )
        for a in sales:
            merged.append(
                {
                    "source": "sales",
                    "id": a.id,
                    "business_id": a.business_id,
                    "action": a.activity_type,
                    "channel": a.channel or "",
                    "note": a.summary or a.outcome or "",
                    "created_at": a.created_at.isoformat() if a.created_at else "",
                    "sort_at": a.created_at.isoformat() if a.created_at else "",
                }
            )
        merged.sort(key=lambda x: x.get("sort_at") or "", reverse=True)
        return {"count": len(merged), "results": merged}


@app.delete("/api/businesses/{business_id}")
def delete_business(business_id: int, hard: bool = False, _user: str = Depends(require_login)):
    with SessionLocal() as db:
        b = db.get(Business, business_id)
        if not b:
            raise HTTPException(404, "Business not found")
        if hard:
            snapshot = business_dict(b)
            for a in db.scalars(select(ActivityLog).where(ActivityLog.business_id == business_id)).all():
                db.delete(a)
            db.delete(b)
            db.commit()
            return {"deleted": True, "hard": True, "id": business_id, "snapshot": snapshot}
        b.archived = True
        b.updated_at = utcnow()
        log_activity(db, b.id, "archived")
        db.commit()
        db.refresh(b)
        return {"deleted": False, "hard": False, "business": business_dict(b), "undo": "unarchive"}


@app.post("/api/businesses/{business_id}/unarchive")
def unarchive_business(business_id: int, _user: str = Depends(require_login)):
    with SessionLocal() as db:
        b = db.get(Business, business_id)
        if not b:
            raise HTTPException(404, "Business not found")
        b.archived = False
        b.updated_at = utcnow()
        log_activity(db, b.id, "unarchived")
        db.commit()
        db.refresh(b)
        return business_dict(b)


@app.post("/api/businesses/bulk-archive")
def bulk_archive(payload: BulkUpdate, _user: str = Depends(require_login)):
    payload.archived = True
    return bulk_update(payload, _user)


@app.post("/api/businesses/bulk-unarchive")
def bulk_unarchive(payload: BulkUpdate, _user: str = Depends(require_login)):
    payload.archived = False
    return bulk_update(payload, _user)


# ---- Templates (server-side) ----

class TemplatePayload(BaseModel):
    channel: str
    name: str = Field(default="", max_length=200)
    subject: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=10000)
    is_default: bool = False
    product_line: str = Field(default="", max_length=40)
    country: str = Field(default="", max_length=8)
    purpose: str = Field(default="", max_length=80)
    governance_ok: bool = True


@app.get("/api/templates")
def list_templates(_user: str = Depends(require_login)):
    with SessionLocal() as db:
        seed_default_templates(db)
        rows = db.scalars(select(MessageTemplate).order_by(MessageTemplate.channel, MessageTemplate.id)).all()
        store = {ch: [] for ch in CHANNELS}
        for t in rows:
            if t.channel in store:
                store[t.channel].append(template_dict(t))
        return store


@app.post("/api/templates")
def create_template(payload: TemplatePayload, _user: str = Depends(require_login)):
    channel = str(payload.channel or "").strip().lower()
    if channel not in CHANNELS:
        raise HTTPException(422, "Invalid channel")
    with SessionLocal() as db:
        seed_default_templates(db)
        if payload.is_default:
            for t in db.scalars(select(MessageTemplate).where(MessageTemplate.channel == channel)).all():
                t.is_default = False
        t = MessageTemplate(
            channel=channel,
            name=(payload.name or f"New {channel} template").strip()[:200],
            subject=(payload.subject or "")[:500] if channel == "email" else "",
            body=payload.body or "",
            is_default=bool(payload.is_default),
            product_line=(payload.product_line or "")[:40],
            country=(payload.country or "")[:8],
            purpose=(payload.purpose or "")[:80],
            governance_ok=bool(payload.governance_ok),
        )
        db.add(t)
        db.commit()
        db.refresh(t)
        return template_dict(t)


@app.patch("/api/templates/{template_id}")
def update_template(template_id: int, payload: TemplatePayload, _user: str = Depends(require_login)):
    with SessionLocal() as db:
        t = db.get(MessageTemplate, template_id)
        if not t:
            raise HTTPException(404, "Template not found")
        channel = str(payload.channel or t.channel).strip().lower()
        if channel not in CHANNELS:
            raise HTTPException(422, "Invalid channel")
        if payload.is_default:
            for other in db.scalars(select(MessageTemplate).where(MessageTemplate.channel == channel)).all():
                other.is_default = other.id == t.id
        t.channel = channel
        t.name = (payload.name or t.name).strip()[:200]
        t.subject = (payload.subject or "")[:500] if channel == "email" else ""
        t.body = payload.body if payload.body is not None else t.body
        t.is_default = bool(payload.is_default) or t.is_default
        t.product_line = (payload.product_line or "")[:40]
        t.country = (payload.country or "")[:8]
        t.purpose = (payload.purpose or "")[:80]
        t.governance_ok = bool(payload.governance_ok)
        t.updated_at = utcnow()
        db.commit()
        db.refresh(t)
        return template_dict(t)


@app.post("/api/templates/{template_id}/default")
def set_default_template(template_id: int, _user: str = Depends(require_login)):
    with SessionLocal() as db:
        t = db.get(MessageTemplate, template_id)
        if not t:
            raise HTTPException(404, "Template not found")
        for other in db.scalars(select(MessageTemplate).where(MessageTemplate.channel == t.channel)).all():
            other.is_default = other.id == t.id
        db.commit()
        db.refresh(t)
        return template_dict(t)


@app.delete("/api/templates/{template_id}")
def delete_template(template_id: int, _user: str = Depends(require_login)):
    with SessionLocal() as db:
        t = db.get(MessageTemplate, template_id)
        if not t:
            raise HTTPException(404, "Template not found")
        channel = t.channel
        count = len(db.scalars(select(MessageTemplate).where(MessageTemplate.channel == channel)).all())
        if count <= 1:
            raise HTTPException(422, "Keep at least one template per channel.")
        was_default = t.is_default
        db.delete(t)
        db.commit()
        if was_default:
            remaining = db.scalars(select(MessageTemplate).where(MessageTemplate.channel == channel)).first()
            if remaining:
                remaining.is_default = True
                db.commit()
        return {"deleted": True, "id": template_id}


# ---- Export / Import ----

CSV_HEADERS = [
    "Name", "Country", "City", "Category", "Address", "Phone", "International phone", "Email",
    "Website", "Google Maps", "Instagram", "Facebook", "TikTok", "LinkedIn", "YouTube", "X",
    "Rating", "Review count", "Business status", "Primary type", "Editorial summary", "Place ID",
    "Updated at", "Contacted", "Lost", "Email sent", "WhatsApp sent", "Instagram sent",
    "Marked", "Archived", "Outcome", "Next follow up", "Last contacted", "Search labels", "Notes",
]


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "on")


@app.get("/api/export.csv")
def export_csv(
    q: str = "",
    country: str = "",
    city: str = "",
    category: str = "",
    status: str = "all",
    scope: str = "city",
    contact: str = "any",
    ids: str = "",
    day: str = "",
    _user: str = Depends(require_login),
):
    selected_ids = []
    if ids.strip():
        for part in ids.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                selected_ids.append(int(part))
            except ValueError:
                raise HTTPException(422, "ids must be a comma-separated list of integers")
    with SessionLocal() as db:
        stmt = businesses_query(
            db, q=q, country=country, city=city, category=category,
            status=status, scope=scope, contact=contact, ids=selected_ids or None,
            include_archived=status == "archived", day=day,
        )
        rows = db.scalars(stmt.order_by(Business.name)).all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(CSV_HEADERS)
    for b in rows:
        try:
            s = json.loads(b.social_links_json or "{}")
        except Exception:
            s = {}
        writer.writerow([
            b.name, b.country, b.city, b.category, b.address, b.phone, b.international_phone, b.email,
            b.website, b.google_maps_url, s.get("instagram", ""), s.get("facebook", ""), s.get("tiktok", ""),
            s.get("linkedin", ""), s.get("youtube", ""), s.get("x", ""), b.rating, b.reviews_count,
            b.business_status, b.primary_type, b.editorial_summary, b.place_id,
            iso_or_empty(b.updated_at), b.contacted, b.lost,
            getattr(b, "email_sent", False), getattr(b, "whatsapp_sent", False), getattr(b, "instagram_sent", False),
            getattr(b, "marked_for_contact", False), getattr(b, "archived", False),
            getattr(b, "outcome", "") or "", iso_or_empty(getattr(b, "next_follow_up_at", None)),
            iso_or_empty(getattr(b, "last_contacted_at", None)), getattr(b, "search_labels", "") or "", b.notes,
        ])
    return StreamingResponse(
        iter(["\ufeff" + output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=maps-business-leads.csv"},
    )


@app.post("/api/import.csv")
async def import_csv(file: UploadFile = File(...), _user: str = Depends(require_login)):
    raw = await file.read()
    if not raw:
        raise HTTPException(422, "Empty CSV file")
    try:
        text_data = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text_data = raw.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text_data))
    if not reader.fieldnames:
        raise HTTPException(422, "CSV has no headers")
    inserted, updated, skipped = 0, 0, 0
    duplicate_hints = []
    with SessionLocal() as db:
        for row in reader:
            def col(*names, default=""):
                for name in names:
                    for key in row.keys():
                        if key and key.strip().lower() == name.lower():
                            return (row.get(key) or default).strip()
                return default

            place_id = col("Place ID", "place_id")
            name = col("Name", "name")
            if not place_id and not name:
                skipped += 1
                continue
            if not place_id:
                # Stable synthetic id from name+city+country for imports without Place ID
                seed = f"{name}|{col('City')}|{col('Country')}".lower()
                place_id = "import/" + hashlib.sha1(seed.encode()).hexdigest()[:24]

            existing = db.scalar(select(Business).where(Business.place_id == place_id))
            if existing:
                b = existing
                updated += 1
            else:
                # Soft duplicate detection — never auto-merge uncertain matches
                website = (col("Website") or "").strip().lower().rstrip("/")
                city = col("City")
                country = col("Country")
                soft = None
                if website:
                    soft = db.scalar(
                        select(Business).where(
                            Business.website != "",
                            Business.website.ilike(f"%{website.replace('https://','').replace('http://','')}%"),
                        )
                    )
                if not soft and name and city:
                    soft = db.scalar(
                        select(Business).where(
                            Business.name.ilike(name),
                            Business.city.ilike(city),
                        )
                    )
                hint_idx = None
                if soft:
                    duplicate_hints.append(
                        {
                            "import_name": name,
                            "import_place_id": place_id,
                            "matched_id": soft.id,
                            "matched_name": soft.name,
                            "reason": "website_or_name_city",
                            "action": "created_new_no_merge",
                        }
                    )
                    hint_idx = len(duplicate_hints) - 1
                b = Business(place_id=place_id, name=name or "Imported business")
                db.add(b)
                inserted += 1
                with db.no_autoflush:
                    init_new_prospect(db, b, lead_source="csv_import")
                db.flush()
                if hint_idx is not None:
                    duplicate_hints[hint_idx]["new_id"] = b.id
                    duplicate_hints[hint_idx]["existing_id"] = soft.id

            b.name = name or b.name or "Imported business"
            b.country = col("Country") or b.country
            b.city = col("City") or b.city
            category = col("Category")
            if category:
                if not b.category:
                    b.category = category
                b.search_labels = append_search_label(b.search_labels or b.category, category)
            b.address = col("Address") or b.address
            b.phone = col("Phone") or b.phone
            b.international_phone = col("International phone", "International Phone") or b.international_phone
            b.email = col("Email") or b.email
            b.website = col("Website") or b.website
            b.google_maps_url = col("Google Maps", "Google Maps URL") or b.google_maps_url
            try:
                socials = json.loads(b.social_links_json or "{}")
            except Exception:
                socials = {}
            for key, header in (
                ("instagram", "Instagram"), ("facebook", "Facebook"), ("tiktok", "TikTok"),
                ("linkedin", "LinkedIn"), ("youtube", "YouTube"), ("x", "X"),
            ):
                val = col(header)
                if val:
                    socials[key] = val
            b.social_links_json = json.dumps(socials)
            rating = col("Rating")
            if rating:
                try:
                    b.rating = float(rating)
                except ValueError:
                    pass
            reviews = col("Review count", "Reviews")
            if reviews:
                try:
                    b.reviews_count = int(float(reviews))
                except ValueError:
                    pass
            b.business_status = col("Business status") or b.business_status
            b.primary_type = col("Primary type") or b.primary_type
            b.editorial_summary = col("Editorial summary") or b.editorial_summary
            if col("Contacted"):
                b.contacted = truthy(col("Contacted"))
            if col("Lost"):
                b.lost = truthy(col("Lost"))
            if col("Email sent"):
                b.email_sent = truthy(col("Email sent"))
            if col("WhatsApp sent"):
                b.whatsapp_sent = truthy(col("WhatsApp sent"))
            if col("Instagram sent"):
                b.instagram_sent = truthy(col("Instagram sent"))
            if col("Marked", "Marked for contact"):
                b.marked_for_contact = truthy(col("Marked", "Marked for contact"))
            if col("Archived"):
                b.archived = truthy(col("Archived"))
            if col("Outcome"):
                b.outcome = col("Outcome")[:80]
            if col("Notes"):
                b.notes = col("Notes")[:5000]
            if col("Search labels"):
                b.search_labels = col("Search labels")[:500]
            follow = col("Next follow up", "Next follow-up")
            if follow:
                try:
                    b.next_follow_up_at = parse_follow_up(follow)
                except HTTPException:
                    pass
            last = col("Last contacted")
            if last:
                try:
                    b.last_contacted_at = parse_follow_up(last)
                except HTTPException:
                    pass
            b.updated_at = utcnow()
        db.commit()
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "duplicate_hints": duplicate_hints[:100],
        "duplicate_hint_count": len(duplicate_hints),
        "note": "Uncertain matches are reported only — records are never auto-merged.",
    }
