import pytest
from sqlalchemy import text

from soundings.db.engine import get_engine
from soundings.loader.run import build_scheduler, build_source_registry

pytestmark = pytest.mark.integration


async def _ensure_loader_sources() -> None:
    """The loader daemon reads from catalogue.source; the lifespan-loaded
    real catalogue covers ons.* and mhclg.* already."""
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT id, refresh_cadence FROM catalogue.source WHERE mode = 'loader'")
            )
        ).all()
    assert len(rows) > 0


async def test_scheduler_schedules_one_job_per_loader_source() -> None:
    await _ensure_loader_sources()
    engine = get_engine()
    sched = await build_scheduler(engine, build_source_registry(engine))
    # Every catalogue.source with mode='loader' gets a job; we don't start
    # the scheduler in the test.
    job_source_ids = {job.id for job in sched.get_jobs()}
    assert "ons.mid_year_estimates" in job_source_ids
    assert "ons.census2021" in job_source_ids
    assert "mhclg.imd2025" in job_source_ids
    assert "ons.geography" in job_source_ids
    assert "corpus.retention" in job_source_ids
    assert "corpus.publication" in job_source_ids


def test_source_registry_returns_callable_for_each_phase_1_loader() -> None:
    engine = get_engine()
    registry = build_source_registry(engine)
    for sid in (
        "ons.geography",
        "ons.mid_year_estimates",
        "ons.census2021",
        "mhclg.imd2025",
    ):
        assert callable(registry[sid])
