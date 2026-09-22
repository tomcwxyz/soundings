from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from soundings.db.models import Base


class Observation(Base):
    """An organisation-submitted observation about a place.

    Maps to the `data.observation` table created in migration 0009
    as part of the observations MVP (see
    docs/plans/2026-08-24-observations-mvp.md).
    """

    __tablename__ = "observation"
    __table_args__ = (
        CheckConstraint(
            "evidence_type IN ('quantitative', 'qualitative')",
            name="observation_evidence_type_check",
        ),
        CheckConstraint(
            "confidence IN ('high', 'medium', 'low')",
            name="observation_confidence_check",
        ),
        {"schema": "data"},
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organisation_id: Mapped[str] = mapped_column(String(64), ForeignKey("data.organisation.id"))
    place_id: Mapped[str] = mapped_column(ForeignKey("geography.place.id"))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    theme: Mapped[str] = mapped_column(String(64), ForeignKey("catalogue.theme.key"))
    statement: Mapped[str] = mapped_column(Text)
    indicator_key: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("catalogue.indicator.key"), nullable=True
    )
    value: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    evidence_type: Mapped[str] = mapped_column(String(16))
    methodology_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[str] = mapped_column(String(8))
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
