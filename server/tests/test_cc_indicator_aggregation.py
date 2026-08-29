"""Tests for the CC loader's end-of-load indicator aggregation.

After `load()` writes data.organisation, it folds the freshly-loaded
charities into two `data.indicator_value` rows per LTLA:

- `civil_society.active_charities_count` — count of active charities
  with registered_address_place_id matching, from THIS load
  (retrieved_at >= the load's threshold).
- `civil_society.active_charities_per_10k` — the count divided by the
  place's latest population.total × 10_000.

Period = `YYYY-MM` of the load timestamp; CC publishes monthly so
that's the natural cadence.
"""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text

from soundings.adapters.charity_commission.loader import CharityCommissionLoader
from soundings.adapters.postcodes_io.adapter import PostcodesIoAdapter
from soundings.catalogue.loader import load_catalogue_into_db
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def _cleanup() -> AsyncIterator[None]:
    yield
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.organisation_operates_in"))
        await conn.execute(text("DELETE FROM data.organisation"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))


class _StubBulkClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def iter_active_charities(self) -> AsyncIterator[dict[str, Any]]:
        for row in self._rows:
            yield row

    async def iter_area_of_operation(self) -> AsyncIterator[dict[str, Any]]:
        # These loader tests exercise the registered-address fallback.
        # Dedicated client/mapping tests cover area-of-operation parsing.
        for row in []:
            yield row

    async def iter_classifications(self) -> AsyncIterator[dict[str, Any]]:
        # Structured classification loading is covered separately.
        for row in []:
            yield row


async def _seed_places_and_population() -> None:
    """Three LTLAs, each with a known population. Loads the full
    catalogue (sources + indicators) so the FK from data.indicator_value
    has the civil_society.* indicator keys to reference."""
    engine = get_engine()
    # Catalogue loader idempotently seeds catalogue.source + catalogue.indicator.
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent.parent
    await load_catalogue_into_db(
        engine,
        sources_path=repo_root / "catalogue" / "sources.yaml",
        indicators_path=repo_root / "catalogue" / "indicators.yaml",
    )
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.organisation_operates_in"))
        await conn.execute(text("DELETE FROM data.organisation"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        for code, name in [
            ("E06000004", "Stockton-on-Tees"),
            ("E06000001", "Hartlepool"),
            ("E06000002", "Middlesbrough"),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO geography.place (id, type, code, name) "
                    "VALUES (:id, 'ltla24', :code, :name)"
                ),
                {"id": f"ltla24:{code}", "code": code, "name": name},
            )
        for postcode, ltla_code in [
            ("TS181AB", "E06000004"),
            ("TS261AB", "E06000001"),
            ("TS11AB", "E06000002"),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO geography.postcode "
                    "(postcode, ltla24, retrieved_at) VALUES (:p, :ltla, NOW())"
                ),
                {"p": postcode, "ltla": f"ltla24:{ltla_code}"},
            )
        # Populations: Stockton 200k, Hartlepool 100k, Middlesbrough 150k.
        for ltla_code, pop in [
            ("E06000004", 200_000),
            ("E06000001", 100_000),
            ("E06000002", 150_000),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO data.indicator_value "
                    "(place_id, indicator_key, period, value, source_id, "
                    "retrieved_at, caveats) VALUES "
                    "(:pid, 'population.total', '2024', :val, "
                    "'ons.mid_year_estimates', NOW(), '[]'::jsonb)"
                ),
                {"pid": f"ltla24:{ltla_code}", "val": pop},
            )


def _charities(
    *,
    stockton: int = 0,
    hartlepool: int = 0,
    middlesbrough: int = 0,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    counter = 1000
    for postcode, n in [
        ("TS18 1AB", stockton),
        ("TS26 1AB", hartlepool),
        ("TS1 1AB", middlesbrough),
    ]:
        for _ in range(n):
            rows.append(
                {
                    "registration_number": str(counter),
                    "name": f"Charity {counter}",
                    "postcode": postcode,
                    "status": "Registered",
                    "classification": [],
                }
            )
            counter += 1
    return rows


async def test_aggregation_writes_active_charities_count_per_ltla() -> None:
    await _seed_places_and_population()
    bulk = _StubBulkClient(_charities(stockton=8, hartlepool=3, middlesbrough=5))
    postcodes_io = PostcodesIoAdapter(get_engine(), ttl=timedelta(hours=720))
    loader = CharityCommissionLoader(get_engine(), bulk_client=bulk, postcodes_io=postcodes_io)
    await loader.load()

    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT place_id, value FROM data.indicator_value "
                    "WHERE indicator_key = 'civil_society.active_charities_count' "
                    "ORDER BY place_id"
                )
            )
        ).all()
    by_pid = {r.place_id: float(r.value) for r in rows}
    assert by_pid["ltla24:E06000001"] == 3.0
    assert by_pid["ltla24:E06000002"] == 5.0
    assert by_pid["ltla24:E06000004"] == 8.0


