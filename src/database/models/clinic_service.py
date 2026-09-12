"""What a clinic offers, and how long each thing usually takes (Issue 26).

**The model is ``ClinicService``, not ``Service``**, and the file is named to match. Every module in
this codebase has a ``service.py`` holding its persistence layer, so a model called ``Service``
would be misread in every review it ever appears in — ``from ... import service`` and
``from ... import Service`` one letter apart, meaning two completely different things.

The catalogue exists for two readers:

* **the wait estimator** (Issue 42). A clinic's first morning has no history, so
  ``expected_minutes`` is the estimator's prior until enough real samples exist. That is why it is
  validated to a sensible range rather than left free: zero would make an estimate divide by
  nothing, and a value in hours would report a wait measured in days;
* **a patient choosing where to go** (Issue 35). "This clinic does antenatal care on Tuesdays" is
  more useful than a queue length, and it is the only thing discovery can say about a clinic it has
  no live data for.

:data:`queue_clinic_service` links a queue to the services it handles. It is a Core table rather
than a mapped model on purpose: it carries no data of its own, and both its ends are already
site-scoped by their parents, so making it a model would add a row to the tenancy guard's surface
for nothing.
"""

from sqlalchemy import (
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.commons.enums import DbSchema, ServiceCategory
from src.commons.ids import new_id
from src.database.models.base import Base, metadata
from src.database.models.mixins import ActiveMixin, SoftDeleteMixin, TimestampMixin

SCHEMA = DbSchema.CLINICQ.value

#: The shortest a service can sensibly take. A minute: below that, an estimate divides by nothing.
MIN_EXPECTED_MINUTES = 1
#: The longest. Four hours is already implausible for one patient at one window; past it, somebody
#: has typed hours into a minutes field and the estimator would report a wait measured in days.
MAX_EXPECTED_MINUTES = 240

#: Which queue handles which service. No columns of its own, so a Core table rather than a model:
#: both ends are site-scoped by their parents, and a mapped class would add a row to the tenancy
#: guard's surface for nothing.
queue_clinic_service = Table(
    "queue_clinic_service",
    metadata,
    Column(
        "queue_id",
        String(36),
        ForeignKey(f"{SCHEMA}.queue.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "clinic_service_id",
        String(36),
        ForeignKey(f"{SCHEMA}.clinic_service.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    schema=SCHEMA,
)


class ClinicService(Base, TimestampMixin, ActiveMixin, SoftDeleteMixin):
    """One thing a clinic offers: "Chronic medication collection", 5 minutes."""

    __tablename__ = "clinic_service"
    __table_args__ = (
        # Unique per site, like a queue's name: every clinic offers a consultation.
        UniqueConstraint("site_id", "slug", name="uq_clinic_service_site_id_slug"),
        UniqueConstraint("site_id", "name", name="uq_clinic_service_site_id_name"),
        Index(
            "ix_clinicq_clinic_service_site_active",
            "site_id",
            "is_active",
            "display_order",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    site_id: Mapped[str] = mapped_column(
        String(36), ForeignKey(f"{SCHEMA}.site.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ServiceCategory.OTHER.value
    )
    """:class:`~src.commons.enums.ServiceCategory`, so a district report groups across clinics."""
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    """One line a patient reads on the clinic detail page. Never clinical advice."""
    expected_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    """How long one patient usually takes. The wait estimator's prior until Issue 42 has samples."""
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requires_appointment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    """Whether a patient must book rather than walk in (Issue 80 makes it mean something)."""

    queues = relationship(
        "Queue",
        secondary=queue_clinic_service,
        backref="clinic_services",
        lazy="selectin",
    )
    """The queues that handle this service. Optional: a clinic may list what it offers without
    saying which line it happens in."""

    @property
    def category_enum(self) -> ServiceCategory:
        """``category`` as its enum member."""
        return ServiceCategory(self.category)

    def __repr__(self) -> str:
        """Concise identifier for logs and test failures."""
        return f"ClinicService(id={self.id!r}, site_id={self.site_id!r}, slug={self.slug!r})"
