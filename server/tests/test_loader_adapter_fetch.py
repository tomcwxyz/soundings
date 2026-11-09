import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from soundings.adapters.base import LoaderAdapter, LoaderResult
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration


class _DummyLoader(LoaderAdapter):
    source_id = "test.dummy.loader"

    async def load(self, run_id: str | None = None) -> LoaderResult:
        return LoaderResult(rows_written=0)


async def _seed_source_indicator_and_value(
    *,
    place_id: str,
    indicator_key: str,
    period: str,
    value: float,
    finished_at: datetime,
    refresh_cadence: str = "0 3 1 * *",  # monthly → ~30 day window
) -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM data.loader_run"))
        await conn.execute(
            text(
                "INSERT INTO catalogue.source "
                "(id, label, publisher, publisher_url, dataset_url, licence, mode, refresh_cadence, rate_limit) "
                "VALUES (:id, :label, :pub, :url, :ds, :lic, 'loader', :cad, '{}'::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET refresh_cadence = EXCLUDED.refresh_cadence"
            ),
            {
                "id": "test.dummy.loader",
                "label": "Dummy Loader",
                "pub": "Test",
                "url": "https://example.invalid/",
                "ds": "https://example.invalid/data",
                "lic": "OGL-UK-3.0",
                "cad": refresh_cadence,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO catalogue.indicator "
                "(key, label, unit, source_id, available_at, refresh_cadence, caveats, related_keys) "
                "VALUES (:k, :l, :u, :s, :avail, :cad, '[]'::jsonb, ARRAY[]::varchar[]) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {
                "k": indicator_key,
                "l": indicator_key,
                "u": "people",
                "s": "test.dummy.loader",
                "avail": [place_id.split(":", 1)[0]],
                "cad": "annual",
            },
        )
        await conn.execute(
            text("DELETE FROM data.trend_point WHERE place_id = :id"), {"id": place_id}
        )
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place WHERE id = :id"), {"id": place_id})
        type_, code = place_id.split(":", 1)
        await conn.execute(
            text("INSERT INTO geography.place (id, type, code, name) VALUES (:id, :t, :c, :n)"),
            {"id": place_id, "t": type_, "c": code, "n": "Test place"},
        )
        run_id = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, finished_at, status, rows_written) "
                "VALUES (:id, :sid, :s, :f, 'ok', 1)"
            ),
            {
                "id": run_id,
                "sid": "test.dummy.loader",
                "s": finished_at - timedelta(minutes=5),
                "f": finished_at,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO data.indicator_value "
                "(place_id, indicator_key, period, value, source_id, retrieved_at, loader_run_id, caveats) "
                "VALUES (:pid, :ik, :p, :v, :sid, :ret, :rid, '[]'::jsonb)"
            ),
            {
                "pid": place_id,
                "ik": indicator_key,
                "p": period,
                "v": value,
                "sid": "test.dummy.loader",
                "ret": finished_at,
                "rid": run_id,
            },
        )


async def test_loader_adapter_fetch_indicator_returns_cached_when_fresh() -> None:
    engine = get_engine()
    fresh = datetime.now(tz=UTC) - timedelta(days=5)
    await _seed_source_indicator_and_value(
        place_id="ltla24:E06000004",
        indicator_key="population.total",
        period="2024",
        value=200000,
        finished_at=fresh,
    )

    loader = _DummyLoader(engine)
    iv = await loader.fetch_indicator("population.total", "ltla24:E06000004", None)
    assert iv is not None
    assert iv.value == 200000
    assert iv.source.cache_status == "cached"
    assert iv.source.source_id == "test.dummy.loader"


async def test_loader_adapter_fetch_indicator_marks_stale_after_1_5x_cadence() -> None:
    engine = get_engine()
    very_old = datetime.now(tz=UTC) - timedelta(days=60)  # > 1.5x monthly
    await _seed_source_indicator_and_value(
        place_id="ltla24:E06000004",
        indicator_key="population.total",
        period="2024",
        value=200000,
        finished_at=very_old,
    )

    loader = _DummyLoader(engine)
    iv = await loader.fetch_indicator("population.total", "ltla24:E06000004", None)
    assert iv is not None
    assert iv.source.cache_status == "stale"


async def test_loader_adapter_fetch_indicator_returns_none_when_missing() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.indicator_value"))

    loader = _DummyLoader(engine)
    iv = await loader.fetch_indicator("population.total", "ltla24:E99999999", None)
    assert iv is None


@pytest.mark.parametrize("has_completed_run", [True, False])
async def test_loader_source_preserves_actual_data_retrieval_time(has_completed_run: bool) -> None:
    engine = get_engine()
    completed = datetime.now(tz=UTC) - timedelta(days=1)
    retrieved = completed - timedelta(days=90)
    await _seed_source_indicator_and_value(
        place_id="ltla24:E06000004",
        indicator_key="population.total",
        period="2024",
        value=200000,
        finished_at=completed,
    )
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE data.indicator_value SET retrieved_at=:retrieved"),
            {"retrieved": retrieved},
        )
        if not has_completed_run:
            await conn.execute(text("DELETE FROM data.loader_run"))
    value = await _DummyLoader(engine).fetch_indicator("population.total", "ltla24:E06000004", None)
    assert value is not None
    assert value.source.retrieved_at == retrieved
    assert value.source.cache_status == ("cached" if has_completed_run else "stale")
