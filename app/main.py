import csv
import io
import json
import os
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import unquote, urlparse

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, create_engine, or_, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./local-dev.db")
# Disable persistent connection pooling in serverless environments such as Vercel.
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
    contacted: Mapped[bool] = mapped_column(Boolean, default=False)
    lost: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

Base.metadata.create_all(engine)
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

def sql_statements(script: str) -> list[str]:
    lines = [line for line in script.splitlines() if not line.strip().startswith("--")]
    return [part.strip() for part in "\n".join(lines).split(";") if part.strip()]

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
                conn.execute(text(statement))
            conn.execute(text("INSERT INTO schema_migrations (version) VALUES (:version)"), {"version": path.name})

apply_migrations()

def migration_status():
    files = [path.name for path in sorted(MIGRATIONS_DIR.glob("*.sql"))]
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT version, applied_at FROM schema_migrations ORDER BY version")).all()
    applied = [{"version": version, "applied_at": applied_at.isoformat() if applied_at else ""} for version, applied_at in rows]
    applied_names = {item["version"] for item in applied}
    return {"applied": applied, "pending": [name for name in files if name not in applied_names]}
app = FastAPI(title="Maps Lead Finder", version="1.0.0")
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / "app" / "templates"
app.mount("/static", StaticFiles(directory=str(TEMPLATE_DIR)), name="static")

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

def discover_public_contacts(url: str) -> tuple[dict, str]:
    if not url or os.getenv("DISCOVER_SOCIAL_LINKS", "true").lower() != "true":
        return {}, ""
    if not is_public_web_url(url):
        return {}, ""
    try:
        r = requests.get(url, timeout=4, headers={"User-Agent": "MapsLeadFinder/1.0 (+public business website link discovery)"})
        if not is_public_web_url(r.url) or "text/html" not in r.headers.get("content-type", "").lower():
            return {}, ""
        html = r.text[:1_500_000]
        links = {}
        for href in re.findall(r'''href\s*=\s*["']([^"']+)["']''', html, re.I):
            h = href.strip()
            if h.startswith("//"):
                h = "https:" + h
            domain = (urlparse(h).hostname or "").lower().removeprefix("www.")
            for d, key in SOCIAL_DOMAINS.items():
                if domain == d or domain.endswith("." + d):
                    links.setdefault(key, h)
        return links, extract_emails(html)
    except Exception:
        return {}, ""

def google_search(country: str, city: str, category: str, max_results: int):
    key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not key or key.startswith("replace_"):
        raise HTTPException(400, "Set GOOGLE_MAPS_API_KEY in your .env file first.")
    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json", "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,places.internationalPhoneNumber,places.websiteUri,places.googleMapsUri,places.rating,places.userRatingCount,places.reviews,places.types,nextPageToken"
    }
    query = f"{category} in {city}, {country}"
    all_places, page_token = [], None
    # Places Text Search is capped at 60 results per query; request up to three pages.
    while len(all_places) < min(max_results, 60):
        body = {"textQuery": query, "pageSize": min(20, max_results - len(all_places))}
        if page_token:
            body["pageToken"] = page_token
        try:
            response = requests.post(url, headers=headers, json=body, timeout=30)
        except requests.RequestException as e:
            raise HTTPException(502, f"Google Places API request failed: {e}")
        if not response.ok:
            try: detail = response.json().get("error", {}).get("message", response.text)
            except Exception: detail = response.text
            raise HTTPException(response.status_code, f"Google Places API: {detail}")
        data = response.json()
        all_places.extend(data.get("places", []))
        page_token = data.get("nextPageToken")
        if not page_token or len(all_places) >= min(max_results, 60):
            break
    return query, all_places[:min(max_results, 60)]

def business_dict(b: Business):
    import json
    try: socials = json.loads(b.social_links_json or "{}")
    except Exception: socials = {}
    return {"id": b.id, "place_id": b.place_id, "name": b.name, "country": b.country, "city": b.city,
        "category": b.category, "address": b.address, "phone": b.phone, "international_phone": b.international_phone,
        "website": b.website, "email": b.email, "google_maps_url": b.google_maps_url, "rating": b.rating, "reviews_count": b.reviews_count,
        "latest_review_date": b.latest_review_date, "latest_review_rating": b.latest_review_rating,
        "latest_review_text": b.latest_review_text, "social_links": socials, "review_response_time": b.review_response_time,
        "contacted": b.contacted, "lost": b.lost, "notes": b.notes,
        "updated_at": b.updated_at.isoformat() if b.updated_at else ""}

@app.get("/", response_class=HTMLResponse)
def home():
    with open(TEMPLATE_DIR / "index.html", encoding="utf-8") as f: return f.read()

@app.get("/api/health")
def health(): return {"ok": True}

@app.get("/api/migrations")
def migrations():
    return migration_status()

LOCATIONS_PATH = Path(__file__).resolve().parent / "data" / "locations.json"
try:
    with open(LOCATIONS_PATH, encoding="utf-8") as f:
        LOCATIONS = json.load(f)
except OSError:
    LOCATIONS = {"countries": {}}

@app.get("/api/locations")
def locations():
    return LOCATIONS

