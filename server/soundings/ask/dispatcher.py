"""Tool dispatcher mapping Anthropic tool_use blocks to in-process handlers.

The orchestrator inspects the assistant turn for ``tool_use`` blocks, then
delegates each one to this dispatcher. Every non-terminal tool has an async
handler that builds its Pydantic input model, invokes the underlying tool
function against the shared state, and returns ``model_dump(mode="json")``.

``compose_answer`` is the terminal tool: the dispatcher parses it but does
not execute it — the orchestrator owns that step.
"""

import json
import logging
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlalchemy import text

from soundings.ask.blocks import AnswerBlock, ComposeAnswerArgs, EvidenceClaim
from soundings.ask.grounding import EvidenceSet, extract_figures, shown_decimals
from soundings.contracts.observation import GetObservationsInput
from soundings.contracts.source_ref import SourceRef
from soundings.tools.aggregate_sub_areas import (
    AggregateSubAreasInput,
    aggregate_sub_areas,
)
from soundings.tools.aggregate_sub_areas import (
    tool_spec as aggregate_sub_areas_spec,
)
from soundings.tools.calculate import (
    CalculateDifferenceInput,
    CalculateShareInput,
    CalculateWeightedAverageInput,
    calculate_difference,
    calculate_share,
    calculate_weighted_average,
)
from soundings.tools.calculate import (
    tool_specs as calculation_specs,
)
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
from soundings.tools.get_change import (
    GetChangeInput,
    get_change,
)
from soundings.tools.get_change import (
    tool_spec as get_change_spec,
)
from soundings.tools.get_civil_society_profile import (
    GetCivilSocietyProfileInput,
    get_civil_society_profile,
)
from soundings.tools.get_civil_society_profile import (
    tool_spec as get_csp_spec,
)
from soundings.tools.get_containing_places import (
    GetContainingPlacesInput,
    get_containing_places,
)
from soundings.tools.get_containing_places import (
    tool_spec as get_containing_places_spec,
)
from soundings.tools.get_funder_profile import (
    GetFunderProfileInput,
    get_funder_profile,
)
from soundings.tools.get_funder_profile import (
    tool_spec as get_funder_profile_spec,
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
from soundings.tools.search_grants import (
    SearchGrantsInput,
    search_grants,
)
from soundings.tools.search_grants import (
    tool_spec as search_grants_spec,
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
    "food.ppfi.",
    "economy.active_companies_",
    "economy.new_incorporations_12m",
    "population.",
)

TERMINAL_TOOL = "compose_answer"

COMPOSE_ANSWER_DESCRIPTION = (
    "Compose the final answer to the user's question. Each block is rendered "
    "client-side; the orchestrator validates caps (max 30 blocks, max 16 visual)."
)


# Tools that check their operands against this conversation's evidence. MCP
# calls are stateless, so these exist only inside Ask; an MCP client does its
# own arithmetic on the values the other tools return.
ASK_ONLY_TOOLS = frozenset(
    {"calculate_share", "calculate_difference", "calculate_weighted_average"}
)


class FinalisedAnswer(BaseModel):
    answer: ComposeAnswerArgs
    # Figures in the answer that no tool result supports, as shown.
    untraced: list[str] = Field(default_factory=list)


class ToolDispatcher:
    """Map Anthropic tool_use blocks to in-process Python handlers."""

    def __init__(self, state: Any) -> None:
        self._state = state
        self._fetch_cache: dict[tuple[str, str], Any] = {}
        self._compare_cache: dict[tuple[str, frozenset[str]], Any] = {}
        # Provenance for every number a tool returned this run, one entry per
        # source, dated by the stalest retrieval used.
        self._sources: dict[str, SourceRef] = {}
        # Every number a tool returned this run; the answer's figures are
        # checked against it before anything is streamed.
        self.evidence = EvidenceSet()
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
            get_containing_places_spec(),
            get_indicators_spec(),
            get_place_profile_spec(),
            compare_places_spec(),
            get_trend_spec(),
            get_change_spec(),
            find_orgs_spec(),
            get_csp_spec(),
            search_grants_spec(),
            get_funder_profile_spec(),
            detect_insights_spec(),
            get_peer_dist_spec(),
            get_sub_areas_spec(),
            get_observations_spec(),
            aggregate_sub_areas_spec(),
            *calculation_specs(),
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
            # Reader-facing text may be rendered as Markdown. Escape raw HTML
            # before it reaches the UI; formatting such as **bold** survives.
            for text_field in ("markdown", "caption", "headline", "evidence", "title"):
                raw_text = getattr(block, text_field, None)
                if isinstance(raw_text, str):
                    setattr(
                        block,
                        text_field,
                        raw_text.replace("<", "&lt;").replace(">", "&gt;"),
                    )
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
        previous_count = len(self.evidence.reference_facts)
        self._record(result)
        # Ask-only metadata: the model can cite the actual observation it used.
        # Never attach this to the underlying public HTTP/MCP tool results.
        new_count = len(self.evidence.reference_facts) - previous_count
        if new_count:
            result["_evidence_facts"] = self.evidence.reference_metadata(after=previous_count)
            if new_count > 80:
                result["_evidence_facts_omitted"] = new_count - 80
        return result

    def restore_prior_results(self, messages: list[dict[str, Any]]) -> None:
        """Restore evidence from persisted tool results for follow-up questions.

        Earlier assistant prose must never become evidence.
        """
        for message in messages:
            if message.get("role") != "user" or not isinstance(message.get("content"), list):
                continue
            for block in message["content"]:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                if block.get("is_error") or not isinstance(block.get("content"), str):
                    continue
                try:
                    result = json.loads(block["content"])
                except (ValueError, TypeError):
                    continue
                if isinstance(result, dict) and "error" not in result:
                    self._record(result)

    def _record(self, result: Any) -> None:
        """Collect provenance and evidence from a tool result."""
        self.evidence.add_result(result)
        for ref in _iter_source_dicts(result):
            try:
                source = SourceRef.model_validate(ref)
            except ValidationError:
                continue
            held = self._sources.get(source.source_id)
            if held is None or source.retrieved_at < held.retrieved_at:
                self._sources[source.source_id] = source

    async def finalise_answer(self, tool_input: dict[str, Any]) -> FinalisedAnswer:
        """Parse, sanitise and grounding-check a compose_answer call."""
        parsed = self._parse_compose_answer(tool_input)
        await self._sanitise_blocks(parsed)
        return FinalisedAnswer(answer=parsed, untraced=self._untraced(parsed))

    def _claim_context_issue(self, claim: EvidenceClaim) -> str | None:
        """Check the claimed observation really has this geography and measure."""
        fact = self.evidence.reference_facts.get(claim.ref_id)
        if fact is None:
            return f"Unknown evidence reference: {claim.ref_id}"
        if (
            fact.indicator_key != claim.indicator_key
            or fact.place_id != claim.place_id
            or fact.period != claim.period
        ):
            return (
                f"Evidence context mismatch for {claim.ref_id}: "
                "indicator, place or period differs from the observation"
            )
        return None

    def _check_claims(self, statements: list[str], claims: list[EvidenceClaim]) -> list[str]:
        """Verify each numeric occurrence against its own exact quoted passage.

        A shared value such as 32.4% appearing for two places must be
        supported twice, with two separately contextualised claims.
        """
        missing: list[str] = []
        quote_locations: list[tuple[int, int, int, EvidenceClaim]] = []
        for claim in claims:
            issue = self._claim_context_issue(claim)
            if issue:
                missing.append(issue)
            locations = [
                (index, statement.find(claim.quote))
                for index, statement in enumerate(statements)
                if claim.quote in statement
            ]
            if len(locations) != 1 or statements[locations[0][0]].count(claim.quote) != 1:
                missing.append(f"Evidence quote is absent or repeated: {claim.quote[:80]}")
                continue
            index, start = locations[0]
            quote_locations.append((index, start, start + len(claim.quote), claim))

        for index, statement in enumerate(statements):
            for figure in extract_figures(statement):
                matches = [
                    claim
                    for field_index, start, end, claim in quote_locations
                    if field_index == index and start <= figure.start and figure.end <= end
                ]
                if not matches:
                    missing.append(f"{figure.text} (no evidence claim covers this occurrence)")
                elif not any(
                    self._claim_context_issue(claim) is None
                    and self.evidence.traces_with_refs(figure, [claim.ref_id])
                    for claim in matches
                ):
                    missing.append(f"{figure.text} (not supported by its claim's observation)")
        return missing

    def untraced_block(self, block: AnswerBlock) -> list[str]:
        """Check scoped claims, narrative figures and model-typed charts/tables."""
        missing: list[str] = []
        ref_ids = getattr(block, "evidence_refs", [])
        claims: list[EvidenceClaim] = getattr(block, "evidence_claims", [])
        if ref_ids:
            missing.extend(
                f"Unknown evidence reference: {ref_id}"
                for ref_id in ref_ids
                if ref_id not in self.evidence.reference_facts
            )
        statements = [
            statement
            for text_field in ("markdown", "headline", "evidence", "title", "caption", "y_label")
            if isinstance((statement := getattr(block, text_field, None)), str)
        ]
        if claims:
            missing.extend(self._check_claims(statements, claims))
        else:
            # Block-wide refs are ambiguous when prose combines statistics
            # from different observation contexts. Require passage-level refs
            # rather than letting an arbitrary matching value justify them.
            figures = [figure for statement in statements for figure in extract_figures(statement)]
            observed = [
                figure
                for figure in figures
                if any(
                    self.evidence.traces_with_refs(figure, [ref_id])
                    for ref_id in self.evidence.reference_facts
                )
            ]
            contexts = {
                (fact.indicator_key, fact.place_id, fact.period)
                for fact in self.evidence.reference_facts.values()
                if any(
                    self.evidence.traces_with_refs(figure, [fact.ref_id or ""])
                    for figure in observed
                )
            }
            if len(observed) > 1 and len(contexts) > 1:
                missing.append("Multiple observations require evidence_claims with exact passages")
            for statement in statements:
                for figure in extract_figures(statement):
                    if ref_ids:
                        if not self.evidence.traces_with_refs(figure, ref_ids):
                            missing.append(f"{figure.text} (not supported by cited observations)")
                        continue
                    # Legacy uncited calculation results remain valid. Ambiguous
                    # raw observations need an explicit reference.
                    matches = {
                        (fact.indicator_key, fact.place_id, fact.period)
                        for fact in self.evidence.reference_facts.values()
                        if self.evidence.traces_with_refs(figure, [fact.ref_id or ""])
                    }
                    if len(matches) > 1:
                        missing.append(
                            f"{figure.text} (ambiguous observation; cite evidence_claims)"
                        )
                    elif not self.evidence.traces(figure):
                        missing.append(figure.text)
        if block.type == "sub-area-table":
            for entry in block.sub_areas:
                if entry.value is not None and not self.evidence.supports_in_context(
                    entry.value,
                    indicator_key=block.indicator_key,
                    place_id=entry.place_id,
                    period=block.period,
                    decimals=max(0, shown_decimals(entry.value)),
                ):
                    missing.append(f"{entry.value:g} ({block.type}: {entry.place_id})")
            if block.parent_value is not None and not self.evidence.supports_in_context(
                block.parent_value,
                indicator_key=block.indicator_key,
                place_id=block.parent_place_id,
                period=block.period,
                decimals=max(0, shown_decimals(block.parent_value)),
            ):
                missing.append(f"{block.parent_value:g} ({block.type}: parent)")
        else:
            for value in _typed_values(block):
                if not self.evidence.supports(
                    value, is_percent=True, decimals=max(0, shown_decimals(value))
                ):
                    missing.append(f"{value:g} ({block.type})")
        return list(dict.fromkeys(missing))

    def _untraced(self, parsed: ComposeAnswerArgs) -> list[str]:
        return list(
            dict.fromkeys(
                figure for block in parsed.blocks for figure in self.untraced_block(block)
            )
        )

    @property
    def _handlers(self) -> dict[str, Any]:
        return {
            "find_place": self._handle_find_place,
            "get_containing_places": self._handle_get_containing_places,
            "get_indicators": self._handle_get_indicators,
            "get_place_profile": self._handle_get_place_profile,
            "compare_places": self._handle_compare_places,
            "get_trend": self._handle_get_trend,
            "get_change": self._handle_get_change,
            "find_organisations_in_place": self._handle_find_organisations,
            "get_civil_society_profile": self._handle_get_csp,
            "search_grants": self._handle_search_grants,
            "get_funder_profile": self._handle_get_funder_profile,
            "detect_insights": self._handle_detect_insights,
            "get_peer_distribution": self._handle_get_peer_distribution,
            "get_sub_areas": self._handle_get_sub_areas,
            "get_observations": self._handle_get_observations,
            "aggregate_sub_areas": self._handle_aggregate_sub_areas,
            "calculate_share": self._handle_calculate_share,
            "calculate_difference": self._handle_calculate_difference,
            "calculate_weighted_average": self._handle_calculate_weighted_average,
        }

    # --- Non-terminal handlers -------------------------------------------

    async def _handle_find_place(self, args: dict[str, Any]) -> dict[str, Any]:
        model = FindPlaceInput.model_validate(args)
        result = await find_place(model, self._state.geography_service)
        return result.model_dump(mode="json")

    async def _handle_get_containing_places(self, args: dict[str, Any]) -> dict[str, Any]:
        model = GetContainingPlacesInput.model_validate(args)
        result = await get_containing_places(model, self._state.geography_service)
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

    async def _handle_get_change(self, args: dict[str, Any]) -> dict[str, Any]:
        model = GetChangeInput.model_validate(args)
        result = await get_change(model, self._state.orchestrator)
        return result.model_dump(mode="json")

    async def _handle_find_organisations(self, args: dict[str, Any]) -> dict[str, Any]:
        model = FindOrganisationsInPlaceInput.model_validate(args)
        result = await find_organisations_in_place(model, self._state.orchestrator)
        return result.model_dump(mode="json")

    async def _handle_get_csp(self, args: dict[str, Any]) -> dict[str, Any]:
        model = GetCivilSocietyProfileInput.model_validate(args)
        result = await get_civil_society_profile(model, self._state.orchestrator)
        return result.model_dump(mode="json")

    async def _handle_search_grants(self, args: dict[str, Any]) -> dict[str, Any]:
        model = SearchGrantsInput.model_validate(args)
        result = await search_grants(model, self._state.engine)
        return result.model_dump(mode="json")

    async def _handle_get_funder_profile(self, args: dict[str, Any]) -> dict[str, Any]:
        model = GetFunderProfileInput.model_validate(args)
        result = await get_funder_profile(model, self._state.engine)
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

    async def _handle_aggregate_sub_areas(self, args: dict[str, Any]) -> dict[str, Any]:
        model = AggregateSubAreasInput.model_validate(args)
        result = await aggregate_sub_areas(model, self._state.orchestrator, self._state.engine)
        return result.model_dump(mode="json")

    # Calculations run against this conversation's evidence, so they live
    # here rather than in the stateless HTTP/MCP tool layer.

    async def _handle_calculate_share(self, args: dict[str, Any]) -> dict[str, Any]:
        model = CalculateShareInput.model_validate(args)
        result = calculate_share(
            numerator=model.numerator,
            denominator=model.denominator,
            label=model.label,
            evidence=self.evidence,
        )
        return result.model_dump(mode="json")

    async def _handle_calculate_difference(self, args: dict[str, Any]) -> dict[str, Any]:
        model = CalculateDifferenceInput.model_validate(args)
        result = calculate_difference(
            value=model.value, baseline=model.baseline, label=model.label, evidence=self.evidence
        )
        return result.model_dump(mode="json")

    async def _handle_calculate_weighted_average(self, args: dict[str, Any]) -> dict[str, Any]:
        model = CalculateWeightedAverageInput.model_validate(args)
        result = calculate_weighted_average(
            values=model.values,
            weights=model.weights,
            weight_label=model.weight_label,
            label=model.label,
            evidence=self.evidence,
        )
        return result.model_dump(mode="json")

    @property
    def sources(self) -> list[SourceRef]:
        """Sources behind this run's tool results, in first-seen order."""
        return list(self._sources.values())


def _iter_source_dicts(node: Any) -> Iterator[dict[str, Any]]:
    """Every dict under a "source" or "sources" key, at any depth."""
    if isinstance(node, dict):
        for key, child in node.items():
            if key == "source" and isinstance(child, dict):
                yield child
            elif key == "sources" and isinstance(child, list):
                yield from (c for c in child if isinstance(c, dict))
            else:
                yield from _iter_source_dicts(child)
    elif isinstance(node, list):
        for child in node:
            yield from _iter_source_dicts(child)


def _typed_values(block: Any) -> list[float]:
    """Numbers the model typed into a block (not ones the UI will fetch)."""
    if block.type == "bar-chart":
        return [b.value for b in block.bars]
    if block.type == "composition-chart":
        return [s.value for s in block.segments]
    if block.type == "sub-area-table":
        values = [e.value for e in block.sub_areas if e.value is not None]
        return values + ([block.parent_value] if block.parent_value is not None else [])
    return []
