"""Price time-series (SCD type-2): each product price change is a new dated row
rather than an overwrite, so quotes pin to a date and stale prices are a query."""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampedBase


class PriceHistory(TimestampedBase):
    __tablename__ = "price_history"

    irc: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    price_type: Mapped[str] = mapped_column(String(20), nullable=False)  # announced|invoice|insurer_reference
    insurer: Mapped[str | None] = mapped_column(String(20), nullable=True)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False)       # Rial
    currency: Mapped[str] = mapped_column(String(8), default="IRR", nullable=False)
    source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
