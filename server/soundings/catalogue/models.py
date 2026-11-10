from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

MeasureKind = Literal[
    "count",
    "currency",
    "proportion",
    "rate",
    "ratio",
    "per_person",
    "density",
    "score",
    "rank",
    "decile",
    "duration",
    "concentration",
    "unclassified",
]


class MeasureContract(BaseModel):
    """What a value of this indicator means and the range it can take."""

    kind: MeasureKind
    numerator: str | None = None
    denominator: str | None = None
    universe: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    # Indicators whose stored series this one must not simply repeat. Catches
    # a derived measure that was never derived (density == population).
    must_differ_from: list[str] = Field(default_factory=list)


class SourceModel(BaseModel):
    id: str
    label: str
    publisher: str
    publisher_url: str | None = None
    dataset_url: str | None = None
    licence: str
    mode: Literal["loader", "passthrough"]
    refresh_cadence: str | None = None
    ttl_hours: int | None = None
    rate_limit: dict[str, Any] = Field(default_factory=dict)


class IndicatorModel(BaseModel):
    key: str
    label: str
    description: str | None = None
    unit: str
    higher_is: Literal["better", "worse", "neutral"] | None = None
    source_id: str
    available_at: list[str]
    refresh_cadence: str | None = None
    caveats: list[str] = Field(default_factory=list)
    related_keys: list[str] = Field(default_factory=list)
    # What kind of number this is. Bounds default from the unit; shares and
    # rates must also name their numerator and denominator.
    contract: MeasureContract | None = None


def load_sources_yaml(path: Path) -> list[SourceModel]:
    raw = yaml.safe_load(path.read_text())
    return [SourceModel(**s) for s in raw["sources"]]


def load_indicators_yaml(path: Path) -> list[IndicatorModel]:
    raw = yaml.safe_load(path.read_text())
    items = raw.get("indicators", raw) if isinstance(raw, dict) else raw
    return [IndicatorModel(**i) for i in items]
