"""Loads canonical place codes + names from ONS OGP boundary feature services.

Geometries are NOT loaded here — that's the geometries loader (Task 26).
This adapter just walks each layer, extracts (code, name) and upserts as
`Place` rows with `id = "<type>:<code>"`.
"""

from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.adapters.ons_geography.arcgis import feature_rows
from soundings.adapters.ons_geography.endpoints import BOUNDARY_LAYERS, OgpLayer
from soundings.db.models.geography import Place

PAGE_SIZE = 2000  # OGP default max records per request


class OnsGeographyPlacesLoader(LoaderAdapter):
    source_id = "ons.geography"

    def __init__(
        self,
        engine: AsyncEngine,
        http_client: httpx.AsyncClient | None = None,
        layers: dict[str, OgpLayer] | None = None,
    ) -> None:
        self._engine = engine
        self._client = http_client
        self._layers = layers or BOUNDARY_LAYERS

    async def load(self, run_id: str | None = None) -> LoaderResult:
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=60.0)
        try:
            total = 0
            for layer in self._layers.values():
                total += await self._load_layer(client, layer)
            return LoaderResult(rows_written=total)
        finally:
            if owns_client:
                await client.aclose()

    async def _load_layer(self, client: httpx.AsyncClient, layer: OgpLayer) -> int:
        offset = 0
        rows_written = 0
        while True:
            features = await self._fetch_page(client, layer, offset=offset)
            if not features:
                break
            await self._upsert_features(layer, features)
            rows_written += len(features)
            if len(features) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return rows_written

    async def _fetch_page(
        self, client: httpx.AsyncClient, layer: OgpLayer, *, offset: int
    ) -> list[dict[str, Any]]:
        params = {
            "where": "1=1",
            "outFields": f"{layer.code_field},{layer.name_field}",
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": str(offset),
            "resultRecordCount": str(PAGE_SIZE),
        }
        url = f"{layer.feature_url}/query"
        response = await client.get(url, params=params)
        response.raise_for_status()
        body = response.json()
        return [f.get("attributes", {}) for f in feature_rows(body)]

    async def _upsert_features(self, layer: OgpLayer, features: list[dict[str, Any]]) -> None:
        rows = []
        for f in features:
            code = f.get(layer.code_field)
            name = f.get(layer.name_field)
            if not code or not name:
                continue
            rows.append(
                {
                    "id": f"{layer.place_type}:{code}",
                    "type": layer.place_type,
                    "code": code,
                    "name": name,
                }
            )
        if not rows:
            return
        async with self._engine.begin() as conn:
            stmt = insert(Place).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Place.id],
                set_={"name": stmt.excluded.name},
            )
            await conn.execute(stmt)
