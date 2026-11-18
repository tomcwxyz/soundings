"""Integration tests for GiveFoodAdapter (fake client + real PostGIS test DB)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from soundings.adapters.givefood.adapter import (
    FOOD_BANKS_INDICATOR,
    GiveFoodAdapter,
)
from soundings.adapters.givefood.client import GiveFoodUnavailableError
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration


async def _seed_place() -> None:
    """One LTLA polygon: the unit square (0,0)-(1,1)."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM cache.source_cache"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name, geom) VALUES "
                "('ltla24:FB1','ltla24','FB1','Foodland', "
                "ST_GeomFromEWKT('SRID=4326;MULTIPOLYGON(((0 0,0 1,1 1,1 0,0 0)))'))"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO catalogue.source (id, label, publisher, publisher_url, "
                "dataset_url, licence, mode, rate_limit) VALUES "
                "('givefood','Give Food','Give Food','https://www.givefood.org.uk/', "
                "'https://www.givefood.org.uk/api/2/docs/','attribution','passthrough','{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            )
        )


class _FakeClient:
    """Stub returning canned trimmed dump rows."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls = 0

    async def fetch_foodbanks(self) -> list[dict]:
        self.calls += 1
        return self._rows


class _FailingClient:
    async def fetch_foodbanks(self) -> list[dict]:
        raise GiveFoodUnavailableError("boom")


# Two inside the unit square, one outside (lng=5).
_ROWS = [
    {
        "lat": 0.5,
        "lng": 0.5,
        "postcode": "A",
        "lsoa": "E01000001",
        "name": "Inside One",
        "org": "Org",
    },
    {
        "lat": 0.2,
        "lng": 0.8,
        "postcode": "B",
        "lsoa": "E01000002",
        "name": "Inside Two",
        "org": "Org",
    },
    {"lat": 0.5, "lng": 5.0, "postcode": "C", "lsoa": "E01000003", "name": "Outside", "org": "Org"},
]


async def test_fetch_indicator_counts_in_boundary_only() -> None:
    await _seed_place()
    fake = _FakeClient(_ROWS)
    adapter = GiveFoodAdapter(get_engine(), client=fake)
    iv = await adapter.fetch_indicator(FOOD_BANKS_INDICATOR, "ltla24:FB1", None)
    assert iv is not None
    assert iv.value == 2.0  # the outside one is excluded
    assert iv.unit == "count"
    assert iv.source.source_id == "givefood"


async def test_fetch_indicator_unknown_indicator_returns_none() -> None:
    await _seed_place()
    adapter = GiveFoodAdapter(get_engine(), client=_FakeClient(_ROWS))
    assert await adapter.fetch_indicator("not.food_banks", "ltla24:FB1", None) is None


async def test_fetch_indicator_second_call_uses_cache() -> None:
    await _seed_place()
    fake = _FakeClient(_ROWS)
    adapter = GiveFoodAdapter(get_engine(), client=fake)
    await adapter.fetch_indicator(FOOD_BANKS_INDICATOR, "ltla24:FB1", None)
    await adapter.fetch_indicator(FOOD_BANKS_INDICATOR, "ltla24:FB1", None)
    assert fake.calls == 1  # dump fetched once; count served from cache


async def test_fetch_indicator_transport_failure_propagates_uncached() -> None:
    await _seed_place()
    adapter = GiveFoodAdapter(get_engine(), client=_FailingClient())
    with pytest.raises(GiveFoodUnavailableError):
        await adapter.fetch_indicator(FOOD_BANKS_INDICATOR, "ltla24:FB1", None)
    # second call still raises -> nothing cached
    with pytest.raises(GiveFoodUnavailableError):
        await adapter.fetch_indicator(FOOD_BANKS_INDICATOR, "ltla24:FB1", None)


async def test_amenity_locations_returns_points_in_boundary() -> None:
    await _seed_place()
    adapter = GiveFoodAdapter(get_engine(), client=_FakeClient(_ROWS))
    fc = await adapter.amenity_locations(FOOD_BANKS_INDICATOR, "ltla24:FB1")
    assert fc is not None and fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 2  # outside one excluded
    f0 = fc["features"][0]
    assert f0["geometry"]["type"] == "Point"
    # [lng, lat] order; properties carry name + layer
    assert f0["geometry"]["coordinates"] == [0.5, 0.5]
    assert f0["properties"]["layer"] == FOOD_BANKS_INDICATOR
    assert f0["properties"]["name"] in {"Inside One", "Inside Two"}


async def test_amenity_locations_unknown_indicator_returns_none() -> None:
    await _seed_place()
    adapter = GiveFoodAdapter(get_engine(), client=_FakeClient(_ROWS))
    assert await adapter.amenity_locations("not.food_banks", "ltla24:FB1") is None


async def test_pre_warm_caches_counts_for_places() -> None:
    await _seed_place()
    fake = _FakeClient(_ROWS)
    adapter = GiveFoodAdapter(get_engine(), client=fake)
    await adapter.pre_warm_for_places(["ltla24:FB1"])
    # After warming, a fetch makes no further upstream call.
    fake.calls = 0
    iv = await adapter.fetch_indicator(FOOD_BANKS_INDICATOR, "ltla24:FB1", None)
    assert iv is not None and iv.value == 2.0
    assert fake.calls == 0


async def test_stale_snapshot_yields_count_with_age_and_warning() -> None:
    await _seed_place()
    adapter = GiveFoodAdapter(get_engine(), client=_FailingClient())
    old = datetime.now(tz=UTC) - timedelta(days=2)
    await adapter._cache.put(
        "givefood",
        "locations:v2",
        {"rows": _ROWS, "fetched_at": old.isoformat()},
        ttl=timedelta(days=8),
    )

    iv = await adapter.fetch_indicator(FOOD_BANKS_INDICATOR, "ltla24:FB1", None)
    assert iv is not None
    assert iv.value == 2.0
    assert iv.source.cache_status == "stale"
    assert iv.source.retrieved_at == old
    assert any("last successful" in caveat for caveat in iv.caveats)

    fc = await adapter.amenity_locations(FOOD_BANKS_INDICATOR, "ltla24:FB1")
    assert fc is not None
    assert fc["cache_status"] == "stale"
    assert fc["data_as_of"] == old.isoformat()
    assert fc["warnings"]
    assert len(fc["features"]) == 2
    assert fc["features"][0]["properties"]["data_stale"] is True


async def test_too_old_snapshot_does_not_masquerade_as_current() -> None:
    await _seed_place()
    adapter = GiveFoodAdapter(get_engine(), client=_FailingClient())
    old = datetime.now(tz=UTC) - timedelta(days=7, hours=1)
    await adapter._cache.put(
        "givefood",
        "locations:v2",
        {"rows": _ROWS, "fetched_at": old.isoformat()},
        ttl=timedelta(days=8),
    )
    with pytest.raises(GiveFoodUnavailableError):
        await adapter.fetch_indicator(FOOD_BANKS_INDICATOR, "ltla24:FB1", None)


async def test_expired_refresh_interval_fetches_new_locations() -> None:
    await _seed_place()
    fake = _FakeClient(_ROWS)
    adapter = GiveFoodAdapter(get_engine(), client=fake)
    old = datetime.now(tz=UTC) - timedelta(days=2)
    await adapter._cache.put(
        "givefood",
        "locations:v2",
        {"rows": _ROWS[:1], "fetched_at": old.isoformat()},
        ttl=timedelta(days=8),
    )
    iv = await adapter.fetch_indicator(FOOD_BANKS_INDICATOR, "ltla24:FB1", None)
    assert iv is not None and iv.value == 2.0
    assert iv.source.cache_status != "stale"
    assert fake.calls == 1
