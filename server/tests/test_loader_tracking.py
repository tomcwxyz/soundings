"""Import history describes the complete job, including failed partial work."""

from collections.abc import AsyncIterator
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


_AUDIT_ROWS = (
    "DELETE FROM data.indicator_value "
    "WHERE place_id = 'lsoa21:W01AUDIT1' OR place_id LIKE 'lsoa21:W01IDENT%'"
)


@pytest.fixture(autouse=True)
async def _clean_history() -> AsyncIterator[None]:
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
        await conn.execute(text(_AUDIT_ROWS))
    yield
    # The contract-audit tests deliberately store bad values; leave none
    # behind for other suites that import this source.
    async with get_engine().begin() as conn:
        await conn.execute(text(_AUDIT_ROWS))


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


# --- measure contracts -------------------------------------------------------

WIMD_DECILE = "deprivation.wimd2025.decile"
AUDIT_PLACE = "lsoa21:W01AUDIT1"


async def _seed_audit_value(value: float) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES (:id, 'lsoa21', 'W01AUDIT1', 'Audit LSOA') ON CONFLICT (id) DO NOTHING"
            ),
            {"id": AUDIT_PLACE},
        )
        await conn.execute(
            text(
                "INSERT INTO data.indicator_value "
                "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) "
                "VALUES (:pid, :k, '2025', :v, :sid, now(), '[]'::jsonb) "
                "ON CONFLICT (place_id, indicator_key, period) DO UPDATE SET value = EXCLUDED.value"
            ),
            {"pid": AUDIT_PLACE, "k": WIMD_DECILE, "v": value, "sid": SOURCE_ID},
        )


async def test_import_that_writes_values_outside_their_contract_is_recorded_as_failed() -> None:
    """A loader that finishes without raising can still have written nonsense.
    A decile of 14 must not leave an 'ok' import in the history."""
    from soundings.loader.contract_audit import MeasureContractError

    async def import_data() -> LoaderResult:
        await _seed_audit_value(14)
        return LoaderResult(rows_written=1)

    with pytest.raises(MeasureContractError, match=WIMD_DECILE):
        await run_tracked_loader(get_engine(), SOURCE_ID, import_data)

    row = (await _history())[0]
    assert row.status == "failed"
    assert WIMD_DECILE in row.notes
    assert "above the maximum" in row.notes


async def test_import_within_contract_is_recorded_as_ok() -> None:
    async def import_data() -> LoaderResult:
        await _seed_audit_value(3)
        return LoaderResult(rows_written=1)

    await run_tracked_loader(get_engine(), SOURCE_ID, import_data)

    assert (await _history())[0].status == "ok"


async def test_audit_flags_a_derived_series_identical_to_its_base() -> None:
    """Density equal to population in every row means it was never derived."""
    from soundings.catalogue.contracts import ContractRegistry
    from soundings.catalogue.models import IndicatorModel, MeasureContract
    from soundings.loader.contract_audit import audit_source

    base, derived = "deprivation.wimd2025.rank", WIMD_DECILE
    contracts = ContractRegistry(
        [
            IndicatorModel(
                key=base, label="b", unit="rank", source_id=SOURCE_ID, available_at=["lsoa21"]
            ),
            IndicatorModel(
                key=derived,
                label="d",
                unit="score",
                source_id=SOURCE_ID,
                available_at=["lsoa21"],
                contract=MeasureContract(kind="score", must_differ_from=[base]),
            ),
        ]
    )
    async with get_engine().begin() as conn:
        await conn.execute(
            text("DELETE FROM data.indicator_value WHERE indicator_key IN (:a, :b)"),
            {"a": base, "b": derived},
        )
        for i in range(25):
            pid = f"lsoa21:W01IDENT{i:02d}"
            await conn.execute(
                text(
                    "INSERT INTO geography.place (id, type, code, name) "
                    "VALUES (:id, 'lsoa21', :code, 'Identity LSOA') ON CONFLICT (id) DO NOTHING"
                ),
                {"id": pid, "code": pid.split(":")[1]},
            )
            for key in (base, derived):
                await conn.execute(
                    text(
                        "INSERT INTO data.indicator_value "
                        "(place_id, indicator_key, period, value, source_id, retrieved_at, "
                        "caveats) VALUES (:pid, :k, '2025', :v, :sid, now(), '[]'::jsonb)"
                    ),
                    {"pid": pid, "k": key, "v": 100 + i, "sid": SOURCE_ID},
                )

    violations = await audit_source(get_engine(), SOURCE_ID, contracts)

    assert len(violations) == 1
    assert derived in violations[0] and base in violations[0]
    assert "identical" in violations[0]
