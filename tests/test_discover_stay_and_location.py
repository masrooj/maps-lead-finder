"""QA guards: mark/booking stay on current panel; Discover country/city session (no AU/Sydney hard default)."""

from pathlib import Path

from tests.conftest import frontend_source

_REPO = Path(__file__).resolve().parents[1]
_RUNTIME = (_REPO / "app/static/js/app/runtime.js").read_text(encoding="utf-8")
_CQ = (_REPO / "app/static/js/panels/contact-queue.js").read_text(encoding="utf-8")


def _fn_body(src: str, name: str) -> str:
    """Best-effort extract of a top-level function body by name."""
    import re

    m = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
        src,
    )
    assert m, f"function {name} not found"
    start = m.end()
    depth = 1
    i = start
    while i < len(src) and depth:
        ch = src[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return src[start : i - 1]


def test_no_australia_sydney_hard_default_in_discover_location():
    fill = _fn_body(_RUNTIME, "fillCities")
    load = _fn_body(_RUNTIME, "loadLocations")
    sync = _fn_body(_RUNTIME, "syncDiscoverLocation")
    assert "Australia" not in fill
    assert "Sydney" not in fill
    assert "Australia" not in load
    assert "Sydney" not in load
    assert "Australia" not in sync
    assert "Sydney" not in sync
    assert "discoverCountry" in _RUNTIME
    assert "discoverCity" in _RUNTIME
    assert "Select country" in load


def test_saved_view_and_search_sync_discover_location():
    assert "syncDiscoverLocation(v.filters.country" in _RUNTIME
    assert "const synced=syncDiscoverLocation()" in _RUNTIME


def test_discover_mark_and_booking_do_not_jump_to_contact_queue():
    toggle = _fn_body(_RUNTIME, "toggleMark")
    assert "setAppPanel" not in toggle
    assert "setContactQueueTab" not in toggle
    assert "contactQueue" not in toggle

    # Bulk mark handler: drop rows, stay on Discover
    assert "Drop marked rows from Discover; stay on Discover" in _RUNTIME
    bulk_idx = _RUNTIME.find("$('bulkMark').addEventListener")
    assert bulk_idx > 0
    bulk_chunk = _RUNTIME[bulk_idx : bulk_idx + 600]
    assert "setAppPanel" not in bulk_chunk
    assert "setContactQueueTab" not in bulk_chunk

    # Discover Has booking click path
    book_idx = _RUNTIME.find("await setDiscoverBooking(")
    assert book_idx > 0
    book_chunk = _RUNTIME[book_idx : book_idx + 700]
    assert "setAppPanel('contactQueue')" not in book_chunk
    assert "setContactQueueTab('booking')" not in book_chunk


def test_contact_queue_actions_refresh_stay_not_switch_tab():
    assert "async function refreshContactQueueStay" in _CQ
    assert "function goContactQueueTab" not in _CQ
    assert "await goContactQueueTab(" not in _CQ
    # Promote / demote / booking / already-connected stay via refresh helper
    assert _CQ.count("await refreshContactQueueStay()") >= 4


def test_frontend_markers_present_via_shell(auth_client):
    html = auth_client.get("/").text
    src = frontend_source(html)
    assert "syncDiscoverLocation" in src
    assert "refreshContactQueueStay" in src
    assert "discoverCountry" in src
