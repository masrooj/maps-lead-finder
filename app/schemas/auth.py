"""Auth / session OpenAPI models."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(default="", max_length=320, description="Email")
    password: str = Field(default="", max_length=500)
    client: str = Field(default="web", max_length=40, description="web | mobile | api")


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class LoginResponse(BaseModel):
    username: str
    role: str
    has_password: bool = False
    access_token: str = ""
    refresh_token: str = ""
    token_type: str = "bearer"
    expires_in: int = 0
    user_id: Optional[int] = None
    name: str = ""


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=500)


class LogoutBody(BaseModel):
    refresh_token: str = Field(default="", max_length=500)


class MeResponse(BaseModel):
    username: str
    role: str = "owner"
    name: str = ""
    user_id: Optional[int] = None