async def test_aggregation_writes_per_10k_using_population() -> None:
    """Stockton: 8 charities / 200_000 population × 10_000 = 0.4 per 10k.
    Hartlepool: 3 / 100_000 × 10_000 = 0.3.
    Middlesbrough: 5 / 150_000 × 10_000 ≈ 0.333."""
    await _seed_places_and_population()
    bulk = _StubBulkClient(_charities(stockton=8, hartlepool=3, middlesbrough=5))
    postcodes_io = PostcodesIoAdapter(get_engine(), ttl=timedelta(hours=720))
    loader = CharityCommissionLoader(get_engine(), bulk_client=bulk, postcodes_io=postcodes_io)
    await loader.load()

    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT place_id, value FROM data.indicator_value "
                    "WHERE indicator_key = 'civil_society.active_charities_per_10k' "
                    "ORDER BY place_id"
                )
            )
        ).all()
    by_pid = {r.place_id: float(r.value) for r in rows}
    assert by_pid["ltla24:E06000004"] == pytest.approx(0.4)
    assert by_pid["ltla24:E06000001"] == pytest.approx(0.3)
    assert by_pid["ltla24:E06000002"] == pytest.approx(0.3333, abs=1e-3)


async def test_aggregation_skips_per_10k_for_places_without_population() -> None:
    """If a place has no population.total row, we still write the
    `_count` indicator for it but skip the per_10k. Logged in
    loader_run.notes per the design."""
    await _seed_places_and_population()
    # Wipe Stockton's population specifically.
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM data.indicator_value "
                "WHERE indicator_key = 'population.total' "
                "AND place_id = 'ltla24:E06000004'"
            )
        )
    bulk = _StubBulkClient(_charities(stockton=8, hartlepool=3))
    postcodes_io = PostcodesIoAdapter(get_engine(), ttl=timedelta(hours=720))
    loader = CharityCommissionLoader(get_engine(), bulk_client=bulk, postcodes_io=postcodes_io)
    await loader.load()

    async with get_engine().connect() as conn:
        per_10k_rows = (
            await conn.execute(
                text(
                    "SELECT place_id FROM data.indicator_value "
                    "WHERE indicator_key = 'civil_society.active_charities_per_10k'"
                )
            )
        ).all()
        count_rows = (
            await conn.execute(
                text(
                    "SELECT place_id FROM data.indicator_value "
                    "WHERE indicator_key = 'civil_society.active_charities_count'"
                )
            )
        ).all()

    per_10k_pids = {r.place_id for r in per_10k_rows}
    count_pids = {r.place_id for r in count_rows}
    # Stockton has the count but not per_10k (no population).
    assert "ltla24:E06000004" in count_pids
    assert "ltla24:E06000004" not in per_10k_pids
    # Hartlepool has both.
    assert "ltla24:E06000001" in count_pids
    assert "ltla24:E06000001" in per_10k_pids


async def test_aggregation_re_runs_idempotently() -> None:
    """Two loads with different counts → indicator_value reflects the
    latest. UPSERT, not insert-only."""
    await _seed_places_and_population()
    postcodes_io = PostcodesIoAdapter(get_engine(), ttl=timedelta(hours=720))
    # First load: 5 charities in Stockton.
    bulk1 = _StubBulkClient(_charities(stockton=5))
    await CharityCommissionLoader(get_engine(), bulk_client=bulk1, postcodes_io=postcodes_io).load()
    # Second load: 8 charities in Stockton.
    bulk2 = _StubBulkClient(_charities(stockton=8))
    await CharityCommissionLoader(get_engine(), bulk_client=bulk2, postcodes_io=postcodes_io).load()

    async with get_engine().connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT value FROM data.indicator_value "
                    "WHERE place_id = 'ltla24:E06000004' "
                    "AND indicator_key = 'civil_society.active_charities_count'"
                )
            )
        ).first()
    assert row is not None
    assert float(row.value) == 8.0  # second load's count, not stacked
