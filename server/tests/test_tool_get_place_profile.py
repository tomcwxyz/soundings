import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.adapters.mhclg_imd2025.adapter import MhclgImd2025Adapter
from soundings.adapters.ons_mid_year_estimates.adapter import OnsMidYearEstimatesAdapter
from soundings.db.engine import get_engine
from soundings.orchestration.orchestrator import IndicatorOrchestrator
from soundings.orchestration.registry import AdapterRegistry
from soundings.tools.get_place_profile import GetPlaceProfileInput, get_place_profile

pytestmark = pytest.mark.integration


class _BenchmarkTestAdapter(LoaderAdapter):
    """Reads from data.indicator_value, like every real loader adapter.
    Used only by the benchmark tests below."""

    source_id = "benchmark_test.source"

    async def load(self, run_id: str | None = None) -> LoaderResult:  # pragma: no cover
        return LoaderResult(rows_written=0)


async def _seed() -> None:
    engine = get_engine()
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM data.loader_run"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES ('ltla24:E06000004', 'ltla24', 'E06000004', 'Stockton-on-Tees')"
            )
        )
        run = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, finished_at, status, rows_written) "
                "VALUES (:id, 'ons.mid_year_estimates', :s, :f, 'ok', 1)"
            ),
            {"id": run, "s": now - timedelta(minutes=5), "f": now},
        )
        await conn.execute(
            text(
                "INSERT INTO data.indicator_value "
                "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) VALUES "
                "('ltla24:E06000004', 'population.total', '2024', 200000, 'ons.mid_year_estimates', :ret, '[]'::jsonb)"
            ),
            {"ret": now},
        )


async def test_get_place_profile_resolves_population_domain() -> None:
    engine = get_engine()
    await _seed()

    registry = AdapterRegistry(engine)
    registry.register("ons.mid_year_estimates", OnsMidYearEstimatesAdapter)
    registry.register("mhclg.imd2025", MhclgImd2025Adapter)
    orchestrator = IndicatorOrchestrator(engine, registry)

    result = await get_place_profile(
        GetPlaceProfileInput(place_id="ltla24:E06000004", include=["population"]),
        orchestrator,
        engine,
    )
    assert result.place.id == "ltla24:E06000004"
    assert result.place.name == "Stockton-on-Tees"
    by_key = {v.indicator: v.value for v in result.indicators}
    assert by_key.get("population.total") == 200000
    # Every indicator value carries an explicit confidence label.
    assert all(
        iv.confidence in ("official", "modelled", "experimental") for iv in result.indicators
    )
    # MYE is a loader-mode source publishing official statistics.
    assert result.indicators[0].confidence == "official"


async def _seed_benchmark_universe(
    *,
    queried_value: float,
    peer_values: list[float],
    region_value: float,
    higher_is: str,
) -> None:
    """Seed an indicator with a queried LTLA + N LTLA peers + one region.

    The region is a different `place.type` and must be excluded from the
    peer universe. The queried place must not appear in its own denominator.
    """
    engine = get_engine()
    now = datetime.now(tz=UTC)
    queried_id = "ltla24:E06BENCH"
    peer_ids = [f"ltla24:E06PEER{i}" for i in range(len(peer_values))]
    region_id = "rgn24:E12000001"
    indicator_key = "benchmark_test.metric"
    source_id = "benchmark_test.source"

    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM data.loader_run"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        await conn.execute(
            text("DELETE FROM catalogue.indicator WHERE key = :k OR key LIKE 'benchmark_test.%'"),
            {"k": indicator_key},
        )
        await conn.execute(text("DELETE FROM catalogue.source WHERE id = :s"), {"s": source_id})
        await conn.execute(
            text(
                "INSERT INTO catalogue.source "
                "(id, label, publisher, licence, mode, rate_limit) "
                "VALUES (:s, 'bench', 'bench', 'CC0', 'loader', '{}'::jsonb)"
            ),
            {"s": source_id},
        )
        await conn.execute(
            text(
                "INSERT INTO catalogue.indicator "
                "(key, label, unit, higher_is, source_id, available_at, "
                " caveats, related_keys) "
                "VALUES (:k, 'bench', 'unit', :hi, :s, ARRAY['ltla24']::varchar[], "
                "'[]'::jsonb, ARRAY[]::varchar[])"
            ),
            {"k": indicator_key, "hi": higher_is, "s": source_id},
        )
        for pid, ptype, name in [
            (queried_id, "ltla24", "Queried Place"),
            (region_id, "rgn24", "Region"),
            *((p, "ltla24", f"Peer {i}") for i, p in enumerate(peer_ids)),
        ]:
            await conn.execute(
                text("INSERT INTO geography.place (id, type, code, name) VALUES (:id, :t, :c, :n)"),
                {"id": pid, "t": ptype, "c": pid.split(":")[1], "n": name},
            )
        run = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, finished_at, status, rows_written) "
                "VALUES (:id, :s, :st, :ft, 'ok', 1)"
            ),
            {"id": run, "s": source_id, "st": now - timedelta(minutes=5), "ft": now},
        )
        rows = [
            (queried_id, queried_value),
            (region_id, region_value),
            *zip(peer_ids, peer_values, strict=True),
        ]
        for pid, val in rows:
            await conn.execute(
                text(
                    "INSERT INTO data.indicator_value "
                    "(place_id, indicator_key, period, value, source_id, "
                    " retrieved_at, caveats) "
                    "VALUES (:pid, :k, '2024', :v, :s, :ret, '[]'::jsonb)"
                ),
                {"pid": pid, "k": indicator_key, "v": val, "s": source_id, "ret": now},
            )


