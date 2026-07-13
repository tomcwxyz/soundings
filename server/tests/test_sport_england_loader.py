"""Tests for the Sport England Active Lives loader."""

from typing import Any

import pytest
from sqlalchemy import text

from soundings.adapters.sport_england.loader import (
    SOURCE_ID,
    SportEnglandActiveLivesLoader,
)
from soundings.db.engine import get_engine

# --- _extract (pure) ------------------------------------------------------


def test_extract_maps_rates_to_proportions() -> None:
    rows = [
        {
            "gss_code": "E06000047",
            "la_name": "County Durham",
            "active_rate": 0.6207,
            "fairly_active_rate": 0.125,
            "inactive_rate": 0.2543,
        },
        {
            "gss_code": "E09000001",
            "la_name": "City of London",
            "active_rate": None,  # skipped
            "fairly_active_rate": 0.10,
            "inactive_rate": None,  # skipped
        },
    ]
    out = list(SportEnglandActiveLivesLoader._extract(rows))

    assert ("ltla24:E06000047", "sport.active_share", pytest.approx(0.6207)) in out
    assert ("ltla24:E06000047", "sport.fairly_active_share", pytest.approx(0.125)) in out
    assert ("ltla24:E06000047", "sport.inactive_share", pytest.approx(0.2543)) in out

    # City of London: only fairly_active has a value
    assert ("ltla24:E09000001", "sport.fairly_active_share", pytest.approx(0.10)) in out
    assert not any(p == "ltla24:E09000001" and k == "sport.active_share" for p, k, _ in out)
    assert not any(p == "ltla24:E09000001" and k == "sport.inactive_share" for p, k, _ in out)


def test_extract_skips_rows_without_gss_code() -> None:
    rows = [
        {"gss_code": None, "la_name": "Region header", "active_rate": 50.0},
        {
            "gss_code": "E06000047",
            "la_name": "County Durham",
            "active_rate": 0.60,
            "fairly_active_rate": 0.12,
            "inactive_rate": 0.28,
        },
    ]
    out = list(SportEnglandActiveLivesLoader._extract(rows))
    assert len(out) == 3  # 3 indicators for one LA
    assert all(p == "ltla24:E06000047" for p, _, _ in out)


# --- integration: FK-tolerant UPSERT --------------------------------------


class _StubClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetch_workbook(self) -> bytes:
        return b""

    def read_la_sheet(self, content: bytes):
        return iter(self._rows)


async def _seed() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM data.indicator_value WHERE source_id = :sid"), {"sid": SOURCE_ID}
        )
        await conn.execute(
            text("DELETE FROM data.trend_point WHERE source_id = :sid"), {"sid": SOURCE_ID}
        )
        await conn.execute(
            text(
                "INSERT INTO catalogue.source (id, label, publisher, licence, mode, rate_limit) "
                "VALUES (:sid, 'Sport England', 'Sport England', 'OGL-UK-3.0', 'loader', '{}'::jsonb) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"sid": SOURCE_ID},
        )
        for key, label, unit in [
            ("sport.active_share", "Active", "proportion"),
            ("sport.fairly_active_share", "Fairly Active", "proportion"),
            ("sport.inactive_share", "Inactive", "proportion"),
        ]:
            await conn.execute(
                text(
                    "INSERT INTO catalogue.indicator "
                    "(key, label, unit, source_id, available_at, caveats, related_keys) "
                    "VALUES (:k, :l, :u, :sid, ARRAY['ltla24'], "
                    "'[]'::jsonb, ARRAY[]::text[]) ON CONFLICT (key) DO NOTHING"
                ),
                {"k": key, "l": label, "u": unit, "sid": SOURCE_ID},
            )
        # Insert two known places; leave a third unknown
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES ('ltla24:E06000047', 'ltla24', 'E06000047', 'County Durham'), "
                "       ('ltla24:E09000001', 'ltla24', 'E09000001', 'City of London') "
                "ON CONFLICT (id) DO NOTHING"
            )
        )


@pytest.mark.integration
async def test_loader_upserts_known_places_and_skips_unknown() -> None:
    await _seed()
    rows = [
        {  # known LA → written
            "gss_code": "E06000047",
            "la_name": "County Durham",
            "active_rate": 0.6207,
            "fairly_active_rate": 0.125,
            "inactive_rate": 0.2543,
        },
        {  # unknown LA (not in spine) → skipped
            "gss_code": "E99999999",
            "la_name": "Unknown",
            "active_rate": 0.99,
            "fairly_active_rate": 0.01,
            "inactive_rate": 0.0,
        },
        {  # known LA, missing rates → only fairly_active written
            "gss_code": "E09000001",
            "la_name": "City of London",
            "active_rate": None,
            "fairly_active_rate": 0.10,
            "inactive_rate": None,
        },
    ]
    loader = SportEnglandActiveLivesLoader(get_engine(), client=_StubClient(rows))
    result = await loader.load()

    async with get_engine().connect() as conn:
        db_rows = (
            await conn.execute(
                text(
                    "SELECT place_id, indicator_key, value FROM data.indicator_value "
                    "WHERE source_id = :sid ORDER BY place_id, indicator_key"
                ),
                {"sid": SOURCE_ID},
            )
        ).all()
    got = {(r.place_id, r.indicator_key): float(r.value) for r in db_rows}

    # Unknown LA skipped
    assert not any(pid == "ltla24:E99999999" for pid, _ in got)

    # County Durham: all 3 indicators as proportions
    assert got[("ltla24:E06000047", "sport.active_share")] == pytest.approx(0.6207)
    assert got[("ltla24:E06000047", "sport.fairly_active_share")] == pytest.approx(0.125)
    assert got[("ltla24:E06000047", "sport.inactive_share")] == pytest.approx(0.2543)

    # City of London: only fairly_active
    assert got[("ltla24:E09000001", "sport.fairly_active_share")] == pytest.approx(0.10)
    assert not any(pid == "ltla24:E09000001" and k == "sport.active_share" for pid, k in got)

    # 3 (Durham) + 1 (City of London) = 4 rows
    assert result.rows_written == 4
