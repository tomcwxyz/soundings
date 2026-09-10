"""Structure-aware inspection of ONS Code History Database zip archives.

CHD releases contain several CSV tables whose filenames and schemas have
changed across editions. This module separates discovering what is present from
interpreting row semantics: callers inspect real headers/samples and then opt
into a schema-specific parser.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import islice
from typing import Literal

ChdTableKind = Literal[
    "history",
    "change_history",
    "changes",
    "equivalents",
    "hierarchy",
    "other",
]

# Legacy old/new-code history shape used by earlier CHD editions/adapters.
_HISTORY_HEADER_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"GEOGCD_O", "GEOGCDO", "OLD_CODE"}),
    frozenset({"GEOGCD_N", "GEOGCDN", "NEW_CODE"}),
    frozenset({"GEOGCHGTYPE", "CHGTYPE", "CHANGE_TYPE"}),
    frozenset({"EFFECTIVE_DATE", "EFFDATE", "OPER_DATE"}),
)

# Live June 2026 ArcGIS CSV collection signatures.
_CHANGE_HISTORY_HEADERS = frozenset(
    {"GEOGCD", "OPER_DATE", "TERM_DATE", "PARENTCD", "ENTITYCD", "STATUS"}
)
_CHANGES_HEADERS = frozenset({"GEOGCD", "GEOGCD_P", "OPER_DATE", "ENTITYCD", "YEAR"})
_EQUIVALENTS_HEADERS = frozenset(
    {"GEOGCD", "OPER_DATE", "TERM_DATE", "ENTITYCD", "YEAR", "STATUS", "GEOGCDH"}
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
        """Legacy old/new-code history tables."""
        return tuple(table for table in self.tables if table.kind == "history")

    @property
    def change_history_tables(self) -> tuple[ChdCsvTable, ...]:
        """Live CHD place/parent validity tables (e.g. ChangeHistory.csv)."""
        return tuple(table for table in self.tables if table.kind == "change_history")

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
    """Inventory CSV tables in a CHD zip without over-interpreting rows.

    Known table shapes are identified from headers rather than filenames. A
    hierarchy filename remains as a conservative fallback for older packages
    whose column semantics have not yet been pinned.

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

    if _CHANGE_HISTORY_HEADERS <= normalised_headers:
        return "change_history"
    if _CHANGES_HEADERS <= normalised_headers:
        return "changes"
    if _EQUIVALENTS_HEADERS <= normalised_headers:
        return "equivalents"
    if all(normalised_headers & group for group in _HISTORY_HEADER_GROUPS):
        return "history"

    lowered_name = name.lower()
    if "hierarch" in lowered_name:
        return "hierarchy"
    return "other"
