"""Encrypt AppSetting secret values at rest (Fernet-compatible, SESSION_SECRET derived)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Optional

_PREFIX = "enc:v1:"


def _fernet():
    """Lazy Fernet from cryptography if installed; else None."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    raw = (os.getenv("SESSION_SECRET") or os.getenv("SETTINGS_ENCRYPTION_KEY") or "dev-insecure").encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def _xor_keystream(key: bytes, n: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < n:
        block = hashlib.sha256(key + counter.to_bytes(8, "big")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:n])


def encrypt_setting(plaintext: str) -> str:
    text = plaintext or ""
    if not text:
        return ""
    if text.startswith(_PREFIX):
        return text
    f = _fernet()
    if f is not None:
        token = f.encrypt(text.encode("utf-8")).decode("ascii")
        return _PREFIX + "f:" + token
    # Stdlib fallback: XOR + HMAC (still better than plaintext)
    secret = (os.getenv("SESSION_SECRET") or os.getenv("SETTINGS_ENCRYPTION_KEY") or "dev-insecure").encode()
    salt = hashlib.sha256(secret + b"|salt").digest()[:16]
    key = hashlib.pbkdf2_hmac("sha256", secret, salt, 100_000, dklen=32)
    raw = text.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(raw, _xor_keystream(key, len(raw))))
    mac = hmac.new(key, salt + ct, hashlib.sha256).digest()
    blob = base64.urlsafe_b64encode(salt + mac + ct).decode("ascii")
    return _PREFIX + "x:" + blob


def decrypt_setting(stored: Optional[str]) -> str:
    text = (stored or "").strip()
    if not text:
        return ""
    if not text.startswith(_PREFIX):
        return text  # legacy plaintext
    body = text[len(_PREFIX) :]
    if body.startswith("f:"):
        f = _fernet()
        if f is None:
            raise RuntimeError("Encrypted setting needs cryptography package to decrypt")
        return f.decrypt(body[2:].encode("ascii")).decode("utf-8")
    if body.startswith("x:"):
        blob = base64.urlsafe_b64decode(body[2:].encode("ascii"))
        salt, mac, ct = blob[:16], blob[16:48], blob[48:]
        secret = (os.getenv("SESSION_SECRET") or os.getenv("SETTINGS_ENCRYPTION_KEY") or "dev-insecure").encode()
        key = hashlib.pbkdf2_hmac("sha256", secret, salt, 100_000, dklen=32)
        expect = hmac.new(key, salt + ct, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expect):
            raise ValueError("Corrupt encrypted setting")
        raw = bytes(a ^ b for a, b in zip(ct, _xor_keystream(key, len(ct))))
        return raw.decode("utf-8")
    return text
