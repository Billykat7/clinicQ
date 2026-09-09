"""Shared bulk-delete request/response models (Issue #116).

The list/detail UX framework's single **Delete** button POSTs the selected row ids (or names) to a
``…/bulk-delete`` endpoint that applies each under the resource's own rules and returns a
**per-id result** so the UI can report partial success rather than silently dropping failures.

These models are resource-agnostic — every console from the RBAC consoles through the M21 rollout
(properties, vendors, tenants, inspections, maintenance, lease templates) reuses them, so the
request/response contract stays identical across the whole admin surface.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BulkDeleteIn(BaseModel):
    """A bulk-delete request: the ids (or names) to delete in one call (Issue #116)."""

    ids: list[str] = Field(
        min_length=1,
        max_length=100,
        description="Row identifiers to delete (max 100 per request).",
    )


class BulkDeleteResultRow(BaseModel):
    """The outcome of one id in a bulk delete, so the UI can report partial success."""

    id: str
    deleted: bool
    detail: str | None = Field(
        default=None, description="Why a row was skipped (null when it was deleted)."
    )


class BulkDeleteOut(BaseModel):
    """Per-id results plus a summary for a bulk delete."""

    results: list[BulkDeleteResultRow]
    deleted_count: int
    failed_count: int


def bulk_delete_summary(results: list[BulkDeleteResultRow]) -> BulkDeleteOut:
    """Fold per-id rows into the summarised bulk-delete response."""
    deleted = sum(1 for r in results if r.deleted)
    return BulkDeleteOut(
        results=results,
        deleted_count=deleted,
        failed_count=len(results) - deleted,
    )
