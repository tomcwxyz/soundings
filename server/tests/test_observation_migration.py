"""Verify the observation-schema migration creates the expected tables.

This is an integration test — it requires a running Postgres test database
(``soundings_test`` on port 5433).  It runs ``alembic upgrade head`` against
that database (via a subprocess to avoid the asyncio.run() nesting issue)
and then checks, via ``to_regclass``, that the three new tables created by
revision 0009 exist:

  - catalogue.theme
  - contribution.contributor_session
  - data.observation

It also sanity-checks the check constraints and indexes.
"""

import subprocess
import sys

import pytest
from sqlalchemy import text

from soundings.db.engine import get_engine

pytestmark = [pytest.mark.integration]


def _run_alembic_upgrade_head() -> None:
    """Run ``alembic upgrade head`` in a subprocess.

    env.py uses ``asyncio.run()`` to drive the async engine, which cannot
    be called from within the already-running pytest-asyncio event loop, so
    we shell out to a fresh Python process instead.
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=".",
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"alembic upgrade head failed (rc={result.returncode}):\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


async def test_observation_migration_creates_tables() -> None:
    """Apply all migrations and verify the three new tables exist."""
    _run_alembic_upgrade_head()

    engine = get_engine()
    async with engine.connect() as conn:
        for schema, table in (
            ("catalogue", "theme"),
            ("contribution", "contributor_session"),
            ("data", "observation"),
        ):
            exists = await conn.scalar(
                text("SELECT to_regclass(:rel)"), {"rel": f"{schema}.{table}"}
            )
            assert exists is not None, f"{schema}.{table} should exist after migration"


async def test_observation_check_constraints() -> None:
    """The observation table has the expected CHECK constraints."""
    _run_alembic_upgrade_head()

    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT con.conname "
                "FROM pg_constraint con "
                "JOIN pg_class cls ON cls.oid = con.conrelid "
                "JOIN pg_namespace nsp ON nsp.oid = cls.relnamespace "
                "WHERE nsp.nspname = 'data' "
                "AND cls.relname = 'observation' "
                "AND con.contype = 'c' "
                "ORDER BY con.conname"
            )
        )
        names = {row[0] for row in result}
    assert "ck_observation_evidence_type" in names
    assert "ck_observation_confidence" in names


async def test_observation_indexes() -> None:
    """The observation table has the expected indexes."""
    _run_alembic_upgrade_head()

    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'data' AND tablename = 'observation' "
                "ORDER BY indexname"
            )
        )
        names = {row[0] for row in result}
    assert "ix_observation_place_id" in names
    assert "ix_observation_theme" in names
    assert "ix_observation_indicator_key" in names
    assert "ix_observation_organisation_id" in names
