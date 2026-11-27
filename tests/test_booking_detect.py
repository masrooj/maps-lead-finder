"""Unit tests for website booking detector (Discover Scan booking v1)."""
from __future__ import annotations

from app.services.booking_detect import (
    detect_booking_from_html,
    detect_booking_on_website,
    match_booking_in_html,
)


def test_fresha_script_embed_hit():
    html = """
    <html><body>
      <script src="https://www.fresha.com/widget.js"></script>
      <a href="https://www.fresha.com/a/salon-x">Book</a>
    </body></html>
    """
    assert match_booking_in_html(html) == "Fresha"
    out = detect_booking_from_html(html)
    assert out["has_booking"] is True
    assert out["software_name"] == "Fresha"


def test_booksy_url_hit():
    html = '<a href="https://booksy.com/en-us/123-barber">Book online</a>'
    assert match_booking_in_html(html) == "Booksy"


def test_calendly_iframe_hit():
    html = '<iframe src="https://calendly.com/shop/30min"></iframe>'
    assert match_booking_in_html(html) == "Calendly"


def test_treatwell_hit():
    html = 'Book via <a href="https://www.treatwell.co.uk/place/x">Treatwell</a>'
    assert match_booking_in_html(html) == "Treatwell"


def test_setmore_hit():
    html = '<script src="https://assets.setmore.com/widget.js"></script>'
    assert match_booking_in_html(html) == "Setmore"


def test_plain_site_miss():
    html = """
    <html><body>
      <h1>Local Barber</h1>
      <p>Call us to book. We take walk-ins.</p>
      <a href="/about">About</a>
      <a href="/contact">Contact</a>
    </body></html>
    """
    assert match_booking_in_html(html) is None
    out = detect_booking_from_html(html, page_url="https://localbarber.example/")
    assert out["has_booking"] is False
    assert out["software_name"] == ""


def test_same_site_book_path_alone_is_not_enough():
    """Conservative: /book on own site without provider signature → miss."""
    html = '<a href="/book">Book now</a><a href="/booking">Booking</a>'
    assert match_booking_in_html(html, page_url="https://myshop.example/") is None


def test_detect_on_website_uses_injected_fetch():
    html = '<script src="https://www.fresha.com/embed.js"></script>'

    def fake_fetch(url: str, timeout: float = 5.0):
        return url, html

    out = detect_booking_on_website(
        "https://myshop.example/",
        fetch_html=fake_fetch,
        crawl_book_pages=False,
    )
    assert out["has_booking"] is True
    assert out["software_name"] == "Fresha"


def test_detect_on_website_empty_url():
    assert detect_booking_on_website("") == {"has_booking": False, "software_name": ""}


def test_detect_direct_provider_url_without_fetch():
    out = detect_booking_on_website("https://booksy.com/en-gb/999-cut")
    assert out["has_booking"] is True
    assert out["software_name"] == "Booksy"


def test_square_appointments_signature():
    html = 'href="https://squareup.com/appointments/book/ABC"'
    assert match_booking_in_html(html) == "Square Appointments"


def test_places_reservable_used_when_website_miss(monkeypatch):
    from types import SimpleNamespace

    from app.services import booking_detect as bd

    monkeypatch.setattr(
        bd,
        "detect_booking_on_website",
        lambda url, **kw: {"has_booking": False, "software_name": ""},
    )
    monkeypatch.setattr(bd, "places_reservable", lambda place_id, **kw: True)

    b = SimpleNamespace(website="https://plain.example/", place_id="places/ChIJ_test")
    out = bd.detect_booking_for_business(b)
    assert out["has_booking"] is True
    assert out["software_name"] == "Google Reserve"
    assert out["source"] == "places_reservable"


def test_places_resource_name_skips_synthetic():
    from app.services.booking_detect import _places_resource_name

    assert _places_resource_name("import/abc") == ""
    assert _places_resource_name("places/ChIJ123") == "places/ChIJ123"
    assert _places_resource_name("ChIJ123") == "places/ChIJ123"
