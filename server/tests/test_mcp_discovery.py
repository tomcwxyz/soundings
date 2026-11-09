"""Ensure external MCP agents see the same data tools and guidance as Ask."""

import pytest

from soundings.ask.dispatcher import TERMINAL_TOOL, ToolDispatcher
from soundings.mcp.server import build_mcp_server


def test_mcp_has_ask_tool_parity_and_descriptions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("soundings.mcp.server._MCP_SERVER", None)
    # No database or model calls: exercise the tool registration contract.
    mcp = build_mcp_server(state=None)
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    ask_specs = {
        str(spec["name"]): str(spec["description"])
        for spec in ToolDispatcher(state=None).tool_specs()
        if spec.get("name") != TERMINAL_TOOL
    }

    assert ask_specs.keys() <= tools.keys()
    for name, description in ask_specs.items():
        assert description
        assert tools[name].description == description


def test_mcp_indicator_discovery_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("soundings.mcp.server._MCP_SERVER", None)
    mcp = build_mcp_server(state=None)
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    assert "list_indicators" in tools
    discovery = tools["list_indicators"]
    assert "Discover" in (discovery.description or "")
    assert {"query", "domain", "geography_level"} <= set(
        discovery.parameters["properties"]
    )
