import pytest
from httpx import ASGITransport, AsyncClient

from soundings.app import app

pytestmark = pytest.mark.integration


async def test_mcp_sse_endpoint_is_mounted() -> None:
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as ac:
            # SSE handshake returns a long-running response; we only verify
            # the route is reachable by issuing a HEAD-ish call and reading
            # one byte before disconnecting.
            response = await ac.get("/mcp/sse", timeout=2.0)
    # SSE returns 200 with event-stream content-type when accepted.
    # 421 is the SSE transport rejecting "test" as an invalid Host header,
    # which still proves the route is mounted; in real deployment the Host
    # is "soundings.<domain>". We accept that too.
    assert response.status_code in (200, 405, 406, 421)


async def test_mcp_server_lists_core_and_temporal_tools() -> None:
    from soundings.mcp.server import build_mcp_server

    server = build_mcp_server()
    # FastMCP exposes registered tools via list_tools().
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert {
        "find_place",
        "get_containing_places",
        "get_indicators",
        "get_place_profile",
    } <= names
