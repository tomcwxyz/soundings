"""Integration tests for DwpStatXploreAdapter."""

import json

import httpx
import pytest
from sqlalchemy import text

from soundings.adapters.dwp_statxplore.adapter import DwpStatXploreAdapter
from soundings.adapters.dwp_statxplore.client import StatXploreClient
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration

DATE_FIELD = "str:field:UC_Monthly:F_UC_DATE:DATE_NAME"
DATE_VALUESET = "str:valueset:UC_Monthly:F_UC_DATE:DATE_NAME:C_UC_DATE"
MEASURE = "str:count:UC_Monthly:V_F_UC_CASELOAD_FULL"
PERIOD_VALUES = {"202401": 100.0, "202402": 120.0, "202403": 145.0}


async def _seed_statxplore_source() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM cache.source_cache"))
        await conn.execute(
            text(
                "INSERT INTO catalogue.source (id, label, publisher, publisher_url, "
                "dataset_url, licence, mode, rate_limit) VALUES "
                "('dwp.statxplore', 'DWP Stat-Xplore', "
                "'Department for Work and Pensions', "
                "'https://stat-xplore.dwp.gov.uk/', "
                "'https://stat-xplore.dwp.gov.uk/', "
                "'OGL-UK-3.0', 'passthrough', '{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            )
        )


def _sample_payload(periods: list[str]) -> dict:
    return {
        "cubes": {MEASURE: {"values": [[PERIOD_VALUES[p] for p in periods]]}},
        "fields": [
            {"items": [{"labels": ["E06000004", "Stockton-on-Tees"]}]},
            {"items": [{"labels": [p]} for p in periods]},
        ],
    }


def _build_transport(
    *,
    table_calls: list[list[str]] | None = None,
    schema_calls: list[str] | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET":
            if schema_calls is not None:
                schema_calls.append(path)
            decoded = httpx.URL(str(request.url)).raw_path.decode("utf-8")
            if "F_UC_DATE%3ADATE_NAME%3AC_UC_DATE" in decoded:
                return httpx.Response(
                    200,
                    json={
                        "id": DATE_VALUESET,
                        "type": "VALUESET",
                        "children": [
                            {
                                "id": f"str:value:UC_Monthly:F_UC_DATE:DATE_NAME:C_UC_DATE:{p}",
                                "type": "VALUE",
                            }
                            for p in PERIOD_VALUES
                        ],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": DATE_FIELD,
                    "type": "FIELD",
                    "children": [
                        {
                            "id": DATE_VALUESET,
                            "label": "Month",
                            "type": "VALUESET",
                        }
                    ],
                },
            )

        body = json.loads(request.read().decode("utf-8"))
        date_recode = body.get("recodes", {}).get(DATE_FIELD)
        if date_recode and date_recode.get("map"):
            periods = [
                value_group[0].rsplit(":", 1)[-1]
                for value_group in date_recode["map"]
            ]
        else:
            periods = list(PERIOD_VALUES)
        if table_calls is not None:
            table_calls.append(periods)
        return httpx.Response(200, json=_sample_payload(periods))

    return httpx.MockTransport(handler)


async def test_fetch_indicator_returns_latest_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STATXPLORE_API_KEY", "test-key")
    await _seed_statxplore_source()
    table_calls: list[list[str]] = []

    async with httpx.AsyncClient(
        transport=_build_transport(table_calls=table_calls)
    ) as http:
        client = StatXploreClient(http_client=http)
        adapter = DwpStatXploreAdapter(get_engine(), statxplore_client=client)
        iv = await adapter.fetch_indicator(
            "economy.universal_credit_claimants",
            "ltla24:E06000004",
            period=None,
        )

    assert iv is not None
    assert iv.value == 145.0
    assert iv.period == "202403"
    assert iv.unit == "people"
    assert iv.source.source_id == "dwp.statxplore"
    assert table_calls == [["202403"]]


async def test_fetch_indicator_by_explicit_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STATXPLORE_API_KEY", "test-key")
    await _seed_statxplore_source()
    table_calls: list[list[str]] = []

    async with httpx.AsyncClient(
        transport=_build_transport(table_calls=table_calls)
    ) as http:
        client = StatXploreClient(http_client=http)
        adapter = DwpStatXploreAdapter(get_engine(), statxplore_client=client)
        iv = await adapter.fetch_indicator(
            "economy.universal_credit_claimants",
            "ltla24:E06000004",
            period="202402",
        )

    assert iv is not None
    assert iv.value == 120.0
    assert table_calls == [["202402"]]


async def test_fetch_trend_returns_ordered_series(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STATXPLORE_API_KEY", "test-key")
    await _seed_statxplore_source()
    table_calls: list[list[str]] = []

    async with httpx.AsyncClient(
        transport=_build_transport(table_calls=table_calls)
    ) as http:
        client = StatXploreClient(http_client=http)
        adapter = DwpStatXploreAdapter(get_engine(), statxplore_client=client)
        trend = await adapter.fetch_trend(
            "economy.universal_credit_claimants",
            "ltla24:E06000004",
        )

    assert trend is not None
    assert len(trend.points) == 3
    assert trend.points[0].period == "202401"
    assert trend.points[-1].value == 145.0
    assert table_calls == [["202401", "202402", "202403"]]


async def test_fetch_trend_filters_upstream_by_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STATXPLORE_API_KEY", "test-key")
    await _seed_statxplore_source()
    table_calls: list[list[str]] = []

    async with httpx.AsyncClient(
        transport=_build_transport(table_calls=table_calls)
    ) as http:
        client = StatXploreClient(http_client=http)
        adapter = DwpStatXploreAdapter(get_engine(), statxplore_client=client)
        trend = await adapter.fetch_trend(
            "economy.universal_credit_claimants",
            "ltla24:E06000004",
            period_from="202402",
        )

    assert trend is not None
    assert [p.period for p in trend.points] == ["202402", "202403"]
    assert table_calls == [["202402", "202403"]]


async def test_unknown_indicator_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STATXPLORE_API_KEY", "test-key")
    await _seed_statxplore_source()

    async with httpx.AsyncClient(transport=_build_transport()) as http:
        client = StatXploreClient(http_client=http)
        adapter = DwpStatXploreAdapter(get_engine(), statxplore_client=client)
        iv = await adapter.fetch_indicator(
            "welfare.nope.not.real",
            "ltla24:E06000004",
            period=None,
        )
    assert iv is None


async def test_repeated_indicator_query_uses_schema_and_value_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STATXPLORE_API_KEY", "test-key")
    await _seed_statxplore_source()
    table_calls: list[list[str]] = []
    schema_calls: list[str] = []

    async with httpx.AsyncClient(
        transport=_build_transport(
            table_calls=table_calls,
            schema_calls=schema_calls,
        )
    ) as http:
        client = StatXploreClient(http_client=http)
        adapter = DwpStatXploreAdapter(get_engine(), statxplore_client=client)
        for _ in range(2):
            await adapter.fetch_indicator(
                "economy.universal_credit_claimants",
                "ltla24:E06000004",
                period=None,
            )

    assert len(schema_calls) == 2
    assert table_calls == [["202403"]]
