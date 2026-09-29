"""Test that the theme seeder inserts the 12 initial observation themes.

Requires a running Postgres test database (``soundings_test`` on port 5433)
that has already been migrated up to revision 0009.
"""

import pytest
from sqlalchemy import text

from soundings.db.engine import get_engine
from soundings.seed.themes import seed_themes

pytestmark = [pytest.mark.integration]

EXPECTED_KEYS = {
    "housing",
    "health",
    "mental_health",
    "employment",
    "education",
    "crime",
    "food_insecurity",
    "debt",
    "immigration_asylum",
    "digital_exclusion",
    "social_isolation",
    "climate_environment",
}


async def test_seed_themes_inserts_12_rows() -> None:
    """seed_themes inserts exactly the 12 expected theme keys."""
    engine = get_engine()
    await seed_themes(engine)

    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT key FROM catalogue.theme ORDER BY key"))
        keys = {row[0] for row in result}

    assert keys == EXPECTED_KEYS
    assert len(keys) == 12


async def test_seed_themes_is_idempotent() -> None:
    """Running seed_themes twice leaves exactly 12 rows (ON CONFLICT DO NOTHING)."""
    engine = get_engine()
    await seed_themes(engine)
    await seed_themes(engine)

    async with engine.connect() as conn:
        count = await conn.scalar(text("SELECT count(*) FROM catalogue.theme"))

    assert count == 12
