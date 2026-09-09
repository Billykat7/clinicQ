"""Soft delete mixin: is_deleted flag instead of hard delete."""

from sqlalchemy import Boolean
from sqlalchemy.orm import Mapped, mapped_column


class SoftDeleteMixin:
    """Mixin for is_deleted boolean (default False)."""

    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        server_default="false",
    )
