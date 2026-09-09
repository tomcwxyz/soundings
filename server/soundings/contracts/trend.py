"""Trend + TrendPoint — time-series response shape for `get_trend`.

`period` remains the source label while each point also exposes a structured
temporal extent. Series breaks continue to come from catalogue caveats.
"""

from typing import Self

from pydantic import BaseModel, Field, model_validator

from soundings.contracts.source_ref import SourceRef
from soundings.contracts.temporal import TemporalExtent, parse_period


class TrendPoint(BaseModel):
    period: str
    value: float | None
    revised: bool = False
    temporal: TemporalExtent | None = None

    @model_validator(mode="after")
    def _derive_temporal_extent(self) -> Self:
        if self.temporal is None:
            self.temporal = parse_period(self.period)
        return self


class Trend(BaseModel):
    place_id: str
    indicator: str
    unit: str
    points: list[TrendPoint] = Field(default_factory=list)
    source: SourceRef
    breaks_in_series: list[str] = Field(default_factory=list)
