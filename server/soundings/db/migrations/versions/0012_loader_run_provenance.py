"""add structured loader-run provenance

Revision ID: 0012_loader_run_provenance
Revises: 0011_grant_query_index
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_loader_run_provenance"
down_revision: str | Sequence[str] | None = "0011_grant_query_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "loader_run",
        sa.Column(
            "provenance",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema="data",
    )


def downgrade() -> None:
    op.drop_column("loader_run", "provenance", schema="data")
