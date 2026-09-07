"""Tests for full-corpus GrantNav download and refresh."""

import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest

from soundings.grants.grantnav_refresh import download_grantnav_csv, refresh_grant_index


def _csv_payload() -> bytes:
    header = (
        "Identifier,Title,Description,Currency,Amount Awarded,Award Date,"
        "Funding Org:Identifier,Funding Org:Name,Recipient Org:Identifier,"
        "Recipient Org:Name\n"
    )
    row = (
        "360G-Test-1,A grant,Community work,GBP,10000,2026-01-01,"
        "GB-CHC-1,Funder,GB-CHC-2,Recipient\n"
    )
    return (header + row * 4).encode()


@pytest.mark.asyncio
async def test_download_grantnav_csv_streams_valid_export_with_provenance(tmp_path: Path) -> None:
    payload = _csv_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept"].startswith("text/csv")
        assert "Soundings/360Giving-index" in request.headers["user-agent"]
        return httpx.Response(
            200,
            content=payload,
            headers={
                "Content-Type": "text/csv",
                "ETag": '"snapshot-123"',
                "Last-Modified": "Mon, 07 Sep 2026 05:00:00 GMT",
            },
        )

    destination = tmp_path / "grantnav.csv"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        download = await download_grantnav_csv(
            destination,
            url="https://example.test/grants.csv",
            http_client=client,
        )

    assert download.bytes_written == len(payload)
    assert download.sha256 == hashlib.sha256(payload).hexdigest()
    assert download.requested_url == "https://example.test/grants.csv"
    assert download.source_url == "https://example.test/grants.csv"
    assert download.content_type == "text/csv"
    assert download.etag == '"snapshot-123"'
    assert download.last_modified == "Mon, 07 Sep 2026 05:00:00 GMT"
    assert destination.read_bytes() == payload


@pytest.mark.asyncio
async def test_download_rejects_non_grant_csv(tmp_path: Path) -> None:
    destination = tmp_path / "not-grants.csv"
    payload = ("hello,world\n" + "x,y\n" * 100).encode()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
    ) as client:
        with pytest.raises(ValueError, match="missing required columns"):
            await download_grantnav_csv(
                destination,
                url="https://example.test/not-grants.csv",
                http_client=client,
            )


@pytest.mark.asyncio
async def test_refresh_imports_full_corpus_with_snapshot_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _csv_payload()
    observed: dict[str, Any] = {}

    async def fake_import(
        engine: Any,
        path: Path,
        *,
        full_corpus: bool,
        provenance: dict[str, Any] | None = None,
    ) -> int:
        observed["engine"] = engine
        observed["path"] = path
        observed["full_corpus"] = full_corpus
        observed["provenance"] = provenance
        observed["exists_during_import"] = path.exists()
        return 4

    monkeypatch.setattr(
        "soundings.grants.grantnav_refresh.import_grantnav_csv",
        fake_import,
    )
    fake_engine = object()

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=payload,
                headers={
                    "Content-Type": "text/csv",
                    "ETag": '"snapshot-456"',
                },
            )
        )
    ) as client:
        rows = await refresh_grant_index(
            fake_engine,  # type: ignore[arg-type]
            url="https://example.test/grants.csv",
            http_client=client,
            temp_dir=tmp_path,
        )

    assert rows == 4
    assert observed["engine"] is fake_engine
    assert observed["full_corpus"] is True
    assert observed["exists_during_import"] is True
    assert not Path(observed["path"]).exists()
    provenance = observed["provenance"]
    assert provenance["acquisition"] == "grantnav-http"
    assert provenance["requested_url"] == "https://example.test/grants.csv"
    assert provenance["sha256"] == hashlib.sha256(payload).hexdigest()
    assert provenance["bytes"] == len(payload)
    assert provenance["etag"] == '"snapshot-456"'
