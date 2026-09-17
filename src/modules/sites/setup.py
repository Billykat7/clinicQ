"""A clinic finishes setting itself up, and submits itself for checking (Issue 223).

An operator adds a clinic (Issue 222) and sends **one link**. That link is the staff invitation
Issue 22 already built, issued for ``clinic_manager`` at the new clinic: accepting it creates the
manager's account and their role there, and lands them here — a checklist of what their clinic still
needs before patients should see it, and one button to put it forward.

**Why the link is an invitation rather than a token of its own.** The first draft of this issue gave
the setup journey its own single-use token, reaching a clinic's configuration with *no account at
all*. That is a second authentication surface — one that can change a clinic's rooms, hours and
waiting-room board — built beside a mechanism that already does exactly this job, and a person who
sets a clinic up needs an account the next morning anyway to run its queue. So the link creates the
account it was always going to need, every step is audited under a real person, and every write goes
through the settings pages and the API that already exist, with the site guard (Issue 19) doing the
scoping it already does. There is no new way into a clinic.

**The checklist is derived, never stored.** Each step asks the clinic's own rows whether it is done,
so a change made anywhere — this page, the settings tabs, the API — shows up here, and nothing can
disagree. The one column is :attr:`~src.database.models.site.Site.setup_completed_at`, and it
records *when the clinic said it was finished*, which is not the same question.

**Submitting is the clinic's; approving is the operator's.** Both move ``site.status`` through
:func:`~src.modules.sites.onboarding.transition`, the only writer, and they are different grants:
this one is ``sites.onboarding`` at ``assigned`` (a manager, for their own clinic) and can only make
the one move ``draft → pending_verification``; the decision is ``business`` (Issue 221).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.commons.enums import SiteStatus
from src.commons.time import now_sast
from src.database.models import ClinicService, Queue, Site, SiteOpeningHours, User
from src.database.models.staff_invitation import StaffInvitation
from src.modules.sites import onboarding


class SetupStep(StrEnum):
    """The things a clinic settles before patients should see it. In the order they are asked."""

    #: Name, address and the point on the map are the clinic's own, not the operator's guess.
    CLINIC = "clinic"
    #: The rooms patients queue for. A room *is* a queue (Issue 25).
    ROOMS = "rooms"
    #: What the clinic offers, pharmacy and dispensary included (Issue 26).
    SERVICES = "services"
    #: The week it works (Issue 24). Without it the join gate says the clinic is closed.
    HOURS = "hours"
    #: What the waiting-room board may show (Issue 27). ``number_only`` until the clinic says otherwise.
    BOARD = "board"
    #: Somebody else who can run it, so it does not depend on one person (Issue 22).
    STAFF = "staff"


#: Where each step is finished. The checklist links to the tab that owns it, rather than
#: reimplementing the form: one place edits a clinic's hours, and it is the hours tab.
STEP_TAB: dict[SetupStep, str] = {
    SetupStep.CLINIC: "profile",
    SetupStep.ROOMS: "queues",
    SetupStep.SERVICES: "services",
    SetupStep.HOURS: "hours",
    SetupStep.BOARD: "display",
    SetupStep.STAFF: "staff",
}

#: What each step is called, and what it is for, in the words a clinic manager reads.
STEP_WORDS: dict[SetupStep, tuple[str, str]] = {
    SetupStep.CLINIC: (
        "Check your clinic's details",
        "The name, address and map point patients see. Correct anything that is not right.",
    ),
    SetupStep.ROOMS: (
        "Name your rooms",
        "The lines patients wait in. You start with a standard set — rename, remove or add until "
        "they match how your clinic actually runs.",
    ),
    SetupStep.SERVICES: (
        "Say what you offer",
        "Including the pharmacy or dispensary, if you have one. It is what a walk-in is asked "
        "which of, and what patients search for.",
    ),
    SetupStep.HOURS: (
        "Set your opening hours",
        "Until these are set, patients are told you are closed and nobody can join from a phone.",
    ),
    SetupStep.BOARD: (
        "Decide what the waiting-room screen shows",
        "It shows ticket numbers and nothing else until you change it. Anything more is a "
        "decision about your patients' privacy, so it is yours to make.",
    ),
    SetupStep.STAFF: (
        "Invite the people who run it",
        "Reception, nurses, doctors. A clinic that depends on one account is a clinic that stops "
        "when that person is away.",
    ),
}


@dataclass(frozen=True, slots=True)
class StepState:
    """One step: what it is, whether it is settled, and what it says about itself now."""

    step: SetupStep
    title: str
    why: str
    done: bool
    #: What the clinic has so far, in a few words: "4 rooms", "no hours yet".
    detail: str
    #: The settings tab that finishes it.
    tab: str


@dataclass(frozen=True, slots=True)
class SetupState:
    """A clinic's whole setup: every step, and what it may do next."""

    site_id: str
    site_name: str
    status: SiteStatus
    steps: tuple[StepState, ...]
    completed_at: datetime | None

    @property
    def done_count(self) -> int:
        """How many steps are settled."""
        return sum(1 for step in self.steps if step.done)

    @property
    def complete(self) -> bool:
        """Whether every step is settled."""
        return all(step.done for step in self.steps)

    @property
    def can_submit(self) -> bool:
        """Whether the clinic may put itself forward: everything done, and still a draft.

        **Not** "everything done and the state machine allows the move". It allows
        ``verified → pending_verification`` — that is how an admin says "we need more information"
        about a clinic that is already listed (Issue 29) — and reading ``can_submit`` off the
        machine would hand a listed clinic a button that takes itself out of the directory. The
        move is the admin's; asking to be listed is the clinic's, and it is asked once, from a
        draft.
        """
        return self.complete and self.status is SiteStatus.DRAFT

    @property
    def waiting(self) -> bool:
        """Whether it has been submitted and is waiting for a decision."""
        return self.status is SiteStatus.PENDING_VERIFICATION


