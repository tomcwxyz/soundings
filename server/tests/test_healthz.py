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


async def test_healthz_reports_configuration_gap_even_when_loaders_are_fresh() -> None:
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
    assert body["checks"]["loader_runs"] == "unavailable: mhclg.live_tables"
    assert body["checks"]["grant_index"] == "ok"
    assert body["checks"]["capture"] == "ok"
    assert body["status"] == "degraded"


async def test_healthz_distinguishes_missing_import_history() -> None:
    # No history cannot prove that existing data was never imported.
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.loader_run"))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.get("/healthz")
    body = response.json()
    assert body["status"] == "degraded"
    assert "untracked:" in body["checks"]["loader_runs"]
    assert "stale:" not in body["checks"]["loader_runs"]


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


async def test_freshness_separates_unknown_history_from_stale_and_failed() -> None:
    from soundings.http.health import _loader_freshness_issues

    async with app.router.lifespan_context(app):
        engine = get_engine()
        now = datetime.now(tz=UTC)
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM data.loader_run"))
            await conn.execute(
                text(
                    "INSERT INTO catalogue.source "
                    "(id,label,publisher,licence,mode,refresh_cadence,rate_limit) "
                    "VALUES ('test.health.fixture','test','test','CC0','loader','0 0 * * *','{}') "
                    "ON CONFLICT(id) DO NOTHING"
                )
            )
            for sid, status, age in (
                ("mhclg.imd2025", "ok", 70),
                ("companies_house", "ok", 1),
                ("companies_house", "failed", 0),
            ):
                timestamp = now - timedelta(days=age)
                await conn.execute(
                    text(
                        "INSERT INTO data.loader_run "
                        "(id,source_id,status,started_at,finished_at,rows_written) "
                        "VALUES (:id,:sid,:status,:ts,:ts,1)"
                    ),
                    {"id": uuid.uuid4(), "sid": sid, "status": status, "ts": timestamp},
                )
        issues = await _loader_freshness_issues(engine)
        assert "mhclg.imd2025" in issues["stale"]
        assert "companies_house" in issues["failed"]
        assert "ons.nspl" in issues["untracked"]
        assert "mhclg.live_tables" in issues["unavailable"]
        all_ids = {sid for sources in issues.values() for sid in sources}
        assert "ctx.manual_signup" not in all_ids
        assert "threesixtygiving" not in all_ids
        assert not any(sid.startswith("test.") for sid in all_ids)