async def test_get_place_profile_attaches_peer_filtered_benchmark() -> None:
    """benchmark_percentile uses same-type peers only, excluding self.

    Queried value 25 vs LTLA peers (10, 20, 30, 40): two below, four total →
    p50. The region's value of 9999 must not pull the rank down; the
    queried place's own value must not appear in the denominator.
    """
    engine = get_engine()
    await _seed_benchmark_universe(
        queried_value=25,
        peer_values=[10, 20, 30, 40],
        region_value=9999,
        higher_is="worse",
    )

    registry = AdapterRegistry(engine)
    registry.register("benchmark_test.source", _BenchmarkTestAdapter)
    orchestrator = IndicatorOrchestrator(engine, registry)

    result = await get_place_profile(
        GetPlaceProfileInput(place_id="ltla24:E06BENCH", include=["benchmark_test"]),
        orchestrator,
        engine,
    )

    assert len(result.indicators) == 1
    ind = result.indicators[0]
    assert ind.indicator == "benchmark_test.metric"
    assert ind.value == 25
    assert ind.benchmark_percentile == 50.0
    assert ind.higher_is == "worse"


async def test_get_place_profile_benchmark_none_when_no_peers() -> None:
    """A place with no same-type peers gets None, not zero."""
    engine = get_engine()
    await _seed_benchmark_universe(
        queried_value=42,
        peer_values=[],
        region_value=999,
        higher_is="better",
    )

    registry = AdapterRegistry(engine)
    registry.register("benchmark_test.source", _BenchmarkTestAdapter)
    orchestrator = IndicatorOrchestrator(engine, registry)

    result = await get_place_profile(
        GetPlaceProfileInput(place_id="ltla24:E06BENCH", include=["benchmark_test"]),
        orchestrator,
        engine,
    )

    assert len(result.indicators) == 1
    assert result.indicators[0].benchmark_percentile is None
    assert result.indicators[0].higher_is == "better"


@pytest.mark.integration
async def test_profile_percentiles_ignore_peer_values_that_break_their_contract() -> None:
    from datetime import UTC, datetime

    from sqlalchemy import text

    from soundings.tools.get_place_profile import _compute_percentiles

    engine = get_engine()
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        for i, rate in enumerate([0.60, 0.70, 66.0, 71.0, 75.0]):
            pid = f"ltla24:PROF{i:02d}"
            await conn.execute(
                text(
                    "INSERT INTO geography.place (id, type, code, name) "
                    "VALUES (:id, 'ltla24', :code, 'p')"
                ),
                {"id": pid, "code": f"E2{i:07d}"},
            )
            await conn.execute(
                text(
                    "INSERT INTO data.indicator_value "
                    "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) "
                    "VALUES (:pid, 'economy.employment_rate', '2024', :v, 'ons.aps', :ret, "
                    "'[]'::jsonb)"
                ),
                {"pid": pid, "v": rate, "ret": now},
            )
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES ('ltla24:PROF09', 'ltla24', 'E2PROF09', 'queried')"
            )
        )

    # The queried place is 0.65: above one valid peer and below the other.
    # Counting the three bad peers would put it at 25.
    out = await _compute_percentiles(engine, "ltla24:PROF09", [("economy.employment_rate", 0.65)])

    assert out["economy.employment_rate"] == pytest.approx(50.0)
