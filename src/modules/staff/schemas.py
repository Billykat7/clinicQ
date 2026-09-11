"""Response models for the staff API (Issue 19)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class StaffMemberOut(BaseModel):
    """One staff member of a clinic, as a colleague at that clinic sees them."""

    id: str
    email: str
    first_name: str | None
    last_name: str | None
    roles: list[str] = Field(description="The roles this person holds at this clinic.")
    is_active: bool
    last_login: datetime | None


class StaffListOut(BaseModel):
    """The staff of one clinic."""

    site_id: str
    total: int = Field(ge=0)
    items: list[StaffMemberOut]
