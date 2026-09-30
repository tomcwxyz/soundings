"""Tests for the PPFI loader."""

from typing import Any

import pytest
from sqlalchemy import text

from soundings.adapters.ppfi.loader import (
    CAVEATS,
    PPFI_COLUMNS,
    SOURCE_ID,
    PpfiLoader,
    _coerce_decile,
)
from soundings.db.engine import get_engine


def test_coerce_decile_accepts_only_integer_deciles() -> None:
    assert _coerce_decile(1) == 1.0
    assert _coerce_decile("10") == 10.0
    assert _coerce_decile(0) is None
    assert _coerce_decile(11) is None
    assert _coerce_decile(2.5) is None
    assert _coerce_decile("not-a-number") is None


def test_extract_maps_combined_and_domain_deciles() -> None:
    rows = [
        {
            "LSOA21CD": "E01000001",
            "pp_dec_combined": 1,
            "pp_dec_domain_supermarket_proximity": 2,
            "pp_dec_domain_supermarket_accessibility": 3,
            "pp_dec_domain_ecommerce_access": 4,
            "pp_dec_domain_nonsupermarket_proximity": 5,
            "pp_dec_domain_socio_demographic": 6,
            "pp_dec_domain_food_for_families": 7,
            "pp_dec_domain_fuel_poverty": 8,
        },
        {
            "LSOA21CD": "W01000001",  # technical mirror is England-only
            "pp_dec_combined": 1,
        },
        {
            "LSOA21CD": "E01000002",
            "pp_dec_combined": 0,  # invalid/missing sentinel
        },
    ]

    out = list(PpfiLoader._extract(rows))

    assert ("lsoa21:E01000001", "food.ppfi.overall_decile", 1.0) in out
    assert ("lsoa21:E01000001", "food.ppfi.fuel_poverty_decile", 8.0) in out
    assert len(out) == 8
    assert not any(place_id.startswith("lsoa21:W") for place_id, _, _ in out)


class _StubClient:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def fetch_geojson(self) -> bytes:
        return b"{}"

    def iter_rows(self, content: bytes):  # type: ignore[no-untyped-def]
        return iter(self._rows)


async def _seed() -> None:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM data.indicator_value"))
        await conn.execute(text("DELETE FROM data.trend_point"))
        await conn.execute(text("DELETE FROM geography.postcode"))
        await conn.execute(text("DELETE FROM geography.place_hierarchy"))
        await conn.execute(text("DELETE FROM geography.place"))
        await conn.execute(
            text(
                "INSERT INTO catalogue.source "
                "(id, label, publisher, licence, mode, rate_limit) "
                "VALUES ('hasp.ppfi', 'PPFI', 'HASP', 'OGL-UK-3.0', "
                "'loader', '{}'::jsonb) ON CONFLICT (id) DO NOTHING"
            )
        )
        for indicator_key in PPFI_COLUMNS.values():
            await conn.execute(
                text(
                    "INSERT INTO catalogue.indicator "
                    "(key, label, unit, source_id, available_at, caveats, related_keys) "
                    "VALUES (:key, :label, 'decile', 'hasp.ppfi', ARRAY['lsoa21'], "
                    "'[]'::jsonb, ARRAY[]::text[]) ON CONFLICT (key) DO NOTHING"
                ),
                {"key": indicator_key, "label": indicator_key},
            )
        await conn.execute(
            text(
                "INSERT INTO geography.place (id, type, code, name) "
                "VALUES ('lsoa21:E01000001', 'lsoa21', 'E01000001', 'LSOA 1')"
            )
        )


@pytest.mark.integration
async def test_loader_upserts_deciles_with_methodology_caveats() -> None:
    await _seed()
    row = {"LSOA21CD": "E01000001"}
    for index, column in enumerate(PPFI_COLUMNS, start=1):
        row[column] = min(index, 10)

    loader = PpfiLoader(get_engine(), client=_StubClient([row]))
    result = await loader.load()

    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT indicator_key, value, caveats FROM data.indicator_value "
                    "WHERE source_id = :sid ORDER BY indicator_key"
                ),
                {"sid": SOURCE_ID},
            )
        ).all()

    assert result.rows_written == 8
    assert len(rows) == 8
    assert all(1 <= float(row.value) <= 10 for row in rows)
    assert all(CAVEATS[0] in list(row.caveats) for row in rows)
