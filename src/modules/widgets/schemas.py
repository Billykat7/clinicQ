"""Request/response models for the widgets API.

Separate ``In`` and ``Out`` shapes on purpose: what a client may set and what the API returns are
different sets of fields, and collapsing them is how a client ends up able to write ``id`` or
``created_at``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WidgetIn(BaseModel):
    """The fields a client may set when creating or updating a widget."""

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class WidgetOut(BaseModel):
    """One widget as the API returns it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    modified_at: datetime


class WidgetListOut(BaseModel):
    """A page of widgets, plus the total the filter matched."""

    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    items: list[WidgetOut]
