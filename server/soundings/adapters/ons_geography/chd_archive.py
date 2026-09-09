"""Structure-aware inspection of ONS Code History Database zip archives.

CHD releases contain several CSV tables whose filenames have changed across
editions. This module deliberately separates *discovering what is present*
from interpreting hierarchy semantics: callers can inspect filenames,
headers and small samples without hard-coding an unverified hierarchy schema.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import islice
from typing import Literal

ChdTableKind = Literal["history", "hierarchy", "other"]

_HISTORY_HEADER_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"GEOGCD_O", "GEOGCDO", "OLD_CODE"}),
    frozenset({"GEOGCD_N", "GEOGCDN", "NEW_CODE"}),
    frozenset({"GEOGCHGTYPE", "CHGTYPE", "CHANGE_TYPE"}),
    frozenset({"EFFECTIVE_DATE", "EFFDATE", "OPER_DATE"}),
)


@dataclass(frozen=True)
class ChdCsvTable:
    name: str
    headers: tuple[str, ...]
    kind: ChdTableKind
    sample_rows: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class ChdArchiveInventory:
    tables: tuple[ChdCsvTable, ...]

    @property
    def history_tables(self) -> tuple[ChdCsvTable, ...]:
        return tuple(table for table in self.tables if table.kind == "history")

    @property
    def hierarchy_tables(self) -> tuple[ChdCsvTable, ...]:
        return tuple(table for table in self.tables if table.kind == "hierarchy")

    def summary(self) -> list[dict[str, object]]:
        """Compact serialisable inventory for logs, diagnostics or one-off runs."""
        return [
            {
                "name": table.name,
                "kind": table.kind,
                "headers": list(table.headers),
                "sample_rows": [dict(row) for row in table.sample_rows],
            }
            for table in self.tables
        ]


def normalise_chd_header(value: str) -> str:
    """Canonicalise CHD field names for tolerant cross-edition matching."""
    return value.strip().upper()


def inspect_chd_archive(blob: bytes, *, sample_size: int = 2) -> ChdArchiveInventory:
    """Inventory CSV tables in a CHD zip without interpreting hierarchy rows.

    Geography History is identified from the known field families rather than
    its filename alone. Hierarchy tables remain intentionally conservative: we
    flag files whose names advertise hierarchy content and expose their real
    headers/sample rows for a later schema-specific parser.

    CSV members are streamed from the zip. Inspection reads only the header and
    the requested number of sample rows, so a header-only inventory does not
    decompress large table bodies.
    """
    if sample_size < 0:
        raise ValueError("sample_size must be non-negative")

    tables: list[ChdCsvTable] = []
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for name in sorted(zf.namelist()):
            if not name.lower().endswith(".csv"):
                continue
            with zf.open(name) as member:
                with io.TextIOWrapper(
                    member,
                    encoding="utf-8-sig",
                    errors="replace",
                    newline="",
                ) as stream:
                    headers, samples = _inspect_csv(stream, sample_size=sample_size)
            tables.append(
                ChdCsvTable(
                    name=name,
                    headers=headers,
                    kind=_classify_table(name, headers),
                    sample_rows=samples,
                )
            )
    return ChdArchiveInventory(tables=tuple(tables))


def _inspect_csv(
    stream: Iterable[str],
    *,
    sample_size: int,
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    reader = csv.DictReader(stream)
    headers = tuple(header.strip() for header in (reader.fieldnames or ()) if header)
    samples: list[dict[str, str]] = []
    for row in islice(reader, sample_size):
        samples.append(
            {
                str(key).strip(): "" if value is None else str(value).strip()
                for key, value in row.items()
                if key is not None
            }
        )
    return headers, tuple(samples)


def _classify_table(name: str, headers: tuple[str, ...]) -> ChdTableKind:
    normalised_headers = {normalise_chd_header(header) for header in headers}
    if all(normalised_headers & group for group in _HISTORY_HEADER_GROUPS):
        return "history"

    lowered_name = name.lower()
    if "hierarch" in lowered_name:
        return "hierarchy"
    return "other"
