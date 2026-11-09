"""HTTP route tests for GET /v1/place/{place_id}/geometry and peers/geometry."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from soundings.app import app
from soundings.db.engine import get_engine


class _StubRegistry:
    def __init__(self, adapter: object) -> None:
        self._adapter = adapter

    def adapter_for_source(self, source_id: str) -> object:
        return self._adapter


class _StubOsmAdapter:
    async def amenity_locations(self, indicator_key: str, place_id: str) -> dict:
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-1.5, 54.7]},
                    "properties": {"name": indicator_key, "layer": indicator_key},
                }
            ],
        }


pytestmark = pytest.mark.integration

# Simple triangle WKT geometries (SRID 4326 not strictly needed for tests,
# ST_GeomFromEWKT with explicit SRID ensures the column accepts them).
_TRIANGLE_A = "SRID=4326;MULTIPOLYGON(((0 0,0 1,1 0,0 0)))"
_TRIANGLE_B = "SRID=4326;MULTIPOLYGON(((2 2,2 3,3 2,2 2)))"


async def _seed_places_with_geometry() -> None:
    """Three LTLAs — two with geometry, one without — plus indicator values."""
    engine = get_engine()
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM data.loader_run"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        run = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, finished_at, status, rows_written) "
                "VALUES (:id, 'ons.mid_year_estimates', :s, :f, 'ok', 3)"
            ),
            {"id": run, "s": now - timedelta(minutes=5), "f": now},
        )
        # Place with geometry
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name, geom) "
                "VALUES (:id, 'ltla24', :code, :name, ST_GeomFromEWKT(:geom))"
            ),
            {
                "id": "ltla24:E06000001",
                "code": "E06000001",
                "name": "Hartlepool",
                "geom": _TRIANGLE_A,
            },
        )
        # Peer with geometry
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name, geom) "
                "VALUES (:id, 'ltla24', :code, :name, ST_GeomFromEWKT(:geom))"
            ),
            {
                "id": "ltla24:E06000004",
                "code": "E06000004",
                "name": "Stockton",
                "geom": _TRIANGLE_B,
            },
        )
        # Peer without geometry
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES (:id, 'ltla24', :code, :name)"
            ),
            {"id": "ltla24:E06000005", "code": "E06000005", "name": "Darlington"},
        )
        # Indicator values for all three places
        for pid, val in [
            ("ltla24:E06000001", 100),
            ("ltla24:E06000004", 200),
            ("ltla24:E06000005", 300),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO data.indicator_value "
                    "(place_id, indicator_key, period, value, source_id, "
                    "retrieved_at, caveats) VALUES "
                    "(:pid, 'population.total', '2024', :val, "
                    "'ons.mid_year_estimates', :ret, '[]'::jsonb)"
                ),
                {"pid": pid, "val": val, "ret": now},
            )


# ---------------------------------------------------------------------------
# Task 1: GET /v1/place/{place_id}/geometry
# ---------------------------------------------------------------------------


async def test_get_place_geometry_returns_feature() -> None:
    await _seed_places_with_geometry()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/v1/place/ltla24:E06000001/geometry")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["type"] == "Feature"
    assert body["properties"]["id"] == "ltla24:E06000001"
    assert body["properties"]["name"] == "Hartlepool"
    assert body["properties"]["type"] == "ltla24"
    assert body["geometry"] is not None
    assert body["geometry"]["type"] == "MultiPolygon"


async def test_small_urban_boundary_survives_geometry_route() -> None:
    """Regression: degree-based ST_Simplify(0.005) erased urban LSOAs."""
    await _seed_places_with_geometry()
    # Roughly 20-metre square; simplify(0.005) previously collapsed it.
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE geography.place SET geom = "
                "ST_GeomFromText('MULTIPOLYGON(((0 0,0.0002 0,"
                "0.0002 0.0002,0 0.0002,0 0)))', 4326) "
                "WHERE id = 'ltla24:E06000001'"
            )
        )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/v1/place/ltla24:E06000001/geometry")
    assert response.status_code == 200, response.text
    polygon = response.json()["geometry"]["coordinates"][0][0]
    assert len(polygon) >= 4
    assert any(point[0] == pytest.approx(0.0002) for point in polygon)


async def test_get_place_geometry_404_for_missing_place() -> None:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/v1/place/nonexistent/geometry")
    assert response.status_code == 404


async def test_get_place_geometry_null_for_no_geom() -> None:
    await _seed_places_with_geometry()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/v1/place/ltla24:E06000005/geometry")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["type"] == "Feature"
    assert body["geometry"] is None
    assert body["properties"]["id"] == "ltla24:E06000005"
    assert body["properties"]["name"] == "Darlington"


# ---------------------------------------------------------------------------
# Task 2: GET /v1/place/{place_id}/peers/geometry
# ---------------------------------------------------------------------------


async def test_get_peers_geometry_returns_feature_collection() -> None:
    await _seed_places_with_geometry()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get(
                "/v1/place/ltla24:E06000001/peers/geometry",
                params={"indicator": "population.total", "period": "2024"},
            )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["type"] == "FeatureCollection"
    features = body["features"]
    # Two peers (excluding the place itself)
    assert len(features) == 2
    ids = {f["properties"]["id"] for f in features}
    assert ids == {"ltla24:E06000004", "ltla24:E06000005"}
    # Stockton has geom, Darlington doesn't
    stockton = next(f for f in features if f["properties"]["id"] == "ltla24:E06000004")
    assert stockton["geometry"] is not None
    assert stockton["geometry"]["type"] == "MultiPolygon"
    assert stockton["properties"]["value"] == 200
    darlington = next(f for f in features if f["properties"]["id"] == "ltla24:E06000005")
    assert darlington["geometry"] is None
    assert darlington["properties"]["value"] == 300


async def test_get_peers_geometry_percentile() -> None:
    """Values: Stockton 200, Darlington 300. n=2.
    Darlington rank 1 (highest) → percentile 100.
    Stockton rank 2 → percentile 0."""
    await _seed_places_with_geometry()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get(
                "/v1/place/ltla24:E06000001/peers/geometry",
                params={"indicator": "population.total", "period": "2024"},
            )
    assert response.status_code == 200, response.text
    body = response.json()
    by_id = {f["properties"]["id"]: f["properties"] for f in body["features"]}
    assert by_id["ltla24:E06000005"]["percentile"] == pytest.approx(100.0)
    assert by_id["ltla24:E06000004"]["percentile"] == pytest.approx(0.0)


async def test_get_peers_geometry_defaults_to_latest_period_when_omitted() -> None:
    """Regression: with no `period` the endpoint must still attach values
    (latest period per place). The old `iv.period = :period` join returned
    all-null values when period was omitted, collapsing the choropleth
    colour scale to its fallback domain."""
    await _seed_places_with_geometry()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get(
                "/v1/place/ltla24:E06000001/peers/geometry",
                params={"indicator": "population.total"},  # no period
            )
    assert response.status_code == 200, response.text
    by_id = {f["properties"]["id"]: f["properties"] for f in response.json()["features"]}
    assert by_id["ltla24:E06000004"]["value"] == 200
    assert by_id["ltla24:E06000005"]["value"] == 300


async def test_get_peers_geometry_404_for_missing_place() -> None:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get(
                "/v1/place/nonexistent/peers/geometry",
                params={"indicator": "population.total", "period": "2024"},
            )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Task 3: GET /v1/geographies/{place_type}/geometry
# ---------------------------------------------------------------------------


async def test_geographies_geometry_returns_all_areas_of_type() -> None:
    await _seed_places_with_geometry()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get(
                "/v1/geographies/ltla24/geometry",
                params={"indicator": "population.total", "period": "2024"},
            )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["type"] == "FeatureCollection"
    # ALL ltla24 places (not peers-of-one) — nothing excluded.
    ids = {f["properties"]["id"] for f in body["features"]}
    assert ids == {"ltla24:E06000001", "ltla24:E06000004", "ltla24:E06000005"}
    by_id = {f["properties"]["id"]: f["properties"] for f in body["features"]}
    assert by_id["ltla24:E06000004"]["value"] == 200
    # Darlington has no geom → geometry null but still present.
    darlington = next(f for f in body["features"] if f["properties"]["id"] == "ltla24:E06000005")
    assert darlington["geometry"] is None


async def test_geographies_geometry_defaults_to_latest_period_when_omitted() -> None:
    await _seed_places_with_geometry()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get(
                "/v1/geographies/ltla24/geometry",
                params={"indicator": "population.total"},  # no period
            )
    assert response.status_code == 200, response.text
    by_id = {f["properties"]["id"]: f["properties"] for f in response.json()["features"]}
    assert by_id["ltla24:E06000004"]["value"] == 200


async def _seed_mixed_value_coverage() -> None:
    """Seed three ltla24 places: two with values, one without."""
    engine = get_engine()
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM data.loader_run"))
        await conn.execute(text("DELETE FROM geography.place"))
        # Ensure source exists
        await conn.execute(
            text(
                "INSERT INTO catalogue.source (id, label, publisher, licence, mode, rate_limit) "
                "VALUES ('test.source', 'test', 'test', 'test', 'passthrough', '{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
        # Ensure indicator exists
        await conn.execute(
            text(
                "INSERT INTO catalogue.indicator (key, label, unit, source_id, available_at, caveats, related_keys) "
                "VALUES ('test.indicator', 'test', 'test', 'test.source', ARRAY['ltla24'], '[]'::jsonb, ARRAY[]::varchar[]) "
                "ON CONFLICT (key) DO NOTHING"
            )
        )
        run = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, finished_at, status, rows_written) "
                "VALUES (:id, 'test.source', :s, :f, 'ok', 2)"
            ),
            {"id": run, "s": now - timedelta(minutes=5), "f": now},
        )
        # Place with higher indicator value
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name, geom) "
                "VALUES (:id, 'ltla24', :code, :name, ST_GeomFromEWKT(:geom))"
            ),
            {
                "id": "ltla24:E06000010",
                "code": "E06000010",
                "name": "Place With High Value",
                "geom": _TRIANGLE_A,
            },
        )
        # Place with lower indicator value
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name, geom) "
                "VALUES (:id, 'ltla24', :code, :name, ST_GeomFromEWKT(:geom))"
            ),
            {
                "id": "ltla24:E06000011",
                "code": "E06000011",
                "name": "Place With Low Value",
                "geom": _TRIANGLE_B,
            },
        )
        # Third place without indicator value
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES (:id, 'ltla24', :code, :name)"
            ),
            {
                "id": "ltla24:E06000012",
                "code": "E06000012",
                "name": "Place Without Value",
            },
        )
        # First two places get values; third has no value
        await conn.execute(
            text(
                "INSERT INTO data.indicator_value "
                "(place_id, indicator_key, period, value, source_id, "
                "retrieved_at, caveats) VALUES "
                "(:pid, 'test.indicator', '2024', :val, "
                "'test.source', :ret, '[]'::jsonb)"
            ),
            {"pid": "ltla24:E06000010", "val": 100.0, "ret": now},
        )
        await conn.execute(
            text(
                "INSERT INTO data.indicator_value "
                "(place_id, indicator_key, period, value, source_id, "
                "retrieved_at, caveats) VALUES "
                "(:pid, 'test.indicator', '2024', :val, "
                "'test.source', :ret, '[]'::jsonb)"
            ),
            {"pid": "ltla24:E06000011", "val": 50.0, "ret": now},
        )


async def test_geographies_geometry_null_percentile_for_missing_values() -> None:
    """Regression: places with no indicator value must have percentile=null
    instead of a negative percentile (which happened when rank > n)."""
    await _seed_mixed_value_coverage()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get(
                "/v1/geographies/ltla24/geometry",
                params={"indicator": "test.indicator", "period": "2024"},
            )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["type"] == "FeatureCollection"
    by_id = {f["properties"]["id"]: f["properties"] for f in body["features"]}
    # Place with higher value: rank 1, percentile 100
    assert by_id["ltla24:E06000010"]["value"] == 100.0
    assert by_id["ltla24:E06000010"]["percentile"] == pytest.approx(100.0)
    # Place with lower value: rank 2, percentile 0
    assert by_id["ltla24:E06000011"]["value"] == 50.0
    assert by_id["ltla24:E06000011"]["percentile"] == pytest.approx(0.0)
    # Place without value: rank > n, percentile null, value null
    assert by_id["ltla24:E06000012"]["value"] is None
    assert by_id["ltla24:E06000012"]["percentile"] is None


async def test_geographies_geometry_guards_large_lsoa_layer() -> None:
    """Guard: place_type=lsoa21 returns 422 without large=true, 200 with it."""
    await _seed_parent_with_lsoa_children()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            blocked = await ac.get(
                "/v1/geographies/lsoa21/geometry",
                params={"indicator": "deprivation.imd.score"},
            )
            allowed = await ac.get(
                "/v1/geographies/lsoa21/geometry",
                params={"indicator": "deprivation.imd.score", "large": "true"},
            )
    assert blocked.status_code == 422
    assert allowed.status_code == 200


# ---------------------------------------------------------------------------
# Task 4: GET /v1/place/{place_id}/children/geometry
# ---------------------------------------------------------------------------


async def _seed_parent_with_lsoa_children() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name, geom) VALUES "
                "('ltla24:P1','ltla24','P1','Parent', ST_GeomFromEWKT(:g))"
            ),
            {"g": "SRID=4326;MULTIPOLYGON(((0 0,0 2,2 0,0 0)))"},
        )
        for cid, geom in [
            ("lsoa21:L1", "SRID=4326;MULTIPOLYGON(((0 0,0 1,1 0,0 0)))"),
            ("lsoa21:L2", "SRID=4326;MULTIPOLYGON(((1 1,1 2,2 1,1 1)))"),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO geography.place (id, type, code, name, geom) VALUES "
                    "(:id,'lsoa21',:c,:n, ST_GeomFromEWKT(:g))"
                ),
                {"id": cid, "c": cid.split(":")[1], "n": cid, "g": geom},
            )
            await conn.execute(
                text(
                    "INSERT INTO geography.place_hierarchy (child_id, parent_id) VALUES (:c,'ltla24:P1')"
                ),
                {"c": cid},
            )
        # Only L1 has an IMD value.
        await conn.execute(
            text(
                "INSERT INTO data.indicator_value (place_id, indicator_key, value, period, source_id, retrieved_at, caveats) "
                "VALUES ('lsoa21:L1','deprivation.imd.score', 42.0, '2025', 'mhclg.imd2025', NOW(), '[]'::jsonb)"
            )
        )


async def test_children_geometry_returns_valued_children_only() -> None:
    await _seed_parent_with_lsoa_children()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get(
                "/v1/place/ltla24:P1/children/geometry",
                params={"indicator": "deprivation.imd.score"},
            )
    assert response.status_code == 200
    fc = response.json()
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 1  # L2 has no value → excluded
    props = fc["features"][0]["properties"]
    assert props["id"] == "lsoa21:L1" and props["value"] == 42.0


async def test_children_geometry_empty_for_indicator_without_subarea_data() -> None:
    await _seed_parent_with_lsoa_children()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get(
                "/v1/place/ltla24:P1/children/geometry",
                params={"indicator": "population.total"},
            )
    assert response.status_code == 200
    assert response.json()["features"] == []


# ---------------------------------------------------------------------------
# Task 5: GET /v1/place/{place_id}/amenities/geometry
# ---------------------------------------------------------------------------


async def test_amenities_geometry_merges_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(app.state, "adapter_registry", _StubRegistry(_StubOsmAdapter()))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/v1/place/ltla24:E06000047/amenities/geometry",
                params={
                    "indicators": "infrastructure.food_banks_count,infrastructure.schools_count"
                },
            )
    assert resp.status_code == 200
    fc = resp.json()
    layers = {f["properties"]["layer"] for f in fc["features"]}
    assert layers == {"infrastructure.food_banks_count", "infrastructure.schools_count"}
    assert len(fc["features"]) == 2


# ---------------------------------------------------------------------------
# Task 6: source-aware routing
# ---------------------------------------------------------------------------


class _SourceAwareRegistry:
    def __init__(self, by_source: dict[str, object]) -> None:
        self._by_source = by_source

    def adapter_for_source(self, source_id: str):
        return self._by_source[source_id]


class _LayerStubAdapter:
    """Tags each returned feature with WHICH adapter (source) produced it, so
    the test can prove routing — not just that a layer name survived."""

    def __init__(self, source_tag: str) -> None:
        self._source_tag = source_tag

    async def amenity_locations(self, indicator_key: str, place_id: str) -> dict:
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-1.5, 54.7]},
                    "properties": {
                        "name": indicator_key,
                        "layer": indicator_key,
                        "source": self._source_tag,
                    },
                }
            ],
        }


async def test_amenities_geometry_routes_by_indicator_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = get_engine()
    # Seed two indicators with different sources in the catalogue.
    async with engine.begin() as conn:
        for sid in ("givefood", "osm_overpass"):
            await conn.execute(
                text(
                    "INSERT INTO catalogue.source (id, label, publisher, licence, mode, rate_limit) "
                    "VALUES (:id, :label, 'p', 'x', 'passthrough', '{}'::jsonb) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": sid, "label": sid},
            )
        await conn.execute(
            text(
                "INSERT INTO catalogue.indicator (key, label, unit, source_id, available_at, caveats, related_keys) "
                "VALUES ('infrastructure.food_banks_count','fb','count','givefood', ARRAY['ltla24'], '[]'::jsonb, ARRAY[]::varchar[]) "
                "ON CONFLICT (key) DO UPDATE SET source_id='givefood'"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO catalogue.indicator (key, label, unit, source_id, available_at, caveats, related_keys) "
                "VALUES ('infrastructure.schools_count','sc','count','osm_overpass', ARRAY['ltla24'], '[]'::jsonb, ARRAY[]::varchar[]) "
                "ON CONFLICT (key) DO UPDATE SET source_id='osm_overpass'"
            )
        )

    gf_adapter = _LayerStubAdapter("givefood")
    osm_adapter = _LayerStubAdapter("osm_overpass")
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(
            app.state,
            "adapter_registry",
            _SourceAwareRegistry({"givefood": gf_adapter, "osm_overpass": osm_adapter}),
        )
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/v1/place/ltla24:E06000047/amenities/geometry",
                params={
                    "indicators": "infrastructure.food_banks_count,infrastructure.schools_count"
                },
            )
    assert resp.status_code == 200
    # Each indicator must be served by the adapter that OWNS it (its source).
    source_by_layer = {
        f["properties"]["layer"]: f["properties"]["source"] for f in resp.json()["features"]
    }
    assert source_by_layer == {
        "infrastructure.food_banks_count": "givefood",
        "infrastructure.schools_count": "osm_overpass",
    }
