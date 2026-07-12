import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from soundings.db.models import Base


class IndicatorValue(Base):
    __tablename__ = "indicator_value"
    __table_args__ = ({"schema": "data"},)

    place_id: Mapped[str] = mapped_column(ForeignKey("geography.place.id"), primary_key=True)
    indicator_key: Mapped[str] = mapped_column(
        ForeignKey("catalogue.indicator.key"), primary_key=True
    )
    period: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("catalogue.source.id"))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    loader_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    caveats: Mapped[list[str]] = mapped_column(JSONB, default=list)


class TrendPoint(Base):
    __tablename__ = "trend_point"
    __table_args__ = ({"schema": "data"},)

    place_id: Mapped[str] = mapped_column(ForeignKey("geography.place.id"), primary_key=True)
    indicator_key: Mapped[str] = mapped_column(
        ForeignKey("catalogue.indicator.key"), primary_key=True
    )
    period: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    revised: Mapped[bool] = mapped_column(default=False)
    source_id: Mapped[str] = mapped_column(ForeignKey("catalogue.source.id"))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Organisation(Base):
    __tablename__ = "organisation"
    __table_args__ = ({"schema": "data"},)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    classification: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    registered_address_place_id: Mapped[str | None] = mapped_column(
        ForeignKey("geography.place.id"), nullable=True
    )
    source_id: Mapped[str] = mapped_column(ForeignKey("catalogue.source.id"))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class OrganisationOperatesIn(Base):
    __tablename__ = "organisation_operates_in"
    __table_args__ = ({"schema": "data"},)

    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("data.organisation.id"), primary_key=True
    )
    place_id: Mapped[str] = mapped_column(ForeignKey("geography.place.id"), primary_key=True)


class OrganisationClassification(Base):
    __tablename__ = "organisation_classification"
    __table_args__ = ({"schema": "data"},)

    organisation_id: Mapped[str] = mapped_column(
        ForeignKey("data.organisation.id", ondelete="CASCADE"), primary_key=True
    )
    classification_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    classification_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    classification_label: Mapped[str] = mapped_column(String(128))


class GrantRecord(Base):
    __tablename__ = "grant_record"
    __table_args__ = ({"schema": "data"},)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    funder_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recipient_org_id: Mapped[str | None] = mapped_column(
        ForeignKey("data.organisation.id"), nullable=True
    )
    amount: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    awarded_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    beneficiary_place_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    source_id: Mapped[str] = mapped_column(ForeignKey("catalogue.source.id"))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LoaderRun(Base):
    __tablename__ = "loader_run"
    __table_args__ = ({"schema": "data"},)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[str] = mapped_column(ForeignKey("catalogue.source.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    rows_written: Mapped[int] = mapped_column(default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
