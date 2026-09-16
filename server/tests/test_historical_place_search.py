from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import text

from soundings.adapters.postcodes_io.adapter import PostcodesIoAdapter
from soundings.db.engine import get_engine
from soundings.geography.service import GeographyService
from soundings.tools.find_place import FindPlaceInput, find_place, tool_spec

pytestmark = pytest.mark.integration

_CURRENT_ID = "ltla24:SEARCHLIVE1"
_HISTORICAL_ID = "gss_e07:SEARCHOLD1"


def _build_service(engine) -> GeographyService:
    transport = httpx.MockTransport(lambda request: httpx.Response(404, json={"status": 404}))
    client = httpx.AsyncClient(transport=transport)
    adapter = PostcodesIoAdapter(engine, ttl=timedelta(hours=720), http_client=client)
    return GeographyService(engine, adapter)


async def _seed_search_places() -> None:
    engine = get_engine()
    ids = [_CURRENT_ID, _HISTORICAL_ID]
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM geography.place_hierarchy "
                "WHERE child_id = ANY(:ids) OR parent_id = ANY(:ids)"
            ),
            {"ids": ids},
        )
        await conn.execute(
            text("DELETE FROM geography.place WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        await conn.execute(
            text(
                "INSERT INTO geography.place "
                "(id, type, code, name, valid_from, valid_to) VALUES "
                "(:current_id, 'ltla24', 'SEARCHLIVE1', 'Temporal Search Current', NULL, NULL), "
                "(:historical_id, 'gss_e07', 'SEARCHOLD1', 'Temporal Search Historic', "
                ":valid_from, :valid_to)"
            ),
            {
                "current_id": _CURRENT_ID,
                "historical_id": _HISTORICAL_ID,
                "valid_from": date(2009, 1, 1),
                "valid_to": date(2023, 4, 1),
            },
        )


async def test_default_name_search_excludes_expired_historical_places() -> None:
    await _seed_search_places()
    engine = get_engine()
    service = _build_service(engine)

    matches = await service.find_place_by_name("Temporal Search Historic")

    assert all(match.place.id != _HISTORICAL_ID for match in matches)


async def test_name_search_as_of_can_resolve_historical_place() -> None:
    await _seed_search_places()
    engine = get_engine()
    service = _build_service(engine)

    matches = await service.find_place_by_name(
        "Temporal Search Historic",
        as_of=date(2015, 6, 1),
    )

    assert matches
    assert matches[0].place.id == _HISTORICAL_ID


async def test_name_search_as_of_excludes_place_before_it_existed() -> None:
    await _seed_search_places()
    engine = get_engine()
    service = _build_service(engine)

    matches = await service.find_place_by_name(
        "Temporal Search Historic",
        as_of=date(2008, 12, 31),
    )

    assert all(match.place.id != _HISTORICAL_ID for match in matches)


def test_find_place_schema_exposes_as_of() -> None:
    schema = tool_spec()["input_schema"]
    assert isinstance(schema, dict)
    assert "as_of" in schema["properties"]


async def test_find_place_passes_as_of_for_name_queries() -> None:
    await _seed_search_places()
    engine = get_engine()
    service = _build_service(engine)

    result = await find_place(
        FindPlaceInput(query="Temporal Search Historic", as_of=date(2015, 6, 1)),
        service,
    )

    assert result.matches
    assert result.matches[0].id == _HISTORICAL_ID
