"""MCP server exposing Soundings tools.

Uses ``FastMCP`` from the MCP Python SDK. The same implementations from
``soundings.tools.*`` back HTTP, MCP and the in-process Ask dispatcher.
"""

from datetime import date
from typing import Any
from urllib.parse import urlsplit

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import text

from soundings.ask.dispatcher import ToolDispatcher
from soundings.contracts.observation import GetObservationsInput
from soundings.core.config import get_settings
from soundings.tools.compare_places import ComparePlacesInput, compare_places
from soundings.tools.detect_insights import DetectInsightsInput, detect_insights
from soundings.tools.find_organisations_in_place import (
    FindOrganisationsInPlaceInput,
    find_organisations_in_place,
)
from soundings.tools.find_place import FindPlaceInput, find_place
from soundings.tools.get_change import GetChangeInput, get_change
from soundings.tools.get_civil_society_profile import (
    GetCivilSocietyProfileInput,
    get_civil_society_profile,
)
from soundings.tools.get_containing_places import (
    BoundaryMode,
    GetContainingPlacesInput,
    get_containing_places,
)
from soundings.tools.get_funder_profile import (
    GetFunderProfileInput,
    get_funder_profile,
)
from soundings.tools.get_indicators import GetIndicatorsInput, get_indicators
from soundings.tools.get_observations import get_observations
from soundings.tools.get_peer_distribution import GetPeerDistributionInput, get_peer_distribution
from soundings.tools.get_place_profile import GetPlaceProfileInput, get_place_profile
from soundings.tools.get_sub_areas import GetSubAreasInput, get_sub_areas
from soundings.tools.get_trend import GetTrendInput, get_trend
from soundings.tools.search_grants import SearchGrantsInput, search_grants

# Ask and MCP share human-readable guidance: no second catalogue of prose.
_TOOL_DESCRIPTIONS = {
    str(spec["name"]): str(spec["description"])
    for spec in ToolDispatcher(state=None).tool_specs()
    if spec.get("name") != "compose_answer"
}

_MCP_SERVER: FastMCP | None = None


