"""Live test for the /v1/ask endpoint — real Claude call.

Nightly only. Requires ANTHROPIC_API_KEY in env.
"""

import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from soundings.app import app
from soundings.db.engine import get_engine

pytestmark = [pytest.mark.live, pytest.mark.integration]


async def _seed_stockton() -> None:
    engine = get_engine()
    now = datetime.now(tz=UTC)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM data.loader_run"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        run = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES ('ltla24:E06000004', 'ltla24', 'E06000004',"
                " 'Stockton-on-Tees')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO data.loader_run "
                "(id, source_id, started_at, finished_at, status,"
                " rows_written) "
                "VALUES (:id, 'ons.mid_year_estimates', :s, :f, 'ok', 1)"
            ),
            {"id": run, "s": now - timedelta(minutes=5), "f": now},
        )
        await conn.execute(
            text(
                "INSERT INTO data.indicator_value "
                "(place_id, indicator_key, period, value, source_id,"
                " retrieved_at, caveats) "
                "VALUES ('ltla24:E06000004', 'population.total', '2024',"
                " 200000, 'ons.mid_year_estimates', :ret, '[]'::jsonb)"
            ),
            {"ret": now},
        )


async def test_ask_summary_returns_text_and_indicator_blocks() -> None:
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not configured for live Ask test")
    await _seed_stockton()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/v1/ask",
                json={
                    "query": "Summarise Stockton-on-Tees",
                    "place_id": "ltla24:E06000004",
                },
            )
    assert response.status_code == 200
    # Parse SSE events
    events: list[dict[str, object]] = []
    for line in response.text.split("\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    # Should have at least one text block
    block_events = [e for e in events if e.get("type") == "block"]
    text_blocks = [
        e
        for e in block_events
        if isinstance(e.get("block"), dict) and e["block"].get("type") == "text"
    ]
    assert len(text_blocks) >= 1

    # Should have a done event
    assert any(e.get("type") == "done" for e in events)
