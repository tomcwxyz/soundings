"""Pydantic loader for catalogue/dfe-mapping.yaml.

Each entry pins one soundings indicator key to the DfE Explore
Education Statistics dataset UUID + indicator UUID + optional
filter selection. DfE rotates UUIDs on annual republication, so
mapping entries rot — Task 20's live test catches that.
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

DEFAULT_MAPPING_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent.parent / "catalogue" / "dfe-mapping.yaml"
)


class DfeMapping(BaseModel):
    indicator_key: str
    data_set_id: str
    indicator_id: str
    filter_selection: dict[str, str] = Field(default_factory=dict)
    filters: dict[str, Any] = Field(default_factory=dict)
    location_level: str  # DfE-native level code: LA, NAT, REG, etc.
    time_period_code: str = "AY"  # academic year by default
    place_type: str  # the soundings place type, e.g. "ltla24"
    unit: str = "value"
    caveats: list[str] = Field(default_factory=list)


def load_dfe_mapping(path: Path | None = None) -> list[DfeMapping]:
    target = path or DEFAULT_MAPPING_PATH
    raw = yaml.safe_load(target.read_text())
    items = raw.get("mappings", raw) if isinstance(raw, dict) else raw
    return [DfeMapping(**m) for m in items]