class NotReadyError(ValueError):
    """The clinic still has something on its checklist, so it cannot be put forward yet."""


def _count(db: Session, model: type, site_id: str) -> int:
    """How many live rows of ``model`` this clinic has."""
    statement = select(func.count()).select_from(model).where(model.site_id == site_id)  # type: ignore[attr-defined]
    if hasattr(model, "is_active"):
        statement = statement.where(model.is_active.is_(True))  # type: ignore[attr-defined]
    return int(db.execute(statement).scalar_one())


def _plural(count: int, one: str, many: str) -> str:
    """``1 room`` / ``4 rooms``."""
    return f"{count} {one if count == 1 else many}"


def setup_state(db: Session, site: Site) -> SetupState:
    """Where this clinic's setup stands now, derived from its own rows.

    Nothing here is stored: ask the clinic what it has, and the answer is the checklist. That is
    what lets a manager finish a step on its settings tab, or an operator do it through the API, and
    see it settled here without a second thing having to be told.
    """
    rooms = _count(db, Queue, site.id)
    services = _count(db, ClinicService, site.id)
    open_days = int(
        db.execute(
            select(func.count(func.distinct(SiteOpeningHours.weekday))).where(
                SiteOpeningHours.site_id == site.id
            )
        ).scalar_one()
    )
    # Somebody other than the person reading this: a clinic that depends on one account stops when
    # that person is away. An invitation not yet accepted counts — the asking is what this step is.
    invited = int(
        db.execute(
            select(func.count())
            .select_from(StaffInvitation)
            .where(
                StaffInvitation.site_id == site.id,
                StaffInvitation.revoked_at.is_(None),
            )
        ).scalar_one()
    )
    staff_here = int(
        db.execute(
            select(func.count(func.distinct(User.id)))
            .select_from(User)
            .where(User.is_active.is_(True))
        ).scalar_one()
    )

    facts: dict[SetupStep, tuple[bool, str]] = {
        # The operator typed these in; the clinic confirming them is what makes them the clinic's.
        SetupStep.CLINIC: (
            site.setup_confirmed_details,
            f"{site.name}, {site.city}"
            if site.setup_confirmed_details
            else "Not checked yet",
        ),
        SetupStep.ROOMS: (rooms > 0, _plural(rooms, "room", "rooms")),
        SetupStep.SERVICES: (
            services > 0,
            _plural(services, "service", "services"),
        ),
        SetupStep.HOURS: (
            open_days > 0,
            _plural(open_days, "day a week", "days a week")
            if open_days
            else "No hours yet",
        ),
        SetupStep.BOARD: (
            site.setup_confirmed_board,
            f"Showing {site.display_mode.replace('_', ' ')}"
            if site.setup_confirmed_board
            else "Not decided yet",
        ),
        SetupStep.STAFF: (
            invited > 0,
            _plural(invited, "person invited", "people invited")
            if invited
            else f"{staff_here} account here",
        ),
    }

    steps = tuple(
        StepState(
            step=step,
            title=STEP_WORDS[step][0],
            why=STEP_WORDS[step][1],
            done=facts[step][0],
            detail=facts[step][1],
            tab=STEP_TAB[step],
        )
        for step in SetupStep
    )
    return SetupState(
        site_id=site.id,
        site_name=site.name,
        status=site.status_enum,
        steps=steps,
        completed_at=site.setup_completed_at,
    )


def submit_for_checking(
    db: Session, site: Site, *, submitted_by: User, ip_address: str | None = None
) -> Site:
    """Put the clinic forward: ``draft → pending_verification``. The caller commits.

    Through :func:`~src.modules.sites.onboarding.transition`, which is the only writer of
    ``site.status`` — so this and the operator's decision cannot disagree about what a move does,
    and the submission is audited and announced like every other one.

    Raises:
        NotReadyError: Something on the checklist is unfinished, or the listing cannot make this
            move from where it is.
    """
    state = setup_state(db, site)
    if not state.complete:
        unfinished = [step.title for step in state.steps if not step.done]
        raise NotReadyError(
            "There is still something to do before this clinic can be checked: "
            + "; ".join(unfinished)
            + "."
        )
    if not state.can_submit:
        raise NotReadyError(
            f"A clinic that is {site.status} is not waiting to be put forward. Only a draft is."
        )
    site.setup_completed_at = now_sast()
    return onboarding.transition(
        db,
        site,
        SiteStatus.PENDING_VERIFICATION,
        decided_by=submitted_by,
        note=None,
        ip_address=ip_address,
    )
