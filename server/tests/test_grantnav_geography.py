"""Integration coverage for GrantNav → Soundings geography enrichment."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from soundings.catalogue.loader import load_catalogue_into_db
from soundings.db.engine import get_engine
from soundings.grants.import_grantnav_csv import import_grantnav_csv
from soundings.grants.store import GrantStore

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    yield
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.grant_record"))
        await conn.execute(
            text(
                "DELETE FROM data.loader_run WHERE source_id='threesixtygiving' "
                "AND notes LIKE 'grant_index_scope=%'"
            )
        )
        await conn.execute(text("DELETE FROM geography.code_change"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))


async def _seed_geography() -> None:
    engine = get_engine()
    repo_root = Path(__file__).resolve().parent.parent.parent
    await load_catalogue_into_db(
        engine,
        sources_path=repo_root / "catalogue" / "sources.yaml",
        indicators_path=repo_root / "catalogue" / "indicators.yaml",
    )
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.grant_record"))
        await conn.execute(text("DELETE FROM geography.code_change"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) VALUES "
                "('ltla24:E06000002', 'ltla24', 'E06000002', 'Middlesbrough'), "
                "('ltla24:E06000060', 'ltla24', 'E06000060', 'Buckinghamshire')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO geography.code_change "
                "(old_code, new_code, change_type, effective_date, notes) VALUES "
                "('E07000004', 'E06000060', 'Reorganisation', '2020-04-01', 'test')"
            )
        )


def _write_csv(path: Path) -> None:
    path.write_text(
        "Identifier,Title,Description,Currency,Amount Awarded,Award Date,"
        "Funding Org:Identifier,Funding Org:Name,Recipient Org:Identifier,"
        "Recipient Org:Name,Beneficiary District Geographic code (additional data)\n"
        "grant-current,Food support,Food poverty work,GBP,10000,2026-01-01,"
        "GB-CHC-1,Funder A,GB-CHC-2,Recipient A,E06000002\n"
        "grant-historic,Community support,Community work,GBP,12000,2025-09-01,"
        "GB-CHC-1,Funder A,GB-CHC-3,Recipient B,E07000004\n",
        encoding="utf-8",
    )


async def test_import_resolves_current_and_historic_district_codes(tmp_path: Path) -> None:
    await _seed_geography()
    path = tmp_path / "grantnav.csv"
    _write_csv(path)

    engine = get_engine()
    written = await import_grantnav_csv(engine, path, full_corpus=True)
    assert written == 2

    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, beneficiary_place_ids FROM data.grant_record "
                    "WHERE id IN ('grant-current', 'grant-historic') ORDER BY id"
                )
            )
        ).all()

    places = {str(row.id): list(row.beneficiary_place_ids) for row in rows}
    assert places["grant-current"] == ["ltla24:E06000002"]
    assert places["grant-historic"] == ["ltla24:E06000060"]

    current = await GrantStore(engine).search(place_id="ltla24:E06000002")
    historic = await GrantStore(engine).search(place_id="ltla24:E06000060")
    assert [grant["id"] for grant in current["grants"]] == ["grant-current"]
    assert [grant["id"] for grant in historic["grants"]] == ["grant-historic"]
