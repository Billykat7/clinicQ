"""Active mixin: is_active flag for soft enable/disable."""

from sqlalchemy import Boolean
from sqlalchemy.orm import Mapped, mapped_column


class ActiveMixin:
    """Mixin for is_active boolean (default True)."""

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
        server_default="true",
    )
