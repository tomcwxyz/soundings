"""Import history describes the complete job, including failed partial work."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from soundings.adapters.base import LoaderResult
from soundings.db.engine import get_engine
from soundings.loader import run as loader_run
from soundings.loader.tracking import run_tracked_loader

pytestmark = pytest.mark.integration
SOURCE_ID = "gov_wales.wimd2025"


async def _history() -> list[object]:
    async with get_engine().connect() as conn:
        return list(
            (
                await conn.execute(
                    text(
                        "SELECT status, rows_written, notes, finished_at FROM data.loader_run "
                        "WHERE source_id=:sid ORDER BY started_at DESC"
                    ),
                    {"sid": SOURCE_ID},
                )
            ).all()
        )


@pytest.fixture(autouse=True)
async def _clean_history() -> None:
    from pathlib import Path

    from soundings.catalogue.loader import load_catalogue_into_db

    root = Path(__file__).resolve().parents[2]
    await load_catalogue_into_db(
        get_engine(),
        sources_path=root / "catalogue/sources.yaml",
        indicators_path=root / "catalogue/indicators.yaml",
    )
    async with get_engine().begin() as conn:
        await conn.execute(
            text("DELETE FROM data.loader_run WHERE source_id=:sid"), {"sid": SOURCE_ID}
        )


async def test_tracking_records_running_then_complete_result() -> None:
    async def import_data() -> LoaderResult:
        row = (await _history())[0]
        assert row.status == "running"
        assert row.finished_at is None
        return LoaderResult(rows_written=17, notes="complete snapshot")

    result = await run_tracked_loader(get_engine(), SOURCE_ID, import_data)
    row = (await _history())[0]
    assert result.rows_written == row.rows_written == 17
    assert row.status == "ok"
    assert row.notes == "complete snapshot"
    assert row.finished_at is not None


async def test_tracking_does_not_mark_failed_partial_import_successful() -> None:
    async def partial_import() -> LoaderResult:
        raise ValueError("second stage failed")

    with pytest.raises(ValueError, match="second stage failed"):
        await run_tracked_loader(get_engine(), SOURCE_ID, partial_import)
    row = (await _history())[0]
    assert row.status == "failed"
    assert row.rows_written == 0
    assert row.finished_at is not None
    assert "ValueError: second stage failed" in row.notes


async def test_registry_records_results_used_by_scheduled_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = AsyncMock(return_value=LoaderResult(rows_written=23))
    monkeypatch.setattr(loader_run.Wimd2025Loader, "load", load)
    registry = loader_run.build_source_registry(get_engine())
    scheduler = await loader_run.build_scheduler(get_engine(), registry)
    await scheduler.get_job(SOURCE_ID).func()
    row = (await _history())[0]
    assert row.status == "ok"
    assert row.rows_written == 23


async def test_once_uses_same_tracked_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        loader_run.Wimd2025Loader, "load", AsyncMock(return_value=LoaderResult(rows_written=29))
    )
    assert await loader_run._run_once(SOURCE_ID) == 0
    row = (await _history())[0]
    assert row.status == "ok"
    assert row.rows_written == 29


async def test_failed_later_geography_stage_has_no_success_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(text("DELETE FROM data.loader_run WHERE source_id='ons.geography'"))
    monkeypatch.setattr(
        loader_run.OnsGeographyPlacesLoader, "load", AsyncMock(return_value=LoaderResult(12))
    )
    monkeypatch.setattr(
        loader_run.OnsGeographyHierarchyLoader,
        "load",
        AsyncMock(side_effect=ValueError("hierarchy stage failed")),
    )
    with pytest.raises(ValueError, match="hierarchy stage failed"):
        await loader_run.build_source_registry(get_engine())["ons.geography"]()
    async with get_engine().connect() as conn:
        rows = (
            (
                await conn.execute(
                    text("SELECT status FROM data.loader_run WHERE source_id='ons.geography'")
                )
            )
            .scalars()
            .all()
        )
    assert rows == ["failed"]
