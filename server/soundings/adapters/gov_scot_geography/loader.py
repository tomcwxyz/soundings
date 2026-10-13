"""Load the 2022 Scottish Data Zone hierarchy into Soundings Postgres.

The loader builds place names/IDs and transitive hierarchy relations.
It deliberately DOES NOT invent boundaries or assign postcodes. Boundary
geometry and postcode-to-DZ resolution are separate pieces of work.

Official lookup: https://data.gov.scot/dataset/2022_data_zone_lookup
"""

from collections.abc import Sequence

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.adapters.gov_scot_geography.parser import (
    ScottishLookupRow,
    parse_scot_dz_lookup,
)

SOURCE_ID = "gov_scot.geography2022"
LOOKUP_URL = (
    "https://data.gov.scot/dataset/2022_data_zone_lookup/resource/"
    "81b010f5-0207-4ee5-bd2d-e7821aa6a3dc/download"
)
SCOTLAND_ID = "country:S92000003"
DZ_COUNT = 7392
IZ_COUNT = 1334
BATCH_SIZE = 1000

PLACE_UPSERT = text(
    "INSERT INTO geography.place (id,type,code,name) "
    "VALUES (:id,:type,:code,:name) "
    "ON CONFLICT (id) DO NOTHING"
)
EDGE_UPSERT = text(
    "INSERT INTO geography.place_hierarchy (child_id,parent_id) "
    "VALUES (:child_id,:parent_id) "
    "ON CONFLICT DO NOTHING"
)


def place_rows(records: Sequence[ScottishLookupRow]) -> list[dict[str, str]]:
    places: dict[str, dict[str, str]] = {}
    for row in records:
        for typ, code, label in (
            ("dz22", row.dz, row.dz_name),
            ("iz22", row.iz, row.iz_name),
        ):
            place_id = f"{typ}:{code}"
            places.setdefault(
                place_id,
                {"id": place_id, "type": typ, "code": code, "name": label},
            )
    return list(places.values())


def hierarchy_edges(records: Sequence[ScottishLookupRow]) -> list[dict[str, str]]:
    edges: set[tuple[str, str]] = set()
    for row in records:
        dz, iz, la = (
            f"dz22:{row.dz}",
            f"iz22:{row.iz}",
            f"ltla24:{row.la}",
        )
        edges.update(
            (
                (dz, iz),
                (dz, la),
                (dz, SCOTLAND_ID),
                (iz, la),
                (iz, SCOTLAND_ID),
                (la, SCOTLAND_ID),
            )
        )
    return [{"child_id": child_id, "parent_id": parent_id} for child_id, parent_id in sorted(edges)]


class ScotGeography2022Loader(LoaderAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        client: httpx.AsyncClient | None = None,
        url: str = LOOKUP_URL,
        minimum_dz: int = DZ_COUNT,
        minimum_iz: int = IZ_COUNT,
    ) -> None:
        super().__init__(engine)
        self._client = client
        self._url = url
        self._minimum_dz = minimum_dz
        self._minimum_iz = minimum_iz

    async def load(self, run_id: str | None = None) -> LoaderResult:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=120.0, follow_redirects=True)
        try:
            response = await client.get(self._url)
            response.raise_for_status()
            return await self.load_from_bytes(response.content)
        finally:
            if owns_client:
                await client.aclose()

    async def load_from_bytes(self, content: bytes) -> LoaderResult:
        parsed = parse_scot_dz_lookup(content)
        dz_count = len({r.dz for r in parsed})
        iz_count = len({r.iz for r in parsed})
        if dz_count < self._minimum_dz or iz_count < self._minimum_iz:
            raise ValueError(
                f"Incomplete Scottish lookup: {dz_count} Data Zones, {iz_count} Intermediate Zones"
            )

        needed_parents = {f"ltla24:{r.la}" for r in parsed} | {SCOTLAND_ID}
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT id FROM geography.place WHERE id = ANY(:ids)"),
                    {"ids": sorted(needed_parents)},
                )
            ).all()
        missing = needed_parents - {r.id for r in rows}
        if missing:
            raise RuntimeError(
                f"Scottish geography parent places not seeded: {', '.join(sorted(missing))}"
            )

        places = place_rows(parsed)
        edges = hierarchy_edges(parsed)
        async with self._engine.begin() as conn:
            for start in range(0, len(places), BATCH_SIZE):
                await conn.execute(PLACE_UPSERT, places[start : start + BATCH_SIZE])
            for start in range(0, len(edges), BATCH_SIZE):
                await conn.execute(EDGE_UPSERT, edges[start : start + BATCH_SIZE])
        return LoaderResult(
            rows_written=len(places),
            notes=(
                f"{dz_count} Data Zones; {iz_count} Intermediate Zones; "
                f"{len(edges)} current hierarchy edges; no boundary geometry"
            ),
        )
