"""add temporal validity to place hierarchy

Revision ID: 0013_temporal_place_hierarchy
Revises: 0012_loader_run_provenance
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_temporal_place_hierarchy"
down_revision: str | Sequence[str] | None = "0012_loader_run_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "place_hierarchy",
        sa.Column("valid_from", sa.Date(), nullable=True),
        schema="geography",
    )
    op.add_column(
        "place_hierarchy",
        sa.Column("valid_to", sa.Date(), nullable=True),
        schema="geography",
    )
    op.create_index(
        "ix_place_hierarchy_child_validity",
        "place_hierarchy",
        ["child_id", "valid_from", "valid_to"],
        unique=False,
        schema="geography",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_place_hierarchy_child_validity",
        table_name="place_hierarchy",
        schema="geography",
    )
    op.drop_column("place_hierarchy", "valid_to", schema="geography")
    op.drop_column("place_hierarchy", "valid_from", schema="geography")
