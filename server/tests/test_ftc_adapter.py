"""Unit tests for the FTC adapter's current capability boundary."""

from unittest.mock import AsyncMock, MagicMock

from soundings.adapters.find_that_charity.adapter import FindThatCharityAdapter


async def test_fetch_organisations_scotland_returns_empty_until_area_source_exists() -> None:
    adapter = FindThatCharityAdapter(MagicMock())
    adapter._ftc = MagicMock()
    adapter._ftc.search = AsyncMock()

    result = await adapter.fetch_organisations("ltla24:S12000033")

    adapter._ftc.search.assert_not_called()
    assert result == []


async def test_fetch_organisations_northern_ireland_returns_empty_until_area_source_exists() -> (
    None
):
    adapter = FindThatCharityAdapter(MagicMock())
    adapter._ftc = MagicMock()
    adapter._ftc.search = AsyncMock()

    result = await adapter.fetch_organisations("ltla24:N09000005")

    adapter._ftc.search.assert_not_called()
    assert result == []


async def test_fetch_organisations_england_empty() -> None:
    adapter = FindThatCharityAdapter(MagicMock())
    adapter._ftc = MagicMock()
    adapter._ftc.search = AsyncMock()

    result = await adapter.fetch_organisations("ltla24:E06000004")

    adapter._ftc.search.assert_not_called()
    assert result == []
