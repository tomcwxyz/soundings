"""Integration coverage for GrantNav import-run provenance."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from soundings.catalogue.loader import load_catalogue_into_db
from soundings.db.engine import get_engine
from soundings.grants.import_grantnav_csv import import_grantnav_csv
from soundings.grants.status import get_grant_index_status

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.grant_record"))
        await conn.execute(text("DELETE FROM data.loader_run WHERE source_id = 'threesixtygiving'"))
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.grant_record"))
        await conn.execute(text("DELETE FROM data.loader_run WHERE source_id = 'threesixtygiving'"))


async def test_full_import_records_snapshot_and_preserves_publisher_row(tmp_path: Path) -> None:
    engine = get_engine()
    repo_root = Path(__file__).resolve().parent.parent.parent
    await load_catalogue_into_db(
        engine,
        sources_path=repo_root / "catalogue" / "sources.yaml",
        indicators_path=repo_root / "catalogue" / "indicators.yaml",
    )

    path = tmp_path / "grantnav.csv"
    path.write_text(
        "Identifier,Title,Description,Currency,Amount Awarded,Award Date,"
        "Funding Org:Identifier,Funding Org:Name,Recipient Org:Identifier,"
        "Recipient Org:Name,Publisher:Name\n"
        "360G-Test-1,A grant,Community work,GBP,10000,2026-01-01,"
        "GB-CHC-1,Funder,GB-CHC-2,Recipient,Original Publisher\n",
        encoding="utf-8",
    )

    written = await import_grantnav_csv(
        engine,
        path,
        full_corpus=True,
        provenance={
            "acquisition": "grantnav-http",
            "source_url": "https://grantnav.example/search.csv",
            "sha256": "abc123",
            "etag": '"snapshot-1"',
        },
    )
    assert written == 1

    async with engine.connect() as conn:
        run = (
            (
                await conn.execute(
                    text(
                        "SELECT provenance, status, rows_written, notes "
                        "FROM data.loader_run "
                        "WHERE source_id = 'threesixtygiving' "
                        "ORDER BY started_at DESC LIMIT 1"
                    )
                )
            )
            .mappings()
            .one()
        )
        grant = (
            (await conn.execute(text("SELECT raw FROM data.grant_record WHERE id = '360G-Test-1'")))
            .mappings()
            .one()
        )

    assert run["status"] == "ok"
    assert run["rows_written"] == 1
    assert run["notes"] == "grant_index_scope=full"
    assert run["provenance"]["dataset"] == "GrantNav"
    assert run["provenance"]["coverage"] == "full"
    assert run["provenance"]["acquisition"] == "grantnav-http"
    assert run["provenance"]["source_url"] == "https://grantnav.example/search.csv"
    assert run["provenance"]["sha256"] == "abc123"
    assert run["provenance"]["etag"] == '"snapshot-1"'
    assert run["provenance"]["bytes"] == path.stat().st_size
    assert grant["raw"]["grantnav_row"]["Publisher:Name"] == "Original Publisher"

    status = await get_grant_index_status(engine)
    assert status["complete"] is True
    assert status["coverage"] == "full-grantnav-export"
    assert status["snapshot"] is not None
    assert status["snapshot"]["rows_written"] == 1
    assert status["snapshot"]["provenance"]["sha256"] == "abc123"
