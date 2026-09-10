"""Lifecycle coverage for Charity Commission organisation data."""

import csv
import io
import zipfile

import httpx
import pytest
from sqlalchemy import select

from soundings.adapters.charity_commission.client import CharityCommissionBulkClient
from soundings.db.engine import get_engine
from soundings.db.models.data import OrganisationLifecycle

pytestmark = pytest.mark.integration

_COLUMNS = [
    "registered_charity_number",
    "linked_charity_number",
    "charity_name",
    "charity_registration_status",
    "date_of_registration",
    "date_of_removal",
    "charity_contact_postcode",
    "charity_activities",
    "latest_income",
]


def _archive(rows: list[dict[str, str]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        text = io.StringIO()
        writer = csv.DictWriter(text, fieldnames=_COLUMNS, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in _COLUMNS})
        archive.writestr("publicextract.charity.txt", text.getvalue())
    return buffer.getvalue()


def _rows() -> list[dict[str, str]]:
    return [
        {
            "registered_charity_number": "100",
            "linked_charity_number": "0",
            "charity_name": "ACTIVE TRUST",
            "charity_registration_status": "Registered",
            "date_of_registration": "2018-02-03",
            "charity_contact_postcode": "NE1 1AA",
        },
        {
            "registered_charity_number": "200",
            "linked_charity_number": "0",
            "charity_name": "FORMER TRUST",
            "charity_registration_status": "Removed",
            "date_of_registration": "1999-04-05",
            "date_of_removal": "2021-06-07",
            "charity_contact_postcode": "NE2 2BB",
        },
        {
            "registered_charity_number": "100",
            "linked_charity_number": "1",
            "charity_name": "LINKED ENTRY",
            "charity_registration_status": "Registered",
            "date_of_registration": "2018-02-03",
            "charity_contact_postcode": "NE1 1AA",
        },
    ]


async def _client() -> tuple[CharityCommissionBulkClient, httpx.AsyncClient]:
    payload = _archive(_rows())
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    )
    return CharityCommissionBulkClient(http_client=http), http


async def test_main_iterator_keeps_registered_and_removed_main_entries() -> None:
    client, http = await _client()
    try:
        rows = [row async for row in client.iter_main_charities()]
    finally:
        await http.aclose()

    assert [row["registration_number"] for row in rows] == ["100", "200"]
    assert rows[0]["status"] == "Registered"
    assert rows[1]["status"] == "Removed"
    assert rows[1]["date_of_removal"] == "2021-06-07"


async def test_active_iterator_remains_current_state_only() -> None:
    client, http = await _client()
    try:
        rows = [row async for row in client.iter_active_charities()]
    finally:
        await http.aclose()

    assert [row["registration_number"] for row in rows] == ["100"]


async def test_organisation_lifecycle_table_exists() -> None:
    async with get_engine().connect() as conn:
        await conn.execute(select(OrganisationLifecycle.organisation_id).limit(0))
