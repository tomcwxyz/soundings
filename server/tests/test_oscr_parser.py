"""OSCR official-format public bulk register parser quality gates."""

import io
import zipfile

import pytest

from soundings.adapters.oscr.parser import parse_register_csv, parse_register_zip

CSV = (
    "Charity Number,Charity Name,Charity Status,Postcode,Registration Date,"
    "Main Operating Location,Geographical Spread,Income,Expenditure,Purposes\n"
    "SC000001,Local Example,Active,EH47 8BY,26/06/1986,West Lothian,"
    "Local,1000,50,Community\n"
    "SC000002,Other Group,Not Monitored,EH1 2XY,03/04/2004,City of Edinburgh,"
    "Scotland and UK,,100,Education\n"
).encode()


def zip_csv(data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("ScottishCharityRegister.csv", data)
    return buf.getvalue()


def test_parse_register() -> None:
    first, second = parse_register_zip(zip_csv(CSV))
    assert first.number == "SC000001"
    assert first.postcode == "EH478BY"
    assert first.registration_date == "1986-06-26"
    assert first.latest_income == 1000
    assert second.latest_income is None
    assert second.status == "Not Monitored"


def test_reject_duplicate_or_removed_status() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        parse_register_csv(CSV + CSV.split(b"\n")[1] + b"\n")
    with pytest.raises(ValueError, match="Unexpected"):
        parse_register_csv(CSV.replace(b"Not Monitored", b"Removed"))


def test_reject_missing_columns_and_invalid_archive() -> None:
    with pytest.raises(ValueError, match="missing"):
        parse_register_csv(b"Name,Postcode\nCharity,EH1\n")
    with pytest.raises(ValueError, match="not a ZIP"):
        parse_register_zip(b"not a zip")
