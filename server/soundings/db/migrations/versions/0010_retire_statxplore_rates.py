"""retire misleading Stat-Xplore rate indicators

Revision ID: 0010_retire_statxplore_rates
Revises: 0009_answer_cache_messages
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
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
    """Remove retired catalogue rows and persisted values that reference them."""
    metadata = sa.MetaData()
    trend_point = sa.Table(
        "trend_point",
        metadata,
        sa.Column("indicator_key", sa.String()),
        schema="data",
    )
    indicator_value = sa.Table(
        "indicator_value",
        metadata,
        sa.Column("indicator_key", sa.String()),
        schema="data",
    )
    indicator = sa.Table(
        "indicator",
        metadata,
        sa.Column("key", sa.String()),
        schema="catalogue",
    )

    bind = op.get_bind()
    bind.execute(sa.delete(trend_point).where(trend_point.c.indicator_key.in_(RETIRED_KEYS)))
    bind.execute(
        sa.delete(indicator_value).where(indicator_value.c.indicator_key.in_(RETIRED_KEYS))
    )
    bind.execute(sa.delete(indicator).where(indicator.c.key.in_(RETIRED_KEYS)))


def downgrade() -> None:
    """Retirement is intentionally irreversible; deleted observations are not restored."""
