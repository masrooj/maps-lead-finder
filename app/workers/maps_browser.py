"""Playwright Maps Reserve/Book checker (v2).

Requires optional dependency: playwright (+ browser install).
When Playwright is missing or MAPS_VERIFY_ENABLED is false, returns uncertain.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

# CTA button/link text patterns (multi-locale light set)
_CTA_RE = re.compile(
    r"\b(reserve|book\s*online|book\s*now|book|appointments?|reservar|réserver|buchen)\b",
    re.I,
)

_BOOKING_HOST_HINTS = (
    "fresha.com",
    "booksy.com",
    "treatwell.",
    "setmore.com",
    "calendly.com",
    "opentable.com",
    "resy.com",
    "squareup.com",
    "mindbody",
    "vagaro.com",
    "google.com/maps/reserve",
    "reserve.google",
    "wheniwork",
    "simplybook",
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _host_hint(url: str) -> str | None:
    low = (url or "").lower()
    for h in _BOOKING_HOST_HINTS:
        if h in low:
            return h
    return None


def run_maps_booking_check(google_maps_url: str) -> dict[str, Any]:
    """Open Maps URL and try Reserve/Book. Returns raw verify dict."""
    url = (google_maps_url or "").strip()
    if not url:
        return {
            "status": "uncertain",
            "vendor_hint": None,
            "evidence": "empty_url",
            "checked_at": _utcnow_iso(),
        }

    # No separate enable key — auto: serverless never; else Playwright if installed.
    if os.getenv("VERCEL", "").strip() or os.getenv("AWS_LAMBDA_FUNCTION_NAME", "").strip():
        return {
            "status": "uncertain",
            "vendor_hint": None,
            "evidence": "serverless_no_browser — use Scan booking (Places+website) on Vercel",
            "checked_at": _utcnow_iso(),
        }

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "status": "uncertain",
            "vendor_hint": None,
            "evidence": "playwright_not_installed — Scan booking uses Places API instead",
            "checked_at": _utcnow_iso(),
        }

    timeout_ms = int(os.getenv("MAPS_VERIFY_TIMEOUT_MS", "25000") or "25000")
    evidence_parts: list[str] = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                locale="en-US",
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            # Dismiss common consent banners (best-effort)
            for label in ("Accept all", "I agree", "Accept", "Got it"):
                try:
                    btn = page.get_by_role("button", name=re.compile(label, re.I))
                    if btn.count() and btn.first.is_visible():
                        btn.first.click(timeout=2000)
                        evidence_parts.append(f"dismissed:{label}")
                        break
                except Exception:
                    pass

            # Find Reserve / Book CTA
            cta = None
            for role in ("link", "button"):
                try:
                    loc = page.get_by_role(role, name=_CTA_RE)
                    if loc.count():
                        cta = loc.first
                        break
                except Exception:
                    continue

            if cta is None:
                # Fallback: text search
                try:
                    loc = page.locator("a, button").filter(has_text=_CTA_RE)
                    if loc.count():
                        cta = loc.first
                except Exception:
                    cta = None

            if cta is None:
                browser.close()
                return {
                    "status": "no_booking",
                    "vendor_hint": None,
                    "evidence": "no_reserve_book_cta|" + ";".join(evidence_parts),
                    "checked_at": _utcnow_iso(),
                }

            try:
                cta_text = (cta.inner_text(timeout=2000) or "").strip()[:80]
            except Exception:
                cta_text = "cta"
            evidence_parts.append(f"cta:{cta_text}")

            popup = None
            try:
                with context.expect_page(timeout=5000) as pop_info:
                    cta.click(timeout=5000)
                popup = pop_info.value
            except Exception:
                try:
                    cta.click(timeout=5000)
                except Exception as click_exc:
                    browser.close()
                    return {
                        "status": "uncertain",
                        "vendor_hint": None,
                        "evidence": f"click_failed:{click_exc}|" + ";".join(evidence_parts),
                        "checked_at": _utcnow_iso(),
                    }

            page.wait_for_timeout(1500)
            targets = [page]
            if popup:
                targets.append(popup)

            for t in targets:
                try:
                    t.wait_for_load_state("domcontentloaded", timeout=8000)
                except Exception:
                    pass
                cur = t.url or ""
                hint = _host_hint(cur)
                if hint:
                    browser.close()
                    host = (urlparse(cur).hostname or hint).removeprefix("www.")
                    return {
                        "status": "has_booking",
                        "vendor_hint": host,
                        "evidence": f"post_click_url:{cur}|" + ";".join(evidence_parts),
                        "checked_at": _utcnow_iso(),
                    }
                try:
                    body = t.content()[:200_000].lower()
                except Exception:
                    body = ""
                if any(h in body for h in _BOOKING_HOST_HINTS) or (
                    "select a time" in body or "choose a time" in body or "book appointment" in body
                ):
                    browser.close()
                    return {
                        "status": "has_booking",
                        "vendor_hint": "GoogleReserve",
                        "evidence": "booking_ui_shell|" + ";".join(evidence_parts),
                        "checked_at": _utcnow_iso(),
                    }

            # CTA existed but could not confirm booking UI
            browser.close()
            return {
                "status": "uncertain",
                "vendor_hint": None,
                "evidence": "cta_clicked_unconfirmed|" + ";".join(evidence_parts),
                "checked_at": _utcnow_iso(),
            }
    except Exception as exc:
        return {
            "status": "error",
            "vendor_hint": None,
            "evidence": f"playwright_error:{exc}",
            "checked_at": _utcnow_iso(),
        }
