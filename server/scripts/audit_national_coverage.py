"""Read-only nation/source coverage audit against a selected Soundings database.

Run with DATABASE_URL explicitly pointing to a development/isolated database:
  cd server
  uv run python scripts/audit_national_coverage.py

Reports real materialised records; catalogue declarations alone are not
misrepresented as populated data. It never calls upstream or mutates rows.
"""

import asyncio
import json

from sqlalchemy import text

from soundings.db.engine import get_engine

NATIONS = {"E": "England", "W": "Wales", "S": "Scotland", "N": "Northern Ireland"}
CORE_PLACE_TYPES = ("country", "ltla24", "lsoa21", "msoa21", "dz22", "iz22", "dz11")


async def audit() -> dict[str, object]:
    engine = get_engine()
    async with engine.connect() as conn:
        places = (
            await conn.execute(
                text(
                    "SELECT left(code, 1) AS nation, type, count(*) AS n "
                    "FROM geography.place WHERE type = ANY(:types) "
                    "GROUP BY left(code, 1), type ORDER BY nation, type"
                ),
                {"types": list(CORE_PLACE_TYPES)},
            )
        ).all()
        values = (
            await conn.execute(
                text(
                    "SELECT left(p.code,1) AS nation, p.type, iv.source_id, "
                    "count(*) AS observations, count(DISTINCT p.id) AS places, "
                    "count(DISTINCT iv.indicator_key) AS indicators "
                    "FROM data.indicator_value iv "
                    "JOIN geography.place p ON p.id = iv.place_id "
                    "GROUP BY left(p.code,1), p.type, iv.source_id "
                    "ORDER BY nation, p.type, iv.source_id"
                )
            )
        ).all()
        registered = (
            await conn.execute(
                text(
                    "SELECT left(p.code,1) AS nation, count(*) AS n "
                    "FROM data.organisation o "
                    "JOIN geography.place p ON p.id = o.registered_address_place_id "
                    "GROUP BY left(p.code,1) ORDER BY nation"
                )
            )
        ).all()
        operating = (
            await conn.execute(
                text(
                    "SELECT left(p.code,1) AS nation, "
                    "count(DISTINCT o.organisation_id) AS n "
                    "FROM data.organisation_operates_in o "
                    "JOIN geography.place p ON p.id = o.place_id "
                    "GROUP BY left(p.code,1) ORDER BY nation"
                )
            )
        ).all()
    return {
        "organisations_registered_here": [
            {"nation": NATIONS.get(r.nation, r.nation), "count": r.n} for r in registered
        ],
        "organisations_operating_here": [
            {"nation": NATIONS.get(r.nation, r.nation), "count": r.n} for r in operating
        ],
        "note": "Materialised record coverage, not upstream coverage or answer quality.",
        "place_counts": [
            {"nation": NATIONS.get(r.nation, r.nation), "type": r.type, "count": r.n}
            for r in places
        ],
        "indicator_coverage": [
            {
                "nation": NATIONS.get(r.nation, r.nation),
                "place_type": r.type,
                "source_id": r.source_id,
                "observations": r.observations,
                "distinct_places": r.places,
                "distinct_indicators": r.indicators,
            }
            for r in values
        ],
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(audit()), indent=2))
