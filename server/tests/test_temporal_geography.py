from datetime import date, timedelta

import pytest
from sqlalchemy import text

from soundings.adapters.postcodes_io.adapter import PostcodesIoAdapter
from soundings.db.engine import get_engine
from soundings.geography.service import GeographyService

pytestmark = pytest.mark.integration


async def _seed_temporal_hierarchy() -> GeographyService:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) VALUES "
                "('lsoa21:CHILD', 'lsoa21', 'CHILD', 'Example neighbourhood'), "
                "('ltla24:OLD', 'ltla24', 'OLD', 'Old district'), "
                "('ltla24:NEW', 'ltla24', 'NEW', 'New district'), "
                "('region:CURRENT', 'region', 'CURRENT', 'Current region')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO geography.place_hierarchy "
                "(child_id, parent_id, valid_from, valid_to) VALUES "
                "('lsoa21:CHILD', 'ltla24:OLD', '2010-01-01', '2020-01-01'), "
                "('lsoa21:CHILD', 'ltla24:NEW', '2020-01-01', NULL), "
                "('lsoa21:CHILD', 'region:CURRENT', NULL, NULL)"
            )
        )

    adapter = PostcodesIoAdapter(engine, ttl=timedelta(hours=720))
    return GeographyService(engine, adapter)


async def test_current_boundary_uses_current_and_undated_edges() -> None:
    service = await _seed_temporal_hierarchy()

    result = await service.find_containing_places_context(
        "lsoa21:CHILD",
        boundary_mode="current_boundary",
    )

    ids = {place.id for place in result.places}
    assert "ltla24:NEW" in ids
    assert "region:CURRENT" in ids
    assert "ltla24:OLD" not in ids
    assert result.partial is False


async def test_historical_boundary_selects_edge_valid_on_requested_date() -> None:
    service = await _seed_temporal_hierarchy()

    result = await service.find_containing_places_context(
        "lsoa21:CHILD",
        boundary_mode="historical",
        as_of=date(2015, 6, 1),
    )

    ids = {place.id for place in result.places}
    assert ids == {"ltla24:OLD"}
    assert result.boundary_date == date(2015, 6, 1)
    assert result.partial is True
    assert any("undated" in caveat for caveat in result.caveats)


async def test_hierarchy_validity_is_half_open_at_boundary_change() -> None:
    service = await _seed_temporal_hierarchy()

    result = await service.find_containing_places_context(
        "lsoa21:CHILD",
        boundary_mode="historical",
        as_of=date(2020, 1, 1),
    )

    ids = {place.id for place in result.places}
    assert "ltla24:NEW" in ids
    assert "ltla24:OLD" not in ids


async def test_historical_mode_never_substitutes_undated_current_edge() -> None:
    service = await _seed_temporal_hierarchy()

    result = await service.find_containing_places_context(
        "lsoa21:CHILD",
        boundary_mode="historical",
        as_of=date(2005, 6, 1),
    )

    assert result.places == ()
    assert result.partial is True
    assert any("undated" in caveat for caveat in result.caveats)
    assert any("No dated hierarchy edges" in caveat for caveat in result.caveats)


async def test_historical_mode_requires_date() -> None:
    service = await _seed_temporal_hierarchy()

    with pytest.raises(ValueError, match="requires as_of"):
        await service.find_containing_places_context(
            "lsoa21:CHILD",
            boundary_mode="historical",
        )
