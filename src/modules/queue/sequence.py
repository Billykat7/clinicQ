"""Ticket numbers and reference codes: allocated by the database, safe under any concurrency (Issue 39).

Two receptionists tapping *Add walk-in* at the same instant must not both be handed ``A043``. The
classic way a queue system breaks in production is computing the next number in Python:

.. code-block:: python

    # Never. Two transactions both read 42 and both write 43.
    sequence = db.scalar(select(func.count()).where(Ticket.queue_id == queue.id)) + 1

so this module never reads a count. It allocates the number **inside the database**, in one
statement that increments a counter row and returns the new value::

    INSERT INTO ticket_sequence (queue_id, service_day, last_value) VALUES (:queue, :day, 1)
    ON CONFLICT (queue_id, service_day) DO UPDATE SET last_value = ticket_sequence.last_value + 1
    RETURNING last_value

On PostgreSQL the ``ON CONFLICT DO UPDATE`` takes the counter row's lock and holds it until the
transaction ends, so every join on one queue that day waits for the one ahead of it and then reads
the value that one committed. Three consequences, each proved in
``tests/integration/queue/test_sequence_concurrency.py``:

* **No repeats.** A second transaction cannot read the counter until the first has committed or
  rolled back. Behind that, ``uq_ticket_queue_id_service_day_sequence`` refuses a duplicate even if
  something bypasses this module.
* **No gaps.** A join that fails after allocating (the queue turned out to be full, a check
  raised) rolls its increment back with everything else, so the day reads 1, 2, 3 with no holes.
* **Only one queue waits.** The lock is one row, keyed by queue and day: a join at the pharmacy
  never waits for a join at triage.

The cost is that joins on one queue are serialised for the length of their transactions, which is
why a join transaction must stay short: allocate, insert, audit, commit.

**The service day is the Johannesburg date** (:func:`src.commons.time.business_date`). A new day is
a new counter row, which is the whole of the midnight reset: nothing runs at midnight, so nothing
can fail to run.

**Reference codes** are for a person reading a ticket aloud at a reception desk: six characters
from :data:`REFERENCE_ALPHABET`, which leaves out ``0``/``O``, ``1``/``I``/``L`` and so every pair a
tired receptionist or a smudged stub could confuse. Random rather than derived from the number, so
yesterday's ``A043`` and today's do not share one; unique across the platform by constraint, with a
retry on the (roughly one in 887 million per pair) collision.
"""

import secrets
from datetime import date, datetime
from typing import Final

from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.dml import ReturningInsert

from src.commons.enums import TicketSource
from src.commons.time import business_date, now_sast
from src.database.models.queue import Queue
from src.database.models.ticket import REFERENCE_CODE_LENGTH, Ticket, TicketSequence

#: The characters a reference code is made of: digits and capitals without ``0 O 1 I L``. 31
#: symbols, so six of them give 31**6 (about 887 million) codes.
REFERENCE_ALPHABET: Final = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
#: Characters a person may type between groups (``K7M-4QP``, ``K7M 4QP``); ignored when parsing.
_SEPARATORS: Final = frozenset("- ")
#: How many times a colliding reference code is redrawn before giving up. Reaching the limit means
#: something other than chance is wrong (a broken random source), so it raises rather than looping.
MAX_REFERENCE_ATTEMPTS: Final = 5
#: The fewest digits a ticket number is written with: ``A007``, not ``A7``, so numbers line up on
#: the board and read the same way aloud.
NUMBER_DIGITS: Final = 3

#: The column a colliding reference code is reported against: PostgreSQL names the constraint
#: (``uq_ticket_reference_code``) and SQLite the column (``ticket.reference_code``); both contain it.
_REFERENCE_COLUMN: Final = "reference_code"


class ReferenceCodeExhaustedError(RuntimeError):
    """No free reference code was found in :data:`MAX_REFERENCE_ATTEMPTS` draws."""


def new_reference_code() -> str:
    """Draw a random reference code from :data:`REFERENCE_ALPHABET` with a CSPRNG.

    Not a secret (it finds a ticket at the desk, it never signs anybody in), but drawn from
    :mod:`secrets` all the same, so codes issued one after another cannot be predicted from each
    other.
    """
    return "".join(
        secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_CODE_LENGTH)
    )


def format_reference_code(code: str) -> str:
    """Write a code the way a person reads it: two groups of three, ``K7M-4QP``."""
    half = len(code) // 2
    return f"{code[:half]}-{code[half:]}"


def parse_reference_code(raw: str) -> str | None:
    """Read back a code a person typed: case and separators ignored; ``None`` if it cannot be one.

    ``k7m 4qp`` and ``K7M-4QP`` are the same code. A character outside the alphabet is refused
    rather than guessed at: ``0`` could be meant as ``O`` or ``D``, and neither is in the alphabet, so a
    code containing one was mistyped and a wrong guess would find somebody else's ticket.
    """
    code = "".join(ch for ch in raw.strip().upper() if ch not in _SEPARATORS)
    if len(code) != REFERENCE_CODE_LENGTH or any(
        ch not in REFERENCE_ALPHABET for ch in code
    ):
        return None
    return code


