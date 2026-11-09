"""Loads simplified BUC/BSC/BFC boundary geometries from ONS OGP.

This loader assumes the corresponding `geography.place` rows already exist
(via `OnsGeographyPlacesLoader`) and only updates `geom`. Polygons are wrapped
into MultiPolygon at SRID 4326 to match the column type. Features whose code
doesn't match any seeded place are skipped (counted in `notes` not
`rows_written`).
"""

import json
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.adapters.ons_geography.arcgis import feature_rows
from soundings.adapters.ons_geography.endpoints import BOUNDARY_LAYERS, OgpLayer

PAGE_SIZE = 1000


class OnsGeographyGeometriesLoader(LoaderAdapter):
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
        client = self._client or httpx.AsyncClient(timeout=120.0)
        try:
            total_written = 0
            total_skipped = 0
            for layer in self._layers.values():
                written, skipped = await self._load_layer(client, layer)
                total_written += written
                total_skipped += skipped
            note = f"skipped {total_skipped} feature(s) with no matching place row"
            return LoaderResult(rows_written=total_written, notes=note)
        finally:
            if owns_client:
                await client.aclose()

    async def _load_layer(self, client: httpx.AsyncClient, layer: OgpLayer) -> tuple[int, int]:
        offset = 0
        written = 0
        skipped = 0
        while True:
            features = await self._fetch_geojson_page(client, layer, offset=offset)
            if not features:
                break
            w, s = await self._update_geometries(layer, features)
            written += w
            skipped += s
            if len(features) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return written, skipped

    async def _fetch_geojson_page(
        self, client: httpx.AsyncClient, layer: OgpLayer, *, offset: int
    ) -> list[dict[str, Any]]:
        params = {
            "where": "1=1",
            "outFields": f"{layer.code_field},{layer.name_field}",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": str(offset),
            "resultRecordCount": str(PAGE_SIZE),
        }
        response = await client.get(f"{layer.feature_url}/query", params=params)
        response.raise_for_status()
        features = feature_rows(response.json())
        return features

    async def _update_geometries(
        self, layer: OgpLayer, features: list[dict[str, Any]]
    ) -> tuple[int, int]:
        written = 0
        skipped = 0
        async with self._engine.begin() as conn:
            for feature in features:
                props = feature.get("properties", {})
                geom = feature.get("geometry")
                code = props.get(layer.code_field)
                if not code or not geom:
                    skipped += 1
                    continue
                place_id = f"{layer.place_type}:{code}"
                geojson = self._wrap_as_multipolygon(geom)
                if geojson is None:
                    skipped += 1
                    continue
                result = await conn.execute(
                    text(
                        "UPDATE geography.place "
                        "SET geom = ST_Multi(ST_GeomFromGeoJSON(:gj)) "
                        "WHERE id = :id"
                    ),
                    {"id": place_id, "gj": json.dumps(geojson)},
                )
                if result.rowcount == 0:
                    skipped += 1
                else:
                    written += result.rowcount
        return written, skipped

    @staticmethod
    def _wrap_as_multipolygon(geom: dict[str, Any]) -> dict[str, Any] | None:
        gtype = geom.get("type")
        if gtype == "MultiPolygon":
            return geom
        if gtype == "Polygon":
            return {"type": "MultiPolygon", "coordinates": [geom["coordinates"]]}
        return None
