"""Offline, source-shaped checks for the StatsWales WIMD 2025 export."""

import pytest

from soundings.adapters.gov_wales_wimd2025.parser import parse_wimd_csv

CSV = (
    "Data values,Data description,Area code,Area name,Domain\n"
    "570,Rank,W01000003,Amlwch Port,WIMD\n"
    "3,Decile,W01000003,Amlwch Port,WIMD\n"
    "407,Rank,W01000003,Amlwch Port,Income\n"
    "1,Rank,W01000004,Neighbourhood,Access to services\n"
    "1,Decile,W01000004,Neighbourhood,Access to services\n"
    "83,Rank,E01000001,English area,WIMD\n"
    "123,Rank,W06000001,Welsh council,WIMD\n"
    "2,Quintile,W01000003,Amlwch Port,WIMD\n"
)


def test_wimd_parses_official_long_form_and_filters_non_lsoas() -> None:
    rows = parse_wimd_csv(CSV.encode("utf-8"))
    values = {(r.lsoa_code, r.indicator_key): r.value for r in rows}
    assert len(values) == 5
    assert values[("W01000003", "deprivation.wimd2025.rank")] == 570
    assert values[("W01000003", "deprivation.wimd2025.decile")] == 3
    assert values[("W01000004", "deprivation.wimd2025.access_to_services.rank")] == 1
    assert all(code.startswith("W010") for code, _ in values)


def test_wimd_accepts_bom_and_unformatted_integer_decimal() -> None:
    csv = CSV.replace("570,Rank,", "570.0,Rank,")
    rows = parse_wimd_csv(("\ufeff" + csv).encode())
    assert rows[0].value == 570


@pytest.mark.parametrize(
    "replacement",
    ["0,Rank,", "1918,Rank,", "1.2,Rank,", "nan,Rank,"],
)
def test_wimd_rejects_invalid_ranks(replacement: str) -> None:
    with pytest.raises(ValueError, match="Out-of-range"):
        parse_wimd_csv(CSV.replace("570,Rank,", replacement).encode())


def test_wimd_rejects_duplicate_observation() -> None:
    csv = CSV + "100,Rank,W01000003,Amlwch Port,WIMD\n"
    with pytest.raises(ValueError, match="Duplicate"):
        parse_wimd_csv(csv.encode())


def test_wimd_rejects_unexpected_export() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        parse_wimd_csv(b"foo,bar\n1,2\n")
