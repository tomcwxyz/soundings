"""Tool dispatcher mapping Anthropic tool_use blocks to in-process handlers.

The orchestrator inspects the assistant turn for ``tool_use`` blocks, then
delegates each one to this dispatcher. Every non-terminal tool has an async
handler that builds its Pydantic input model, invokes the underlying tool
function against the shared state, and returns ``model_dump(mode="json")``.

``compose_answer`` is the terminal tool: the dispatcher parses it but does
not execute it — the orchestrator owns that step.
"""

import logging
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import text

from soundings.ask.blocks import AnswerBlock, ComposeAnswerArgs
from soundings.contracts.observation import GetObservationsInput
from soundings.tools.compare_places import (
    ComparePlacesInput,
    compare_places,
)
from soundings.tools.compare_places import (
    tool_spec as compare_places_spec,
)
from soundings.tools.detect_insights import (
    DetectInsightsInput,
    detect_insights,
)
from soundings.tools.detect_insights import (
    tool_spec as detect_insights_spec,
)
from soundings.tools.find_organisations_in_place import (
    FindOrganisationsInPlaceInput,
    find_organisations_in_place,
)
from soundings.tools.find_organisations_in_place import (
    tool_spec as find_orgs_spec,
)
from soundings.tools.find_place import (
    FindPlaceInput,
    find_place,
)
from soundings.tools.find_place import (
    tool_spec as find_place_spec,
)
from soundings.tools.get_civil_society_profile import (
    GetCivilSocietyProfileInput,
    get_civil_society_profile,
)
from soundings.tools.get_civil_society_profile import (
    tool_spec as get_csp_spec,
)
from soundings.tools.get_indicators import (
    GetIndicatorsInput,
    get_indicators,
)
from soundings.tools.get_indicators import (
    tool_spec as get_indicators_spec,
)
from soundings.tools.get_observations import get_observations
from soundings.tools.get_observations import (
    tool_spec as get_observations_spec,
)
from soundings.tools.get_peer_distribution import (
    GetPeerDistributionInput,
    get_peer_distribution,
)
from soundings.tools.get_peer_distribution import (
    tool_spec as get_peer_dist_spec,
)
from soundings.tools.get_place_profile import (
    GetPlaceProfileInput,
    get_place_profile,
)
from soundings.tools.get_place_profile import (
    tool_spec as get_place_profile_spec,
)
from soundings.tools.get_sub_areas import (
    GetSubAreasInput,
    get_sub_areas,
)
from soundings.tools.get_sub_areas import (
    tool_spec as get_sub_areas_spec,
)
from soundings.tools.get_trend import (
    GetTrendInput,
    get_trend,
)
from soundings.tools.get_trend import (
    tool_spec as get_trend_spec,
)

logger = logging.getLogger(__name__)

# Validates a single block in isolation, so one malformed block can be dropped
# without failing the whole answer (see _parse_compose_answer).
_ANSWER_BLOCK_ADAPTER: TypeAdapter[Any] = TypeAdapter(AnswerBlock)

# Indicator prefixes with per-area choropleth data (see the "Do NOT
# choropleth infrastructure.* ..." guidance in prompts.py). Keep this in sync
# with that prompt text — it's the code-level backstop for when the model
# ignores the instruction. Everything else (notably infrastructure.* OSM
# counts, which are fetched live per place rather than bulk-backfilled across
# peers, and environment.air_quality.*/crime.*, which have no stored per-area
# series) renders as a mostly-null, visually broken map.
_CHOROPLETH_ELIGIBLE_PREFIXES = (
    "deprivation.",
    "environment.greenspace.",
    "economy.active_companies_",
    "economy.new_incorporations_12m",
    "population.",
)

TERMINAL_TOOL = "compose_answer"

COMPOSE_ANSWER_DESCRIPTION = (
    "Compose the final answer to the user's question. Each block is rendered "
    "client-side; the orchestrator validates caps (max 30 blocks, max 16 visual)."
)


