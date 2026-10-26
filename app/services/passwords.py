"""Password hashing for per-user login (PBKDF2 — no extra deps)."""
from __future__ import annotations

import hashlib
import hmac
import secrets


_ITERATIONS = 120_000
_PREFIX = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS)
    return f"{_PREFIX}${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or not password:
        return False
    try:
        prefix, iters_s, salt, hexdigest = stored.split("$", 3)
        if prefix != _PREFIX:
            return False
        iters = int(iters_s)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iters)
        return hmac.compare_digest(digest.hex(), hexdigest)
    except Exception:
        return False
