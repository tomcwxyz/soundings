"""MCP server exposing the three Phase 1 tools.

Uses `FastMCP` from the mcp Python SDK. The same tool implementations from
`soundings.tools.*` are used for both transports (HTTP and MCP) — this
module is just the registration boilerplate.

The server is mounted on the FastAPI app at `/mcp` via the SSE sub-app.
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from soundings.tools.compare_places import ComparePlacesInput, compare_places
from soundings.tools.find_organisations_in_place import (
    FindOrganisationsInPlaceInput,
    find_organisations_in_place,
)
from soundings.tools.find_place import FindPlaceInput, find_place
from soundings.tools.get_civil_society_profile import (
    GetCivilSocietyProfileInput,
    get_civil_society_profile,
)
from soundings.tools.get_indicators import GetIndicatorsInput, get_indicators
from soundings.tools.get_observations import (
    GetObservationsInput,
    get_observations,
)
from soundings.tools.get_place_profile import GetPlaceProfileInput, get_place_profile
from soundings.tools.get_trend import GetTrendInput, get_trend

_MCP_SERVER: FastMCP | None = None


def build_mcp_server(state: Any | None = None) -> FastMCP:
    """Build (or return the cached) FastMCP instance.

    `state` is the FastAPI app.state object — passed in by the lifespan so
    tool handlers can reach the orchestrator + geography service. When
    omitted (e.g. in tests), tools are registered against a placeholder
    that raises if invoked.
    """
    global _MCP_SERVER
    if _MCP_SERVER is not None:
        return _MCP_SERVER

    mcp = FastMCP(name="soundings")

    @mcp.tool(name="find_place")
    async def _find_place(query: str, geography_types: list[str] | None = None) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP find_place invoked without app state")
        result = await find_place(
            FindPlaceInput(query=query, geography_types=geography_types),
            state.geography_service,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="get_indicators")
    async def _get_indicators(
        place_id: str,
        indicators: list[str],
        period: str | None = None,
        format: str = "tall",
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP get_indicators invoked without app state")
        result = await get_indicators(
            GetIndicatorsInput(
                place_id=place_id,
                indicators=indicators,
                period=period,
                format=format,
            ),
            state.orchestrator,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="get_observations")
    async def _get_observations(
        place_id: str | None = None,
        theme: str | None = None,
        indicator_key: str | None = None,
        organisation_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP get_observations invoked without app state")
        result = await get_observations(
            GetObservationsInput(
                place_id=place_id,
                theme=theme,
                indicator_key=indicator_key,
                organisation_id=organisation_id,
                limit=limit,
            ),
            state.engine,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="get_place_profile")
    async def _get_place_profile(place_id: str, include: list[str] | None = None) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP get_place_profile invoked without app state")
        result = await get_place_profile(
            GetPlaceProfileInput(place_id=place_id, include=include or []),
            state.orchestrator,
            state.engine,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="compare_places")
    async def _compare_places(
        place_ids: list[str],
        indicators: list[str],
        comparison_basis: str = "percentile",
        period: str | None = None,
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP compare_places invoked without app state")
        result = await compare_places(
            ComparePlacesInput(
                place_ids=place_ids,
                indicators=indicators,
                comparison_basis=comparison_basis,
                period=period,
            ),
            state.orchestrator,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="get_trend")
    async def _get_trend(
        place_id: str,
        indicator: str,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP get_trend invoked without app state")
        result = await get_trend(
            GetTrendInput(
                place_id=place_id,
                indicator=indicator,
                period_from=period_from,
                period_to=period_to,
            ),
            state.orchestrator,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="find_organisations_in_place")
    async def _find_organisations_in_place(
        place_id: str,
        activity_filter: list[str] | None = None,
        funded_only: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP find_organisations_in_place invoked without app state")
        result = await find_organisations_in_place(
            FindOrganisationsInPlaceInput(
                place_id=place_id,
                activity_filter=activity_filter,
                funded_only=funded_only,
                limit=limit,
            ),
            state.orchestrator,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="get_civil_society_profile")
    async def _get_civil_society_profile(place_id: str) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP get_civil_society_profile invoked without app state")
        result = await get_civil_society_profile(
            GetCivilSocietyProfileInput(place_id=place_id),
            state.orchestrator,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="ask")
    async def _ask(
        query: str,
        place_id: str | None = None,
    ) -> dict[str, Any]:
        """Ask a natural-language question about a UK place."""
        if state is None:
            raise RuntimeError("MCP ask invoked without app state")

        from soundings.ask.dispatcher import ToolDispatcher
        from soundings.ask.orchestrator import AskOrchestrator
        from soundings.ask.prompts import SystemPromptBuilder
        from soundings.core.config import get_settings

        settings = get_settings()
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")

        place_name: str | None = None
        if place_id:
            from sqlalchemy import text

            async with state.engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("SELECT name FROM geography.place WHERE id = :id"),
                        {"id": place_id},
                    )
                ).first()
            if row:
                place_name = row.name

        prompt_builder = SystemPromptBuilder(
            place_name=place_name,
            place_id=place_id,
        )
        dispatcher = ToolDispatcher(state)
        orchestrator = AskOrchestrator(
            dispatcher=dispatcher,
            prompt_builder=prompt_builder,
            api_key=settings.anthropic_api_key,
            model=settings.ask_model,
        )

        blocks: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []

        async def callback(event: dict[str, Any]) -> None:
            if event["type"] == "block":
                blocks.append(event["block"])
            elif event["type"] == "sources":
                sources.clear()
                sources.extend(event["sources"])

        await orchestrator.run(query, callback)
        return {"blocks": blocks, "sources": sources}

    _MCP_SERVER = mcp
    return mcp
