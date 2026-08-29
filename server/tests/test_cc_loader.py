"""Integration tests for CharityCommissionLoader.

The loader streams active charities from the bulk client, batch-resolves
their postcodes to LTLA place_ids, and upserts into `data.organisation` +
`data.organisation_operates_in`. Idempotent across re-runs.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text

from soundings.adapters.charity_commission.loader import CharityCommissionLoader
from soundings.adapters.postcodes_io.adapter import PostcodesIoAdapter
from soundings.db.engine import get_engine

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_cc_loader_state() -> AsyncIterator[None]:
    """Wipe data.organisation* + geography.postcode after each test in
    this file. Tests in this module are the only writers to those
    tables right now, and other suites' wipes of data.organisation /
    geography.place would otherwise FK-fail if our rows leaked through.
    """
    yield
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.organisation_operates_in"))
        await conn.execute(text("DELETE FROM data.organisation"))
        await conn.execute(text("DELETE FROM geography.postcode"))


class _StubBulkClient:
    """Stand-in for CharityCommissionBulkClient — yields a fixed list of
    charity dicts shaped like the real client's output."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def iter_active_charities(self) -> AsyncIterator[dict[str, Any]]:
        for row in self._rows:
            yield row

    async def iter_area_of_operation(self) -> AsyncIterator[dict[str, Any]]:
        # These loader tests exercise the registered-address fallback.
        # Dedicated client/mapping tests cover area-of-operation parsing.
        for row in []:
            yield row

    async def iter_classifications(self) -> AsyncIterator[dict[str, Any]]:
        # Structured classification loading is covered separately.
        for row in []:
            yield row


