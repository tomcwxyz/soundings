import io
import zipfile

import pytest

from soundings.adapters.ons_geography.chd_archive import _inspect_csv, inspect_chd_archive


def _archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


class _HeaderThenExplodes:
    """CSV iterable proving sample_size=0 never requests a data row."""

    def __init__(self) -> None:
        self._first = True

    def __iter__(self) -> "_HeaderThenExplodes":
        return self

    def __next__(self) -> str:
        if self._first:
            self._first = False
            return "A,B\n"
        raise AssertionError("inspector requested a row beyond the configured sample")


def test_inspector_classifies_history_from_headers_not_filename() -> None:
    blob = _archive(
        {
            "tables/table_01.csv": (
                b"OLD_CODE,NEW_CODE,CHANGE_TYPE,OPER_DATE,NOTE\n"
                b"E07000004,E06000060,Replacement,01/04/2020,Buckinghamshire\n"
            ),
            "tables/Geography_Hierarchies.csv": (b"CHILD,PARENT,AS_AT\nA,B,2020-01-01\n"),
            "tables/Information.csv": b"KEY,VALUE\nrelease,June 2026\n",
        }
    )

    inventory = inspect_chd_archive(blob)

    assert [table.name for table in inventory.history_tables] == ["tables/table_01.csv"]
    assert [table.name for table in inventory.hierarchy_tables] == [
        "tables/Geography_Hierarchies.csv"
    ]
    kinds = {table.name: table.kind for table in inventory.tables}
    assert kinds["tables/Information.csv"] == "other"


def test_inspector_recognises_live_2026_chd_shapes() -> None:
    blob = _archive(
        {
            "ChangeHistory.csv": (
                b"GEOGCD,GEOGNM,OPER_DATE,TERM_DATE,PARENTCD,ENTITYCD,STATUS\n"
                b"E01000001,City of London 001A,01/02/2004 00:00,,E02000001,E01,live\n"
            ),
            "Changes.csv": (
                b"GEOGCD,GEOGCD_P,OPER_DATE,ENTITYCD,YEAR\n"
                b"00GG01,E05006654,01/04/2009 00:00,00G,2009\n"
            ),
            "Equivalents.csv": (
                b"GEOGCD,GEOGCDH,OPER_DATE,TERM_DATE,ENTITYCD,YEAR,STATUS\n"
                b"E38000007,99E,01/04/2026 00:00,,E38,2026,live\n"
            ),
        }
    )

    inventory = inspect_chd_archive(blob)
    kinds = {table.name: table.kind for table in inventory.tables}

    assert kinds == {
        "ChangeHistory.csv": "change_history",
        "Changes.csv": "changes",
        "Equivalents.csv": "equivalents",
    }
    assert [table.name for table in inventory.change_history_tables] == ["ChangeHistory.csv"]


def test_inspector_exposes_headers_and_bounded_samples() -> None:
    blob = _archive(
        {
            "Geography_History.csv": (
                b"GEOGCD_O,GEOGCD_N,GEOGCHGTYPE,EFFECTIVE_DATE\n"
                b"A,B,Replacement,2020-01-01\n"
                b"B,C,Replacement,2021-01-01\n"
                b"C,D,Replacement,2022-01-01\n"
            )
        }
    )

    inventory = inspect_chd_archive(blob, sample_size=2)
    table = inventory.history_tables[0]

    assert table.headers == (
        "GEOGCD_O",
        "GEOGCD_N",
        "GEOGCHGTYPE",
        "EFFECTIVE_DATE",
    )
    assert len(table.sample_rows) == 2
    assert table.sample_rows[0]["GEOGCD_O"] == "A"
    assert inventory.summary()[0]["kind"] == "history"


def test_inspector_allows_header_only_inventory() -> None:
    blob = _archive(
        {"History.csv": (b"GEOGCDO,GEOGCDN,CHGTYPE,EFFDATE\nA,B,Replacement,20200101\n")}
    )

    inventory = inspect_chd_archive(blob, sample_size=0)

    assert inventory.history_tables[0].sample_rows == ()


def test_header_only_inspection_does_not_consume_first_data_row() -> None:
    headers, samples = _inspect_csv(_HeaderThenExplodes(), sample_size=0)

    assert headers == ("A", "B")
    assert samples == ()


def test_inspector_rejects_negative_sample_size() -> None:
    with pytest.raises(ValueError, match="sample_size"):
        inspect_chd_archive(_archive({}), sample_size=-1)
