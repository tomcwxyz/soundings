"""Integration tests for the /v1/ask endpoint."""

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


async def test_ask_returns_400_on_empty_query() -> None:
    from soundings.app import app

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/v1/ask", json={"query": ""})
    assert response.status_code == 400


def test_sse_watchdog_exceeds_orchestrator_request_timeout() -> None:
    """The per-event SSE wait must stay above the orchestrator's own request
    timeout, or a slow-but-legitimate answer (the richest ones — big
    adaptive-thinking turns producing many blocks) gets killed by the SSE
    watchdog before the orchestrator's own timeout/completion can fire.

    This asserts the relationship instead of a raw number so the two can't
    drift apart again the way they did when REQUEST_TIMEOUT_SECONDS was
    raised 120->180 without updating the SSE watchdog to match.
    """
    from soundings.ask.orchestrator import REQUEST_TIMEOUT_SECONDS
    from soundings.http.ask import SSE_WATCHDOG_SECONDS

    assert SSE_WATCHDOG_SECONDS > REQUEST_TIMEOUT_SECONDS


def test_extract_place_id_from_find_place_result() -> None:
    """_extract_place_id should find a place_id from a find_place tool_result."""
    import json

    from soundings.http.ask import _extract_place_id

    messages = [
        {"role": "user", "content": "Where are the food banks in Leeds?"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "find_place", "input": {"query": "Leeds"}}
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": json.dumps([{"id": "ltla24:E08000035", "name": "Leeds"}]),
                }
            ],
        },
    ]
    assert _extract_place_id(messages) == "ltla24:E08000035"


def test_extract_place_id_from_single_place_result() -> None:
    """_extract_place_id should handle a single dict (not a list)."""
    import json

    from soundings.http.ask import _extract_place_id

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "t1",
                    "content": json.dumps({"id": "ltla24:E06000047", "name": "County Durham"}),
                }
            ],
        },
    ]
    assert _extract_place_id(messages) == "ltla24:E06000047"


def test_extract_place_id_returns_none_when_not_found() -> None:
    """_extract_place_id should return None when no place_id is found."""
    from soundings.http.ask import _extract_place_id

    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]
    assert _extract_place_id(messages) is None


def test_extract_place_id_handles_non_json_tool_result() -> None:
    """_extract_place_id should handle non-JSON tool_result content gracefully."""
    from soundings.http.ask import _extract_place_id

    messages = [
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "not json"}],
        },
    ]
    assert _extract_place_id(messages) is None


async def test_ask_returns_503_without_api_key() -> None:
    from soundings.app import app
    from soundings.core.config import get_settings

    # Temporarily remove the API key
    settings = get_settings()
    original_key = settings.anthropic_api_key
    settings.anthropic_api_key = ""

    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post("/v1/ask", json={"query": "Stockton"})
        assert response.status_code == 503
    finally:
        settings.anthropic_api_key = original_key
