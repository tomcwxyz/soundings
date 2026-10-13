"""Pure parser for the Scottish Government's 2022 Data Zone lookup.

The official lookup specifies DZ22, IZ22 and LA code columns. Name columns
differ between published resources, so they are optional; code placeholders
are used when no authoritative display name is supplied. Never infer a
spatial relation from a place name.
"""

import csv
import io
import re
from dataclasses import dataclass

DZ_PATTERN = re.compile(r"^S010[0-9]{5}$")
IZ_PATTERN = re.compile(r"^S020[0-9]{5}$")
LA_PATTERN = re.compile(r"^S120[0-9]{5}$")


@dataclass(frozen=True)
class ScottishLookupRow:
    dz: str
    iz: str
    la: str
    dz_name: str
    iz_name: str


def parse_scot_dz_lookup(content: bytes) -> list[ScottishLookupRow]:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
    if not reader.fieldnames:
        raise ValueError("Scottish Data Zone lookup is missing CSV headers")
    names = {name.strip().casefold(): name for name in reader.fieldnames}
    required = {"dz22", "iz22", "la"}
    if missing := required - names.keys():
        raise ValueError(f"Scottish Data Zone lookup missing: {', '.join(sorted(missing))}")

    def field(row: dict[str, str | None], *candidates: str) -> str:
        for candidate in candidates:
            col = names.get(candidate.casefold())
            if col:
                value = (row.get(col) or "").strip()
                if value:
                    return value
        return ""

    parsed: dict[str, ScottishLookupRow] = {}
    iz_to_la: dict[str, str] = {}
    for raw in reader:
        dz, iz, la = (
            field(raw, "DZ22"),
            field(raw, "IZ22"),
            field(raw, "LA"),
        )
        if not (
            DZ_PATTERN.fullmatch(dz)
            and IZ_PATTERN.fullmatch(iz)
            and LA_PATTERN.fullmatch(la)
        ):
            raise ValueError(f"Invalid Scottish 2022 geography codes: {dz}/{iz}/{la}")
        record = ScottishLookupRow(
            dz=dz,
            iz=iz,
            la=la,
            dz_name=field(raw, "DZ22Name", "DZ22NM", "DataZoneName") or dz,
            iz_name=field(raw, "IZ22Name", "IZ22NM", "IntermediateZoneName") or iz,
        )
        previous = parsed.get(dz)
        if previous is not None and (previous.iz, previous.la) != (iz, la):
            raise ValueError(f"Conflicting parent mapping for Data Zone {dz}")
        previous_la = iz_to_la.setdefault(iz, la)
        if previous_la != la:
            raise ValueError(f"Conflicting parent mapping for Intermediate Zone {iz}")
        parsed[dz] = record
    if not parsed:
        raise ValueError("Scottish Data Zone lookup is empty")
    return list(parsed.values())
