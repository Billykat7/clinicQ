"""Grant *usage* telemetry: when was each grant last exercised (Issue #176, M29).

``permission_audit_log`` records every grant **change** — actor, before, after. Nothing recorded a
grant's **use**, so nobody could distinguish a load-bearing grant from one copied into a role three
years ago during a migration, and the only safe move with any grant was to leave it alone. Grants
accumulate; least privilege erodes by default. This is IAM's Access Advisor ("last accessed"), which
exists for exactly that reason: pruning requires evidence, and "nobody complained" is not evidence.

**This is advisory data, never an audit trail.** Two consequences, both deliberate and both load-
bearing:

* An in-process buffer coalesces hits and an APScheduler job flushes them on an interval
  (:mod:`src.core.permission_usage`). A buffer lost on shutdown is **acceptable**: the worst case is
  a ``last_used_at`` that is a few minutes stale, which changes no pruning decision. The table must
  never be presented in the UI as a record of what happened.
* **Deliberately not stored:** no per-request rows, no user id, no path, no payload, no IP. "Which
  grant, how recently, roughly how often" is the entire requirement. Anything more turns a pruning
  aid into a surveillance log with a retention obligation nobody asked for.

**Retention** needs no sweep: ``last_used_at`` is one timestamp per grant, so the table is bounded
by the grant count, and a revoked grant's row is deleted with the grant.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base

#: The single ``permission_usage_window`` row's primary key. One row, always — the table answers one
#: question ("since when has usage been recorded here?") for the whole deployment.
USAGE_WINDOW_ID = "default"


class PermissionUsage(Base):
    """One grant's usage: when it was last exercised, and roughly how often.

    Keyed by ``(role, resource, verb)`` — the grant, not the caller. ``verb`` carries a cumulative
    CRUD verb *or* a named action key (Issue #140), because both are things a role holds and both
    are things an operator prunes; they never collide, since the two catalogs are disjoint by
    construction.
    """

    __tablename__ = "permission_usage"

    role: Mapped[str] = mapped_column(String(50), primary_key=True)
    resource: Mapped[str] = mapped_column(String(120), primary_key=True)
    verb: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    """When this grant was most recently exercised on an **allow** (business timezone).

    A denial is not recorded: it is already visible through the 403 logging path, and it says
    nothing about whether the grant is needed — it says the opposite.
    """
    hit_count: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"),
        nullable=False,
        default=0,
        server_default="0",
    )
    """Roughly how many allowed requests this grant has carried since collection began.

    *Roughly*: hits are coalesced in-process between flushes and a buffer lost on shutdown is
    dropped. It is an order-of-magnitude signal for prioritising a pruning worklist, never a count
    to reconcile against anything.
    """


class PermissionUsageWindow(Base):
    """When usage collection began on this deployment — the caveat the worklist needs.

    A grant can read "never used" simply because the window is young, and an operator looking at a
    fresh deployment has no way to tell that from a genuinely dead grant. So the start date is
    stored, seeded by the migration that creates these tables, and shown next to the "never used in
    90 days" filter.

    Kept as its own one-row table rather than as a column on :class:`PermissionUsage`: it is a fact
    about the *deployment*, not about any grant, and putting it on the grant table would mean
    either repeating it on every row or having nowhere to record it before the first row exists.
    """

    __tablename__ = "permission_usage_window"

    id: Mapped[str] = mapped_column(
        String(16), primary_key=True, default=USAGE_WINDOW_ID
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
