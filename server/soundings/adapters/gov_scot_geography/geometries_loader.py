"""Official Scottish Government Data Zone 2022 polygon geometries.

Uses the published ArcGIS feature layer with DZCode/DZName. Pagination,
coordinate reference, integrity checks, and small batches are explicit:
a half-loaded page isn't silently counted as a complete national layer.

Run only after the Scottish lookup has seeded `dz22` places:
  python -m soundings.loader.run --once gov_scot.dz22_boundaries
"""

import json
import logging
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.adapters.gov_scot_geography.loader import DZ_COUNT

SOURCE_ID = "gov_scot.dz22_boundaries"
ARCGIS_LAYER = (
    "https://services-eu1.arcgis.com/4QkhM5AS8YOlkb6T/"
    "arcgis/rest/services/2022_Data_Zones_and_Intermediate_Zones/FeatureServer/0"
)
PAGE_SIZE = 100
LOG = logging.getLogger(__name__)
UPDATE_GEOMETRY = text(
    "UPDATE geography.place SET "
    "geom = ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326)), "
    "name = CASE WHEN name = code THEN :name ELSE name END "
    "WHERE id = :id AND type = 'dz22'"
)


def feature_to_update(feature: dict[str, Any]) -> dict[str, str] | None:
    """Accept only official Scottish Data Zone IDs and WGS84 polygon geometry."""
    properties = feature.get("properties") or {}
    code = str(properties.get("DZCode") or "").strip()
    if len(code) != 9 or not code.startswith("S010") or not code[4:].isdigit():
        return None
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        return None
    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        return None
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        return None
    # GeoJSON must be WGS84. Disallow accidental insertion of British National
    # Grid coordinates as if they were degrees, without assuming a precise shape.
    try:
        point = coordinates[0][0] if geometry["type"] == "Polygon" else coordinates[0][0][0]
        lon, lat = float(point[0]), float(point[1])
    except (IndexError, ValueError, TypeError):
        return None
    if not (-9.5 <= lon <= 2.5 and 54.0 <= lat <= 61.5):
        return None
    return {
        "id": f"dz22:{code}",
        "name": str(properties.get("DZName") or code).strip(),
        "geometry": json.dumps(geometry),
    }


class ScotDataZoneGeometriesLoader(LoaderAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        client: httpx.AsyncClient | None = None,
        url: str = ARCGIS_LAYER,
        expected_zones: int = DZ_COUNT,
    ) -> None:
        super().__init__(engine)
        self._client = client
        self._url = url
        self._expected_zones = expected_zones

    async def load(self, run_id: str | None = None) -> LoaderResult:
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text("SELECT count(*) FROM geography.place WHERE type = 'dz22'")
            )
            present_count = result.scalar_one()
        if present_count < self._expected_zones:
            raise RuntimeError(
                f"Seed Scottish 2022 Data Zones first: only {present_count} places in spine"
            )

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=180.0, follow_redirects=True)
        try:
            return await self._load_pages(client)
        finally:
            if owns_client:
                await client.aclose()

    async def _load_pages(self, client: httpx.AsyncClient) -> LoaderResult:
        seen: set[str] = set()
        written = 0
        offset = 0
        while True:
            response = await client.get(
                f"{self._url}/query",
                params={
                    "where": "1=1",
                    "outFields": "DZCode,DZName",
                    "orderByFields": "OBJECTID ASC",
                    "returnGeometry": "true",
                    "outSR": "4326",
                    "geometryPrecision": "5",
                    "resultOffset": str(offset),
                    "resultRecordCount": str(PAGE_SIZE),
                    "f": "geojson",
                },
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
            if "error" in payload:
                raise RuntimeError(f"Scottish ArcGIS source error: {payload['error']}")
            features = payload.get("features")
            if not isinstance(features, list):
                raise ValueError("Scottish ArcGIS response missing features")
            if not features:
                break
            updates: list[dict[str, str]] = []
            for feature in features:
                update = feature_to_update(feature)
                if update is None:
                    raise ValueError("Scottish ArcGIS returned an invalid DZ22 geometry or ID")
                if update["id"] in seen:
                    raise ValueError(f"Duplicate Data Zone geometry: {update['id']}")
                seen.add(update["id"])
                updates.append(update)
            async with self._engine.begin() as conn:
                for update in updates:
                    result = await conn.execute(UPDATE_GEOMETRY, update)
                    if result.rowcount != 1:
                        raise RuntimeError(f"Unseeded Scottish polygon place: {update['id']}")
                    written += 1
            offset += len(features)
            LOG.info("Loaded %s Scottish Data Zone boundaries", written)
            if len(features) < PAGE_SIZE:
                break
        if written < self._expected_zones:
            raise ValueError(
                f"Incomplete Scottish DZ22 boundaries: {written}; expected {self._expected_zones}"
            )
        return LoaderResult(
            rows_written=written,
            notes=f"{written} Scottish 2022 Data Zone WGS84 polygons updated",
        )
