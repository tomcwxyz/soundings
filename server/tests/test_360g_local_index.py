"""Integration coverage for the local-first 360Giving adapter path."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from soundings.adapters.threesixtygiving.adapter import ThreeSixtyGivingAdapter
from soundings.catalogue.loader import load_catalogue_into_db
from soundings.db.engine import get_engine
from soundings.grants.store import GrantStore

pytestmark = pytest.mark.integration

STOCKTON = "ltla24:E06000004"
LEEDS = "ltla24:E08000035"
NOW = datetime(2026, 5, 12, tzinfo=UTC)


async def _clean() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.grant_record"))
        await conn.execute(
            text(
                "DELETE FROM data.loader_run WHERE source_id = 'threesixtygiving' "
                "AND notes LIKE '%grant_index_scope=%'"
            )
        )
        await conn.execute(text("DELETE FROM data.organisation_operates_in"))
        await conn.execute(text("DELETE FROM data.organisation"))
        await conn.execute(text("DELETE FROM cache.source_cache"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))


@pytest_asyncio.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    await _clean()
    yield
    await _clean()


def _grant(
    identifier: str,
    *,
    amount: float,
    award_date: str,
    beneficiary_places: list[str],
    purpose: str,
) -> dict[str, Any]:
    return {
        "grant_id": identifier,
        "soundings_beneficiary_place_ids": beneficiary_places,
        "data": {
            "id": identifier,
            "title": purpose,
            "description": purpose,
            "currency": "GBP",
            "amountAwarded": amount,
            "awardDate": award_date,
            "fundingOrganization": [{"id": "GB-CHC-999999", "name": "Example Foundation"}],
            "recipientOrganization": [{"id": "GB-CHC-1001", "name": "Stockton Community Charity"}],
        },
    }


async def _seed_full_index() -> None:
    engine = get_engine()
    repo_root = Path(__file__).resolve().parent.parent.parent
    await load_catalogue_into_db(
        engine,
        sources_path=repo_root / "catalogue" / "sources.yaml",
        indicators_path=repo_root / "catalogue" / "indicators.yaml",
    )

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) VALUES "
                "(:stockton, 'ltla24', 'E06000004', 'Stockton-on-Tees'), "
                "(:leeds, 'ltla24', 'E08000035', 'Leeds')"
            ),
            {"stockton": STOCKTON, "leeds": LEEDS},
        )
        await conn.execute(
            text(
                "INSERT INTO data.organisation "
                "(id, name, classification, registered_address_place_id, source_id, "
                "retrieved_at, raw) VALUES "
                "('charity_commission:1001', 'Stockton Community Charity', "
                "ARRAY[]::varchar[], :stockton, 'charity_commission', NOW(), '{}'::jsonb)"
            ),
            {"stockton": STOCKTON},
        )
        await conn.execute(
            text(
                "INSERT INTO data.organisation_operates_in (organisation_id, place_id) "
                "VALUES ('charity_commission:1001', :stockton)"
            ),
            {"stockton": STOCKTON},
        )

    store = GrantStore(engine)
    await store.upsert_api_grants(
        [
            _grant(
                "grant-stockton-explicit",
                amount=10_000,
                award_date="2026-01-15",
                beneficiary_places=[STOCKTON],
                purpose="Explicit Stockton beneficiaries",
            ),
            _grant(
                "grant-stockton-fallback",
                amount=5_000,
                award_date="2025-08-01",
                beneficiary_places=[],
                purpose="No beneficiary geography",
            ),
            _grant(
                "grant-leeds-explicit",
                amount=20_000,
                award_date="2026-02-01",
                beneficiary_places=[LEEDS],
                purpose="Explicit Leeds beneficiaries",
            ),
            _grant(
                "grant-stockton-old",
                amount=50_000,
                award_date="2024-04-01",
                beneficiary_places=[STOCKTON],
                purpose="Older Stockton grant",
            ),
        ]
    )

    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, finished_at, status, rows_written, notes) "
                "VALUES (:id, 'threesixtygiving', :now, :now, 'ok', 4, "
                "'grant_index_scope=full')"
            ),
            {"id": uuid.uuid4(), "now": NOW},
        )


def _forbid_http(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"local full index should prevent 360Giving HTTP call: {request.url}")


async def _local_adapter(http: httpx.AsyncClient) -> ThreeSixtyGivingAdapter:
    return ThreeSixtyGivingAdapter(
        get_engine(),
        http_client=http,
        now=lambda: NOW,
    )


async def test_full_index_serves_place_indicators_without_http() -> None:
    await _seed_full_index()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_forbid_http)) as http:
        adapter = await _local_adapter(http)
        total = await adapter.fetch_indicator(
            "civil_society.grants_in_last_12m_total", STOCKTON, period=None
        )
        count = await adapter.fetch_indicator(
            "civil_society.grants_in_last_12m_count", STOCKTON, period=None
        )

    assert total is not None
    assert count is not None
    assert total.value == pytest.approx(15_000)
    assert count.value == 2.0
    assert any("full-corpus index" in caveat for caveat in total.caveats)


async def test_explicit_beneficiary_geography_beats_recipient_home_area() -> None:
    await _seed_full_index()
    store = GrantStore(get_engine())

    stockton = await store.search(place_id=STOCKTON, limit=20)
    leeds = await store.search(place_id=LEEDS, limit=20)

    stockton_ids = {grant["id"] for grant in stockton["grants"]}
    leeds_ids = {grant["id"] for grant in leeds["grants"]}
    assert "grant-leeds-explicit" not in stockton_ids
    assert "grant-stockton-fallback" in stockton_ids
    assert "grant-leeds-explicit" in leeds_ids


async def test_full_index_serves_recent_place_and_org_grants_without_http() -> None:
    await _seed_full_index()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_forbid_http)) as http:
        adapter = await _local_adapter(http)
        place_grants = await adapter.recent_grants(STOCKTON, limit=5)
        org_grants = await adapter.recent_grants_for_org("charity_commission:1001", limit=3)

    assert [grant.amount for grant in place_grants] == [10_000, 5_000]
    # Organisation history is independent of beneficiary place, so the Leeds
    # award is correctly present in the recipient's own newest-grants list.
    assert [grant.amount for grant in org_grants] == [20_000, 10_000, 5_000]


async def test_full_index_serves_all_place_history_and_disables_pre_warm_http() -> None:
    await _seed_full_index()
    async with httpx.AsyncClient(transport=httpx.MockTransport(_forbid_http)) as http:
        adapter = await _local_adapter(http)
        all_grants = await adapter._fetch_all_grants_for_place(STOCKTON)
        await adapter.pre_warm_for_places([STOCKTON, LEEDS])

    assert [grant["amount"] for grant in all_grants] == [10_000, 5_000, 50_000]
