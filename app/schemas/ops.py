"""Ops / team request bodies for OpenAPI."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PasswordSetBody(BaseModel):
    password: str = Field(min_length=8, max_length=500)


class UserCreateBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(default="", max_length=200)
    role: str = Field(default="sales", max_length=40)
    password: str = Field(default="", max_length=500)
    timezone: Optional[str] = Field(default=None, max_length=80)
