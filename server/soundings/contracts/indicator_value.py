from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from soundings.contracts.source_ref import SourceRef
from soundings.contracts.temporal import TemporalExtent, parse_period

# v1 reserves "experiential" for v3 contributed observations. Loader-mode
# adapters return "official" or "modelled"; passthrough adapters serving
# experimental data sources can return "experimental".
Confidence = Literal["official", "modelled", "experimental"]


class IndicatorValue(BaseModel):
    """A single indicator value at a single place at a single period.

    ``period`` remains the source-facing label. ``temporal`` adds structured
    semantics for consumers that need to reason about when the observation
    applies without parsing labels themselves.
    """

    place_id: str
    indicator: str
    value: float | None
    unit: str
    period: str
    temporal: TemporalExtent | None = None
    source: SourceRef
    methodology_note: str | None = None
    caveats: list[str] = Field(default_factory=list)
    confidence: Confidence
    # Directionality from catalogue.indicator.higher_is — informs the UI's
    # good/bad framing on the benchmark badge. None for indicators where
    # direction depends on context (e.g. raw counts).
    higher_is: Literal["better", "worse", "neutral"] | None = None
    # Percentile of this value against peer places of the same type (same
    # place.type), excluding self. Populated by tools that have access to
    # the full peer universe in data.indicator_value (loader-mode); None
    # when no peer data is loaded.
    benchmark_percentile: float | None = None

    @model_validator(mode="after")
    def _derive_temporal_extent(self) -> Self:
        if self.temporal is None:
            self.temporal = parse_period(self.period)
        return self
