"""``GET /api/v1/reference/enums``: the ClinicQ wire vocabulary, as data and as OpenAPI (Issue 4).

Every enum a client must send or read back is published here, so:

* OpenAPI lists each one as a named schema with its allowed values, before any domain route uses
  it (a component schema only appears when a route references it);
* a client that renders a select, a USSD menu or a board legend reads the values from the server
  instead of hard-coding a second copy of them.

Public and read-only: it describes the API, not anyone's data.
"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.commons.enums import (
    SITE_DEFAULT_DISPLAY_MODE,
    TICKET_TERMINAL_STATUSES,
    DisplayMode,
    SiteSector,
    TicketSource,
    TicketStatus,
    UserRole,
)

router = APIRouter(prefix="/reference", tags=["reference"])


class WireVocabulary(BaseModel):
    """Every value the ClinicQ enums allow, in declaration order."""

    site_sectors: list[SiteSector] = Field(description="`sites.sector`.")
    ticket_statuses: list[TicketStatus] = Field(description="`tickets.status`.")
    ticket_terminal_statuses: list[TicketStatus] = Field(
        description="Statuses a ticket never leaves; a correction is a new ticket."
    )
    ticket_sources: list[TicketSource] = Field(
        description="`tickets.source`: the channel a ticket was created from."
    )
    display_modes: list[DisplayMode] = Field(
        description="`sites.display_mode`: what the waiting-room board may show."
    )
    site_default_display_mode: DisplayMode = Field(
        description="The display mode every new site starts with."
    )
    user_roles: list[UserRole] = Field(description="`user.role`.")


@router.get(
    "/enums",
    response_model=WireVocabulary,
    summary="The allowed values of every ClinicQ enum",
)
def get_enums() -> WireVocabulary:
    """Return the allowed values of every enum on the ClinicQ wire."""
    return WireVocabulary(
        site_sectors=list(SiteSector),
        ticket_statuses=list(TicketStatus),
        # Declaration order, so the list is stable (a frozenset has none).
        ticket_terminal_statuses=[
            s for s in TicketStatus if s in TICKET_TERMINAL_STATUSES
        ],
        ticket_sources=list(TicketSource),
        display_modes=list(DisplayMode),
        site_default_display_mode=SITE_DEFAULT_DISPLAY_MODE,
        user_roles=list(UserRole),
    )
