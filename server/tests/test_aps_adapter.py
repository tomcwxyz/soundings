"""Integration tests for OnsApsAdapter.

Passthrough over Nomis APS datasets. Reuses NomisClient. One upstream
call per (dataset_id, measures, place_code) returns all available
quarterly periods; fetch_indicator picks the latest (or explicit) period,
fetch_trend slices to a window.
"""

from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from sqlalchemy import text

from soundings.adapters.nomis.client import NomisClient
from soundings.adapters.ons_aps.adapter import OnsApsAdapter
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration


async def _seed_aps_source() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM cache.source_cache"))
        await conn.execute(
            text(
                "INSERT INTO catalogue.source (id, label, publisher, publisher_url, "
                "dataset_url, licence, mode, rate_limit) VALUES "
                "('ons.aps', 'ONS Annual Population Survey', "
                "'Office for National Statistics', 'https://www.ons.gov.uk/', "
                "'https://www.nomisweb.co.uk/', 'OGL-UK-3.0', 'passthrough', "
                "'{}'::jsonb) ON CONFLICT (id) DO NOTHING"
            )
        )


def _stub_mapping(tmp_path: Path, **overrides: Any) -> Path:
    entry: dict[str, Any] = {
        "indicator_key": "economy.employment_rate",
        "source_id": "ons.aps",
        "dataset_id": "NM_17_5",
        "measures": "20100",
        "geography_type_codes": {"ltla24": "TYPE424"},
        "extra_params": {"variable": "45"},
        "value_scale": 0.01,
    }
    entry.update(overrides)
    path = tmp_path / "nomis-mapping.yaml"
    path.write_text(yaml.safe_dump({"mappings": [entry]}))
    return path


def _sample_payload(rows: list[tuple[str, float]]) -> dict[str, Any]:
    return {
        "obs": [
            {
                "obs_value": {"value": value},
                "geography": {"geogcode": "E06000004"},
                "time": {"description": period},
            }
            for period, value in rows
        ]
    }


async def test_fetch_indicator_returns_latest_period(tmp_path: Path) -> None:
    await _seed_aps_source()
    mapping_path = _stub_mapping(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json=_sample_payload(
                [
                    ("Apr 2021-Mar 2022", 73.5),
                    ("Apr 2022-Mar 2023", 74.1),
                    ("Apr 2023-Mar 2024", 75.2),
                ]
            ),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = NomisClient(http_client=http)
        adapter = OnsApsAdapter(get_engine(), nomis_client=client, mapping_path=mapping_path)
        iv = await adapter.fetch_indicator(
            "economy.employment_rate", "ltla24:E06000004", period=None
        )

    assert iv is not None
    # 75.2 percent × value_scale=0.01 → 0.752 fraction
    assert iv.value == pytest.approx(0.752)
    assert iv.period == "Apr 2023-Mar 2024"
    assert iv.source.source_id == "ons.aps"


async def test_fetch_indicator_by_explicit_period(tmp_path: Path) -> None:
    await _seed_aps_source()
    mapping_path = _stub_mapping(tmp_path)
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json=_sample_payload(
                [
                    ("Apr 2021-Mar 2022", 73.5),
                    ("Apr 2022-Mar 2023", 74.1),
                    ("Apr 2023-Mar 2024", 75.2),
                ]
            ),
        )
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = NomisClient(http_client=http)
        adapter = OnsApsAdapter(get_engine(), nomis_client=client, mapping_path=mapping_path)
        iv = await adapter.fetch_indicator(
            "economy.employment_rate", "ltla24:E06000004", period="Apr 2022-Mar 2023"
        )
    assert iv is not None
    assert iv.value == pytest.approx(0.741)


async def test_fetch_trend_returns_ordered_series(tmp_path: Path) -> None:
    await _seed_aps_source()
    mapping_path = _stub_mapping(tmp_path)
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json=_sample_payload(
                [
                    ("Apr 2023-Mar 2024", 75.2),
                    ("Apr 2021-Mar 2022", 73.5),
                    ("Apr 2022-Mar 2023", 74.1),
                ]
            ),
        )
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = NomisClient(http_client=http)
        adapter = OnsApsAdapter(get_engine(), nomis_client=client, mapping_path=mapping_path)
        trend = await adapter.fetch_trend("economy.employment_rate", "ltla24:E06000004")

    assert trend is not None
    periods = [p.period for p in trend.points]
    assert periods == sorted(periods)
    assert trend.points[-1].period == "Apr 2023-Mar 2024"
    assert trend.points[-1].value == pytest.approx(0.752)


async def test_fetch_trend_filters_by_window(tmp_path: Path) -> None:
    await _seed_aps_source()
    mapping_path = _stub_mapping(tmp_path)
    transport = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            json=_sample_payload(
                [
                    ("Apr 2021-Mar 2022", 73.5),
                    ("Apr 2022-Mar 2023", 74.1),
                    ("Apr 2023-Mar 2024", 75.2),
                ]
            ),
        )
    )
    async with httpx.AsyncClient(transport=transport) as http:
        client = NomisClient(http_client=http)
        adapter = OnsApsAdapter(get_engine(), nomis_client=client, mapping_path=mapping_path)
        trend = await adapter.fetch_trend(
            "economy.employment_rate",
            "ltla24:E06000004",
            period_from="Apr 2022-Mar 2023",
        )

    assert trend is not None
    assert [p.period for p in trend.points] == [
        "Apr 2022-Mar 2023",
        "Apr 2023-Mar 2024",
    ]


