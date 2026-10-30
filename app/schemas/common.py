"""Shared response envelopes used across feature tags."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class OkResponse(BaseModel):
    ok: bool = True


class AuthErrorDetail(BaseModel):
    code: str
    message: str


class ProspectListResponse(BaseModel):
    prospects: list[dict[str, Any]] = Field(default_factory=list)
    total: Optional[int] = None


class TaskListResponse(BaseModel):
    tasks: list[dict[str, Any]] = Field(default_factory=list)


class ActivityListResponse(BaseModel):
    activities: list[dict[str, Any]] = Field(default_factory=list)


class InvoiceListResponse(BaseModel):
    invoices: list[dict[str, Any]] = Field(default_factory=list)


class PaymentListResponse(BaseModel):
    payments: list[dict[str, Any]] = Field(default_factory=list)


class ContactQueueResponse(BaseModel):
    leads: list[dict[str, Any]] = Field(default_factory=list)
    total: Optional[int] = None
    mode: Optional[str] = None


class UsersListResponse(BaseModel):
    users: list[dict[str, Any]] = Field(default_factory=list)


class BusinessBulkBody(BaseModel):
    ids: list[int] = Field(default_factory=list)
    marked_for_contact: Optional[bool] = None
    contacted: Optional[bool] = None
    archived: Optional[bool] = None
    lost: Optional[bool] = None
    email_sent: Optional[bool] = None
    whatsapp_sent: Optional[bool] = None
    instagram_sent: Optional[bool] = None
