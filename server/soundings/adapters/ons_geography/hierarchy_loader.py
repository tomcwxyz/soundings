"""Loads ONS geography lookup tables and expands them into transitive
(child_id, parent_id) edges in `geography.place_hierarchy`.

A single OGP lookup row that names LSOA, MSOA, LTLA produces three edges:
LSOA→MSOA, LSOA→LTLA, MSOA→LTLA. Multiple chains can be loaded in one pass
(e.g. the postcode→LSOA→MSOA→LTLA lookup, then a separate LTLA→UTLA→region
→country chain).
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from itertools import combinations
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.db.models.geography import PlaceHierarchy

PAGE_SIZE = 2000


@dataclass(frozen=True)
class LookupChain:
    """One ArcGIS lookup table mapped to a chain of canonical place levels.

    `levels` is ordered child → parent: the first entry is the deepest level
    (e.g. `("lsoa21", "LSOA21CD")`), the last is the shallowest contained.
    """

    url: str
    levels: list[tuple[str, str]]


class OnsGeographyHierarchyLoader(LoaderAdapter):
    source_id = "ons.geography"

    def __init__(
        self,
        engine: AsyncEngine,
        chains: list[LookupChain],
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._engine = engine
        self._chains = chains
        self._client = http_client

    async def load(self, run_id: str | None = None) -> LoaderResult:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=60.0)
        try:
            edges: set[tuple[str, str]] = set()
            for chain in self._chains:
                async for row in self._iter_rows(client, chain):
                    edges.update(self._row_to_edges(row, chain))
            await self._upsert_edges(edges)
            return LoaderResult(rows_written=len(edges))
        finally:
            if owns_client:
                await client.aclose()

    async def _iter_rows(
        self, client: httpx.AsyncClient, chain: LookupChain
    ) -> AsyncIterator[dict[str, Any]]:
        offset = 0
        out_fields = ",".join(field for _, field in chain.levels)
        while True:
            params = {
                "where": "1=1",
                "outFields": out_fields,
                "returnGeometry": "false",
                "f": "json",
                "resultOffset": str(offset),
                "resultRecordCount": str(PAGE_SIZE),
            }
            response = await client.get(f"{chain.url}/query", params=params)
            response.raise_for_status()
            payload = response.json()
            features = payload.get("features", [])
            if not features:
                return
            for f in features:
                yield f.get("attributes", {})
            # ArcGIS caps server-side at maxRecordCount (often 1000), so
            # `len(features) < PAGE_SIZE` falsely signals end-of-stream.
            # Trust `exceededTransferLimit` instead; if absent, fall back to
            # the page-fill heuristic.
            exceeded = payload.get("exceededTransferLimit")
            if exceeded is False or (exceeded is None and len(features) < PAGE_SIZE):
                return
            offset += len(features)

    @staticmethod
    def _row_to_edges(row: dict[str, Any], chain: LookupChain) -> set[tuple[str, str]]:
        present: list[tuple[str, str]] = []
        for place_type, field in chain.levels:
            code = row.get(field)
            if code:
                present.append((place_type, code))
        edges: set[tuple[str, str]] = set()
        for (child_type, child_code), (parent_type, parent_code) in combinations(present, 2):
            # combinations yields pairs in chain-order, so child is always
            # the deeper level.
            edges.add(
                (
                    f"{child_type}:{child_code}",
                    f"{parent_type}:{parent_code}",
                )
            )
        return edges

    async def _upsert_edges(self, edges: set[tuple[str, str]]) -> None:
        if not edges:
            return
        rows = [{"child_id": c, "parent_id": p} for c, p in edges]
        # Postgres caps a query at ~65k bind parameters; with 2 params per row
        # that's ~32k rows per INSERT. Batch to stay well under.
        batch_size = 10_000
        async with self._engine.begin() as conn:
            for i in range(0, len(rows), batch_size):
                batch = rows[i : i + batch_size]
                # The temporal schema allows dated CHD evidence to coexist with
                # this undated current snapshot. A targetless conflict handler
                # lets the partial unique current-snapshot index enforce one
                # undated row per pair without colliding with dated intervals.
                stmt = insert(PlaceHierarchy).values(batch).on_conflict_do_nothing()
                await conn.execute(stmt)