class ToolDispatcher:
    """Map Anthropic tool_use blocks to in-process Python handlers."""

    def __init__(self, state: Any) -> None:
        self._state = state
        self._fetch_cache: dict[tuple[str, str], Any] = {}
        self._compare_cache: dict[tuple[str, frozenset[str]], Any] = {}
        self._sources: list[Any] = []
        self._known_indicator_keys: set[str] | None = None

    def tool_specs(self) -> list[dict[str, object]]:
        """Return the full tool catalogue including the terminal compose_answer.

        The shared tool specs carry an ``output_schema`` key for the HTTP/MCP
        layers; the Anthropic Messages API rejects any key beyond
        ``name``/``description``/``input_schema``, so project each spec down to
        the accepted shape here.
        """
        specs: list[dict[str, object]] = [
            find_place_spec(),
            get_indicators_spec(),
            get_place_profile_spec(),
            compare_places_spec(),
            get_trend_spec(),
            find_orgs_spec(),
            get_csp_spec(),
            detect_insights_spec(),
            get_peer_dist_spec(),
            get_sub_areas_spec(),
            get_observations_spec(),
            {
                "name": TERMINAL_TOOL,
                "description": COMPOSE_ANSWER_DESCRIPTION,
                "input_schema": ComposeAnswerArgs.model_json_schema(),
            },
        ]
        allowed = ("name", "description", "input_schema")
        return [{key: spec[key] for key in allowed if key in spec} for spec in specs]

    def is_terminal_tool(self, tool_name: str) -> bool:
        return tool_name == TERMINAL_TOOL

    def _parse_compose_answer(self, args: dict[str, Any]) -> ComposeAnswerArgs:
        """Parse the terminal compose_answer call, dropping blocks that fail
        validation rather than failing the whole answer.

        The model occasionally emits a block that violates a field constraint —
        e.g. a compare-chart with a single place_id for "how does X compare to
        peers?" (the chart needs at least two). Mirroring the trim-don't-reject
        philosophy of `_enforce_caps` and `_sanitise_blocks`, an individually
        invalid block is discarded so the rest of the answer still renders. If
        nothing survives, raise so a fully broken answer fails loudly.
        """
        raw_blocks = args.get("blocks")
        if not isinstance(raw_blocks, list):
            # Malformed top-level payload — let the model surface its own error.
            return ComposeAnswerArgs.model_validate(args)

        valid: list[Any] = []
        for raw in raw_blocks:
            try:
                valid.append(_ANSWER_BLOCK_ADAPTER.validate_python(raw))
            except ValidationError:
                block_type = raw.get("type") if isinstance(raw, dict) else None
                logger.warning("Dropping invalid compose_answer block (type=%s)", block_type)
        if not valid:
            raise ValueError("compose_answer produced no valid blocks")
        return ComposeAnswerArgs(blocks=valid)

    async def _known_keys(self) -> set[str]:
        """Valid indicator keys from the catalogue (fetched once, then cached)."""
        if self._known_indicator_keys is None:
            async with self._state.engine.connect() as conn:
                rows = await conn.execute(text("SELECT key FROM catalogue.indicator"))
                self._known_indicator_keys = {row[0] for row in rows}
        return self._known_indicator_keys

    async def _sanitise_blocks(self, parsed: ComposeAnswerArgs) -> None:
        """Drop blocks that reference an indicator the catalogue doesn't have,
        and downgrade choropleth maps that name a real but choropleth-
        ineligible indicator.

        The model occasionally invents plausible-but-nonexistent keys (e.g.
        ``civil_society.total_organisations``), which would otherwise surface in
        the UI as a "No data" card or a blank choropleth. It also sometimes
        ignores the prompt's guidance and choropleths an indicator with no
        bulk per-area coverage (e.g. infrastructure.* OSM counts, fetched live
        per place rather than backfilled across peers) — that renders a
        mostly-null, visually broken map. Both cases: a map downgrades to a
        plain boundary map rather than being dropped, since the boundary is
        still useful; other block types with a bad key are dropped.
        """
        known = await self._known_keys()
        kept: list[Any] = []
        for block in parsed.blocks:
            key = getattr(block, "indicator_key", None)
            if key is None:
                kept.append(block)
                continue
            choropleth_ineligible = block.type == "map" and not key.startswith(
                _CHOROPLETH_ELIGIBLE_PREFIXES
            )
            if key not in known or choropleth_ineligible:
                if block.type == "map":
                    block.indicator_key = None  # fall back to a boundary map
                    kept.append(block)
                # indicator-card / trend-chart / compare-chart with a bad key: drop.
                continue
            kept.append(block)
        parsed.blocks = kept

    async def dispatch(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        if tool_name == TERMINAL_TOOL:
            parsed = self._parse_compose_answer(tool_input)
            await self._sanitise_blocks(parsed)
            return parsed.model_dump(mode="json")
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        result: dict[str, Any] = await handler(tool_input)
        return result

    @property
    def _handlers(self) -> dict[str, Any]:
        return {
            "find_place": self._handle_find_place,
            "get_indicators": self._handle_get_indicators,
            "get_place_profile": self._handle_get_place_profile,
            "compare_places": self._handle_compare_places,
            "get_trend": self._handle_get_trend,
            "find_organisations_in_place": self._handle_find_organisations,
            "get_civil_society_profile": self._handle_get_csp,
            "detect_insights": self._handle_detect_insights,
            "get_peer_distribution": self._handle_get_peer_distribution,
            "get_sub_areas": self._handle_get_sub_areas,
            "get_observations": self._handle_get_observations,
        }

    # --- Non-terminal handlers -------------------------------------------

    async def _handle_find_place(self, args: dict[str, Any]) -> dict[str, Any]:
        model = FindPlaceInput.model_validate(args)
        result = await find_place(model, self._state.geography_service)
        return result.model_dump(mode="json")

    async def _handle_get_indicators(self, args: dict[str, Any]) -> dict[str, Any]:
        model = GetIndicatorsInput.model_validate(args)
        result = await get_indicators(model, self._state.orchestrator)
        return result.model_dump(mode="json")

    async def _handle_get_place_profile(self, args: dict[str, Any]) -> dict[str, Any]:
        model = GetPlaceProfileInput.model_validate(args)
        result = await get_place_profile(model, self._state.orchestrator, self._state.engine)
        return result.model_dump(mode="json")

    async def _handle_compare_places(self, args: dict[str, Any]) -> dict[str, Any]:
        model = ComparePlacesInput.model_validate(args)
        result = await compare_places(model, self._state.orchestrator)
        return result.model_dump(mode="json")

    async def _handle_get_trend(self, args: dict[str, Any]) -> dict[str, Any]:
        model = GetTrendInput.model_validate(args)
        result = await get_trend(model, self._state.orchestrator)
        return result.model_dump(mode="json")

    async def _handle_find_organisations(self, args: dict[str, Any]) -> dict[str, Any]:
        model = FindOrganisationsInPlaceInput.model_validate(args)
        result = await find_organisations_in_place(model, self._state.orchestrator)
        return result.model_dump(mode="json")

    async def _handle_get_csp(self, args: dict[str, Any]) -> dict[str, Any]:
        model = GetCivilSocietyProfileInput.model_validate(args)
        result = await get_civil_society_profile(model, self._state.orchestrator)
        return result.model_dump(mode="json")

    async def _handle_detect_insights(self, args: dict[str, Any]) -> dict[str, Any]:
        model = DetectInsightsInput.model_validate(args)
        result = await detect_insights(model, self._state.engine)
        return result.model_dump(mode="json")

    async def _handle_get_peer_distribution(self, args: dict[str, Any]) -> dict[str, Any]:
        model = GetPeerDistributionInput.model_validate(args)
        result = await get_peer_distribution(model, self._state.orchestrator)
        return result.model_dump(mode="json")

    async def _handle_get_sub_areas(self, args: dict[str, Any]) -> dict[str, Any]:
        model = GetSubAreasInput.model_validate(args)
        result = await get_sub_areas(model, self._state.orchestrator, self._state.engine)
        return result.model_dump(mode="json")

    async def _handle_get_observations(self, args: dict[str, Any]) -> dict[str, Any]:
        model = GetObservationsInput.model_validate(args)
        result = await get_observations(model, self._state.engine)
        return result.model_dump(mode="json")

    @property
    def sources(self) -> list[Any]:
        """Accumulated SourceRefs from tool calls. Read-only view."""
        return list(self._sources)
