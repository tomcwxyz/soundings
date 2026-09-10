"""Durable Charity Commission lifecycle facts and annual observations.

The main CC bulk extract is a current snapshot, but registration/removal dates
are durable source facts. This module stores those facts once and projects them
into ordinary annual Soundings observations so existing temporal tools can
query them.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.postcodes_io.adapter import _normalise_postcode
from soundings.db.models.data import OrganisationLifecycle

SOURCE_ID = "charity_commission"
LIFECYCLE_INSERT_CHUNK = 1000
LIFECYCLE_INDICATORS = (
    "civil_society.charities_registered_count",
    "civil_society.charities_removed_count",
    "civil_society.charities_net_change",
)


def _parse_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


async def resolve_lifecycle_places_locally(
    engine: AsyncEngine,
    rows: list[dict[str, Any]],
) -> dict[str, str | None]:
    """Resolve source postcodes using only Soundings' local postcode spine.

    Unlike the active-charity resolver this never calls postcodes.io. Historic
    or removed-only postcodes can therefore be missing without turning one
    monthly lifecycle refresh into a large upstream crawl.
    """
    originals = [str(row.get("postcode") or "") for row in rows if row.get("postcode")]
    if not originals:
        return {}

    norm_to_originals: dict[str, list[str]] = {}
    for original in originals:
        norm_to_originals.setdefault(_normalise_postcode(original), []).append(original)

    async with engine.connect() as conn:
        db_rows = (
            await conn.execute(
                text(
                    "SELECT postcode, ltla24 FROM geography.postcode "
                    "WHERE postcode = ANY(:codes)"
                ),
                {"codes": list(norm_to_originals)},
            )
        ).all()

    by_norm = {str(row.postcode): row.ltla24 for row in db_rows}
    resolved: dict[str, str | None] = {}
    for norm, source_values in norm_to_originals.items():
        place_id = by_norm.get(norm)
        for source_value in source_values:
            resolved[source_value] = place_id
    return resolved


def build_lifecycle_rows(
    rows: list[dict[str, Any]],
    resolved: dict[str, str | None],
    retrieved_at: datetime,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for charity in rows:
        postcode = str(charity.get("postcode") or "")
        out.append(
            {
                "organisation_id": f"charity_commission:{charity['registration_number']}",
                "name": str(charity.get("name") or ""),
                "status": str(charity.get("status") or ""),
                "registered_on": _parse_date(charity.get("date_of_registration")),
                "removed_on": _parse_date(charity.get("date_of_removal")),
                "postcode": postcode or None,
                "registered_address_place_id": resolved.get(postcode),
                "source_id": SOURCE_ID,
                "retrieved_at": retrieved_at,
            }
        )
    return out


async def upsert_lifecycle_rows(
    engine: AsyncEngine,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    async with engine.begin() as conn:
        for start in range(0, len(rows), LIFECYCLE_INSERT_CHUNK):
            stmt = insert(OrganisationLifecycle).values(rows[start : start + LIFECYCLE_INSERT_CHUNK])
            stmt = stmt.on_conflict_do_update(
                index_elements=[OrganisationLifecycle.organisation_id],
                set_={
                    "name": stmt.excluded.name,
                    "status": stmt.excluded.status,
                    "registered_on": stmt.excluded.registered_on,
                    "removed_on": stmt.excluded.removed_on,
                    "postcode": stmt.excluded.postcode,
                    "registered_address_place_id": stmt.excluded.registered_address_place_id,
                    "source_id": stmt.excluded.source_id,
                    "retrieved_at": stmt.excluded.retrieved_at,
                },
            )
            await conn.execute(stmt)


async def rebuild_lifecycle_indicators(
    engine: AsyncEngine,
    retrieved_at: datetime,
) -> str | None:
    """Replace all annual lifecycle observations from canonical facts.

    Full replacement matters when the Commission corrects a registration or
    removal date: the old period must disappear rather than survive beside the
    corrected one. Raw lifecycle ingestion remains usable before catalogue
    bootstrap; in that case materialisation is skipped rather than failing the
    whole loader.
    """
    async with engine.begin() as conn:
        catalogue_rows = (
            await conn.execute(
                text("SELECT key FROM catalogue.indicator WHERE key = ANY(:keys)"),
                {"keys": list(LIFECYCLE_INDICATORS)},
            )
        ).all()
        available = {str(row.key) for row in catalogue_rows}
        missing = sorted(set(LIFECYCLE_INDICATORS) - available)
        if missing:
            return "lifecycle observations skipped; catalogue missing: " + ", ".join(missing)

        await conn.execute(
            text(
                "DELETE FROM data.indicator_value "
                "WHERE source_id = :sid AND indicator_key = ANY(:keys)"
            ),
            {"sid": SOURCE_ID, "keys": list(LIFECYCLE_INDICATORS)},
        )

        await conn.execute(
            text(
                "INSERT INTO data.indicator_value "
                "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) "
                "SELECT registered_address_place_id, "
                "       'civil_society.charities_registered_count', "
                "       EXTRACT(YEAR FROM registered_on)::int::text, COUNT(*), "
                "       :sid, :retrieved, '[]'::jsonb "
                "FROM data.organisation_lifecycle "
                "WHERE source_id = :sid "
                "  AND registered_address_place_id IS NOT NULL "
                "  AND registered_on IS NOT NULL "
                "GROUP BY registered_address_place_id, EXTRACT(YEAR FROM registered_on)"
            ),
            {"sid": SOURCE_ID, "retrieved": retrieved_at},
        )
        await conn.execute(
            text(
                "INSERT INTO data.indicator_value "
                "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) "
                "SELECT registered_address_place_id, "
                "       'civil_society.charities_removed_count', "
                "       EXTRACT(YEAR FROM removed_on)::int::text, COUNT(*), "
                "       :sid, :retrieved, '[]'::jsonb "
                "FROM data.organisation_lifecycle "
                "WHERE source_id = :sid "
                "  AND registered_address_place_id IS NOT NULL "
                "  AND removed_on IS NOT NULL "
                "GROUP BY registered_address_place_id, EXTRACT(YEAR FROM removed_on)"
            ),
            {"sid": SOURCE_ID, "retrieved": retrieved_at},
        )
        await conn.execute(
            text(
                "INSERT INTO data.indicator_value "
                "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) "
                "SELECT place_id, 'civil_society.charities_net_change', year, SUM(delta), "
                "       :sid, :retrieved, '[]'::jsonb "
                "FROM ("
                "  SELECT registered_address_place_id AS place_id, "
                "         EXTRACT(YEAR FROM registered_on)::int::text AS year, 1 AS delta "
                "  FROM data.organisation_lifecycle "
                "  WHERE source_id = :sid "
                "    AND registered_address_place_id IS NOT NULL "
                "    AND registered_on IS NOT NULL "
                "  UNION ALL "
                "  SELECT registered_address_place_id AS place_id, "
                "         EXTRACT(YEAR FROM removed_on)::int::text AS year, -1 AS delta "
                "  FROM data.organisation_lifecycle "
                "  WHERE source_id = :sid "
                "    AND registered_address_place_id IS NOT NULL "
                "    AND removed_on IS NOT NULL"
                ") events GROUP BY place_id, year"
            ),
            {"sid": SOURCE_ID, "retrieved": retrieved_at},
        )

        unresolved = (
            await conn.execute(
                text(
                    "SELECT "
                    "COUNT(*) FILTER (WHERE registered_on IS NOT NULL) AS registrations, "
                    "COUNT(*) FILTER (WHERE removed_on IS NOT NULL) AS removals "
                    "FROM data.organisation_lifecycle "
                    "WHERE source_id = :sid AND registered_address_place_id IS NULL"
                ),
                {"sid": SOURCE_ID},
            )
        ).first()

    if unresolved is None:
        return None
    registrations = int(unresolved.registrations or 0)
    removals = int(unresolved.removals or 0)
    if not registrations and not removals:
        return None
    return (
        "lifecycle geography unresolved for "
        f"{registrations} registration events and {removals} removal events"
    )