def build_mcp_server(state: Any | None = None) -> FastMCP:
    """Build (or return the cached) FastMCP instance."""
    global _MCP_SERVER
    if _MCP_SERVER is not None:
        return _MCP_SERVER

    # The reverse proxy preserves the public host; allow only the configured
    # site alongside loopback hosts rather than disabling rebinding protection.
    site_origin = get_settings().ui_origin.rstrip("/")
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*", urlsplit(site_origin).netloc],
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
            site_origin,
        ],
    )
    mcp = FastMCP(name="soundings", transport_security=security)

    @mcp.tool(name="find_place", description=_TOOL_DESCRIPTIONS["find_place"])
    async def _find_place(
        query: str,
        geography_types: list[str] | None = None,
        as_of: date | None = None,
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP find_place invoked without app state")
        result = await find_place(
            FindPlaceInput(query=query, geography_types=geography_types, as_of=as_of),
            state.geography_service,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="get_containing_places", description=_TOOL_DESCRIPTIONS["get_containing_places"])
    async def _get_containing_places(
        place_id: str,
        boundary_mode: BoundaryMode = "current_boundary",
        as_of: date | None = None,
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP get_containing_places invoked without app state")
        result = await get_containing_places(
            GetContainingPlacesInput.model_validate(
                {
                    "place_id": place_id,
                    "boundary_mode": boundary_mode,
                    "as_of": as_of,
                }
            ),
            state.geography_service,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="get_indicators", description=_TOOL_DESCRIPTIONS["get_indicators"])
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

    @mcp.tool(name="get_observations", description=_TOOL_DESCRIPTIONS["get_observations"])
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

    @mcp.tool(name="get_place_profile", description=_TOOL_DESCRIPTIONS["get_place_profile"])
    async def _get_place_profile(place_id: str, include: list[str] | None = None) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP get_place_profile invoked without app state")
        result = await get_place_profile(
            GetPlaceProfileInput(place_id=place_id, include=include or []),
            state.orchestrator,
            state.engine,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="compare_places", description=_TOOL_DESCRIPTIONS["compare_places"])
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

    @mcp.tool(name="get_trend", description=_TOOL_DESCRIPTIONS["get_trend"])
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

    @mcp.tool(name="get_change", description=_TOOL_DESCRIPTIONS["get_change"])
    async def _get_change(
        place_id: str,
        indicator: str,
        period_from: str | None = None,
        period_to: str | None = None,
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP get_change invoked without app state")
        result = await get_change(
            GetChangeInput(
                place_id=place_id,
                indicator=indicator,
                period_from=period_from,
                period_to=period_to,
            ),
            state.orchestrator,
        )
        return result.model_dump(mode="json")

    @mcp.tool(
        name="find_organisations_in_place",
        description=_TOOL_DESCRIPTIONS["find_organisations_in_place"],
    )
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

    @mcp.tool(
        name="get_civil_society_profile",
        description=_TOOL_DESCRIPTIONS["get_civil_society_profile"],
    )
    async def _get_civil_society_profile(place_id: str) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP get_civil_society_profile invoked without app state")
        result = await get_civil_society_profile(
            GetCivilSocietyProfileInput(place_id=place_id),
            state.orchestrator,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="search_grants", description=_TOOL_DESCRIPTIONS["search_grants"])
    async def _search_grants(
        query: str | None = None,
        funder: str | None = None,
        recipient: str | None = None,
        place_id: str | None = None,
        awarded_from: str | None = None,
        awarded_to: str | None = None,
        amount_min: float | None = None,
        amount_max: float | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP search_grants invoked without app state")
        result = await search_grants(
            SearchGrantsInput.model_validate(
                {
                    "query": query,
                    "funder": funder,
                    "recipient": recipient,
                    "place_id": place_id,
                    "awarded_from": awarded_from,
                    "awarded_to": awarded_to,
                    "amount_min": amount_min,
                    "amount_max": amount_max,
                    "limit": limit,
                    "offset": offset,
                }
            ),
            state.engine,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="get_funder_profile", description=_TOOL_DESCRIPTIONS["get_funder_profile"])
    async def _get_funder_profile(
        funder: str,
        top_n: int = 10,
        refresh: bool = True,
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP get_funder_profile invoked without app state")
        result = await get_funder_profile(
            GetFunderProfileInput(funder=funder, top_n=top_n, refresh=refresh),
            state.engine,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="get_sub_areas", description=_TOOL_DESCRIPTIONS["get_sub_areas"])
    async def _get_sub_areas(
        place_id: str,
        indicator_key: str,
        child_type: str = "auto",
        period: str | None = None,
        limit: int = 50,
        sort_by: str = "worst_first",
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP get_sub_areas invoked without app state")
        result = await get_sub_areas(
            GetSubAreasInput(
                place_id=place_id,
                indicator_key=indicator_key,
                child_type=child_type,
                period=period,
                limit=limit,
                sort_by=sort_by,
            ),
            state.orchestrator,
            state.engine,
        )
        return result.model_dump(mode="json")

    @mcp.tool(
        name="get_peer_distribution",
        description=_TOOL_DESCRIPTIONS["get_peer_distribution"],
    )
    async def _get_peer_distribution(
        indicator_key: str,
        place_id: str,
        period: str | None = None,
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP get_peer_distribution invoked without app state")
        result = await get_peer_distribution(
            GetPeerDistributionInput(indicator_key=indicator_key, place_id=place_id, period=period),
            state.orchestrator,
        )
        return result.model_dump(mode="json")

    @mcp.tool(name="detect_insights", description=_TOOL_DESCRIPTIONS["detect_insights"])
    async def _detect_insights(
        place_id: str,
        indicator_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP detect_insights invoked without app state")
        result = await detect_insights(
            DetectInsightsInput(place_id=place_id, indicator_keys=indicator_keys),
            state.engine,
        )
        return result.model_dump(mode="json")

    @mcp.tool(
        name="list_indicators",
        description=(
            "Discover Soundings indicators by name, domain or geography level. "
            "Returns keys, descriptions, units, availability levels and caveats. "
            "available_at is a geography-level declaration, NOT a nation guarantee."
        ),
    )
    async def _list_indicators(
        query: str | None = None,
        domain: str | None = None,
        geography_level: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        if state is None:
            raise RuntimeError("MCP list_indicators invoked without app state")
        limit = max(1, min(limit, 100))
        offset = max(0, offset)
        async with state.engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        """
                        SELECT key, label, description, unit, source_id,
                               available_at, caveats
                        FROM catalogue.indicator
                        WHERE (CAST(:search AS text) IS NULL
                               OR key ILIKE :search OR label ILIKE :search)
                          AND (CAST(:domain AS text) IS NULL
                               OR split_part(key, '.', 1) = :domain)
                          AND (CAST(:level AS text) IS NULL
                               OR :level = ANY(available_at))
                        ORDER BY key LIMIT :limit OFFSET :offset
                        """
                    ),
                    {
                        "search": f"%{query}%" if query else None,
                        "domain": domain,
                        "level": geography_level,
                        "limit": limit + 1,
                        "offset": offset,
                    },
                )
            ).all()
        return {
            "indicators": [
                {
                    "key": row.key,
                    "label": row.label,
                    "description": row.description,
                    "unit": row.unit,
                    "source_id": row.source_id,
                    "available_at": list(row.available_at or []),
                    "caveats": list(row.caveats or []),
                }
                for row in rows[:limit]
            ],
            "has_more": len(rows) > limit,
            "next_offset": offset + limit if len(rows) > limit else None,
            "note": "Geography availability does not imply UK nation coverage.",
        }

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
