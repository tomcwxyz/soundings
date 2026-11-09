"""Parse the official StatsWales WIMD 2025 rankings CSV.

Dataset 9706edd9-73ad-4902-bb12-7ccd7038626e is long-form:
Data values, Data description, Area code, Area name, Domain.
Only 2021 Welsh LSOAs are accepted. We import *published* Rank and
Decile cells, never derive deciles from ranks or use composite scores.
"""

import csv
import io
import math
import re
from dataclasses import dataclass

LSOA_PATTERN = re.compile(r"^W010[0-9]{5}$")
DOMAIN_KEYS: dict[str, str] = {
    "wimd": "deprivation.wimd2025",
    "income": "deprivation.wimd2025.income",
    "employment": "deprivation.wimd2025.employment",
    "health": "deprivation.wimd2025.health",
    "education": "deprivation.wimd2025.education",
    "access to services": "deprivation.wimd2025.access_to_services",
    "housing": "deprivation.wimd2025.housing",
    "community safety": "deprivation.wimd2025.community_safety",
    "physical environment": "deprivation.wimd2025.physical_environment",
}
FIELDS = {"data values", "data description", "area code", "domain"}


@dataclass(frozen=True)
class WimdRow:
    lsoa_code: str
    indicator_key: str
    value: float


def parse_wimd_csv(content: bytes) -> list[WimdRow]:
    """Reject unexpected exports instead of silently importing no data."""
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
    if reader.fieldnames is None:
        raise ValueError("WIMD CSV has no header")
    canonical = {name.strip().lower(): name for name in reader.fieldnames}
    if missing := FIELDS - canonical.keys():
        raise ValueError(f"WIMD CSV missing columns: {', '.join(sorted(missing))}")

    rows: list[WimdRow] = []
    seen: set[tuple[str, str]] = set()
    for raw in reader:
        code = (raw.get(canonical["area code"]) or "").strip()
        if not LSOA_PATTERN.fullmatch(code):
            continue  # Ignore authority aggregates / non-2021 codes.
        domain = (raw.get(canonical["domain"]) or "").strip().lower()
        base_key = DOMAIN_KEYS.get(domain)
        measure = (raw.get(canonical["data description"]) or "").strip().lower()
        if base_key is None or measure not in {"rank", "decile"}:
            continue

        raw_value = (raw.get(canonical["data values"]) or "").replace(",", "").strip()
        if raw_value in {"", "[x]", "-", ".."}:
            continue
        try:
            value = float(raw_value)
        except ValueError as exc:
            raise ValueError(f"Invalid WIMD value for {code}: {raw_value!r}") from exc
        ceiling = 1917 if measure == "rank" else 10
        if not math.isfinite(value) or not value.is_integer() or not 1 <= value <= ceiling:
            raise ValueError(f"Out-of-range WIMD {measure} for {code}: {value}")
        indicator_key = f"{base_key}.{measure}"
        key = (code, indicator_key)
        if key in seen:
            raise ValueError(f"Duplicate WIMD observation: {code} / {indicator_key}")
        seen.add(key)
        rows.append(WimdRow(code, indicator_key, value))

    if not rows:
        raise ValueError("No Welsh LSOA WIMD rank/decile observations in CSV")
    return rows
