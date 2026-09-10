"""add organisation lifecycle facts

Revision ID: 0016_organisation_lifecycle
Revises: 0015_hierarchy_edge_identity
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_organisation_lifecycle"
down_revision: str | Sequence[str] | None = "0015_hierarchy_edge_identity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "organisation_lifecycle",
        sa.Column("organisation_id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("registered_on", sa.Date(), nullable=True),
        sa.Column("removed_on", sa.Date(), nullable=True),
        sa.Column("postcode", sa.String(length=16), nullable=True),
        sa.Column(
            "registered_address_place_id",
            sa.String(),
            sa.ForeignKey("geography.place.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_id",
            sa.String(),
            sa.ForeignKey("catalogue.source.id"),
            nullable=False,
        ),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        schema="data",
    )
    op.create_index(
        "ix_organisation_lifecycle_registered_on",
        "organisation_lifecycle",
        ["registered_on"],
        schema="data",
    )
    op.create_index(
        "ix_organisation_lifecycle_removed_on",
        "organisation_lifecycle",
        ["removed_on"],
        schema="data",
    )
    op.create_index(
        "ix_organisation_lifecycle_place",
        "organisation_lifecycle",
        ["registered_address_place_id"],
        schema="data",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_organisation_lifecycle_place",
        table_name="organisation_lifecycle",
        schema="data",
    )
    op.drop_index(
        "ix_organisation_lifecycle_removed_on",
        table_name="organisation_lifecycle",
        schema="data",
    )
    op.drop_index(
        "ix_organisation_lifecycle_registered_on",
        table_name="organisation_lifecycle",
        schema="data",
    )
    op.drop_table("organisation_lifecycle", schema="data")
