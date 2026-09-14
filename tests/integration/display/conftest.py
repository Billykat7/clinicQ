"""The waiting-room board's fixture: the dashboard's two clinics, patients the board could name, and kiosk boxes (M8).

Built on :func:`tests.integration.dashboard.conftest.dashboard` (clinic A with Triage in Room 2 and the
Pharmacy, clinic B, and one person per job), because a board is the same clinic seen from the waiting
room. What this adds is what a privacy test needs: patients with real-looking names, a way to answer
their consent, a way to change the clinic's display settings the way a manager's save does, and a kiosk box
paired with a clinic (``board.device()``), because since Issue 61 only a paired box or the clinic's own staff
may see a board.

The patients' names are deliberately unusual, so searching a response for one cannot match anything
else by accident.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.commons.enums import (
    BoardLanguage,
    ConsentPurpose,
    DisplayMode,
    PatientChannel,
    TicketSource,
)
from src.commons.time import now_sast
from src.database.models import Patient, Queue, Site
from src.modules.display import devices
from src.modules.patients.consent import record_consent
from src.modules.queue.sequence import issue_ticket
from src.modules.sites.settings import DisplaySettingsChange, apply_display_settings
from tests.factories import PatientFactory
from tests.integration.dashboard.conftest import dashboard

__all__ = ["board", "dashboard"]

#: Names nothing else in a response could contain.
NOMVULA = "Nomvula Zwelithini-Qwabe"
NOMVULA_LITE = "Nomvula Z."
REASON = "Persistent migraine aura"


@pytest.fixture
def board(dashboard: SimpleNamespace) -> SimpleNamespace:
    """The dashboard's world with board helpers: patients, tickets, consent and display settings."""

    def patient(name: str = NOMVULA) -> str:
        """A patient called ``name``; their id."""
        with dashboard.session() as db:
            record = PatientFactory.create(db, display_name=name)
            db.commit()
            return record.id

    def consent(patient_id: str, purpose: ConsentPurpose, granted: bool) -> None:
        """Record the patient's answer, as the consent screen does."""
        with dashboard.session() as db:
            record_consent(
                db,
                db.get_one(Patient, patient_id),
                purpose,
                granted=granted,
                channel=PatientChannel.WEB,
            )
            db.commit()

    def ticket(
        queue_id: str,
        patient_id: str | None = None,
        *,
        reason: str | None = None,
        comment_consent: bool = False,
    ) -> str:
        """Issue a ticket in ``queue_id``; its number."""
        with dashboard.session() as db:
            issued = issue_ticket(
                db,
                queue=db.get_one(Queue, queue_id),
                source=TicketSource.WEB if patient_id else TicketSource.WALK_IN,
                patient_id=patient_id,
                reason_text=reason,
                comment_consent=comment_consent,
            )
            db.commit()
            return issued.number

    def display(
        mode: DisplayMode, *, show_comment: bool = False, site_id: str | None = None
    ) -> None:
        """Change clinic A's (or ``site_id``'s) display settings, confirmed, as a manager's save does."""
        with dashboard.session() as db:
            site = db.get_one(Site, site_id or dashboard.site_a)
            apply_display_settings(
                site,
                DisplaySettingsChange(
                    display_mode=mode,
                    show_comment=show_comment,
                    board_language=BoardLanguage(site.board_language),
                    announce_audio=site.announce_audio,
                    announce_volume=site.announce_volume,
                    retention_days=site.reason_retention_days,
                ),
                confirm_public_display=True,
                confirm_comment_with_full_name=True,
            )
            db.commit()

    def session() -> Session:
        """A session on the board's database."""
        return dashboard.session()

    def device(
        site_id: str | None = None, queue_ids: list[str] | None = None
    ) -> TestClient:
        """A client that is a kiosk box paired with clinic A (or ``site_id``), as a manager's pairing leaves it."""
        with dashboard.session() as db:
            started = devices.start_device(db, user_agent="test kiosk")
            started.device.site_id = site_id or dashboard.site_a
            started.device.paired_at = now_sast()
            started.device.last_seen_at = now_sast()
            started.device.pairing_code_hash = None
            started.device.queue_ids = queue_ids
            db.commit()
        client = TestClient(dashboard.app, follow_redirects=False)
        client.cookies.set(
            dashboard.settings.display_device_cookie_name, started.secret
        )
        return client

    state: Callable[[str], str] = lambda site_id: f"/display/{site_id}/state"  # noqa: E731
    return SimpleNamespace(
        world=dashboard,
        patient=patient,
        consent=consent,
        ticket=ticket,
        display=display,
        session=session,
        state=state,
        device=device,
    )
