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
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, create_engine, or_, select, text, and_
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./local-dev.db")
LOGIN_USERNAME = "hello@appointhive.com"
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "").strip()
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
SESSION_COOKIE = "mlf_session"
SESSION_DAYS = 14
CHANNELS = ("email", "whatsapp", "instagram")
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
}

_engine_options = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    _engine_options["connect_args"] = {"check_same_thread": False}
else:
    _engine_options.update({"pool_size": 1, "max_overflow": 0, "pool_recycle": 300})
engine = create_engine(DATABASE_URL, **_engine_options)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Business(Base):
    __tablename__ = "businesses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    place_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    country: Mapped[str] = mapped_column(String(120), default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    category: Mapped[str] = mapped_column(String(200), default="")
    address: Mapped[str] = mapped_column(Text, default="")
    phone: Mapped[str] = mapped_column(String(100), default="")
    international_phone: Mapped[str] = mapped_column(String(100), default="")
    website: Mapped[str] = mapped_column(Text, default="")
    email: Mapped[str] = mapped_column(String(320), default="")
    google_maps_url: Mapped[str] = mapped_column(Text, default="")
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reviews_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latest_review_date: Mapped[str] = mapped_column(String(100), default="")
    latest_review_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    latest_review_text: Mapped[str] = mapped_column(Text, default="")
    social_links_json: Mapped[str] = mapped_column(Text, default="{}")
    review_response_time: Mapped[str] = mapped_column(String(150), default="Not provided by Places API")
    business_status: Mapped[str] = mapped_column(String(80), default="")
    primary_type: Mapped[str] = mapped_column(String(200), default="")
    editorial_summary: Mapped[str] = mapped_column(Text, default="")
    contacted: Mapped[bool] = mapped_column(Boolean, default=False)
    lost: Mapped[bool] = mapped_column(Boolean, default=False)
    email_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    whatsapp_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    instagram_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    marked_for_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    next_follow_up_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_contacted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(80), default="")
    search_labels: Mapped[str] = mapped_column(String(500), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MessageTemplate(Base):
    __tablename__ = "message_templates"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ActivityLog(Base):
    __tablename__ = "activity_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    business_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(80), default="")
    channel: Mapped[str] = mapped_column(String(40), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


Base.metadata.create_all(engine)
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def sql_statements(script: str) -> list[str]:
    lines = [line for line in script.splitlines() if not line.strip().startswith("--")]
    return [part.strip() for part in "\n".join(lines).split(";") if part.strip()]


def adapt_migration_sql(statement: str) -> str:
    if engine.dialect.name != "sqlite":
        return statement
    return re.sub(r"(?i)\bADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\b", "ADD COLUMN", statement)


def apply_migrations():
    MIGRATIONS_DIR.mkdir(parents=True, exist_ok=True)
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version VARCHAR(255) PRIMARY KEY, "
            "applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
        applied = {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations"))}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            for statement in sql_statements(path.read_text(encoding="utf-8")):
                sql = adapt_migration_sql(statement)
                try:
                    conn.execute(text(sql))
                except Exception as e:
                    msg = str(e).lower()
                    if "duplicate column" in msg or "already exists" in msg:
                        continue
                    raise
            conn.execute(text("INSERT INTO schema_migrations (version) VALUES (:version)"), {"version": path.name})


apply_migrations()


def migration_status():
    files = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version, applied_at FROM schema_migrations ORDER BY version")).all()
    applied = [{"version": version, "applied_at": applied_at.isoformat() if applied_at else ""} for version, applied_at in rows]
    applied_names = {item["version"] for item in applied}
    return {"applied": applied, "pending": [name for name in files if name not in applied_names]}


app = FastAPI(title="Maps Lead Finder", version="1.1.0")
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / "app" / "templates"
app.mount("/static", StaticFiles(directory=str(TEMPLATE_DIR)), name="static")


class LoginRequest(BaseModel):
    username: str = Field(default="", max_length=320)
    password: str = Field(default="", max_length=500)


def auth_configured() -> bool:
    return bool(LOGIN_PASSWORD and SESSION_SECRET)


def secure_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(
        hashlib.sha256(left.encode()).digest(),
        hashlib.sha256(right.encode()).digest(),
    )


def sign_session(username: str, expires_at: int) -> str:
    payload = f"{username.lower()}:{expires_at}"
    signature = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{expires_at}.{signature}"


def verify_session(cookie: Optional[str]) -> Optional[str]:
    if not cookie or not auth_configured():
        return None
    try:
        expires_str, signature = cookie.split(".", 1)
        expires_at = int(expires_str)
    except (ValueError, AttributeError):
        return None
    if expires_at < int(datetime.now(timezone.utc).timestamp()):
        return None
    expected = sign_session(LOGIN_USERNAME, expires_at)
    if not hmac.compare_digest(cookie, expected):
        return None
    return LOGIN_USERNAME


def set_session_cookie(response: Response, request: Request):
    expires_at = int((datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).timestamp())
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
    response.set_cookie(
        key=SESSION_COOKIE,
        value=sign_session(LOGIN_USERNAME, expires_at),
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


def geocode_city_viewport(city: str, country: str) -> Optional[dict]:
    """Return {"low": {lat,lng}, "high": {lat,lng}} city viewport, or None."""
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
    vp = (result.get("geometry") or {}).get("viewport") or {}
    ne, sw = vp.get("northeast") or {}, vp.get("southwest") or {}
    if not all(k in ne for k in ("lat", "lng")) or not all(k in sw for k in ("lat", "lng")):
        loc = (result.get("geometry") or {}).get("location") or {}
        if "lat" not in loc or "lng" not in loc:
            return None
        # ~15km box around point if no viewport
        d = 0.12
        return {
            "low": {"latitude": loc["lat"] - d, "longitude": loc["lng"] - d},
            "high": {"latitude": loc["lat"] + d, "longitude": loc["lng"] + d},
        }
    return {
        "low": {"latitude": sw["lat"], "longitude": sw["lng"]},
        "high": {"latitude": ne["lat"], "longitude": ne["lng"]},
    }


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
        body: dict[str, Any] = {
            "textQuery": query,
            "pageSize": min(PLACES_PAGE_SIZE, limit - len(all_places)),
        }
        if page_token:
            body["pageToken"] = page_token
            # nextPageToken is often not valid for a couple of seconds
            time.sleep(2)
        if location_restriction:
            body["locationRestriction"] = {"rectangle": location_restriction}
        try:
            response = requests.post(url, headers=headers, json=body, timeout=45)
        except requests.RequestException as e:
            raise HTTPException(502, f"Google Places API request failed: {e}") from e
        if not response.ok and page_token and response.status_code in (400, 429):
            time.sleep(2.5)
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
            if location_restriction and pages > 0:
                break
            if location_restriction and pages == 0:
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
        return query, places, {"tiles": 1, "mode": "single_fallback", "note": "Could not geocode city for full scan"}

    tiles = city_grid_rectangles(viewport, want)
    # Also run one unrestricted city query first (often highest-quality hits)
    seen: dict[str, dict] = {}
    for p in places_text_search_pages(query, PLACES_QUERY_CAP):
        pid = p.get("id")
        if pid:
            seen[pid] = p

    tiles_used = 1
    for rect in tiles:
        if len(seen) >= want:
            break
        batch = places_text_search_pages(
            query,
            min(PLACES_QUERY_CAP, want - len(seen)),
            location_restriction=rect,
        )
        tiles_used += 1
        for p in batch:
            pid = p.get("id")
            if pid and pid not in seen:
                seen[pid] = p
        if len(seen) >= want:
            break

    places = list(seen.values())[:want]
    return query, places, {"tiles": tiles_used, "mode": "city_scan", "unique": len(places)}


def iso_or_empty(dt: Optional[datetime]) -> str:
    dt = ensure_aware(dt)
    return dt.isoformat() if dt else ""


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
    }


def template_dict(t: MessageTemplate) -> dict:
    return {
        "id": t.id,
        "channel": t.channel,
        "name": t.name,
        "subject": t.subject or "",
        "body": t.body or "",
        "isDefault": bool(t.is_default),
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
    if existing:
        return
    for channel, data in DEFAULT_TEMPLATES.items():
        db.add(MessageTemplate(
            channel=channel,
            name=data["name"],
            subject=data["subject"],
            body=data["body"],
            is_default=True,
        ))
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
    if not auth_configured():
        raise HTTPException(503, "Login is not configured. Set LOGIN_PASSWORD and SESSION_SECRET.")
    username = str(payload.username or "").strip().lower()
    password = str(payload.password or "")
    username_ok = secure_equal(username, LOGIN_USERNAME.lower())
    password_ok = secure_equal(password, LOGIN_PASSWORD)
    if not (username_ok and password_ok):
        raise HTTPException(401, "Invalid username or password")
    set_session_cookie(response, request)
    return {"username": LOGIN_USERNAME}


@app.post("/api/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    username = verify_session(request.cookies.get(SESSION_COOKIE))
    if not username:
        raise HTTPException(401, "Authentication required")
    return {"username": username}


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
        b = db.get(Business, business_id)
        if not b:
            raise HTTPException(404, "Business not found")
        setattr(b, field, True)
        b.contacted = True
        b.last_contacted_at = utcnow()
        if payload.next_follow_up_at is not None:
            b.next_follow_up_at = parse_follow_up(payload.next_follow_up_at)
        if payload.notes is not None and str(payload.notes).strip():
            note = str(payload.notes).strip()
            existing = (b.notes or "").strip()
            b.notes = (f"{note}\n{existing}" if existing else note)[:5000]
        if payload.outcome is not None:
            b.outcome = str(payload.outcome)[:80]
        b.updated_at = utcnow()
        log_activity(db, b.id, "confirmed_sent", channel=channel, note=payload.outcome or "")
        db.commit()
        db.refresh(b)
        return business_dict(b)


@app.get("/api/businesses/{business_id}/activity")
def business_activity(business_id: int, _user: str = Depends(require_login)):
    with SessionLocal() as db:
        b = db.get(Business, business_id)
        if not b:
            raise HTTPException(404, "Business not found")
        rows = db.scalars(
            select(ActivityLog).where(ActivityLog.business_id == business_id).order_by(ActivityLog.created_at.desc())
        ).all()
        return {"count": len(rows), "results": [activity_dict(a) for a in rows]}


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
                b = Business(place_id=place_id)
                db.add(b)
                inserted += 1

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
    return {"inserted": inserted, "updated": updated, "skipped": skipped}
