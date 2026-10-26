"""Authorization helpers for Owner / Sales / Viewer."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Business, User


def get_user_by_email(db, email: str) -> Optional[User]:
    if not email:
        return None
    return db.scalar(select(User).where(User.email == email.lower()))


def require_role(user: Optional[User], *roles: str) -> User:
    if not user or not user.active:
        raise HTTPException(403, "Active user required")
    if user.role not in roles:
        raise HTTPException(403, f"Requires role: {', '.join(roles)}")
    return user


def can_view_business(user: Optional[User], b: Business) -> bool:
    if not user:
        return False
    if user.role == "owner":
        return True
    if user.role == "viewer":
        return True
    # sales: assigned or unassigned
    return b.owner_id is None or b.owner_id == user.id


def require_business_access(db, user: Optional[User], business_id: int) -> Business:
    b = db.get(Business, business_id)
    if not b:
        raise HTTPException(404, "Prospect not found")
    if not can_view_business(user, b):
        raise HTTPException(403, "Not allowed to access this prospect")
    return b


def require_owner_for_money(user: Optional[User]) -> User:
    return require_role(user, "owner")
