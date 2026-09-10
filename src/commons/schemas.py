"""Pydantic models shared across multiple modules (keep this small)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.commons.enums import BoundedContext


class ModuleInfo(BaseModel):
    """Standard payload for ``GET .../info`` on each bounded context."""

    context: BoundedContext
    summary: str = Field(max_length=500)
