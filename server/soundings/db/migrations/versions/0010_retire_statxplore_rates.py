"""retire misleading Stat-Xplore rate indicators

Revision ID: 0010_retire_statxplore_rates
Revises: 0009_answer_cache_messages
Create Date: 2026-08-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_retire_statxplore_rates"
down_revision: str | Sequence[str] | None = "0009_answer_cache_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RETIRED_KEYS = (
    "economy.claimant_count_rate",
    "deprivation.child_poverty_ahc",
)


def upgrade() -> None:
    """Remove retired catalogue rows and any persisted values that reference them."""
    keys = ", ".join(f"'{key}'" for key in RETIRED_KEYS)
    op.execute(f"DELETE FROM data.trend_point WHERE indicator_key IN ({keys})")
    op.execute(f"DELETE FROM data.indicator_value WHERE indicator_key IN ({keys})")
    op.execute(f"DELETE FROM catalogue.indicator WHERE key IN ({keys})")


def downgrade() -> None:
    """Restore the former catalogue contracts, but not deleted historical values."""
    op.execute(
        """
        INSERT INTO catalogue.indicator (
            key, label, description, unit, higher_is, source_id,
            available_at, refresh_cadence, caveats, related_keys
        ) VALUES
        (
            'economy.claimant_count_rate',
            'Claimant count rate (16-64)',
            'People claiming Jobseeker''s Allowance plus Universal Credit claimants required to seek work, as a proportion of the resident population aged 16–64.',
            'proportion',
            'worse',
            'dwp.statxplore',
            ARRAY['ltla24','utla24','westminster_constituency_24','region','country'],
            'monthly',
            '[]'::jsonb,
            ARRAY[]::varchar[]
        ),
        (
            'deprivation.child_poverty_ahc',
            'Children in low-income families (after housing costs)',
            'Number and percentage of children aged under 16 in households with relative low income (below 60% of median, after housing costs).',
            'proportion',
            'worse',
            'dwp.statxplore',
            ARRAY['ltla24','utla24','westminster_constituency_24'],
            'annual',
            '["Local-area child poverty AHC statistics published from FYE 2024 onwards following DWP discovery work.","Three-year averages recommended for sub-regional series due to sample noise."]'::jsonb,
            ARRAY[]::varchar[]
        )
        ON CONFLICT (key) DO NOTHING
        """
    )
