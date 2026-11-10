"""Comparison + ComparisonValue — response shape for `compare_places`.

Per spec §4.4. `methodology_note` and `caveats` are present on every
other indicator-bearing response (IndicatorValue, get_indicators); the
spec doesn't list them on `compare_places`, but the symmetry is
deliberate — downstream consumers (UI, narrative briefs) treat all
indicator outputs uniformly. Phase 3 plan Task 3 documents this as a
spec extension.
"""

from pydantic import BaseModel, Field

from soundings.contracts.source_ref import SourceRef


class ComparisonValue(BaseModel):
    place_id: str
    value: float | None
    # rank / percentile are populated only when `comparison_basis` is
    # "rank" or "percentile". For "absolute" they stay None.
    rank: int | None = None
    percentile: float | None = None


class Comparison(BaseModel):
    indicator: str
    unit: str
    period: str
    values: list[ComparisonValue] = Field(default_factory=list)
    source: SourceRef
    methodology_note: str | None = None
    caveats: list[str] = Field(default_factory=list)
    # Context places are shown for reference (e.g. parent LTLA alongside
    # LSOA peers) but do not participate in peer ranking.
    is_context: bool = False
    # What the ranking or percentile is relative to, in plain words.
    peer_universe: str | None = None
