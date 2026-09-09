import argparse
import io
import zipfile

import httpx
import pytest

from soundings.adapters.ons_geography.chd_inspect import (
    _non_negative_int,
    inspect_remote_chd,
)


def _archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as zf:
        zf.writestr(
            "Geography_History.csv",
            "OLD_CODE,NEW_CODE,CHANGE_TYPE,OPER_DATE\nA,B,Replacement,01/04/2020\n",
        )
        zf.writestr(
            "Geography_Hierarchies.csv",
            "CHILD,PARENT,AS_AT\nA,B,2020-01-01\n",
        )
    return buffer.getvalue()


async def test_inspect_remote_chd_returns_serialisable_inventory() -> None:
    url = "https://example.invalid/chd.zip"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == url
        return httpx.Response(200, content=_archive())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        report = await inspect_remote_chd(url, sample_size=1, http_client=client)

    assert report["source_url"] == url
    assert report["table_count"] == 2
    assert report["history_table_count"] == 1
    assert report["hierarchy_table_count"] == 1
    tables = report["tables"]
    assert isinstance(tables, list)
    assert {table["kind"] for table in tables} == {"history", "hierarchy"}


@pytest.mark.parametrize(("raw", "expected"), [("0", 0), ("2", 2), ("10", 10)])
def test_non_negative_int_accepts_valid_values(raw: str, expected: int) -> None:
    assert _non_negative_int(raw) == expected


def test_non_negative_int_rejects_negative_values() -> None:
    with pytest.raises(argparse.ArgumentTypeError, match="non-negative"):
        _non_negative_int("-1")
