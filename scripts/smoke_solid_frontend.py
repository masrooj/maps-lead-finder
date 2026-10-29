#!/usr/bin/env python3
"""Smoke + feature-parity checks for SOLID frontend restructure."""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = os.environ.get("SMOKE_BASE", "http://127.0.0.1:8000")


def get_password() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("LOGIN_PASSWORD="):
            return line.split("=", 1)[1]
    raise SystemExit("LOGIN_PASSWORD missing in .env")


def wait_health(timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/api/health", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit("health timeout")


def opener_with_login():
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    req = urllib.request.Request(
        BASE + "/api/login",
        data=json.dumps(
            {"username": "hello@appointhive.com", "password": get_password()}
        ).encode(),
        headers={"Content-Type": "application/json"},
    )
    with opener.open(req, timeout=30) as r:
        assert r.status == 200, r.status
    return opener


def hit(opener, path: str):
    try:
        with opener.open(BASE + path, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def main() -> int:
    os.chdir(ROOT)
    wait_health()
    opener = opener_with_login()
    print("LOGIN ok")

    static_paths = [
        "/",
        "/static/js/main.js",
        "/static/js/shell/topbar.js",
        "/static/js/shell/login.js",
        "/static/js/shell/router.js",
        "/static/js/shell/sidebar.js",
        "/static/js/core/dialog.js",
        "/static/js/core/notifications.js",
        "/static/js/features/invoices.js",
        "/static/js/panels/contact-queue.js",
        "/static/js/app/runtime.js",
        "/static/js/app/bridge.js",
        "/static/css/tokens.css",
        "/static/css/app.css",
    ]
    sf = []
    for p in static_paths:
        c, _ = hit(opener, p)
        print("STATIC", c, p)
        if c != 200:
            sf.append(p)

    eps = [
        "/api/me",
        "/api/health",
        "/api/locations",
        "/api/businesses?limit=5",
        "/api/sales/dashboard?product_line=STANDALONE",
        "/api/sales/contact-queue?product_line=STANDALONE&limit=5",
        "/api/sales/prospects?product_line=STANDALONE&limit=5",
        "/api/sales/pipeline?product_line=STANDALONE",
        "/api/sales/tasks?product_line=STANDALONE",
        "/api/sales/activities?product_line=STANDALONE&limit=20",
        "/api/sales/demos?product_line=STANDALONE",
        "/api/sales/proposals",
        "/api/sales/providers",
        "/api/sales/payments",
        "/api/sales/invoices?product_line=STANDALONE&limit=10",
        "/api/sales/reports/summary?product_line=STANDALONE",
        "/api/sales/settings",
        "/api/sales/payment-settings",
        "/api/sales/be/connection",
        "/api/templates",
    ]
    bad_api = []
    for ep in eps:
        c, body = hit(opener, ep)
        if c == 200:
            print("API_OK", ep, len(body))
        else:
            print("API_BAD", ep, c)
            bad_api.append((ep, c))

    # module graph
    root = ROOT / "app/static/js"
    seen = set()
    q = ["main.js"]
    pat = re.compile(r"import\s+(?:[^'\"]+\s+from\s+)?['\"](\.[^'\"]+)['\"]")
    while q:
        rel = q.pop()
        if rel in seen:
            continue
        seen.add(rel)
        text = (root / rel).read_text(encoding="utf-8")
        for m in pat.finditer(text):
            rel2 = str(
                ((root / rel).parent / m.group(1)).resolve().relative_to(root.resolve())
            )
            if not rel2.endswith(".js"):
                rel2 += ".js"
            q.append(rel2)
    mb = []
    for rel in sorted(seen):
        c, _ = hit(opener, f"/static/js/{rel}")
        if c != 200:
            mb.append((rel, c))
    print("MODULES", len(seen), "BAD", mb or "none")

    html = (ROOT / "app/templates/index.html").read_text()
    assert 'type="module"' in html and "/static/js/main.js" in html
    for pid in [
        "guide",
        "dashboard",
        "leads",
        "contactQueue",
        "pipeline",
        "prospects",
        "tasks",
        "activities",
        "demos",
        "templates",
        "deals",
        "money",
        "reports",
        "appoinHive",
        "paySetup",
        "settings",
    ]:
        assert f'data-app-panel="{pid}"' in html, pid
        assert f'id="{pid}Panel"' in html, pid
    print("PANELS_DOM_OK 16")

    js = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in (ROOT / "app/static/js").rglob("*.js")
    )
    for needle in [
        "contactQueueIsParked",
        "invoiceCardHtml",
        "currencySelectHtml",
        "data-inv-void",
        "loadAppointHive",
        "renderPaylinksHost",
        "openCompose",
        "scoreOpts",
        "notifDismissStore",
        "createProposalInvoiceForProspect",
        "closeTopMenus",
        "showLoginError",
        "PANEL_META",
    ]:
        assert needle in js or needle in html, f"missing feature marker {needle}"
    print("FEATURE_MARKERS_OK")

    raw = subprocess.check_output(
        ["git", "show", "8346fe5:app/templates/index.html"], text=True
    )
    m = re.search(r"<script>(.*?)</script>", raw, re.S)
    orig = set(re.findall(r"(?:async\s+)?function\s+(\w+)\s*\(", m.group(1)))
    curf = set(re.findall(r"(?:async\s+)?function\s+(\w+)\s*\(", js))
    missing = sorted(orig - curf)
    print("FN_MISSING", missing or "none")

    # CSS: index must use intact app.css (split files previously cut mid-ruleset)
    assert 'href="/static/css/app.css"' in html
    app_css = (ROOT / "app/static/css/app.css").read_text()
    assert ".field input:not([type=checkbox])" in app_css
    assert "#composeBody" in app_css
    print("CSS_APP_OK")

    # Architecture guards — prevent regressing to monolith / broken CSS peel
    inline_scripts = re.findall(
        r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", html, re.I
    )
    fat = [s for s in inline_scripts if len(s.strip()) > 200]
    assert not fat, "index.html must not regain large inline <script> (SOLID)"
    for stub in ("base.css", "layout.css", "auth.css", "components.css"):
        assert f"/static/css/{stub}" not in html, f"do not link broken CSS stub {stub}"
    assert "type=\"module\" src=\"/static/js/main.js" in html.replace("'", '"') or \
           'type="module" src="/static/js/main.js' in html
    assert "registerPanel" in js
    assert "export const $ =" in (ROOT / "app/static/js/core/dom.js").read_text()
    pager = (ROOT / "app/static/js/core/pager.js").read_text()
    assert "$(id)" not in pager and "getElementById(id)" in pager
    print("ARCH_SOLID_OK")

    if sf or mb or missing or bad_api:
        print("FAIL", {"static": sf, "modules": mb, "fns": missing, "api": bad_api})
        return 1
    print("ALL_SMOKE_PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
