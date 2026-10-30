"""Prospect / pipeline OpenAPI envelopes (additive; extra fields allowed)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ProspectListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    prospects: list[dict[str, Any]] = Field(default_factory=list)
    total: Optional[int] = None
    sample_size: Optional[int] = None


class ContactQueueResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    # rank_contact_queue returns a flexible dict; keep open for Swagger documentation
    leads: list[dict[str, Any]] = Field(default_factory=list)
