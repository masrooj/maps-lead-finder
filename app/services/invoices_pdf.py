"""Generate rich invoice PDFs (fpdf2) and view payloads (logo, addresses, line summary)."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from app.models import BillingEntity, Business, Invoice, Proposal

log = logging.getLogger(__name__)

# Currency → locale hint for grouping/decimals (Helvetica-safe ASCII symbols).
_CURRENCY_META = {
    "AUD": ("en_AU", "A$"),
    "USD": ("en_US", "US$"),
    "NZD": ("en_NZ", "NZ$"),
    "GBP": ("en_GB", "GBP "),
    "EUR": ("en_IE", "EUR "),
    "CAD": ("en_CA", "C$"),
    "AED": ("en_AE", "AED "),
    "SAR": ("en_SA", "SAR "),
    "SGD": ("en_SG", "S$"),
    "INR": ("en_IN", "INR "),
}


def format_money(amount: Any, currency: str = "USD") -> str:
    cur = (currency or "USD").upper()
    try:
        n = float(amount or 0)
    except Exception:
        return f"{cur} {amount}"
    _locale, symbol = _CURRENCY_META.get(cur, ("en_US", cur + " "))
    if cur == "EUR":
        whole, frac = f"{n:.2f}".split(".")
        parts = []
        while whole:
            parts.append(whole[-3:])
            whole = whole[:-3]
        grouped = ".".join(reversed(parts)) + "," + frac
        return f"{symbol}{grouped}"
    return f"{symbol}{n:,.2f}"


def _ascii(s: str) -> str:
    try:
        s.encode("latin-1")
        return s
    except UnicodeEncodeError:
        return s.encode("ascii", "replace").decode("ascii")


def _lines(inv: Invoice) -> list[dict[str, Any]]:
    try:
        data = json.loads(inv.line_items_json or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _enrich_line_items(raw: list[dict[str, Any]], currency: str) -> list[dict[str, Any]]:
    out = []
    for row in raw or []:
        name = str(row.get("name") or row.get("description") or "Item")
        try:
            qty = float(row.get("qty") if row.get("qty") is not None else row.get("quantity") or 1)
        except Exception:
            qty = 1.0
        if qty <= 0:
            qty = 1.0
        try:
            if row.get("unit_amount") is not None:
                unit = float(row.get("unit_amount"))
            elif row.get("unit_price") is not None:
                unit = float(row.get("unit_price"))
            else:
                unit = float(row.get("amount") or 0)
        except Exception:
            unit = 0.0
        line_total = round(unit * qty, 2)
        out.append(
            {
                "name": name,
                "description": str(row.get("description") or ""),
                "qty": qty,
                "quantity": qty,
                "unit_amount": unit,
                "amount": unit,
                "line_total": line_total,
                "unit_fmt": format_money(unit, currency),
                "line_total_fmt": format_money(line_total, currency),
            }
        )
    return out


def resolve_logo_fs_path(logo_url: str = "") -> Optional[str]:
    """Map /uploads/... or http(s) logo URL to a local file path for PDF embedding."""
    url = (logo_url or "").strip()
    if not url:
        return None
    try:
        if url.startswith("http://") or url.startswith("https://"):
            import requests

            r = requests.get(url, timeout=8)
            if not r.ok or not r.content:
                return None
            ctype = (r.headers.get("content-type") or "").lower()
            suf = ".png" if "png" in ctype else (".gif" if "gif" in ctype else ".jpg")
            fd, path = tempfile.mkstemp(prefix="invlogo_", suffix=suf)
            with os.fdopen(fd, "wb") as fh:
                fh.write(r.content)
            return path
        if url.startswith("/uploads/"):
            from app.services.storage import uploads_root

            p = uploads_root() / url[len("/uploads/") :]
            return str(p) if p.is_file() else None
        p = Path(url)
        return str(p) if p.is_file() else None
    except Exception as exc:
        log.debug("logo resolve failed: %s", exc)
        return None


def invoice_view_payload(
    inv: Invoice,
    business: Optional[Business] = None,
    entity: Optional[BillingEntity] = None,
    proposal: Optional[Proposal] = None,
    *,
    logo_url: str = "",
) -> dict:
    cur = (inv.currency or "USD").upper()
    lines = _enrich_line_items(_lines(inv), cur)
    items_subtotal = round(sum(float(x.get("line_total") or 0) for x in lines), 2)
    subtotal = float(inv.subtotal if inv.subtotal is not None else items_subtotal)
    tax_amount = float(inv.tax_amount or 0)
    total = float(inv.total if inv.total is not None else subtotal + tax_amount)
    discount = float(proposal.discount_amount) if proposal and proposal.discount_amount else 0.0
    tax_label = (proposal.tax_label if proposal and proposal.tax_label else "") or "Tax"
    tax_rate = float(proposal.tax_rate) if proposal and proposal.tax_rate is not None else None

    biz_address = ""
    if business:
        biz_address = (getattr(business, "billing_address", None) or "").strip() or (business.address or "").strip()

    ent_lines = []
    if entity:
        if entity.legal_name:
            ent_lines.append(entity.legal_name)
        if entity.address:
            ent_lines.append(entity.address)
        loc = ", ".join(x for x in [getattr(entity, "country", None) or ""] if x)
        if loc:
            ent_lines.append(loc)
        if entity.tax_id:
            ent_lines.append(f"Tax ID: {entity.tax_id}")

    bill_lines = []
    if business:
        if business.name:
            bill_lines.append(business.name)
        if biz_address:
            bill_lines.append(biz_address)
        loc = ", ".join(x for x in [business.city, business.country] if x)
        if loc:
            bill_lines.append(loc)
        if business.email:
            bill_lines.append(business.email)
        phone = (business.international_phone or business.phone or "").strip()
        if phone:
            bill_lines.append(phone)

    return {
        "id": inv.id,
        "number": inv.number,
        "status": inv.status,
        "currency": cur,
        "subtotal": subtotal,
        "tax_amount": tax_amount,
        "tax_label": tax_label,
        "tax_rate": tax_rate,
        "discount_amount": discount,
        "discount_reason": (proposal.discount_reason if proposal else "") or "",
        "total": total,
        "subtotal_fmt": format_money(subtotal, cur),
        "tax_fmt": format_money(tax_amount, cur),
        "discount_fmt": format_money(discount, cur) if discount else "",
        "total_fmt": format_money(total, cur),
        "provider": inv.provider,
        "hosted_url": inv.hosted_url or "",
        "pay_instructions": inv.pay_instructions or "",
        "expires_at": inv.expires_at.isoformat() if inv.expires_at else "",
        "paid_at": inv.paid_at.isoformat() if inv.paid_at else "",
        "created_at": inv.created_at.isoformat() if inv.created_at else "",
        "is_test_mode": bool(inv.is_test_mode),
        "line_items": lines,
        "items_count": len(lines),
        "logo_url": logo_url or "",
        "business": {
            "id": business.id if business else inv.business_id,
            "name": business.name if business else "",
            "email": business.email if business else "",
            "phone": (business.international_phone or business.phone) if business else "",
            "address": biz_address,
            "city": business.city if business else "",
            "country": business.country if business else "",
            "lines": bill_lines,
        },
        "billing_entity": {
            "legal_name": entity.legal_name if entity else "AppointHive",
            "address": entity.address if entity else "",
            "country": getattr(entity, "country", "") if entity else "",
            "tax_id": entity.tax_id if entity else "",
            "lines": ent_lines or ["AppointHive"],
        },
        "proposal_title": proposal.title if proposal else "",
        "proposal_reference": proposal.reference if proposal else "",
        "summary": {
            "items": len(lines),
            "subtotal_fmt": format_money(subtotal, cur),
            "tax_fmt": format_money(tax_amount, cur),
            "total_fmt": format_money(total, cur),
        },
    }


def build_invoice_pdf_bytes(payload: dict) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    def cell_ln(pdf_obj, w, h, text="", border=0, align="L"):
        pdf_obj.cell(w, h, _ascii(str(text or "")), border=border, align=align, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    ent = payload.get("billing_entity") or {}
    biz = payload.get("business") or {}
    cur = payload.get("currency") or "USD"
    logo_tmp = None

    # —— Header: logo + title ——
    y0 = pdf.get_y()
    logo_url = payload.get("logo_url") or ""
    logo_path = resolve_logo_fs_path(logo_url)
    if logo_path:
        try:
            pdf.image(logo_path, x=pdf.l_margin, y=y0, w=36)
            if logo_path.startswith(tempfile.gettempdir()):
                logo_tmp = logo_path
        except Exception as exc:
            log.debug("pdf logo skip: %s", exc)
            logo_path = None

    pdf.set_xy(pdf.l_margin + (42 if logo_path else 0), y0)
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 10, "INVOICE", align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 7, _ascii(f"#{payload.get('number') or ''}"), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    status = str(payload.get("status") or "").upper()
    meta = f"Status: {status}"
    if payload.get("is_test_mode"):
        meta += "  ·  TEST"
    pdf.cell(0, 6, _ascii(meta), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_y(max(pdf.get_y(), y0 + 28))
    pdf.ln(2)

    # —— From / Bill to ——
    col = page_w / 2 - 2
    y_cols = pdf.get_y()
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(100, 110, 130)
    pdf.cell(col, 5, "FROM", new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.cell(col, 5, "BILL TO", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)
    left = ent.get("lines") or [ent.get("legal_name") or "AppointHive"]
    right = biz.get("lines") or [biz.get("name") or "—"]
    rows = max(len(left), len(right), 1)
    for i in range(rows):
        l = left[i] if i < len(left) else ""
        r = right[i] if i < len(right) else ""
        if i == 0:
            pdf.set_font("Helvetica", "B", 11)
        else:
            pdf.set_font("Helvetica", "", 10)
        x = pdf.l_margin
        y = pdf.get_y()
        pdf.set_xy(x, y)
        pdf.multi_cell(col, 5, _ascii(str(l)[:200]), new_x=XPos.RIGHT, new_y=YPos.TOP)
        y2 = pdf.get_y()
        pdf.set_xy(x + col + 4, y)
        if i == 0:
            pdf.set_font("Helvetica", "B", 11)
        else:
            pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(col, 5, _ascii(str(r)[:200]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        y3 = pdf.get_y()
        pdf.set_y(max(y2, y3))
    pdf.set_y(max(pdf.get_y(), y_cols) + 2)
    pdf.ln(3)

    # —— Meta strip ——
    pdf.set_font("Helvetica", "", 10)
    issued = str(payload.get("created_at") or "")[:19].replace("T", " ")
    due = str(payload.get("expires_at") or "")[:19].replace("T", " ")
    bits = []
    if issued:
        bits.append(f"Issued: {issued}")
    if due:
        bits.append(f"Due: {due}")
    if payload.get("proposal_reference"):
        bits.append(f"Proposal: {payload['proposal_reference']}")
    if payload.get("provider"):
        bits.append(f"Pay via: {payload['provider']}")
    if bits:
        cell_ln(pdf, 0, 6, "  ·  ".join(bits))
    if payload.get("proposal_title"):
        pdf.set_font("Helvetica", "I", 10)
        cell_ln(pdf, 0, 6, f"Re: {payload['proposal_title']}")
    pdf.ln(4)

    # —— Line items table ——
    pdf.set_fill_color(24, 36, 58)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    w_desc, w_qty, w_unit, w_amt = page_w * 0.48, page_w * 0.12, page_w * 0.20, page_w * 0.20
    pdf.cell(w_desc, 8, " Description", border=0, fill=True)
    pdf.cell(w_qty, 8, "Qty", border=0, fill=True, align="R")
    pdf.cell(w_unit, 8, "Unit", border=0, fill=True, align="R")
    pdf.cell(w_amt, 8, "Amount", border=0, fill=True, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)
    fill = False
    for row in payload.get("line_items") or []:
        if fill:
            pdf.set_fill_color(245, 247, 250)
        else:
            pdf.set_fill_color(255, 255, 255)
        name = _ascii(str(row.get("name") or "Item")[:56])
        qty = str(row.get("qty") or row.get("quantity") or 1)
        unit_s = _ascii(row.get("unit_fmt") or format_money(row.get("unit_amount") or row.get("amount"), cur))
        amt_s = _ascii(row.get("line_total_fmt") or format_money(row.get("line_total"), cur))
        pdf.cell(w_desc, 8, " " + name, border=0, fill=True)
        pdf.cell(w_qty, 8, qty, border=0, fill=True, align="R")
        pdf.cell(w_unit, 8, unit_s, border=0, fill=True, align="R")
        pdf.cell(w_amt, 8, amt_s, border=0, fill=True, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        fill = not fill

    if not (payload.get("line_items") or []):
        pdf.set_font("Helvetica", "I", 10)
        cell_ln(pdf, 0, 8, "No line items")

    pdf.ln(4)
    # —— Totals ——
    tw = page_w * 0.45
    pdf.set_x(pdf.l_margin + page_w - tw)
    pdf.set_font("Helvetica", "", 10)

    def total_row(label, value, bold=False):
        pdf.set_x(pdf.l_margin + page_w - tw)
        if bold:
            pdf.set_font("Helvetica", "B", 12)
        else:
            pdf.set_font("Helvetica", "", 10)
        pdf.cell(tw * 0.45, 7, label, align="L")
        pdf.cell(tw * 0.55, 7, _ascii(value), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    total_row("Subtotal", payload.get("subtotal_fmt") or format_money(payload.get("subtotal"), cur))
    if float(payload.get("discount_amount") or 0) > 0:
        reason = payload.get("discount_reason") or "Discount"
        total_row(reason[:28], "-" + (payload.get("discount_fmt") or format_money(payload.get("discount_amount"), cur)))
    if float(payload.get("tax_amount") or 0) > 0 or payload.get("tax_rate"):
        label = payload.get("tax_label") or "Tax"
        if payload.get("tax_rate"):
            try:
                label = f"{label} ({float(payload['tax_rate']) * 100:.1f}%)"
            except Exception:
                pass
        total_row(label[:28], payload.get("tax_fmt") or format_money(payload.get("tax_amount"), cur))
    pdf.set_draw_color(40, 60, 100)
    pdf.set_x(pdf.l_margin + page_w - tw)
    pdf.line(pdf.get_x(), pdf.get_y(), pdf.get_x() + tw, pdf.get_y())
    pdf.ln(1)
    total_row("Total", payload.get("total_fmt") or format_money(payload.get("total"), cur), bold=True)
    pdf.ln(6)

    # —— Payment ——
    if payload.get("hosted_url") or payload.get("pay_instructions"):
        pdf.set_font("Helvetica", "B", 11)
        cell_ln(pdf, 0, 7, "Payment")
        pdf.set_font("Helvetica", "", 10)
        if payload.get("hosted_url"):
            pdf.multi_cell(0, 5, _ascii(f"Pay online: {payload['hosted_url']}"))
        if payload.get("pay_instructions"):
            pdf.ln(1)
            pdf.multi_cell(0, 5, _ascii(str(payload["pay_instructions"])[:1800]))

    pdf.ln(8)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(120, 130, 145)
    cell_ln(pdf, 0, 4, "Generated by AppointHive Sales OS")

    buf = BytesIO()
    pdf.output(buf)
    if logo_tmp:
        try:
            os.unlink(logo_tmp)
        except OSError:
            pass
    return buf.getvalue()


def _total_fmt(payload: dict) -> str:
    return payload.get("total_fmt") or format_money(payload.get("total"), payload.get("currency") or "USD")


def invoice_pay_block(payload: dict) -> str:
    if payload.get("hosted_url"):
        return f"Pay online: {payload['hosted_url']}"
    if payload.get("pay_instructions"):
        return "Payment instructions:\n" + str(payload["pay_instructions"])
    return ""


def invoice_template_vars(payload: dict) -> dict:
    biz = payload.get("business") or {}
    return {
        "name": biz.get("name") or "there",
        "city": biz.get("city") or "",
        "email": biz.get("email") or "",
        "invoice_number": payload.get("number") or "",
        "total_fmt": _total_fmt(payload),
        "currency": payload.get("currency") or "USD",
        "status": payload.get("status") or "",
        "pay_block": invoice_pay_block(payload),
        "pay_url": payload.get("hosted_url") or "",
        "category": "",
    }


def render_invoice_template(text: str, payload: dict) -> str:
    out = text or ""
    for key, val in invoice_template_vars(payload).items():
        out = out.replace("{{" + key + "}}", str(val))
    return out


def invoice_email_body(payload: dict, *, include_pdf_note: bool = True, subject_tpl: str = "", body_tpl: str = "") -> tuple[str, str]:
    """Return (subject, body). Uses optional MessageTemplate strings with {{placeholders}}."""
    if body_tpl:
        subject = render_invoice_template(subject_tpl or "Invoice {{invoice_number}} — {{total_fmt}}", payload)
        body = render_invoice_template(body_tpl, payload)
        return subject, body
    biz = payload.get("business") or {}
    name = biz.get("name") or "there"
    total = _total_fmt(payload)
    lines = [
        f"Hi {name},",
        "",
        f"Please find invoice {payload.get('number')} for {total}.",
    ]
    pay = invoice_pay_block(payload)
    if pay:
        lines += ["", pay]
    if include_pdf_note:
        lines += ["", "A PDF copy of this invoice is attached / available from your AppointHive sales workspace."]
    ent = (payload.get("billing_entity") or {}).get("legal_name") or "AppointHive"
    lines += ["", "Thanks,", ent]
    return f"Invoice {payload.get('number')} — {ent}", "\n".join(lines)
