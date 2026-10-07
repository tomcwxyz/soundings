import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from soundings.app import app
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration


async def _seed_recent_loader_runs() -> None:
    """Stamp a recent successful run for every loader-mode source so the
    healthz loader_runs check doesn't drag the overall status to degraded
    on a fresh DB."""
    engine = get_engine()
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        sources = (
            await conn.execute(text("SELECT id FROM catalogue.source WHERE mode = 'loader'"))
        ).all()
        for row in sources:
            await conn.execute(
                text(
                    "INSERT INTO data.loader_run "
                    "(id, source_id, started_at, finished_at, status, rows_written) "
                    "VALUES (:id, :sid, :s, :f, 'ok', 1)"
                ),
                {
                    "id": uuid.uuid4(),
                    "sid": row.id,
                    "s": now - timedelta(minutes=5),
                    "f": now,
                },
            )
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, finished_at, status, rows_written, notes) "
                "VALUES (:id, 'threesixtygiving', :s, :f, 'ok', 1, "
                "'grant_index_scope=full')"
            ),
            {
                "id": uuid.uuid4(),
                "s": now - timedelta(minutes=5),
                "f": now,
            },
        )


async def test_healthz_returns_ok_when_db_catalogue_and_loaders_fresh() -> None:
    # Clean any stuck pending records left by other integration tests.
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM corpus.raw_record"))
        await conn.execute(text("DELETE FROM corpus.question_record"))
    async with app.router.lifespan_context(app):
        await _seed_recent_loader_runs()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["postgres"] == "ok"
    assert body["checks"]["catalogue"] == "ok"
    assert body["checks"]["loader_runs"] == "ok"
    assert body["checks"]["grant_index"] == "ok"
    assert body["checks"]["capture"] == "ok"
    assert body["status"] == "ok"


async def test_healthz_degrades_when_loader_runs_are_stale() -> None:
    # No loader_runs seeded → every loader source is stale.
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.loader_run"))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/healthz")
    body = response.json()
    assert body["status"] == "degraded"
    assert "stale" in body["checks"]["loader_runs"]


async def test_healthz_degrades_when_full_grant_index_is_missing() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "DELETE FROM data.loader_run "
                "WHERE source_id = 'threesixtygiving' "
                "AND notes LIKE '%grant_index_scope=full%'"
            )
        )
    async with app.router.lifespan_context(app):
        await _seed_recent_loader_runs()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM data.loader_run "
                    "WHERE source_id = 'threesixtygiving' "
                    "AND notes LIKE '%grant_index_scope=full%'"
                )
            )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/healthz")
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["grant_index"].startswith("incomplete:")


async def test_healthz_degrades_when_capture_backlog_is_large() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM corpus.raw_record"))
        await conn.execute(text("DELETE FROM corpus.question_record"))
        # Seed > 100 records pending and > 1 hour old to trip the stuck check.
        old = datetime.now(tz=UTC) - timedelta(hours=2)
        for _ in range(101):
            await conn.execute(
                text(
                    "INSERT INTO corpus.question_record ("
                    "id, timestamp, session_id, consent_version, capture_level, "
                    "tool_called, tool_inputs_redacted, geography_referenced, "
                    "indicators_returned, sources_used, result_status, gap_signals, "
                    "review_status"
                    ") VALUES ("
                    ":id, :ts, :sid, 'v1.0', 'minimal', 'find_place', "
                    "'{}'::jsonb, '{}'::jsonb, ARRAY[]::varchar[], ARRAY[]::varchar[], "
                    "'ok', ARRAY[]::varchar[], 'pending'"
                    ")"
                ),
                {"id": uuid.uuid4(), "ts": old, "sid": uuid.uuid4()},
            )
    async with app.router.lifespan_context(app):
        await _seed_recent_loader_runs()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/healthz")
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["capture"].startswith(("stuck", "backlog"))
    # Cleanup so the next test doesn't inherit the backlog.
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM corpus.question_record"))
