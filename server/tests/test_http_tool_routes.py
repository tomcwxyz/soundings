import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from soundings.app import app
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration


async def _seed_full() -> None:
    engine = get_engine()
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM data.loader_run"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES ('ltla24:E06000004', 'ltla24', 'E06000004', 'Stockton-on-Tees')"
            )
        )
        run = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, finished_at, status, rows_written) "
                "VALUES (:id, 'ons.mid_year_estimates', :s, :f, 'ok', 1)"
            ),
            {"id": run, "s": now - timedelta(minutes=5), "f": now},
        )
        await conn.execute(
            text(
                "INSERT INTO data.indicator_value "
                "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) VALUES "
                "('ltla24:E06000004', 'population.total', '2024', 200000, 'ons.mid_year_estimates', :ret, '[]'::jsonb)"
            ),
            {"ret": now},
        )


async def test_post_get_indicators_returns_200_with_payload() -> None:
    await _seed_full()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/tools/get_indicators",
                json={
                    "place_id": "ltla24:E06000004",
                    "indicators": ["population.total"],
                },
            )
    assert response.status_code == 200
    body = response.json()
    assert body["results"][0]["value"] == 200000
    assert body["results"][0]["source"]["source_id"] == "ons.mid_year_estimates"


async def test_post_get_place_profile_returns_200() -> None:
    await _seed_full()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/tools/get_place_profile",
                json={
                    "place_id": "ltla24:E06000004",
                    "include": ["population"],
                },
            )
    assert response.status_code == 200
    body = response.json()
    assert body["place"]["id"] == "ltla24:E06000004"


async def test_post_find_place_returns_200_for_name_query() -> None:
    await _seed_full()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/tools/find_place",
                json={
                    "query": "stockton",
                    "geography_types": ["ltla24"],
                },
            )
    assert response.status_code == 200
    body = response.json()
    assert any(m["id"] == "ltla24:E06000004" for m in body["matches"])


async def test_get_v1_tools_lists_specs() -> None:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/v1/tools")
    assert response.status_code == 200
    body = response.json()
    names = {t["name"] for t in body["tools"]}
    assert names == {
        "find_place",
        "get_indicators",
        "get_observations",
        "get_place_profile",
        "compare_places",
        "get_trend",
        "find_organisations_in_place",
        "get_civil_society_profile",
        "get_peer_distribution",
    }


async def test_post_tool_validates_input() -> None:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/tools/get_indicators",
                json={"place_id": "ltla24:E06000004"},  # missing indicators
            )
    assert response.status_code == 422