@app.post("/api/search")
def search(payload: dict):
    country = str(payload.get("country", "")).strip()
    city = str(payload.get("city", "")).strip()
    category = str(payload.get("category", "")).strip()
    try: max_results = max(1, min(60, int(payload.get("max_results", 60))))
    except Exception: max_results = 60
    if not country or not city or not category:
        raise HTTPException(422, "Country, city, and business category are required.")
    query, places = google_search(country, city, category, max_results)
    import json
    inserted, updated = 0, 0
    with SessionLocal() as db:
        for p in places:
            place_id = p.get("id")
            if not place_id: continue
            name = (p.get("displayName") or {}).get("text", "Unnamed business")
            reviews = p.get("reviews") or []
            reviews.sort(key=lambda r: r.get("publishTime", ""), reverse=True)
            latest = reviews[0] if reviews else {}
            website = p.get("websiteUri", "")
            existing = db.scalar(select(Business).where(Business.place_id == place_id))
            if existing:
                b = existing; updated += 1
            else:
                b = Business(place_id=place_id); db.add(b); inserted += 1
            b.name = name; b.country = country; b.city = city; b.category = category
            b.address = p.get("formattedAddress", "")
            b.phone = p.get("nationalPhoneNumber", "")
            b.international_phone = p.get("internationalPhoneNumber", "")
            b.website = website
            socials, email = discover_public_contacts(website)
            b.email = email
            b.google_maps_url = p.get("googleMapsUri", "")
            b.rating = p.get("rating")
            b.reviews_count = p.get("userRatingCount")
            b.latest_review_date = latest.get("publishTime", "")
            b.latest_review_rating = latest.get("rating")
            b.latest_review_text = ((latest.get("text") or {}).get("text", ""))[:2000]
            b.social_links_json = json.dumps(socials)
            b.review_response_time = "Not provided by Places API"
        db.commit()
        rows = db.scalars(select(Business).where(Business.city == city, Business.country == country, Business.category == category).order_by(Business.name)).all()
        return {"query": query, "found": len(places), "inserted": inserted, "updated": updated, "results": [business_dict(b) for b in rows]}

@app.get("/api/businesses")
def list_businesses(q: str = "", country: str = "", city: str = "", category: str = "", status: str = "all"):
    with SessionLocal() as db:
        stmt = select(Business)
        if q: stmt = stmt.where(or_(Business.name.ilike(f"%{q}%"), Business.address.ilike(f"%{q}%"), Business.phone.ilike(f"%{q}%"), Business.email.ilike(f"%{q}%")))
        if country: stmt = stmt.where(Business.country == country)
        if city: stmt = stmt.where(Business.city == city)
        if category: stmt = stmt.where(Business.category == category)
        if status == "contacted": stmt = stmt.where(Business.contacted.is_(True))
        elif status == "lost": stmt = stmt.where(Business.lost.is_(True))
        elif status == "uncontacted": stmt = stmt.where(Business.contacted.is_(False), Business.lost.is_(False))
        rows = db.scalars(stmt.order_by(Business.name)).all()
        return {"count": len(rows), "results": [business_dict(b) for b in rows]}

class UpdateBusiness(BaseModel):
    contacted: Optional[bool] = None
    lost: Optional[bool] = None
    notes: Optional[str] = Field(default=None, max_length=5000)

@app.patch("/api/businesses/{business_id}")
def update_business(business_id: int, payload: UpdateBusiness):
    with SessionLocal() as db:
        b = db.get(Business, business_id)
        if not b: raise HTTPException(404, "Business not found")
        data = payload.model_dump(exclude_unset=True)
        for k, v in data.items(): setattr(b, k, v)
        if b.lost: b.contacted = True
        db.commit(); db.refresh(b)
        return business_dict(b)

@app.get("/api/export.csv")
def export_csv(q: str = "", country: str = "", city: str = "", category: str = "", status: str = "all"):
    with SessionLocal() as db:
        stmt = select(Business)
        if q: stmt = stmt.where(or_(Business.name.ilike(f"%{q}%"), Business.address.ilike(f"%{q}%"), Business.phone.ilike(f"%{q}%"), Business.email.ilike(f"%{q}%")))
        if country: stmt = stmt.where(Business.country == country)
        if city: stmt = stmt.where(Business.city == city)
        if category: stmt = stmt.where(Business.category == category)
        if status == "contacted": stmt = stmt.where(Business.contacted.is_(True))
        elif status == "lost": stmt = stmt.where(Business.lost.is_(True))
        elif status == "uncontacted": stmt = stmt.where(Business.contacted.is_(False), Business.lost.is_(False))
        rows = db.scalars(stmt.order_by(Business.name)).all()
    import json
    output = io.StringIO(); writer = csv.writer(output)
    writer.writerow(["Name", "Country", "City", "Category", "Address", "Phone", "International phone", "Email", "Website", "Google Maps", "Instagram", "Facebook", "TikTok", "LinkedIn", "YouTube", "X", "Rating", "Review count", "Latest review date", "Latest review rating", "Latest review text", "Review response time", "Contacted", "Lost", "Notes"])
    for b in rows:
        try: s = json.loads(b.social_links_json or "{}")
        except Exception: s = {}
        writer.writerow([b.name,b.country,b.city,b.category,b.address,b.phone,b.international_phone,b.email,b.website,b.google_maps_url,s.get("instagram",""),s.get("facebook",""),s.get("tiktok",""),s.get("linkedin",""),s.get("youtube",""),s.get("x",""),b.rating,b.reviews_count,b.latest_review_date,b.latest_review_rating,b.latest_review_text,b.review_response_time,b.contacted,b.lost,b.notes])
    return StreamingResponse(iter(["\ufeff" + output.getvalue()]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=maps-business-leads.csv"})
