"""Pydantic schemas for the audit-trail search and POPIA data-subject operations (Issue #78).

Wire shapes only — the persistence types live on
:class:`~src.database.models.audit_event.AuditEvent`; the assembly logic lives in
:mod:`src.modules.audit.service`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from src.commons.enums import AuditAction, AuditEntityType


class AuditEventOut(BaseModel):
    """One audit record as returned by the search API."""

    id: str
    actor: str
    actor_id: str | None = None
    action: AuditAction
    entity_type: AuditEntityType
    entity_id: str
    diff: dict[str, Any] | None = None
    site_id: str | None = None
    request_id: str | None = None
    actor_role: str | None = None
    ip_address: str | None = None
    context: str | None = None
    created_at: datetime


class AuditSearchResult(BaseModel):
    """A page of audit events plus the total matching the filter (for pagination)."""

    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    events: list[AuditEventOut]


class DataSubjectRecordGroup(BaseModel):
    """A related record set held about the data subject, summarised for the export."""

    entity_type: AuditEntityType
    count: int = Field(ge=0)
    records: list[dict[str, Any]]


class DataSubjectExport(BaseModel):
    """Everything the system holds about one data subject (POPIA access request).

    The ``profile`` carries the tenant's own personal data in the clear (the subject is entitled
    to it); ``related`` summarises the operational and financial records that reference them; and
    ``audit_trail`` is every audit event about them — so the export answers "what do you hold, and
    who has touched it".
    """

    subject_id: str
    generated_at: datetime
    profile: dict[str, Any]
    related: list[DataSubjectRecordGroup]
    audit_trail: list[AuditEventOut]


class ErasureResult(BaseModel):
    """The outcome of a POPIA erasure: what was erased vs retained under a legal obligation."""

    subject_id: str
    erased_at: datetime
    erased_fields: list[str]
    retained: list[DataSubjectRecordGroup]
    note: str
