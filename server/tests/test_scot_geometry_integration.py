"""Scottish Data Zone polygons and postcode resolution, isolated PostGIS only."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy import text

from soundings.adapters.gov_scot_geography.geometries_loader import (
    ScotDataZoneGeometriesLoader,
)
from soundings.db.engine import get_engine
from soundings.geography.service import GeographyService

pytestmark = pytest.mark.integration


def _feature(code: str, west: float) -> dict[str, object]:
    return {
        "type": "Feature",
        "properties": {"DZCode": code, "DZName": code + " named zone"},
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [
                    [west, 57.11],
                    [west + 0.01, 57.11],
                    [west + 0.01, 57.12],
                    [west, 57.12],
                    [west, 57.11],
                ]
            ],
        },
    }


async def test_scottish_geometry_loader_handles_paged_geojson() -> None:
    engine = get_engine()
    ids = ("S01099998", "S01099999")
    async with engine.begin() as conn:
        for code in ids:
            await conn.execute(
                text(
                    "INSERT INTO geography.place (id,type,code,name) "
                    "VALUES (:id,'dz22',:code,:code) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": f"dz22:{code}", "code": code},
            )
    features = [_feature(ids[0], -2.25), _feature(ids[1], -2.20)]

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"type": "FeatureCollection", "features": features})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        result = await ScotDataZoneGeometriesLoader(
            engine, client=client, expected_zones=2
        ).load()
    assert result.rows_written == 2
    async with engine.connect() as conn:
        shapes = (
            await conn.execute(
                text(
                    "SELECT ST_SRID(geom) AS srid, ST_IsValid(geom) AS valid "
                    "FROM geography.place WHERE id = 'dz22:S01099998'"
                )
            )
        ).first()
    assert shapes is not None
    assert shapes.srid == 4326
    assert shapes.valid


async def test_scottish_postcode_finds_data_zone_and_intermediate_zone() -> None:
    engine = get_engine()
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        for typ, code, name in (
            ("country", "S92000003", "Scotland"),
            ("dz22", "S01099997", "Test Highland Data Zone"),
            ("iz22", "S02099997", "Test Highland Intermediate Zone"),
        ):
            await conn.execute(
                text(
                    "INSERT INTO geography.place (id,type,code,name) "
                    "VALUES (:id,:type,:code,:name) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": f"{typ}:{code}", "type": typ, "code": code, "name": name},
            )
        await conn.execute(
            text(
                "UPDATE geography.place SET geom = "
                "ST_GeomFromText('MULTIPOLYGON(((-4.2 57.7,-4.1 57.7,"
                "-4.1 57.8,-4.2 57.8,-4.2 57.7)))',4326) "
                "WHERE id = 'dz22:S01099997'"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO geography.place_hierarchy (child_id,parent_id) "
                "VALUES ('dz22:S01099997','iz22:S02099997') ON CONFLICT DO NOTHING"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO geography.postcode "
                "(postcode,country,latitude,longitude,retrieved_at) "
                "VALUES ('IV991ZZ','country:S92000003',57.75,-4.15,:now) "
                "ON CONFLICT (postcode) DO UPDATE SET country = EXCLUDED.country, "
                "latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude, "
                "retrieved_at = EXCLUDED.retrieved_at"
            ),
            {"now": now},
        )

    postcode_adapter = MagicMock()
    service = GeographyService(engine, postcode_adapter)
    places = await service.find_place_by_postcode("IV99 1ZZ")
    assert places is not None
    assert places["dz22"].id == "dz22:S01099997"
    assert places["iz22"].id == "iz22:S02099997"
    postcode_adapter.upsert_postcode.assert_not_called()
