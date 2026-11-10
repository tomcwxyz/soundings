"""withdraw population density (values equalled population counts)

Revision ID: 0017_withdraw_population_density
Revises: 0016_observation_schema
Create Date: 2026-11-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_withdraw_population_density"
down_revision: str | Sequence[str] | None = "0016_observation_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The mapping reused the population.total query with no area denominator, so
# all 5,920 paired observations checked on 9 November 2026 were identical to
# the population count. See evaluation/results/2026-11-09/report.md.
RETIRED_KEYS = ("population.density",)


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
