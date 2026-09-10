"""GeographyService — internal orchestrator-facing geography API.

Resolves postcodes/names/points to canonical place rows and traverses the
place hierarchy. Built on top of `geography.*` tables and the postcodes.io
adapter.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import aliased

from soundings.adapters.postcodes_io.adapter import PostcodesIoAdapter
from soundings.db.models.geography import Place, PlaceHierarchy, Postcode

POSTCODE_FRESHNESS = timedelta(days=30)
BoundaryMode = Literal["current_boundary", "historical"]


@dataclass(frozen=True)
class PlaceMatch:
    place: Place
    confidence: float


@dataclass(frozen=True)
class ContainmentResult:
    """Containing places plus the boundary semantics used to select them."""

    places: tuple[Place, ...]
    boundary_mode: BoundaryMode
    boundary_date: date
    as_of: date | None = None
    partial: bool = False
    caveats: tuple[str, ...] = ()


def _normalise_postcode(postcode: str) -> str:
    return postcode.replace(" ", "").upper()


class GeographyService:
    def __init__(self, engine: AsyncEngine, postcodes_io: PostcodesIoAdapter) -> None:
        self._engine = engine
        self._postcodes_io = postcodes_io

    async def find_place_by_postcode(self, postcode: str) -> dict[str, Place] | None:
        """Returns dict keyed by place type → Place, for all containing levels.

        Cache-first: hits geography.postcode if a fresh row exists, otherwise
        falls through to the postcodes.io adapter to resolve and upsert.
        """
        normalised = _normalise_postcode(postcode)
        cached = await self._read_cached_postcode(normalised)
        if cached is None:
            await self._postcodes_io.upsert_postcode(postcode)
            cached = await self._read_cached_postcode(normalised)
            if cached is None:
                return None

        place_ids = [
            pid
            for pid in (
                cached.lsoa21,
                cached.msoa21,
                cached.ltla24,
                cached.utla24,
                cached.ward24,
                cached.westminster_constituency_24,
                cached.region,
                cached.country,
            )
            if pid
        ]
        if not place_ids:
            return {}

        async with AsyncSession(self._engine) as session:
            places = (await session.scalars(select(Place).where(Place.id.in_(place_ids)))).all()
        return {p.type: p for p in places}

    async def find_place_by_name(
        self,
        query: str,
        geography_types: list[str] | None = None,
        limit: int = 5,
    ) -> list[PlaceMatch]:
        """Fuzzy place-name search via pg_trgm similarity."""
        similarity = func.similarity(Place.name, query).label("score")
        stmt = (
            select(Place, similarity)
            .where(similarity > 0.1)
            .order_by(similarity.desc())
            .limit(limit)
        )
        if geography_types:
            stmt = stmt.where(Place.type.in_(geography_types))
        async with AsyncSession(self._engine) as session:
            rows = (await session.execute(stmt)).all()
        return [PlaceMatch(place=r.Place, confidence=float(r.score)) for r in rows]

    async def find_containing_places(self, place_id: str) -> list[Place]:
        """Current-boundary ancestors of a place.

        This preserves the original API while filtering out hierarchy edges
        whose dated validity has ended. Undated edges are treated as current
        snapshots, never as proof of historical containment.
        """
        result = await self.find_containing_places_context(
            place_id,
            boundary_mode="current_boundary",
        )
        return list(result.places)

    async def find_containing_places_context(
        self,
        place_id: str,
        *,
        boundary_mode: BoundaryMode = "current_boundary",
        as_of: date | None = None,
    ) -> ContainmentResult:
        """Resolve ancestors using explicit current or historical boundaries.

        Hierarchy validity is half-open: ``[valid_from, valid_to)``. Historical
        mode requires ``as_of`` and recursively traverses only dated direct
        edges valid on that date. Undated OGP snapshots are never substituted
        for missing historical evidence.
        """
        if boundary_mode == "historical" and as_of is None:
            raise ValueError("historical boundary mode requires as_of")

        boundary_date = date.today() if boundary_mode == "current_boundary" else as_of
        assert boundary_date is not None

        direct_dated_count = 0
        undated_count = 0
        async with AsyncSession(self._engine) as session:
            if boundary_mode == "current_boundary":
                validity = (
                    or_(
                        PlaceHierarchy.valid_from.is_(None),
                        PlaceHierarchy.valid_from <= boundary_date,
                    ),
                    or_(
                        PlaceHierarchy.valid_to.is_(None),
                        PlaceHierarchy.valid_to > boundary_date,
                    ),
                )
                stmt = (
                    select(Place)
                    .join(PlaceHierarchy, Place.id == PlaceHierarchy.parent_id)
                    .where(PlaceHierarchy.child_id == place_id, *validity)
                    .distinct()
                )
                places = tuple((await session.scalars(stmt)).all())
            else:
                # CHD stores immediate parent relationships. Build the dated
                # ancestor chain recursively at the requested boundary date.
                ancestors = (
                    select(PlaceHierarchy.parent_id.label("parent_id"))
                    .where(
                        PlaceHierarchy.child_id == place_id,
                        PlaceHierarchy.valid_from.is_not(None),
                        PlaceHierarchy.valid_from <= boundary_date,
                        or_(
                            PlaceHierarchy.valid_to.is_(None),
                            PlaceHierarchy.valid_to > boundary_date,
                        ),
                    )
                    .cte("historical_ancestors", recursive=True)
                )
                parent_edge = aliased(PlaceHierarchy)
                ancestors = ancestors.union(
                    select(parent_edge.parent_id.label("parent_id"))
                    .join(ancestors, parent_edge.child_id == ancestors.c.parent_id)
                    .where(
                        parent_edge.valid_from.is_not(None),
                        parent_edge.valid_from <= boundary_date,
                        or_(
                            parent_edge.valid_to.is_(None),
                            parent_edge.valid_to > boundary_date,
                        ),
                    )
                )
                stmt = select(Place).join(ancestors, Place.id == ancestors.c.parent_id).distinct()
                places = tuple((await session.scalars(stmt)).all())

                direct_dated_count = int(
                    (
                        await session.scalar(
                            select(func.count())
                            .select_from(PlaceHierarchy)
                            .where(
                                PlaceHierarchy.child_id == place_id,
                                PlaceHierarchy.valid_from.is_not(None),
                                PlaceHierarchy.valid_from <= boundary_date,
                                or_(
                                    PlaceHierarchy.valid_to.is_(None),
                                    PlaceHierarchy.valid_to > boundary_date,
                                ),
                            )
                        )
                    )
                    or 0
                )
                if direct_dated_count == 0:
                    undated_count = int(
                        (
                            await session.scalar(
                                select(func.count())
                                .select_from(PlaceHierarchy)
                                .where(
                                    PlaceHierarchy.child_id == place_id,
                                    PlaceHierarchy.valid_from.is_(None),
                                    PlaceHierarchy.valid_to.is_(None),
                                )
                            )
                        )
                        or 0
                    )

        caveats: list[str] = []
        partial = False
        if boundary_mode == "current_boundary" and as_of is not None:
            caveats.append(
                "Current-boundary mode uses boundaries valid today; as_of is "
                "observation context only."
            )
        if boundary_mode == "historical" and direct_dated_count == 0:
            partial = True
            if undated_count:
                caveats.append(
                    f"{undated_count} current hierarchy edge(s) for {place_id} are undated and "
                    "were excluded from the historical result."
                )
            caveats.append(
                f"No dated hierarchy edges for {place_id} cover {boundary_date.isoformat()}."
            )

        return ContainmentResult(
            places=places,
            boundary_mode=boundary_mode,
            boundary_date=boundary_date,
            as_of=as_of,
            partial=partial,
            caveats=tuple(caveats),
        )

    async def find_containing_places_by_point(
        self,
        lat: float,
        lng: float,
        types: list[str] | None = None,
    ) -> list[Place]:
        """Point-in-polygon containing-place lookup via PostGIS ST_Within."""
        clauses = "WHERE ST_Within(ST_SetSRID(ST_Point(:lng, :lat), 4326), geom)"
        if types:
            placeholders = ", ".join(f":t{i}" for i, _ in enumerate(types))
            clauses += f" AND type IN ({placeholders})"
        # `clauses` is built only from a fixed set of `:tN` placeholders, not user input.
        sql = f"SELECT id, type, code, name, valid_from, valid_to FROM geography.place {clauses}"  # noqa: S608
        params: dict[str, object] = {"lat": lat, "lng": lng}
        if types:
            for i, t in enumerate(types):
                params[f"t{i}"] = t
        async with AsyncSession(self._engine) as session:
            rows = (await session.execute(text(sql), params)).all()
        return [
            Place(
                id=r.id,
                type=r.type,
                code=r.code,
                name=r.name,
                valid_from=r.valid_from,
                valid_to=r.valid_to,
            )
            for r in rows
        ]

    async def _read_cached_postcode(self, normalised: str) -> Postcode | None:
        cutoff = datetime.now(tz=UTC) - POSTCODE_FRESHNESS
        async with AsyncSession(self._engine) as session:
            return (
                await session.scalars(
                    select(Postcode).where(
                        Postcode.postcode == normalised,
                        Postcode.retrieved_at >= cutoff,
                    )
                )
            ).one_or_none()
