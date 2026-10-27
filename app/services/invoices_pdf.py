"""Generate simple invoice PDFs (fpdf2) and HTML preview text."""
from __future__ import annotations

import json
from io import BytesIO
from typing import Any, Optional

from app.models import BillingEntity, Business, Invoice, Proposal

# Currency → locale hint for grouping/decimals (Helvetica-safe ASCII symbols).
# ASCII-safe symbols (PDF Helvetica / latin-1).
_CURRENCY_META = {
    "AUD": ("en_AU", "A$"),
    "USD": ("en_US", "US$"),
    "NZD": ("en_NZ", "NZ$"),
    "GBP": ("en_GB", "GBP "),
    "EUR": ("en_IE", "EUR "),
    "CAD": ("en_CA", "C$"),
    "AED": ("en_AE", "AED "),
    "SAR": ("en_SA", "SAR "),
}


def format_money(amount: Any, currency: str = "USD") -> str:
    cur = (currency or "USD").upper()
    try:
        n = float(amount or 0)
    except Exception:
        return f"{cur} {amount}"
    _locale, symbol = _CURRENCY_META.get(cur, ("en_US", cur + " "))
    # European-style grouping for EUR; otherwise en-US commas.
    if cur == "EUR":
        whole, frac = f"{n:.2f}".split(".")
        # 1234567 -> 1.234.567
        parts = []
        while whole:
            parts.append(whole[-3:])
            whole = whole[:-3]
        grouped = ".".join(reversed(parts)) + "," + frac
        return f"{symbol}{grouped}"
    return f"{symbol}{n:,.2f}"


def _lines(inv: Invoice) -> list[dict[str, Any]]:
    try:
        data = json.loads(inv.line_items_json or "[]")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def invoice_view_payload(
    inv: Invoice,
    business: Optional[Business] = None,
    entity: Optional[BillingEntity] = None,
    proposal: Optional[Proposal] = None,
) -> dict:
    lines = _lines(inv)
    return {
        "id": inv.id,
        "number": inv.number,
        "status": inv.status,
        "currency": inv.currency,
        "subtotal": inv.subtotal,
        "tax_amount": inv.tax_amount,
        "total": inv.total,
        "provider": inv.provider,
        "hosted_url": inv.hosted_url or "",
        "pay_instructions": inv.pay_instructions or "",
        "expires_at": inv.expires_at.isoformat() if inv.expires_at else "",
        "paid_at": inv.paid_at.isoformat() if inv.paid_at else "",
        "created_at": inv.created_at.isoformat() if inv.created_at else "",
        "line_items": lines,
        "business": {
            "id": business.id if business else inv.business_id,
            "name": business.name if business else "",
            "email": business.email if business else "",
            "city": business.city if business else "",
            "country": business.country if business else "",
        },
        "billing_entity": {
            "legal_name": entity.legal_name if entity else "AppointHive",
            "address": entity.address if entity else "",
            "tax_id": entity.tax_id if entity else "",
        },
        "proposal_title": proposal.title if proposal else "",
    }


def build_invoice_pdf_bytes(payload: dict) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    def line(pdf_obj, w, h, text="", border=0):
        pdf_obj.cell(w, h, text, border=border, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    ent = payload.get("billing_entity") or {}
    line(pdf, 0, 10, (ent.get("legal_name") or "AppointHive")[:80])
    pdf.set_font("Helvetica", "", 11)
    if ent.get("address"):
        pdf.multi_cell(0, 6, str(ent["address"])[:400])
    if ent.get("tax_id"):
        line(pdf, 0, 6, f"Tax ID: {ent['tax_id']}")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 14)
    line(pdf, 0, 8, f"Invoice {payload.get('number') or ''}")
    pdf.set_font("Helvetica", "", 11)
    biz = payload.get("business") or {}
    line(pdf, 0, 6, f"Bill to: {biz.get('name') or '—'}")
    loc = ", ".join(x for x in [biz.get("city"), biz.get("country")] if x)
    if loc:
        line(pdf, 0, 6, loc)
    if biz.get("email"):
        line(pdf, 0, 6, str(biz["email"])[:120])
    line(pdf, 0, 6, f"Status: {payload.get('status') or ''}")
    if payload.get("created_at"):
        line(pdf, 0, 6, f"Issued: {str(payload['created_at'])[:19].replace('T', ' ')}")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(90, 7, "Item", border=1)
    pdf.cell(25, 7, "Qty", border=1)
    pdf.cell(35, 7, "Amount", border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    cur = payload.get("currency") or "USD"
    for row in payload.get("line_items") or []:
        name = str(row.get("name") or row.get("description") or "Item")[:48]
        qty = str(row.get("qty") or row.get("quantity") or 1)
        amt_s = format_money(row.get("amount"), cur)
        # Helvetica may lack £/€ — fall back to ASCII code
        try:
            name.encode("latin-1")
            amt_s.encode("latin-1")
        except UnicodeEncodeError:
            amt_s = format_money(row.get("amount"), cur).encode("ascii", "replace").decode("ascii")
            name = name.encode("ascii", "replace").decode("ascii")
        pdf.cell(90, 7, name, border=1)
        pdf.cell(25, 7, qty, border=1)
        pdf.cell(35, 7, amt_s, border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    total_s = format_money(payload.get("total"), cur)
    try:
        total_s.encode("latin-1")
    except UnicodeEncodeError:
        total_s = total_s.encode("ascii", "replace").decode("ascii")
    line(pdf, 0, 8, f"Total: {total_s}")
    if payload.get("hosted_url"):
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, f"Pay link: {payload['hosted_url']}")
    if payload.get("pay_instructions"):
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        line(pdf, 0, 6, "Payment instructions")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5, str(payload["pay_instructions"])[:1500])
    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def _total_fmt(payload: dict) -> str:
    return format_money(payload.get("total"), payload.get("currency") or "USD")


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
        lines += ["", "A PDF copy of this invoice is available from your AppointHive sales workspace (Download PDF)."]
    lines += ["", "Thanks,", "AppointHive"]
    return f"Invoice {payload.get('number')} — AppointHive", "\n".join(lines)
