"""SMTP outbound mail (invoice PDF attach). Settings override env."""
from __future__ import annotations

import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from app.services.secrets import resolve_secret


MAIL_SETTING_KEYS = [
    ("mail.smtp_host", "SMTP_HOST", False),
    ("mail.smtp_port", "SMTP_PORT", False),
    ("mail.smtp_user", "SMTP_USER", False),
    ("mail.smtp_password", "SMTP_PASSWORD", True),
    ("mail.from_email", "SMTP_FROM", False),
    ("mail.smtp_tls", "SMTP_TLS", False),
]


def smtp_configured(db=None) -> bool:
    return bool(
        resolve_secret("mail.smtp_host", "SMTP_HOST", db=db)
        and resolve_secret("mail.from_email", "SMTP_FROM", db=db)
    )


def mail_settings_public(db=None) -> list[dict]:
    from app.services.secrets import mask_secret

    out = []
    for setting_key, env_name, is_secret in MAIL_SETTING_KEYS:
        val = resolve_secret(setting_key, env_name, db=db)
        meta = mask_secret(val, secret=is_secret)
        out.append(
            {
                "key": setting_key,
                "env": env_name,
                "secret": is_secret,
                "label": env_name.replace("_", " ").title(),
                **meta,
            }
        )
    ready = smtp_configured(db=db)
    out.append({"key": "_status", "env": "", "secret": False, "label": "SMTP ready", "configured": ready, "masked": "", "hint": "ready" if ready else "set host + from"})
    return out


def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    attachment_bytes: Optional[bytes] = None,
    attachment_name: str = "invoice.pdf",
    attachment_mime: str = "application/pdf",
) -> dict:
    host = resolve_secret("mail.smtp_host", "SMTP_HOST")
    from_addr = resolve_secret("mail.from_email", "SMTP_FROM") or resolve_secret("mail.smtp_user", "SMTP_USER")
    if not host or not from_addr:
        raise RuntimeError("SMTP not configured — set mail.smtp_host and mail.from_email (Settings / .env)")
    if not (to or "").strip():
        raise RuntimeError("Recipient email is required")
    port_s = resolve_secret("mail.smtp_port", "SMTP_PORT", "587") or "587"
    try:
        port = int(port_s)
    except ValueError:
        port = 587
    user = resolve_secret("mail.smtp_user", "SMTP_USER")
    password = resolve_secret("mail.smtp_password", "SMTP_PASSWORD")
    tls_flag = (resolve_secret("mail.smtp_tls", "SMTP_TLS", "true") or "true").lower() in ("1", "true", "yes")

    msg = EmailMessage()
    msg["Subject"] = subject or "(no subject)"
    msg["From"] = from_addr
    msg["To"] = to.strip()
    msg.set_content(body or "")
    if attachment_bytes:
        msg.add_attachment(
            attachment_bytes,
            maintype=attachment_mime.split("/")[0] if "/" in attachment_mime else "application",
            subtype=attachment_mime.split("/")[-1] if "/" in attachment_mime else "pdf",
            filename=attachment_name,
        )

    if port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as smtp:
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            if tls_flag:
                context = ssl.create_default_context()
                smtp.starttls(context=context)
                smtp.ehlo()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
    return {"ok": True, "to": to.strip(), "from": from_addr}