async def test_unknown_indicator_returns_none(tmp_path: Path) -> None:
    await _seed_aps_source()
    mapping_path = _stub_mapping(tmp_path)
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"obs": []}))
    async with httpx.AsyncClient(transport=transport) as http:
        client = NomisClient(http_client=http)
        adapter = OnsApsAdapter(get_engine(), nomis_client=client, mapping_path=mapping_path)
        iv = await adapter.fetch_indicator(
            "economy.does_not_exist", "ltla24:E06000004", period=None
        )
    assert iv is None


async def test_per_place_query_is_cached(tmp_path: Path) -> None:
    """Second call for same place hits cache; fetch_indicator then fetch_trend
    share one upstream call."""
    await _seed_aps_source()
    mapping_path = _stub_mapping(tmp_path)
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        del request
        return httpx.Response(
            200,
            json=_sample_payload([("Apr 2022-Mar 2023", 74.1), ("Apr 2023-Mar 2024", 75.2)]),
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = NomisClient(http_client=http)
        adapter = OnsApsAdapter(get_engine(), nomis_client=client, mapping_path=mapping_path)
        await adapter.fetch_indicator("economy.employment_rate", "ltla24:E06000004", period=None)
        await adapter.fetch_trend("economy.employment_rate", "ltla24:E06000004")
    assert call_count == 1


async def test_place_type_not_in_mapping_returns_none(tmp_path: Path) -> None:
    """Indicator mapped only at ltla24 returns None for lsoa21 requests."""
    await _seed_aps_source()
    mapping_path = _stub_mapping(tmp_path)
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"obs": []}))
    async with httpx.AsyncClient(transport=transport) as http:
        client = NomisClient(http_client=http)
        adapter = OnsApsAdapter(get_engine(), nomis_client=client, mapping_path=mapping_path)
        iv = await adapter.fetch_indicator(
            "economy.employment_rate", "lsoa21:E01001234", period=None
        )
    assert iv is None


async def test_series_sharing_a_dataset_do_not_share_a_cache_entry(tmp_path: Path) -> None:
    """Employment (variable 45) and unemployment (variable 84) both query
    NM_17_5 / measures 20599 for the same place. The cache key used to omit
    the variable, so whichever was fetched first was served for both: the
    9 November 2026 evaluation saw unemployment "rates" of 66-75%.
    """
    await _seed_aps_source()
    shared = {
        "source_id": "ons.aps",
        "dataset_id": "NM_17_5",
        "measures": "20599",
        "geography_type_codes": {"ltla24": "TYPE424"},
        "value_scale": 0.01,
    }
    path = tmp_path / "nomis-mapping.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "mappings": [
                    {
                        **shared,
                        "indicator_key": "economy.employment_rate",
                        "extra_params": {"variable": "45"},
                    },
                    {
                        **shared,
                        "indicator_key": "economy.unemployment_rate",
                        "extra_params": {"variable": "84"},
                    },
                ]
            }
        )
    )
    by_variable = {"45": 71.4, "84": 4.9}

    def handler(request: httpx.Request) -> httpx.Response:
        variable = request.url.params["variable"]
        return httpx.Response(
            200, json=_sample_payload([("Apr 2023-Mar 2024", by_variable[variable])])
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter = OnsApsAdapter(
            get_engine(), nomis_client=NomisClient(http_client=http), mapping_path=path
        )
        employment = await adapter.fetch_indicator(
            "economy.employment_rate", "ltla24:E06000004", period=None
        )
        unemployment = await adapter.fetch_indicator(
            "economy.unemployment_rate", "ltla24:E06000004", period=None
        )

    assert employment is not None and unemployment is not None
    assert employment.value == pytest.approx(0.714)
    assert unemployment.value == pytest.approx(0.049)


async def test_values_carry_the_catalogue_unit_not_a_generic_one(tmp_path: Path) -> None:
    """Median pay was served with unit "value" while the catalogue says
    "GBP per week"; answers could not tell which was right."""
    await _seed_aps_source()
    mapping_path = _stub_mapping(tmp_path)
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=_sample_payload([("Apr 2023-Mar 2024", 75.2)]))
    )
    async with httpx.AsyncClient(transport=transport) as http:
        adapter = OnsApsAdapter(
            get_engine(), nomis_client=NomisClient(http_client=http), mapping_path=mapping_path
        )
        iv = await adapter.fetch_indicator(
            "economy.employment_rate", "ltla24:E06000004", period=None
        )
        trend = await adapter.fetch_trend("economy.employment_rate", "ltla24:E06000004")

    assert iv is not None and trend is not None
    assert iv.unit == "proportion"
    assert trend.unit == "proportion"
