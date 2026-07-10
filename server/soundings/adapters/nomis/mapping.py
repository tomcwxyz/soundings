"""Pydantic loader for `catalogue/nomis-mapping.yaml`.

Each entry binds a Soundings indicator key to a Nomis dataset query. Field
codes (`measures`, `cell`, `c2021_*`) are Nomis-specific and pinned per
indicator. Treat any `(unverified)` mapping as needing a sanity-check at
first run, same pattern as ADR-0001 endpoint URLs.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class NomisMapping(BaseModel):
    indicator_key: str
    source_id: str
    dataset_id: str
    measures: str | None = None
    cell: str | None = None
    geography_type_codes: dict[str, str] = Field(default_factory=dict)
    # Free-form Nomis dimension filters (e.g. c_age, gender, c2021_*). Loaders
    # splat these into NomisClient.get_observations as query params. Values are
    # stringly-typed because Nomis is a URL query API.
    extra_params: dict[str, str] = Field(default_factory=dict)
    period: str | None = None  # e.g. "2021" for Census, "latest" for MYE
    # Post-fetch multiplier on obs_value. Used when Nomis returns a percent
    # (measures=20301, range 0–100) but the indicator contract is a fraction
    # (0–1); set value_scale: 0.01.
    value_scale: float | None = None
    # Optional post-fetch computation. Supports:
    #   "complement"  — value = 100 - obs_value (e.g. non-white-British =
    #                   100 - white_british_pct). Requires extra_params to
    #                   filter to the cell being complemented.
    #   "sum_codes"   — sum obs_values for the dimension codes listed in
    #                   sum_codes. No extra_params filter needed; the loader
    #                   fetches all cells and picks the specified ones.
    computation: str | None = None
    sum_codes: list[str] | None = None


def load_nomis_mapping(path: Path) -> list[NomisMapping]:
    raw = yaml.safe_load(path.read_text())
    items = raw.get("mappings", raw) if isinstance(raw, dict) else raw
    return [NomisMapping(**m) for m in items]
