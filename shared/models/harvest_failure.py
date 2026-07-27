"""Page ids a harvest could not fetch, so a later pass can retry ONLY those.

A full NFI sweep is ~70,000 pages at seconds each; re-running it to recover a
few thousand pages the proxy dropped costs hours of wall-clock for nothing. The
observed runs lost 997, 3,493, 164 and 0 pages to dns_fail alone — entirely
transport failures, entirely re-fetchable.

Only TRANSPORT failures are worth retrying. A 5xx here overwhelmingly means the
id does not exist (they arrive in long contiguous blocks — 58,979–70,000 in one
run), so replaying those would re-burn the same hours. The category is stored so
the owner picks what to retry rather than the code deciding once.

A row is resolved (not deleted) once the id is fetched successfully, keeping the
history of which ids are chronically unreachable.
"""
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import TimestampedBase


class HarvestFailure(TimestampedBase):
    __tablename__ = "harvest_failures"
    __table_args__ = (
        UniqueConstraint("crawler", "page_id", name="uq_harvest_failure_page"),
    )

    crawler: Mapped[str] = mapped_column(String(20), index=True, nullable=False)  # "nfi"
    page_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)   # dns_fail, timeout…
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # set when a later pass fetched the page successfully
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True)
