"""Integration tests for full-index funded organisation filtering."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from soundings.adapters.threesixtygiving.adapter import ThreeSixtyGivingAdapter
from soundings.catalogue.loader import load_catalogue_into_db
from soundings.db.engine import get_engine
from soundings.grants.store import GrantStore
from soundings.orchestration.orchestrator import IndicatorOrchestrator
from soundings.orchestration.registry import AdapterRegistry
from soundings.tools.find_organisations_in_place import (
    FindOrganisationsInPlaceInput,
    find_organisations_in_place,
)

pytestmark = pytest.mark.integration

PLACE_ID = "ltla24:E06000004"
NOW = datetime(2026, 9, 7, tzinfo=UTC)


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


async def _seed(*, mark_full: bool) -> IndicatorOrchestrator:
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
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES (:pid, 'ltla24', 'E06000004', 'Stockton-on-Tees')"
            ),
            {"pid": PLACE_ID},
        )
        await conn.execute(
            text(
                """
                INSERT INTO data.organisation
                    (id, name, classification, registered_address_place_id,
                     source_id, retrieved_at, raw)
                VALUES
                    ('charity_commission:1001', 'Funded Small Charity', ARRAY[]::varchar[],
                     :pid, 'charity_commission', NOW(),
                     '{"latest_income":"10000"}'::jsonb),
                    ('charity_commission:2002', 'Unfunded Large Charity', ARRAY[]::varchar[],
                     :pid, 'charity_commission', NOW(),
                     '{"latest_income":"5000000"}'::jsonb)
                """
            ),
            {"pid": PLACE_ID},
        )
        await conn.execute(
            text(
                """
                INSERT INTO data.organisation_operates_in (organisation_id, place_id)
                VALUES
                    ('charity_commission:1001', :pid),
                    ('charity_commission:2002', :pid)
                """
            ),
            {"pid": PLACE_ID},
        )

    await GrantStore(engine).upsert_api_grants(
        [
            {
                "grant_id": "grant-funded-1001",
                "data": {
                    "id": "grant-funded-1001",
                    "title": "Community support",
                    "description": "Local community support",
                    "currency": "GBP",
                    "amountAwarded": 25_000,
                    "awardDate": "2026-04-01",
                    "fundingOrganization": [
                        {"id": "GB-CHC-999999", "name": "Example Foundation"}
                    ],
                    "recipientOrganization": [
                        {"id": "GB-CHC-1001", "name": "Funded Small Charity"}
                    ],
                },
            }
        ]
    )

    if mark_full:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    INSERT INTO data.loader_run
                        (id, source_id, started_at, finished_at, status, rows_written, notes)
                    VALUES
                        (:id, 'threesixtygiving', :now, :now, 'ok', 1,
                         'grant_index_scope=full')
                    """
                ),
                {"id": uuid.uuid4(), "now": NOW},
            )

    registry = AdapterRegistry(engine)
    registry.register("threesixtygiving", ThreeSixtyGivingAdapter)
    return IndicatorOrchestrator(engine=engine, registry=registry)


async def test_funded_only_filters_before_requested_limit_and_enriches_locally() -> None:
    orchestrator = await _seed(mark_full=True)

    result = await find_organisations_in_place(
        FindOrganisationsInPlaceInput(
            place_id=PLACE_ID,
            funded_only=True,
            limit=1,
        ),
        orchestrator,
    )

    # The unfunded charity has the much larger income, so filtering after the
    # caller's limit would incorrectly return nothing. The funded charity must
    # still win because the tool expands the local candidate set before filter.
    assert [org.id for org in result.organisations] == ["charity_commission:1001"]
    assert len(result.organisations[0].recent_grants) == 1
    assert result.organisations[0].recent_grants[0].amount == pytest.approx(25_000)
    assert result.partial is False
    assert any(source.source_id == "threesixtygiving" for source in result.sources)


async def test_funded_only_is_not_applied_to_partial_index() -> None:
    orchestrator = await _seed(mark_full=False)

    result = await find_organisations_in_place(
        FindOrganisationsInPlaceInput(
            place_id=PLACE_ID,
            funded_only=True,
            limit=10,
        ),
        orchestrator,
    )

    assert {org.id for org in result.organisations} == {
        "charity_commission:1001",
        "charity_commission:2002",
    }
    assert all(org.recent_grants == [] for org in result.organisations)
    assert result.partial is True
    assert any("funded_only was not applied" in caveat for caveat in result.caveats)
    assert any("full GrantNav index" in caveat for caveat in result.caveats)
