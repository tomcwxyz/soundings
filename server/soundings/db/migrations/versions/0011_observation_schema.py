"""observation schema

Revision ID: 0011_observation_schema
Revises: 0010_retire_statxplore_rates
Create Date: 2026-08-24

Adds three new tables for the observations MVP (see
docs/plans/2026-08-24-observations-mvp.md):

  - catalogue.theme           — theme controlled vocabulary
  - contribution.contributor_session — magic-link auth sessions
  - data.observation          — the core observation table

The contribution schema is created inline because it did not previously
exist; we also add it to MANAGED_SCHEMAS in env.py so Alembic manages it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0011_observation_schema"
down_revision: str | Sequence[str] | None = "0010_retire_statxplore_rates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # catalogue.theme — theme controlled vocabulary
    # ------------------------------------------------------------------
    op.create_table(
        "theme",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("key"),
        schema="catalogue",
    )

    # ------------------------------------------------------------------
    # contribution schema + contribution.contributor_session
    # ------------------------------------------------------------------
    op.execute("CREATE SCHEMA IF NOT EXISTS contribution")

    op.create_table(
        "contributor_session",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("organisation_id", sa.String(length=64), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["data.organisation.id"],
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_contributor_session_expires",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="contribution",
    )

    # ------------------------------------------------------------------
    # data.observation — the core observation table
    # ------------------------------------------------------------------
    op.create_table(
        "observation",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.func.gen_random_uuid(),
        ),
        sa.Column("organisation_id", sa.String(length=64), nullable=False),
        sa.Column("place_id", sa.String(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("theme", sa.String(length=64), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("indicator_key", sa.String(length=128), nullable=True),
        sa.Column("value", sa.Numeric(), nullable=True),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("evidence_type", sa.String(length=16), nullable=False),
        sa.Column("methodology_note", sa.Text(), nullable=True),
        sa.Column("confidence", sa.String(length=8), nullable=False),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organisation_id"],
            ["data.organisation.id"],
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["geography.place.id"],
        ),
        sa.ForeignKeyConstraint(
            ["theme"],
            ["catalogue.theme.key"],
        ),
        sa.ForeignKeyConstraint(
            ["indicator_key"],
            ["catalogue.indicator.key"],
        ),
        sa.CheckConstraint(
            "evidence_type IN ('quantitative', 'qualitative')",
            name="ck_observation_evidence_type",
        ),
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low')",
            name="ck_observation_confidence",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="data",
    )
    op.create_index(
        "ix_observation_place_id",
        "observation",
        ["place_id"],
        schema="data",
    )
    op.create_index(
        "ix_observation_theme",
        "observation",
        ["theme"],
        schema="data",
    )
    op.create_index(
        "ix_observation_indicator_key",
        "observation",
        ["indicator_key"],
        schema="data",
    )
    op.create_index(
        "ix_observation_organisation_id",
        "observation",
        ["organisation_id"],
        schema="data",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_observation_organisation_id", schema="data")
    op.drop_index("ix_observation_indicator_key", schema="data")
    op.drop_index("ix_observation_theme", schema="data")
    op.drop_index("ix_observation_place_id", schema="data")
    op.drop_table("observation", schema="data")
    op.drop_table("contributor_session", schema="contribution")
    op.execute("DROP SCHEMA IF EXISTS contribution")
    op.drop_table("theme", schema="catalogue")
