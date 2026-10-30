"""JWT access tokens + opaque refresh tokens for mobile / Bearer API clients."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from fastapi import HTTPException
from sqlalchemy import select

from app.config import (
    ACCESS_TOKEN_MINUTES,
    ACCESS_TOKEN_SECRET,
    REFRESH_TOKEN_DAYS,
)
from app.models import AuthRefreshToken, User


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def access_token_secret() -> str:
    secret = (ACCESS_TOKEN_SECRET or "").strip()
    if not secret:
        raise HTTPException(503, detail={"code": "auth_not_configured", "message": "ACCESS_TOKEN_SECRET / SESSION_SECRET required"})
    return secret


def create_access_token(user: User) -> tuple[str, int]:
    """Return (jwt, expires_in_seconds)."""
    minutes = max(1, int(ACCESS_TOKEN_MINUTES or 30))
    expires_in = minutes * 60
    now = _utcnow()
    payload = {
        "sub": (user.email or "").strip().lower(),
        "uid": user.id,
        "role": user.role or "sales",
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    token = jwt.encode(payload, access_token_secret(), algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("ascii")
    return token, expires_in


def verify_access_token(token: str) -> dict[str, Any]:
    """Verify JWT; return claims. Raises HTTPException on failure."""
    raw = (token or "").strip()
    if not raw:
        raise HTTPException(401, detail={"code": "invalid_token", "message": "Authentication required"})
    try:
        claims = jwt.decode(raw, access_token_secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, detail={"code": "token_expired", "message": "Access token expired"}) from None
    except jwt.InvalidTokenError:
        raise HTTPException(401, detail={"code": "invalid_token", "message": "Invalid access token"}) from None
    if claims.get("typ") != "access":
        raise HTTPException(401, detail={"code": "invalid_token", "message": "Invalid access token type"})
    email = str(claims.get("sub") or "").strip().lower()
    if not email:
        raise HTTPException(401, detail={"code": "invalid_token", "message": "Invalid access token"})
    return claims


def issue_token_pair(
    db,
    user: User,
    *,
    client: str = "mobile",
    user_agent: str = "",
    family_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create access JWT + opaque refresh; persist refresh hash. Returns token response fields."""
    if not user or not user.active:
        raise HTTPException(401, detail={"code": "inactive_user", "message": "User inactive"})
    access, expires_in = create_access_token(user)
    refresh_raw = secrets.token_urlsafe(48)
    fid = family_id or uuid.uuid4().hex
    days = max(1, int(REFRESH_TOKEN_DAYS or 30))
    row = AuthRefreshToken(
        user_id=user.id,
        token_hash=_hash_token(refresh_raw),
        family_id=fid,
        expires_at=_utcnow() + timedelta(days=days),
        user_agent=(user_agent or "")[:500],
        client=(client or "mobile")[:40],
    )
    db.add(row)
    db.commit()
    return {
        "access_token": access,
        "refresh_token": refresh_raw,
        "token_type": "bearer",
        "expires_in": expires_in,
    }


def rotate_refresh(db, refresh_token: str, *, user_agent: str = "") -> dict[str, Any]:
    """Rotate refresh token; reuse of old token revokes the whole family."""
    raw = (refresh_token or "").strip()
    if not raw:
        raise HTTPException(401, detail={"code": "invalid_token", "message": "refresh_token required"})
    th = _hash_token(raw)
    row = db.scalar(select(AuthRefreshToken).where(AuthRefreshToken.token_hash == th))
    if not row:
        raise HTTPException(401, detail={"code": "invalid_token", "message": "Invalid refresh token"})

    now = _utcnow()
    if row.revoked_at is not None:
        # Reuse detection — revoke family
        _revoke_family(db, row.family_id)
        db.commit()
        raise HTTPException(401, detail={"code": "invalid_token", "message": "Refresh token reuse detected"})

    exp = row.expires_at
    if exp is not None and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp is None or exp < now:
        row.revoked_at = now
        db.commit()
        raise HTTPException(401, detail={"code": "token_expired", "message": "Refresh token expired"})

    user = db.get(User, row.user_id)
    if not user or not user.active:
        row.revoked_at = now
        db.commit()
        raise HTTPException(401, detail={"code": "inactive_user", "message": "User inactive"})

    row.revoked_at = now
    row.last_used_at = now
    db.flush()
    return issue_token_pair(
        db,
        user,
        client=row.client or "mobile",
        user_agent=user_agent or row.user_agent or "",
        family_id=row.family_id,
    )


def revoke_refresh(db, refresh_token: str) -> bool:
    raw = (refresh_token or "").strip()
    if not raw:
        return False
    th = _hash_token(raw)
    row = db.scalar(select(AuthRefreshToken).where(AuthRefreshToken.token_hash == th))
    if not row or row.revoked_at is not None:
        return False
    row.revoked_at = _utcnow()
    db.commit()
    return True


def revoke_all_for_user(db, user_id: int) -> int:
    now = _utcnow()
    rows = db.scalars(
        select(AuthRefreshToken).where(
            AuthRefreshToken.user_id == user_id,
            AuthRefreshToken.revoked_at.is_(None),
        )
    ).all()
    for row in rows:
        row.revoked_at = now
    db.commit()
    return len(rows)


def _revoke_family(db, family_id: str) -> None:
    now = _utcnow()
    rows = db.scalars(
        select(AuthRefreshToken).where(
            AuthRefreshToken.family_id == family_id,
            AuthRefreshToken.revoked_at.is_(None),
        )
    ).all()
    for row in rows:
        row.revoked_at = now


def load_active_user_by_email(db, email: str) -> Optional[User]:
    if not email:
        return None
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user and not user.active:
        return None
    return user
