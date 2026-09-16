"""add searchable 360Giving grant index

Revision ID: 0011_grant_query_index
Revises: 0010_retire_statxplore_rates
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_grant_query_index"
down_revision: str | Sequence[str] | None = "0010_retire_statxplore_rates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "grant_record",
        sa.Column("title", sa.Text(), nullable=True),
        schema="data",
    )
    op.add_column(
        "grant_record",
        sa.Column("funder_name", sa.Text(), nullable=True),
        schema="data",
    )
    op.add_column(
        "grant_record",
        sa.Column("recipient_name", sa.Text(), nullable=True),
        schema="data",
    )
    op.add_column(
        "grant_record",
        sa.Column("recipient_external_id", sa.String(length=128), nullable=True),
        schema="data",
    )
    op.add_column(
        "grant_record",
        sa.Column("programme", sa.Text(), nullable=True),
        schema="data",
    )
    op.add_column(
        "grant_record",
        sa.Column("raw", postgresql.JSONB(), nullable=True),
        schema="data",
    )

    # Keep FTS as a database concern: generated from the human-readable fields
    # and maintained automatically on every insert/update.
    op.execute(
        """
        ALTER TABLE data.grant_record
        ADD COLUMN search_document tsvector GENERATED ALWAYS AS (
          to_tsvector(
            'english',
            coalesce(title, '') || ' ' ||
            coalesce(purpose, '') || ' ' ||
            coalesce(programme, '') || ' ' ||
            coalesce(funder_name, '') || ' ' ||
            coalesce(recipient_name, '')
          )
        ) STORED
        """
    )

    op.create_index(
        "ix_grant_record_search_document",
        "grant_record",
        ["search_document"],
        schema="data",
        postgresql_using="gin",
    )
    op.create_index(
        "ix_grant_record_awarded_on",
        "grant_record",
        ["awarded_on"],
        schema="data",
    )
    op.create_index(
        "ix_grant_record_funder_id",
        "grant_record",
        ["funder_id"],
        schema="data",
    )
    op.create_index(
        "ix_grant_record_recipient_external_id",
        "grant_record",
        ["recipient_external_id"],
        schema="data",
    )
    op.create_index(
        "ix_grant_record_beneficiary_place_ids",
        "grant_record",
        ["beneficiary_place_ids"],
        schema="data",
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_grant_record_beneficiary_place_ids",
        table_name="grant_record",
        schema="data",
    )
    op.drop_index(
        "ix_grant_record_recipient_external_id",
        table_name="grant_record",
        schema="data",
    )
    op.drop_index(
        "ix_grant_record_funder_id",
        table_name="grant_record",
        schema="data",
    )
    op.drop_index(
        "ix_grant_record_awarded_on",
        table_name="grant_record",
        schema="data",
    )
    op.drop_index(
        "ix_grant_record_search_document",
        table_name="grant_record",
        schema="data",
    )
    op.drop_column("grant_record", "search_document", schema="data")
    op.drop_column("grant_record", "raw", schema="data")
    op.drop_column("grant_record", "programme", schema="data")
    op.drop_column("grant_record", "recipient_external_id", schema="data")
    op.drop_column("grant_record", "recipient_name", schema="data")
    op.drop_column("grant_record", "funder_name", schema="data")
    op.drop_column("grant_record", "title", schema="data")