async def _seed_baseline() -> None:
    """Clean slate + the catalogue.source row + the LTLAs we'll claim."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.organisation_operates_in"))
        await conn.execute(text("DELETE FROM data.organisation"))
        await conn.execute(text("DELETE FROM cache.source_cache"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        await conn.execute(
            text(
                "INSERT INTO catalogue.source "
                "(id, label, publisher, licence, mode, rate_limit) "
                "VALUES ('charity_commission', 'Charity Commission', 'CC', "
                "'OGL-UK-3.0', 'loader', '{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            )
        )
        for code in ["E06000004", "E06000001", "E06000002"]:
            await conn.execute(
                text(
                    "INSERT INTO geography.place (id, type, code, name) "
                    "VALUES (:id, 'ltla24', :code, :name)"
                ),
                {"id": f"ltla24:{code}", "code": code, "name": f"Place {code}"},
            )
        # Pre-seed postcode → LTLA so the loader doesn't need to hit
        # postcodes.io in unit tests. Real loads use the bulk_upsert path.
        # NULL ltla24 for ZZ99 9ZZ — represents a known-unresolvable
        # postcode the resolver can short-circuit without an API call.
        for postcode, ltla_code in [
            ("TS181AB", "E06000004"),
            ("TS261AB", "E06000001"),
            ("TS11AB", "E06000002"),
            ("OX42JY", "E06000004"),
            ("ZZ999ZZ", None),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO geography.postcode "
                    "(postcode, ltla24, retrieved_at) VALUES (:p, :ltla, :ret)"
                ),
                {
                    "p": postcode,
                    "ltla": f"ltla24:{ltla_code}" if ltla_code else None,
                    "ret": datetime.now(tz=UTC),
                },
            )


def _ten_charities() -> list[dict[str, Any]]:
    """4 in E06000004, 3 in E06000001, 2 in E06000002, 1 with an unknown postcode."""
    return [
        {
            "registration_number": str(1000 + i),
            "name": f"Charity {i}",
            "postcode": postcode,
            "status": "Registered",
            "classification": ["1", "12"] if i % 2 == 0 else ["3"],
        }
        for i, postcode in enumerate(
            [
                "TS18 1AB",
                "TS18 1AB",
                "TS18 1AB",
                "TS18 1AB",
                "TS26 1AB",
                "TS26 1AB",
                "TS26 1AB",
                "TS1 1AB",
                "TS1 1AB",
                "ZZ99 9ZZ",  # unknown → no place_id resolved
            ]
        )
    ]


async def test_loader_upserts_data_organisation_with_resolved_place_id() -> None:
    await _seed_baseline()
    bulk = _StubBulkClient(_ten_charities())
    postcodes_io = PostcodesIoAdapter(get_engine(), ttl=timedelta(hours=720))
    loader = CharityCommissionLoader(get_engine(), bulk_client=bulk, postcodes_io=postcodes_io)
    result = await loader.load()

    assert result.rows_written == 10

    async with get_engine().connect() as conn:
        org_rows = (
            await conn.execute(
                text(
                    "SELECT id, name, registered_address_place_id, "
                    "classification, source_id "
                    "FROM data.organisation ORDER BY id"
                )
            )
        ).all()
    assert len(org_rows) == 10
    # First charity (id 1000) had postcode TS18 1AB → ltla24:E06000004.
    by_id = {r.id: r for r in org_rows}
    assert by_id["charity_commission:1000"].registered_address_place_id == "ltla24:E06000004"
    assert by_id["charity_commission:1000"].name == "Charity 0"
    assert by_id["charity_commission:1000"].source_id == "charity_commission"
    assert by_id["charity_commission:1000"].classification == ["1", "12"]
    # The ZZ99 charity (id 1009) has an unresolved postcode → null.
    assert by_id["charity_commission:1009"].registered_address_place_id is None


async def test_loader_populates_organisation_operates_in() -> None:
    """Each resolved org gets a row in data.organisation_operates_in
    linking to its registered LTLA. v1 'operates in' approximation."""
    await _seed_baseline()
    bulk = _StubBulkClient(_ten_charities())
    postcodes_io = PostcodesIoAdapter(get_engine(), ttl=timedelta(hours=720))
    loader = CharityCommissionLoader(get_engine(), bulk_client=bulk, postcodes_io=postcodes_io)
    await loader.load()

    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT organisation_id, place_id FROM "
                    "data.organisation_operates_in ORDER BY organisation_id"
                )
            )
        ).all()
    # 9 charities have resolved postcodes (10 total minus the ZZ99 one).
    assert len(rows) == 9
    # Symmetric with registered_address_place_id.
    for row in rows:
        assert row.place_id.startswith("ltla24:")


async def test_loader_is_idempotent_on_re_run() -> None:
    """Two consecutive runs of the same input → same row counts; second
    run UPDATEs retrieved_at without duplicating rows."""
    await _seed_baseline()
    charities = _ten_charities()
    bulk1 = _StubBulkClient(charities)
    bulk2 = _StubBulkClient(charities)
    postcodes_io = PostcodesIoAdapter(get_engine(), ttl=timedelta(hours=720))
    loader = CharityCommissionLoader(get_engine(), bulk_client=bulk1, postcodes_io=postcodes_io)
    first = await loader.load()
    first_time = datetime.now(tz=UTC)

    # Second run with a freshly-instantiated bulk client (the first one
    # has its iterator exhausted).
    loader._bulk_client = bulk2  # type: ignore[attr-defined]
    second = await loader.load()

    assert first.rows_written == 10
    assert second.rows_written == 10

    async with get_engine().connect() as conn:
        count_row = (
            await conn.execute(text("SELECT COUNT(*) AS n FROM data.organisation"))
        ).first()
    assert count_row is not None
    assert count_row.n == 10  # not 20

    async with get_engine().connect() as conn:
        max_retrieved = (
            await conn.execute(
                text(
                    "SELECT MAX(retrieved_at) AS r FROM data.organisation "
                    "WHERE source_id = 'charity_commission'"
                )
            )
        ).first()
    assert max_retrieved is not None
    assert max_retrieved.r is not None
    assert max_retrieved.r >= first_time - timedelta(seconds=1)


async def test_loader_notes_unresolved_postcodes() -> None:
    await _seed_baseline()
    bulk = _StubBulkClient(_ten_charities())
    postcodes_io = PostcodesIoAdapter(get_engine(), ttl=timedelta(hours=720))
    loader = CharityCommissionLoader(get_engine(), bulk_client=bulk, postcodes_io=postcodes_io)
    result = await loader.load()

    assert result.notes is not None
    # 1 charity had an unresolved postcode (the ZZ99 one).
    assert "1" in result.notes
    assert "unresolved" in result.notes.lower() or "postcode" in result.notes.lower()


async def test_loader_writes_raw_jsonb_payload() -> None:
    """The full merged row from the bulk client lands in
    `data.organisation.raw` so v2 enrichment work has access to the
    fields we didn't promote to top-level columns."""
    await _seed_baseline()
    bulk = _StubBulkClient([_ten_charities()[0]])
    postcodes_io = PostcodesIoAdapter(get_engine(), ttl=timedelta(hours=720))
    loader = CharityCommissionLoader(get_engine(), bulk_client=bulk, postcodes_io=postcodes_io)
    await loader.load()

    async with get_engine().connect() as conn:
        row = (
            await conn.execute(
                text("SELECT raw FROM data.organisation WHERE id = 'charity_commission:1000'")
            )
        ).first()
    assert row is not None
    raw = row.raw
    assert raw["registration_number"] == "1000"
    assert raw["status"] == "Registered"
    assert raw["postcode"] == "TS18 1AB"
