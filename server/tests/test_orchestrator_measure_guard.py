"""Values that break their measure contract are withheld, not served.

Reproduces the 9 November 2026 evaluation failures at the orchestrator: an
unemployment "rate" of 66.5 under a proportion unit must never reach an
answer, whichever tool asks for it.
"""

from datetime import UTC, datetime
from typing import ClassVar

import pytest
from sqlalchemy import text

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.catalogue.contracts import ContractRegistry
from soundings.catalogue.models import IndicatorModel
from soundings.contracts.indicator_value import IndicatorValue
from soundings.contracts.source_ref import SourceRef
from soundings.db.engine import get_engine
from soundings.orchestration.measure_guard import WITHHELD_CODE
from soundings.orchestration.orchestrator import IndicatorOrchestrator
from soundings.orchestration.registry import AdapterRegistry

pytestmark = pytest.mark.integration

SOURCE = "test.guard"
RATE = "test.guard.unemployment_rate"
COUNT = "test.guard.claimants"
PLACE = "ltla24:E06000004"
OTHER = "ltla24:E06000005"


def _contracts() -> ContractRegistry:
    return ContractRegistry(
        [
            IndicatorModel(
                key=RATE, label="r", unit="proportion", source_id=SOURCE, available_at=["ltla24"]
            ),
            IndicatorModel(
                key=COUNT, label="c", unit="people", source_id=SOURCE, available_at=["ltla24"]
            ),
        ]
    )


class _Adapter(LoaderAdapter):
    source_id = SOURCE
    values: ClassVar[dict[str, float]] = {RATE: 66.5, COUNT: 1200.0}

    async def load(self, run_id: str | None = None) -> LoaderResult:
        return LoaderResult(rows_written=0)

    def get_source_ref(self, *, retrieved_at: datetime, cache_status: str) -> SourceRef:  # type: ignore[override]
        return SourceRef(
            source_id=SOURCE,
            source_label=SOURCE,
            publisher="Test",
            retrieved_at=retrieved_at,
            cache_status="cached",
            licence="CC0",
        )

    async def fetch_indicator(
        self, indicator_key: str, place_id: str, period: str | None
    ) -> IndicatorValue | None:
        return IndicatorValue(
            place_id=place_id,
            indicator=indicator_key,
            value=self.values[indicator_key],
            unit="proportion" if indicator_key == RATE else "people",
            period=period or "2025",
            source=self.get_source_ref(retrieved_at=datetime.now(tz=UTC), cache_status="cached"),
            confidence="official",
        )


async def _seed() -> IndicatorOrchestrator:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO catalogue.source (id, label, publisher, licence, mode, rate_limit) "
                "VALUES (:sid, 'guard', 'Test', 'CC0', 'loader', '{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"sid": SOURCE},
        )
        for key, unit in ((RATE, "proportion"), (COUNT, "people")):
            await conn.execute(
                text(
                    "INSERT INTO catalogue.indicator "
                    "(key, label, unit, source_id, available_at, caveats, related_keys) "
                    "VALUES (:k, 'guard', :u, :sid, ARRAY['ltla24'], '[]'::jsonb, "
                    "ARRAY[]::varchar[]) ON CONFLICT (key) DO UPDATE SET unit = EXCLUDED.unit"
                ),
                {"k": key, "u": unit, "sid": SOURCE},
            )
        for pid, name in ((PLACE, "Stockton-on-Tees"), (OTHER, "Darlington")):
            await conn.execute(
                text(
                    "INSERT INTO geography.place (id, type, code, name) "
                    "VALUES (:id, 'ltla24', :code, :name) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": pid, "code": pid.split(":")[1], "name": name},
            )
        await conn.execute(
            text("DELETE FROM data.indicator_value WHERE indicator_key IN (:a, :b)"),
            {"a": RATE, "b": COUNT},
        )
        await conn.execute(
            text("DELETE FROM data.trend_point WHERE indicator_key = :a"), {"a": RATE}
        )
    registry = AdapterRegistry(engine)
    registry.register(SOURCE, lambda eng: _Adapter(eng))
    return IndicatorOrchestrator(engine, registry, contracts=_contracts())


async def test_fetch_withholds_a_value_that_breaks_its_contract() -> None:
    orchestrator = await _seed()

    result = await orchestrator.fetch([RATE, COUNT], PLACE, None)

    assert [v.indicator for v in result.values] == [COUNT]
    assert result.partial is True
    withheld = [c for c in result.caveats if c.startswith(WITHHELD_CODE)]
    assert len(withheld) == 1
    assert RATE in withheld[0]
    # The bad number itself is never repeated to the model.
    assert "66.5" not in withheld[0]


async def test_compare_drops_peer_values_that_break_the_contract() -> None:
    orchestrator = await _seed()
    engine = get_engine()
    async with engine.begin() as conn:
        for pid, value in ((PLACE, 66.5), (OTHER, 0.041)):
            await conn.execute(
                text(
                    "INSERT INTO data.indicator_value "
                    "(place_id, indicator_key, period, value, source_id, retrieved_at, caveats) "
                    "VALUES (:pid, :k, '2025', :v, :sid, now(), '[]'::jsonb)"
                ),
                {"pid": pid, "k": RATE, "v": value, "sid": SOURCE},
            )

    result = await orchestrator.compare_places(
        place_ids=[PLACE, OTHER], indicators=[RATE], basis="absolute"
    )

    by_place = {v.place_id: v.value for v in result.comparisons[0].values}
    assert by_place[PLACE] is None
    assert by_place[OTHER] == pytest.approx(0.041)
    assert any(c.startswith(WITHHELD_CODE) for c in result.caveats)
    assert result.partial is True


async def test_trend_points_that_break_the_contract_are_removed() -> None:
    orchestrator = await _seed()
    engine = get_engine()
    async with engine.begin() as conn:
        for period, value in (("2023", 0.039), ("2024", 71.2), ("2025", 0.044)):
            await conn.execute(
                text(
                    "INSERT INTO data.trend_point "
                    "(place_id, indicator_key, period, value, revised, source_id, retrieved_at) "
                    "VALUES (:pid, :k, :p, :v, false, :sid, now())"
                ),
                {"pid": PLACE, "k": RATE, "p": period, "v": value, "sid": SOURCE},
            )

    result = await orchestrator.get_trend(indicator_key=RATE, place_id=PLACE)

    assert result.trend is not None
    assert [p.period for p in result.trend.points] == ["2023", "2025"]
    assert any(c.startswith(WITHHELD_CODE) for c in result.caveats)
    assert result.partial is True
