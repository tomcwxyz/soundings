"""Unit tests for the Sport England Active Lives client."""

import io

import openpyxl

from soundings.adapters.sport_england.client import SportEnglandActiveLivesClient


def _make_workbook(sheet_name: str = "Table 3 Levels Local Authorit") -> bytes:
    """Create a minimal xlsx mimicking the Active Lives Table 3 layout.

    The real sheet has 8 header rows before data, and data columns at
    specific offsets. We replicate the key columns:
    - col 0: county GSS code (or None for district rows)
    - col 1: county name
    - col 2: district GSS code
    - col 3: district name
    - col 33: Active rate (Nov 2024-25)
    - col 38: Fairly Active rate
    - col 43: Inactive rate
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name

    # 8 header rows (rows 0-7)
    for _ in range(8):
        ws.append([None] * 50)

    # Data rows — mix of county-level (col 0) and district-level (col 2)
    # Row 1: district-level (col 2 has code, col 0 is empty)
    row1 = [None] * 50
    row1[2] = "E06000031"  # Peterborough
    row1[3] = "Peterborough"
    row1[34] = 0.5799  # Active rate (0-1 proportion)
    row1[38] = 0.1399  # Fairly Active rate
    row1[42] = 0.2802  # Inactive rate
    ws.append(row1)

    # Row 2: county-level (col 0 has code, col 2 is empty)
    row2 = [None] * 50
    row2[0] = "E10000012"  # Essex
    row2[1] = "Essex"
    row2[34] = 0.55
    row2[38] = 0.15
    row2[42] = 0.30
    ws.append(row2)

    # Row 3: missing data (rates are None)
    row3 = [None] * 50
    row3[2] = "E09000001"  # City of London
    row3[3] = "City of London"
    row3[34] = None
    row3[38] = None
    row3[42] = None
    ws.append(row3)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_read_la_sheet_parses_district_level() -> None:
    client = SportEnglandActiveLivesClient()
    rows = list(client.read_la_sheet(_make_workbook()))
    assert len(rows) == 3

    # District-level row (Peterborough)
    r1 = rows[0]
    assert r1["gss_code"] == "E06000031"
    assert r1["la_name"] == "Peterborough"
    assert r1["active_rate"] == 0.5799
    assert r1["fairly_active_rate"] == 0.1399
    assert r1["inactive_rate"] == 0.2802


def test_read_la_sheet_parses_county_level_fallback() -> None:
    client = SportEnglandActiveLivesClient()
    rows = list(client.read_la_sheet(_make_workbook()))
    # County-level row (Essex) falls back to col 0
    r2 = rows[1]
    assert r2["gss_code"] == "E10000012"
    assert r2["la_name"] == "Essex"
    assert r2["active_rate"] == 0.55


def test_read_la_sheet_handles_missing_rates() -> None:
    client = SportEnglandActiveLivesClient()
    rows = list(client.read_la_sheet(_make_workbook()))
    r3 = rows[2]
    assert r3["gss_code"] == "E09000001"
    assert r3["active_rate"] is None
    assert r3["inactive_rate"] is None


def test_read_la_sheet_finds_truncated_sheet_name() -> None:
    # Excel truncates sheet names to 31 chars
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Table 3 Levels Local Auth"  # 26 chars — prefix match
    for _ in range(8):
        ws.append([None] * 50)
    row = [None] * 50
    row[2] = "E06000047"
    row[3] = "County Durham"
    row[34] = 0.60
    ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)

    client = SportEnglandActiveLivesClient()
    rows = list(client.read_la_sheet(buf.getvalue()))
    assert len(rows) == 1
    assert rows[0]["gss_code"] == "E06000047"
    assert rows[0]["active_rate"] == 0.60
