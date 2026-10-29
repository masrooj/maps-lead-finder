#!/usr/bin/env python3
"""Peel cohesive helpers from app/static/js/app/runtime.js into SOLID modules."""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1] / "app" / "static"
RUNTIME = ROOT / "js" / "app" / "runtime.js"


def skip_string(s: str, i: int) -> int:
    q = s[i]
    i += 1
    if q in ("'", '"'):
        while i < len(s):
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == q:
                return i + 1
            i += 1
        return i
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == "`":
            return i + 1
        if s[i] == "$" and i + 1 < len(s) and s[i + 1] == "{":
            i += 2
            depth = 1
            while i < len(s) and depth:
                c = s[i]
                if c in ("'", '"', "`"):
                    i = skip_string(s, i)
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                i += 1
            continue
        i += 1
    return i


def skip_balanced(s: str, i: int, open_c: str, close_c: str) -> int:
    """i points at open_c; return index after matching close."""
    assert s[i] == open_c
    depth = 0
    while i < len(s):
        c = s[i]
        if c in ("'", '"', "`"):
            i = skip_string(s, i)
            continue
        if c == "/" and i + 1 < len(s) and s[i + 1] == "/":
            while i < len(s) and s[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < len(s) and s[i + 1] == "*":
            i += 2
            while i + 1 < len(s) and not (s[i] == "*" and s[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def extract_function(src: str, name: str) -> str | None:
    pat = rf"(async\s+)?function\s+{re.escape(name)}\s*\("
    m = re.search(pat, src)
    if not m:
        return None
    i = m.end() - 1  # at '('
    i = skip_balanced(src, i, "(", ")")
    while i < len(src) and src[i].isspace():
        i += 1
    if i < len(src) and src[i] == "{":
        end = skip_balanced(src, i, "{", "}")
        return src[m.start() : end]
    return None


def to_export(block: str, name: str) -> str:
    block = re.sub(
        rf"^async\s+function\s+{name}",
        f"export async function {name}",
        block,
        count=1,
    )
    block = re.sub(
        rf"^function\s+{name}", f"export function {name}", block, count=1
    )
    return block


def rewrite_bridge_calls(block: str, names: list[str]) -> str:
    for n in names:
        block = re.sub(rf"(?<![\w.$]){n}\s*\(", f"B.{n}(", block)
    return block


BRIDGE_CALLS = [
    "setAppPanel",
    "openProspectById",
    "openDrawer",
    "openCompose",
    "loadTemplates",
    "refreshBeConnBanners",
    "setMoneyTab",
    "setCommsTab",
    "setAhTab",
    "setSettingsTab",
    "applyBeConnectOwnerGate",
    "loadBeCatalog",
]


def take(src: str, names: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for n in names:
        b = extract_function(src, n)
        if not b:
            print("MISS", n)
            continue
        out[n] = b
        print("OK", n, len(b))
    return out


def strip_blocks(text: str, blocks: list[str]) -> str:
    for b in blocks:
        if b and b in text:
            text = text.replace(b, "\n", 1)
    return text


def main() -> int:
    src = RUNTIME.read_text(encoding="utf-8")

    (ROOT / "js/app/bridge.js").write_text(
        """/** Late-bound cross-module callbacks + shared caches. */
export const B = {
  setAppPanel: null,
  openProspectById: null,
  openDrawer: null,
  openCompose: null,
  loadTemplates: null,
  refreshBeConnBanners: null,
  setMoneyTab: null,
  setCommsTab: null,
  setAhTab: null,
  setSettingsTab: null,
  applyBeConnectOwnerGate: null,
  loadBeCatalog: null,
  moneyInvoicesCache: [],
  commsInvoicesCache: [],
  _moneyTab: 'ledger',
  _commsTab: 'outreach',
  get moneyTab() { return this._moneyTab; },
  set moneyTab(v) { this._moneyTab = v; },
  get commsTab() { return this._commsTab; },
  set commsTab(v) { this._commsTab = v; },
};
""",
        encoding="utf-8",
    )

    inv_names = [
        "groupByLeadName",
        "invoicePayload",
        "invoicePreviewHtml",
        "invoiceCardHtml",
        "bindInvoiceCardActions",
        "renderGroupedInvoiceCards",
        "renderMoneyInvoices",
        "renderCommsInvoices",
        "loadCommsInvoices",
        "currencySelectHtml",
    ]
    inv_blocks = take(src, inv_names)
    inv_parts = [
        "import { $ } from '../core/dom.js';",
        "import { esc, api, toast, fmtMoney, fmtDateTime } from '../core/api.js';",
        "import { pageSlice, renderPager, ensurePagerHost } from '../core/pager.js';",
        "import { openUiDialog, uiConfirm } from '../core/dialog.js';",
        "import { B } from '../app/bridge.js';",
        "export const CURRENCY_OPTIONS=['USD','AUD','EUR','GBP','NZD','CAD','AED','SAR','SGD','INR'];",
        "",
    ]
    for n, b in inv_blocks.items():
        body = to_export(b, n)
        body = body.replace("moneyInvoicesCache", "B.moneyInvoicesCache")
        body = body.replace("commsInvoicesCache", "B.commsInvoicesCache")
        body = rewrite_bridge_calls(body, [x for x in BRIDGE_CALLS if x != n])
        inv_parts.append(body)
    (ROOT / "js/features/invoices.js").write_text(
        "\n\n".join(inv_parts) + "\n", encoding="utf-8"
    )

    pp_names = ["wireProspectPicker", "pickProspectField"]
    pp_blocks = take(src, pp_names)
    pp = [
        "import { $ } from '../core/dom.js';",
        "import { esc, api, toast } from '../core/api.js';",
        "import { openUiDialog } from '../core/dialog.js';",
        "import { B } from '../app/bridge.js';",
        "",
    ]
    for n, b in pp_blocks.items():
        pp.append(rewrite_bridge_calls(to_export(b, n), BRIDGE_CALLS))
    (ROOT / "js/features/prospect-picker.js").write_text(
        "\n\n".join(pp) + "\n", encoding="utf-8"
    )

    sc_names = ["scoreOpts", "readScoreCriteria", "refreshScorePreview"]
    sc_blocks = take(src, sc_names)
    sc = [
        "import { $ } from '../core/dom.js';",
        "import { esc } from '../core/api.js';",
        "",
    ]
    for n, b in sc_blocks.items():
        sc.append(to_export(b, n))
    (ROOT / "js/features/scoring.js").write_text(
        "\n\n".join(sc) + "\n", encoding="utf-8"
    )

    cq_names = [
        "contactQueueSelectedLeads",
        "fillContactQueueLocationFilters",
        "contactQueueMatchQuery",
        "contactQueueBookingLeads",
        "contactQueueChannelMatch",
        "contactQueueIsParked",
        "contactQueueVisibleLeads",
        "fillContactQueueModes",
        "renderContactQueueExplain",
        "syncContactQueueSearchUi",
        "syncContactQueueTabs",
        "mapsUrlFor",
        "channelTagsHtml",
        "renderContactQueueList",
        "markContactQueueIds",
        "markBookingIds",
        "loadContactQueue",
        "saveContactQueueMode",
    ]
    cq_blocks = take(src, cq_names)
    cq = [
        "import { $ } from '../core/dom.js';",
        "import { esc, api, toast, fmtMoney, fmtDateTime } from '../core/api.js';",
        "import { pageSlice, renderPager, ensurePagerHost } from '../core/pager.js';",
        "import { uiConfirm, openUiDialog } from '../core/dialog.js';",
        "import { registerPanel } from './registry.js';",
        "import { B } from '../app/bridge.js';",
        "import { state } from '../core/state.js';",
        "",
    ]
    for n, b in cq_blocks.items():
        body = to_export(b, n)
        body = rewrite_bridge_calls(body, BRIDGE_CALLS)
        for s in ("contactQueueLeads", "contactQueueTab"):
            body = re.sub(rf"(?<![\w.]){s}(?![\w])", f"state.{s}", body)
        cq.append(body)
    cq.append(
        "\nregisterPanel({ id: 'contactQueue', refresh: async () => { await loadContactQueue(); } });\n"
    )
    (ROOT / "js/panels/contact-queue.js").write_text(
        "\n\n".join(cq) + "\n", encoding="utf-8"
    )

    be_names = [
        "refreshBeConnBanners",
        "renderBeConnDetails",
        "beCatalogIsPlan",
        "beCatalogRows",
        "formatBePrice",
        "renderBeCatalogBody",
        "loadBeCatalog",
        "isOwnerRole",
        "applyBeConnectOwnerGate",
    ]
    be_blocks = take(src, be_names)
    be = [
        "import { $ } from '../core/dom.js';",
        "import { esc, api, toast, fmtMoney } from '../core/api.js';",
        "import { B } from '../app/bridge.js';",
        "let currentRole = 'owner';",
        "export function setBeRole(role){ currentRole = role || 'owner'; }",
        "",
    ]
    for n, b in be_blocks.items():
        body = to_export(b, n)
        body = rewrite_bridge_calls(body, [x for x in BRIDGE_CALLS if x != n])
        be.append(body)
    (ROOT / "js/features/be-bridge.js").write_text(
        "\n\n".join(be) + "\n", encoding="utf-8"
    )

    all_blocks = (
        list(inv_blocks.values())
        + list(pp_blocks.values())
        + list(sc_blocks.values())
        + list(cq_blocks.values())
        + list(be_blocks.values())
    )
    new_src = strip_blocks(src, all_blocks)

    imports_extra = """
import { closeSidebar } from '../shell/sidebar.js';
import { B } from './bridge.js';
import {
  groupByLeadName, invoicePayload, invoicePreviewHtml, invoiceCardHtml,
  bindInvoiceCardActions, renderGroupedInvoiceCards, renderMoneyInvoices,
  renderCommsInvoices, loadCommsInvoices, currencySelectHtml, CURRENCY_OPTIONS
} from '../features/invoices.js';
import { wireProspectPicker, pickProspectField } from '../features/prospect-picker.js';
import { scoreOpts, readScoreCriteria, refreshScorePreview } from '../features/scoring.js';
import {
  contactQueueSelectedLeads, fillContactQueueLocationFilters, contactQueueMatchQuery,
  contactQueueBookingLeads, contactQueueChannelMatch, contactQueueIsParked,
  contactQueueVisibleLeads, fillContactQueueModes, renderContactQueueExplain,
  syncContactQueueSearchUi, syncContactQueueTabs, mapsUrlFor, channelTagsHtml,
  renderContactQueueList, markContactQueueIds, markBookingIds, loadContactQueue,
  saveContactQueueMode
} from '../panels/contact-queue.js';
import {
  refreshBeConnBanners, renderBeConnDetails, beCatalogIsPlan, beCatalogRows,
  formatBePrice, renderBeCatalogBody, loadBeCatalog, isOwnerRole, applyBeConnectOwnerGate,
  setBeRole
} from '../features/be-bridge.js';
"""

    if "from './bridge.js'" not in new_src:
        new_src = new_src.replace(
            "import { registerPanel } from '../panels/registry.js';",
            "import { registerPanel, getPanel } from '../panels/registry.js';",
        )
        new_src = new_src.replace(
            "import { state as sharedState } from '../core/state.js';",
            "import { state as sharedState } from '../core/state.js';\n"
            + imports_extra,
        )

    new_src = re.sub(
        r"const CURRENCY_OPTIONS\s*=\s*\[[^\]]*\];\s*", "", new_src, count=1
    )
    new_src = re.sub(r"let moneyInvoicesCache\s*=\s*\[\];\s*", "", new_src)
    new_src = re.sub(r"let commsInvoicesCache\s*=\s*\[\];\s*", "", new_src)
    new_src = re.sub(
        r"(?<![\w.])moneyInvoicesCache(?![\w])", "B.moneyInvoicesCache", new_src
    )
    new_src = re.sub(
        r"(?<![\w.])commsInvoicesCache(?![\w])", "B.commsInvoicesCache", new_src
    )

    cs = extract_function(new_src, "closeSidebar")
    if cs:
        new_src = new_src.replace(cs, "\n", 1)

    wire = """
/* --- Bridge wiring (Dependency Inversion for peeled modules) --- */
B.setAppPanel = setAppPanel;
B.openProspectById = openProspectById;
B.openDrawer = typeof openDrawer === 'function' ? openDrawer : null;
B.openCompose = typeof openCompose === 'function' ? openCompose : null;
B.loadTemplates = typeof loadTemplates === 'function' ? loadTemplates : null;
B.refreshBeConnBanners = refreshBeConnBanners;
B.setMoneyTab = setMoneyTab;
B.setCommsTab = setCommsTab;
B.setAhTab = setAhTab;
B.setSettingsTab = setSettingsTab;
B.applyBeConnectOwnerGate = applyBeConnectOwnerGate;
B.loadBeCatalog = loadBeCatalog;

"""
    if "Bridge wiring" not in new_src:
        new_src = new_src.replace(
            "/* --- Panel registry (SOLID Open/Closed) --- */",
            wire + "/* --- Panel registry (SOLID Open/Closed) --- */",
        )

    new_src = re.sub(
        r"registerPanel\(\{\s*id:\s*'contactQueue',\s*refresh:[^}]+\}\s*\)\s*;",
        "/* contactQueue registered in panels/contact-queue.js */",
        new_src,
    )

    RUNTIME.write_text(new_src, encoding="utf-8")
    print("runtime bytes", RUNTIME.stat().st_size)

    for p in [
        "js/features/invoices.js",
        "js/features/prospect-picker.js",
        "js/features/scoring.js",
        "js/features/be-bridge.js",
        "js/panels/contact-queue.js",
        "js/app/runtime.js",
        "js/app/bridge.js",
    ]:
        r = subprocess.run(
            ["node", "--check", str(ROOT / p)], capture_output=True, text=True
        )
        print(p, "OK" if r.returncode == 0 else r.stderr[:400])
        if r.returncode != 0:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
