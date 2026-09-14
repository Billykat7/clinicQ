"""The board query and a patient's lookup run on their indexes, read from ``EXPLAIN`` (Issue 39).

A criterion of the form "runs on an index" is only true if the planner says so, over a table big
enough that an index is the cheaper plan. So the table here holds **20,000** tickets (four queues,
fifty service days, a hundred tickets a day each, a third of them with a patient) and is
``ANALYZE``-d, and the planner is **not** nudged: no ``enable_seqscan = off``. The SQL is compiled
from :func:`~src.modules.queue.tickets.board_select` and
:func:`~src.modules.queue.tickets.patient_tickets_select`, so a change to either cannot leave this
test reading a query nobody runs.
"""

import logging
from collections.abc import Iterator
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import Engine, Select, insert, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import sessionmaker

from src.commons.enums import TicketSource, TicketStatus
from src.commons.ids import new_id
from src.commons.time import business_date, now_sast
from src.core.site_scope import SiteAccess
from src.database.models import Ticket, User, Visit
from src.modules.queue.sequence import format_ticket_number, new_reference_code
from src.modules.queue.tickets import board_select, patient_tickets_select
from tests.factories import PatientFactory, QueueFactory, SiteFactory

pytestmark = pytest.mark.postgres

logger = logging.getLogger(__name__)

QUEUES = 4
DAYS = 50
PER_DAY = 100


@pytest.fixture
def busy_clinic(migrated_engine: Engine) -> Iterator[SimpleNamespace]:
    """One clinic, four queues, fifty days of a hundred tickets a day each, analysed."""
    factory = sessionmaker(
        bind=migrated_engine, autoflush=False, expire_on_commit=False
    )
    today = business_date()
    with factory() as db:
        site = SiteFactory.create(db)
        queues = [
            QueueFactory.create(db, site_id=site.id, ticket_prefix=prefix)
            for prefix in "TAPD"
        ]
        patients = [PatientFactory.create(db) for _ in range(300)]
        # One visit for the seeded rows: the plans below read tickets, and only need the key valid.
        visit = Visit(site_id=site.id, started_at=now_sast())
        db.add(visit)
        db.commit()
        rows = []
        # Drawn up front and de-duplicated: a bulk insert has no retry, and 20,000 random draws
        # from 887 million codes collide about one time in five (issue_ticket redraws instead).
        codes: set[str] = set()
        while len(codes) < QUEUES * DAYS * PER_DAY:
            codes.add(new_reference_code())
        unused = iter(codes)
        for back in range(DAYS):
            day = today - timedelta(days=back)
            joined = now_sast() - timedelta(days=back)
            for queue in queues:
                for sequence in range(1, PER_DAY + 1):
                    patient = patients[(back * PER_DAY + sequence) % len(patients)]
                    rows.append(
                        {
                            "id": new_id(),
                            "site_id": site.id,
                            "queue_id": queue.id,
                            # A third remote with a patient, the rest walk-ins without one.
                            "patient_id": patient.id if sequence % 3 == 0 else None,
                            "service_day": day,
                            "sequence": sequence,
                            "order_key": float(sequence),
                            "visit_id": visit.id,
                            "number": format_ticket_number(
                                queue.ticket_prefix, sequence
                            ),
                            "reference_code": next(unused),
                            "source": (
                                TicketSource.USSD
                                if sequence % 3 == 0
                                else TicketSource.WALK_IN
                            ).value,
                            # Past days are finished; today is still in progress.
                            "status": (
                                TicketStatus.DONE if back else TicketStatus.WAITING
                            ).value,
                            "joined_at": joined,
                        }
                    )
        db.execute(insert(Ticket), rows)
        db.commit()
    with migrated_engine.connect() as conn:
        conn.execute(text("ANALYZE clinicq.ticket"))
        conn.commit()
    access = SiteAccess(
        site_id=site.id, user=User(id=new_id(), email="desk@clinicq.example")
    )
    yield SimpleNamespace(
        engine=migrated_engine,
        access=access,
        queue=queues[0],
        patient=patients[3],
        today=today,
    )


def _explain(engine: Engine, statement: Select[tuple[Ticket]], *, analyze: bool) -> str:
    """The plan PostgreSQL chooses for ``statement``, as text."""
    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    options = "ANALYZE, BUFFERS" if analyze else "COSTS"
    with engine.connect() as conn:
        return "\n".join(
            row[0] for row in conn.execute(text(f"EXPLAIN ({options}) {compiled}"))
        )


def test_the_board_query_for_one_queue_runs_on_the_board_index(
    busy_clinic: SimpleNamespace,
) -> None:
    """Criterion 4: one queue's tickets today, in sequence order, from ``ix_clinicq_ticket_board``."""
    statement = board_select(
        busy_clinic.access, busy_clinic.queue.id, busy_clinic.today
    )

    plan = _explain(busy_clinic.engine, statement, analyze=False)
    assert "ix_clinicq_ticket_board" in plan, plan
    assert "Index Scan" in plan or "Bitmap Index Scan" in plan, plan
    assert "Seq Scan on ticket" not in plan, plan

    measured = _explain(busy_clinic.engine, statement, analyze=True)
    logger.info("board query over %d tickets:\n%s", QUEUES * DAYS * PER_DAY, measured)


def test_a_patients_own_tickets_run_on_the_patient_index(
    busy_clinic: SimpleNamespace,
) -> None:
    """The patient lookup reads ``ix_clinicq_ticket_patient``, not the whole table."""
    statement = patient_tickets_select(busy_clinic.patient.id, busy_clinic.today)

    plan = _explain(busy_clinic.engine, statement, analyze=False)
    assert "ix_clinicq_ticket_patient" in plan, plan
    assert "Seq Scan on ticket" not in plan, plan
