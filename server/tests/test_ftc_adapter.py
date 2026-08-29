"""Unit tests for the FTC adapter's current capability boundary."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from soundings.adapters.find_that_charity.adapter import FindThatCharityAdapter


async def test_fetch_organisations_scotland_signals_unavailable_discovery() -> None:
    adapter = FindThatCharityAdapter(MagicMock())
    adapter._ftc = MagicMock()
    adapter._ftc.search = AsyncMock()

    with pytest.raises(NotImplementedError, match="Scotland"):
        await adapter.fetch_organisations("ltla24:S12000033")

    adapter._ftc.search.assert_not_called()


async def test_fetch_organisations_northern_ireland_signals_unavailable_discovery() -> None:
    adapter = FindThatCharityAdapter(MagicMock())
    adapter._ftc = MagicMock()
    adapter._ftc.search = AsyncMock()

    with pytest.raises(NotImplementedError, match="Northern Ireland"):
        await adapter.fetch_organisations("ltla24:N09000005")

    adapter._ftc.search.assert_not_called()


async def test_fetch_organisations_england_empty() -> None:
    adapter = FindThatCharityAdapter(MagicMock())
    adapter._ftc = MagicMock()
    adapter._ftc.search = AsyncMock()

    result = await adapter.fetch_organisations("ltla24:E06000004")

    adapter._ftc.search.assert_not_called()
    assert result == []
