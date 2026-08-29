"""store conversation messages with cached ask answers

Revision ID: 0009_answer_cache_messages
Revises: 0008_organisation_classification
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_answer_cache_messages"
down_revision: str | Sequence[str] | None = "0008_organisation_classification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "answer_cache",
        sa.Column(
            "messages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema="cache",
    )


def downgrade() -> None:
    op.drop_column("answer_cache", "messages", schema="cache")
