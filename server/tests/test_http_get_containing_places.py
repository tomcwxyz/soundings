from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from soundings.app import app
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration

_CHILD_ID = "gss_e01:TESTCHILD"
_PARENT_ID = "gss_e07:TESTPARENT"


async def _seed_historical_containment() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM geography.place_hierarchy "
                "WHERE child_id IN (:child_id, :parent_id) "
                "OR parent_id IN (:child_id, :parent_id)"
            ),
            {"child_id": _CHILD_ID, "parent_id": _PARENT_ID},
        )
        await conn.execute(
            text("DELETE FROM geography.place WHERE id IN (:child_id, :parent_id)"),
            {"child_id": _CHILD_ID, "parent_id": _PARENT_ID},
        )
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name, valid_from, valid_to) VALUES "
                "(:child_id, 'gss_e01', 'TESTCHILD', 'Historic neighbourhood', "
                "DATE '2010-01-01', DATE '2020-01-01'), "
                "(:parent_id, 'gss_e07', 'TESTPARENT', 'Historic district', "
                "DATE '2009-01-01', DATE '2023-04-01')"
            ),
            {"child_id": _CHILD_ID, "parent_id": _PARENT_ID},
        )
        await conn.execute(
            text(
                "INSERT INTO geography.place_hierarchy "
                "(child_id, parent_id, valid_from, valid_to) VALUES "
                "(:child_id, :parent_id, DATE '2010-01-01', DATE '2020-01-01')"
            ),
            {"child_id": _CHILD_ID, "parent_id": _PARENT_ID},
        )


async def test_http_get_containing_places_uses_historical_edges() -> None:
    await _seed_historical_containment()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/tools/get_containing_places",
                json={
                    "place_id": _CHILD_ID,
                    "boundary_mode": "historical",
                    "as_of": "2015-06-01",
                },
            )

    assert response.status_code == 200
    body = response.json()
    assert body["place_id"] == _CHILD_ID
    assert body["boundary_mode"] == "historical"
    assert body["boundary_date"] == date(2015, 6, 1).isoformat()
    assert body["partial"] is False
    assert body["places"] == [
        {
            "id": _PARENT_ID,
            "name": "Historic district",
            "type": "gss_e07",
            "code": "TESTPARENT",
        }
    ]


async def test_http_historical_containment_requires_as_of() -> None:
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/tools/get_containing_places",
                json={"place_id": _CHILD_ID, "boundary_mode": "historical"},
            )

    assert response.status_code == 422
