"""get_peer_distribution tool — full peer-universe values for one indicator.

Returns every same-type peer's value for a single indicator at a single place,
suitable for distribution charts (histograms, box plots) and scatter plots. The
focal place's value is surfaced separately so the caller can highlight it on
the chart. ``peer_place_values`` carries the {place_id, value} pairs so clients
can label individual points without a second round-trip.

Per spec §4.4 (peer universe semantics). Routes through the orchestrator's
``_peer_values_loader`` for loader-mode adapters; passthrough adapters are not
supported by this tool (the orchestrator raises if the adapter is not in
loader mode).
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from soundings.contracts.source_ref import SourceRef
from soundings.orchestration.measure_guard import guard_values
from soundings.orchestration.peer_universe import describe_peer_universe

if TYPE_CHECKING:
    from soundings.orchestration.orchestrator import IndicatorOrchestrator


class GetPeerDistributionInput(BaseModel):
    indicator_key: str
    place_id: str
    period: str | None = None


class GetPeerDistributionOutput(BaseModel):
    indicator_key: str
    place_id: str
    focal_value: float | None = None
    peer_values: list[float] = Field(default_factory=list)
    peer_place_values: list[dict[str, Any]] = Field(default_factory=list)
    peer_count: int = 0
    unit: str = "value"
    period: str = ""
    # What the distribution is drawn from, in plain words.
    peer_universe: str | None = None
    sources: list[SourceRef] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


TOOL_NAME = "get_peer_distribution"
TOOL_DESCRIPTION = (
    "Get all peer values for a single indicator across the full same-type "
    "peer universe of a focal place. Returns the focal place's value plus the "
    "complete list of peer values (with place_ids) for rendering distribution "
    "charts (histograms, box plots) and scatter plots. The peer denominator "
    "is always the complete set of places sharing the focal place's geography "
    "type."
)


def tool_spec() -> dict[str, object]:
    return {
        "name": TOOL_NAME,
        "description": TOOL_DESCRIPTION,
        "input_schema": GetPeerDistributionInput.model_json_schema(),
        "output_schema": GetPeerDistributionOutput.model_json_schema(),
    }


async def get_peer_distribution(
    input: GetPeerDistributionInput, orchestrator: "IndicatorOrchestrator"
) -> GetPeerDistributionOutput:
    # a) Enforce the indicator is available at the focal place's geography level.
    await orchestrator._enforce_level(input.indicator_key, input.place_id)

    # b) Load all peer values for the focal place's geography type.
    peer_type = input.place_id.partition(":")[0]
    peer_values_map, period_used = await orchestrator._peer_values_loader(
        indicator_key=input.indicator_key,
        peer_type=peer_type,
        period=input.period,
    )

    # Values that break the indicator's measure contract are withheld here
    # as everywhere else a number is served.
    peer_values_map, withheld = guard_values(
        orchestrator._contracts, input.indicator_key, peer_values_map
    )
    caveats = [withheld] if withheld is not None else []

    # c) Extract the focal place's value.
    focal_value = peer_values_map.get(input.place_id)

    # d) Build the peer_values list (non-null) and peer_place_values list.
    peer_values: list[float] = []
    peer_place_values: list[dict[str, Any]] = []
    for pid, val in peer_values_map.items():
        if val is None:
            continue
        peer_values.append(val)
        peer_place_values.append({"place_id": pid, "value": val})

    # e) Load indicator metadata for the unit.
    meta = await orchestrator._load_indicator_meta(input.indicator_key)
    unit = meta["unit"] if meta else "value"

    adapter = await orchestrator._registry.adapter_for_indicator(input.indicator_key)
    source = adapter.get_source_ref(retrieved_at=datetime.now(tz=UTC), cache_status="cached")

    # f) Return the output model.
    return GetPeerDistributionOutput(
        indicator_key=input.indicator_key,
        place_id=input.place_id,
        focal_value=focal_value,
        peer_values=peer_values,
        peer_place_values=peer_place_values,
        peer_count=len(peer_values),
        unit=unit,
        period=period_used,
        peer_universe=describe_peer_universe(peer_type, len(peer_values)),
        sources=[source],
        caveats=caveats,
    )
