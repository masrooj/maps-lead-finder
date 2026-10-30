"""Settings-related request/response schemas (OpenAPI)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class EmailSignatureBody(BaseModel):
    text: str = Field(default="AppointHive", max_length=4000)
    include_logo: bool = True


class EmailSignatureResponse(BaseModel):
    text: str
    include_logo: bool
    brand_logo_url: str = ""
    plain: str = ""
    html: str = ""
