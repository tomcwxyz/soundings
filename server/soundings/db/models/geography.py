from datetime import date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from soundings.db.models import Base


class Place(Base):
    __tablename__ = "place"
    __table_args__ = (
        UniqueConstraint("type", "code", "valid_from", name="uq_place_type_code_validfrom"),
        Index("ix_place_geom", "geom", postgresql_using="gist"),
        {"schema": "geography"},
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    type: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(Text)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    geom: Mapped[object | None] = mapped_column(
        Geometry("MULTIPOLYGON", srid=4326, spatial_index=False), nullable=True
    )


class PlaceHierarchy(Base):
    __tablename__ = "place_hierarchy"
    __table_args__ = (
        Index("ix_place_hierarchy_child_validity", "child_id", "valid_from", "valid_to"),
        {"schema": "geography"},
    )

    child_id: Mapped[str] = mapped_column(ForeignKey("geography.place.id"), primary_key=True)
    parent_id: Mapped[str] = mapped_column(ForeignKey("geography.place.id"), primary_key=True)
    # Half-open validity interval [valid_from, valid_to). Null/null means the
    # edge is an undated current snapshot, not evidence of historical validity.
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class Postcode(Base):
    __tablename__ = "postcode"
    __table_args__ = ({"schema": "geography"},)

    postcode: Mapped[str] = mapped_column(String(8), primary_key=True)
    lsoa21: Mapped[str | None] = mapped_column(ForeignKey("geography.place.id"), nullable=True)
    msoa21: Mapped[str | None] = mapped_column(ForeignKey("geography.place.id"), nullable=True)
    ltla24: Mapped[str | None] = mapped_column(ForeignKey("geography.place.id"), nullable=True)
    utla24: Mapped[str | None] = mapped_column(ForeignKey("geography.place.id"), nullable=True)
    ward24: Mapped[str | None] = mapped_column(ForeignKey("geography.place.id"), nullable=True)
    westminster_constituency_24: Mapped[str | None] = mapped_column(
        ForeignKey("geography.place.id"), nullable=True
    )
    region: Mapped[str | None] = mapped_column(ForeignKey("geography.place.id"), nullable=True)
    country: Mapped[str | None] = mapped_column(ForeignKey("geography.place.id"), nullable=True)
    latitude: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    longitude: Mapped[float | None] = mapped_column(Numeric(9, 6), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CodeChange(Base):
    __tablename__ = "code_change"
    __table_args__ = ({"schema": "geography"},)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    old_code: Mapped[str] = mapped_column(String(32))
    new_code: Mapped[str] = mapped_column(String(32))
    change_type: Mapped[str] = mapped_column(String(32))
    effective_date: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
