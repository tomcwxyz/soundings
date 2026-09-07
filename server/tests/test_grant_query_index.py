"""Integration tests for Soundings' local 360Giving query/index layer."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from soundings.catalogue.loader import load_catalogue_into_db
from soundings.db.engine import get_engine
from soundings.grants.status import get_grant_index_status
from soundings.grants.store import GrantStore
from soundings.tools.search_grants import SearchGrantsInput, search_grants

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    yield
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.grant_record"))
        await conn.execute(text("DELETE FROM data.organisation_operates_in"))
        await conn.execute(text("DELETE FROM data.organisation"))
        await conn.execute(
            text(
                "DELETE FROM data.loader_run WHERE source_id = 'threesixtygiving' "
                "AND notes LIKE 'grant_index_scope=%'"
            )
        )
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))


async def _seed() -> None:
    engine = get_engine()
    repo_root = Path(__file__).resolve().parent.parent.parent
    await load_catalogue_into_db(
        engine,
        sources_path=repo_root / "catalogue" / "sources.yaml",
        indicators_path=repo_root / "catalogue" / "indicators.yaml",
    )
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.grant_record"))
        await conn.execute(text("DELETE FROM data.organisation_operates_in"))
        await conn.execute(text("DELETE FROM data.organisation"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) VALUES "
                "('ltla24:E06000002', 'ltla24', 'E06000002', 'Middlesbrough')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO data.organisation "
                "(id, name, classification, registered_address_place_id, source_id, "
                "retrieved_at, raw) VALUES "
                "('charity_commission:123456', 'Refugee Housing North East', "
                "ARRAY[]::varchar[], 'ltla24:E06000002', 'charity_commission', "
                "NOW(), '{}'::jsonb)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO data.organisation_operates_in (organisation_id, place_id) "
                "VALUES ('charity_commission:123456', 'ltla24:E06000002')"
            )
        )


def _grant(
    identifier: str,
    *,
    description: str,
    amount: float,
    award_date: str,
    funder_id: str = "GB-CHC-999999",
    funder_name: str = "Example Foundation",
) -> dict[str, object]:
    return {
        "grant_id": identifier,
        "data": {
            "id": identifier,
            "title": "Support grant",
            "description": description,
            "currency": "GBP",
            "amountAwarded": amount,
            "awardDate": award_date,
            "grantProgramme": [{"title": "Communities"}],
            "fundingOrganization": [{"id": funder_id, "name": funder_name}],
            "recipientOrganization": [
                {"id": "GB-CHC-123456", "name": "Refugee Housing North East"}
            ],
        },
    }


async def test_search_grants_combines_topic_place_and_date_filters() -> None:
    await _seed()
    engine = get_engine()
    store = GrantStore(engine)
    await store.upsert_api_grants(
        [
            _grant(
                "grant-1",
                description="Emergency refugee housing and tenancy support",
                amount=25_000,
                award_date="2026-02-01",
            ),
            _grant(
                "grant-2",
                description="Community arts programme",
                amount=10_000,
                award_date="2025-03-01",
            ),
        ]
    )

    result = await search_grants(
        SearchGrantsInput(
            query="refugee housing",
            place_id="ltla24:E06000002",
            awarded_from="2026-01-01",
        ),
        engine,
    )

    assert result.total == 1
    assert [grant.id for grant in result.grants] == ["grant-1"]
    assert result.grants[0].recipient_name == "Refugee Housing North East"
    assert result.grants[0].funder_name == "Example Foundation"
    assert result.index_complete is False
    assert result.index_coverage == "partial-write-through"
    assert result.caveats


async def test_funder_profile_aggregates_indexed_grants() -> None:
    await _seed()
    store = GrantStore(get_engine())
    await store.upsert_api_grants(
        [
            _grant(
                "grant-1",
                description="Refugee housing",
                amount=25_000,
                award_date="2026-02-01",
            ),
            _grant(
                "grant-2",
                description="Refugee advice",
                amount=15_000,
                award_date="2025-11-01",
            ),
        ]
    )

    profile = await store.funder_profile("GB-CHC-999999")

    assert profile["grants"] == 2
    assert profile["total_gbp"] == pytest.approx(40_000)
    assert profile["average_gbp"] == pytest.approx(20_000)
    assert profile["top_recipients"][0]["name"] == "Refugee Housing North East"
    assert profile["top_recipients"][0]["grants"] == 2


async def test_full_import_loader_run_marks_index_complete() -> None:
    await _seed()
    engine = get_engine()
    status = await get_grant_index_status(engine)
    assert status["complete"] is False

    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, finished_at, status, rows_written, notes) "
                "VALUES (:id, 'threesixtygiving', :now, :now, 'ok', 0, "
                "'grant_index_scope=full')"
            ),
            {"id": uuid.uuid4(), "now": now},
        )

    status = await get_grant_index_status(engine)
    assert status["complete"] is True
    assert status["coverage"] == "full-grantnav-export"
