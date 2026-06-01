from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampedBase(Base):
    __abstract__ = True

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AuditedBase(TimestampedBase):
    """Base for PHI-bearing tables — every write produces an audit event."""

    __abstract__ = True

    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    is_deleted: Mapped[bool] = mapped_column(default=False, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
