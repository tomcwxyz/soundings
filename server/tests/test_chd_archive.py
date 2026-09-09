import io
import zipfile

import pytest

from soundings.adapters.ons_geography.chd_archive import inspect_chd_archive


def _archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buffer.getvalue()


def test_inspector_classifies_history_from_headers_not_filename() -> None:
    blob = _archive(
        {
            "tables/table_01.csv": (
                b"OLD_CODE,NEW_CODE,CHANGE_TYPE,OPER_DATE,NOTE\n"
                b"E07000004,E06000060,Replacement,01/04/2020,Buckinghamshire\n"
            ),
            "tables/Geography_Hierarchies.csv": b"CHILD,PARENT,AS_AT\nA,B,2020-01-01\n",
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

    assert table.headers == ("GEOGCD_O", "GEOGCD_N", "GEOGCHGTYPE", "EFFECTIVE_DATE")
    assert len(table.sample_rows) == 2
    assert table.sample_rows[0]["GEOGCD_O"] == "A"
    assert inventory.summary()[0]["kind"] == "history"


def test_inspector_allows_header_only_inventory() -> None:
    blob = _archive(
        {
            "History.csv": b"GEOGCDO,GEOGCDN,CHGTYPE,EFFDATE\nA,B,Replacement,20200101\n"
        }
    )

    inventory = inspect_chd_archive(blob, sample_size=0)

    assert inventory.history_tables[0].sample_rows == ()


def test_inspector_rejects_negative_sample_size() -> None:
    with pytest.raises(ValueError, match="sample_size"):
        inspect_chd_archive(_archive({}), sample_size=-1)