def format_ticket_number(prefix: str, sequence: int) -> str:
    """The number a patient is called by: the queue's prefix and the sequence, ``A043``.

    Raises:
        ValueError: If ``sequence`` is not positive; the database constraint would refuse it too.
    """
    if sequence < 1:
        raise ValueError(f"A ticket sequence starts at 1, not {sequence}.")
    return f"{prefix}{sequence:0{NUMBER_DIGITS}d}"


def _increment_statement(
    db: Session, queue_id: str, service_day: date
) -> ReturningInsert[tuple[int]]:
    """``INSERT … ON CONFLICT DO UPDATE … RETURNING`` for the session's dialect.

    PostgreSQL in production; SQLite (3.35 or later, which has both clauses) in the unit tests. The
    two constructors build the same statement.
    """
    values = {"queue_id": queue_id, "service_day": service_day, "last_value": 1}
    conflict = [TicketSequence.queue_id, TicketSequence.service_day]
    increment = {"last_value": TicketSequence.last_value + 1}
    if db.get_bind().dialect.name == "postgresql":
        pg = postgresql_insert(TicketSequence).values(**values)
        return pg.on_conflict_do_update(
            index_elements=conflict, set_=increment
        ).returning(TicketSequence.last_value)
    lite = sqlite_insert(TicketSequence).values(**values)
    return lite.on_conflict_do_update(
        index_elements=conflict, set_=increment
    ).returning(TicketSequence.last_value)


def allocate_sequence(db: Session, queue_id: str, service_day: date) -> int:
    """Allocate the next ticket number for a queue and service day, inside the database.

    One statement: insert the day's counter at 1, or increment the existing one, and return the
    value. It locks the counter row until the caller's transaction ends, which is what serialises
    concurrent joins on the same queue and day; the caller should therefore keep that transaction
    short. If the transaction rolls back, so does the increment, and the number is issued to the
    next join instead: the day has no gaps.

    Args:
        db: The session; the allocation joins its transaction.
        queue_id: The queue.
        service_day: The Johannesburg calendar date (:func:`src.commons.time.business_date`).

    Returns:
        The allocated sequence, 1 for the first ticket of the day.
    """
    return int(db.execute(_increment_statement(db, queue_id, service_day)).scalar_one())


def issue_ticket(
    db: Session,
    *,
    queue: Queue,
    source: TicketSource,
    patient_id: str | None = None,
    display_name: str | None = None,
    reason_text: str | None = None,
    comment_consent: bool = False,
    moment: datetime | None = None,
) -> Ticket:
    """Issue one ticket in ``queue``: a database-allocated number, a reference code, status waiting.

    The storage primitive behind joining. It decides nothing about **whether** the patient may join
    (open hours, capacity, duplicates and abuse guards are Issue 40's ``join_queue()``, which is the
    one function every channel calls); it guarantees only that the ticket it writes has a number no
    other ticket in that queue and day has, and that no number is skipped. The caller commits.

    Args:
        db: The session. The allocation, the insert and whatever the caller adds commit together.
        queue: The queue the ticket is issued in; its prefix starts the number.
        source: The channel the ticket came through. Recorded, never used for ordering.
        patient_id: The patient, or ``None`` for a walk-in the desk issued without a number.
        display_name: What the patient or the desk gave as a name.
        reason_text: The optional short reason for the visit.
        comment_consent: Per-visit consent to show the reason on the board.
        moment: When the ticket is issued (aware). ``None`` means now in Johannesburg; the service
            day is that moment's Johannesburg date.

    Returns:
        The flushed :class:`~src.database.models.ticket.Ticket`.

    Raises:
        ValueError: A remote join without a patient; the database's check constraint would refuse
            it too, but this message says why.
        ReferenceCodeExhaustedError: No free reference code after :data:`MAX_REFERENCE_ATTEMPTS`.
    """
    if patient_id is None and source is not TicketSource.WALK_IN:
        raise ValueError(
            f"A {source.value} ticket belongs to the patient who joined; only a walk-in may have none."
        )
    moment = moment or now_sast()
    service_day = business_date(moment)
    sequence = allocate_sequence(db, queue.id, service_day)
    for _ in range(MAX_REFERENCE_ATTEMPTS):
        ticket = Ticket(
            site_id=queue.site_id,
            queue_id=queue.id,
            patient_id=patient_id,
            service_day=service_day,
            sequence=sequence,
            number=format_ticket_number(queue.ticket_prefix, sequence),
            reference_code=new_reference_code(),
            source=source.value,
            display_name=display_name,
            reason_text=reason_text,
            comment_consent=comment_consent,
            joined_at=moment,
        )
        try:
            # A savepoint, so a colliding reference code undoes only this insert and keeps the
            # allocated number (and the counter's lock) for the retry.
            with db.begin_nested():
                db.add(ticket)
        except IntegrityError as exc:
            if _REFERENCE_COLUMN not in str(exc.orig):
                raise
            continue
        return ticket
    raise ReferenceCodeExhaustedError(
        f"No free reference code after {MAX_REFERENCE_ATTEMPTS} attempts."
    )
