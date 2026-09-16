"""allow multiple temporal intervals per hierarchy pair

Revision ID: 0015_hierarchy_edge_identity
Revises: 0014_current_hierarchy_view
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_hierarchy_edge_identity"
down_revision: str | Sequence[str] | None = "0014_current_hierarchy_view"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CURRENT_VIEW_SQL = """
CREATE VIEW geography.current_place_hierarchy AS
SELECT DISTINCT child_id, parent_id
FROM geography.place_hierarchy
WHERE (valid_from IS NULL OR valid_from <= CURRENT_DATE)
  AND (valid_to IS NULL OR valid_to > CURRENT_DATE)
"""


def upgrade() -> None:
    op.execute("DROP VIEW geography.current_place_hierarchy")

    # BIGSERIAL backfills existing rows and gives future edges a stable identity.
    op.execute("ALTER TABLE geography.place_hierarchy ADD COLUMN id BIGSERIAL")
    op.drop_constraint(
        "place_hierarchy_pkey",
        "place_hierarchy",
        schema="geography",
        type_="primary",
    )
    op.create_primary_key(
        "place_hierarchy_pkey",
        "place_hierarchy",
        ["id"],
        schema="geography",
    )

    # Keep exactly one undated OGP snapshot per canonical relationship.
    op.create_index(
        "uq_place_hierarchy_current_snapshot",
        "place_hierarchy",
        ["child_id", "parent_id"],
        unique=True,
        schema="geography",
        postgresql_where=sa.text("valid_from IS NULL AND valid_to IS NULL"),
    )
    # A dated relationship is identified by its start. ONS may revise its end
    # date on a later CHD refresh, so valid_to is deliberately not part of the key.
    op.create_index(
        "uq_place_hierarchy_dated_start",
        "place_hierarchy",
        ["child_id", "parent_id", "valid_from"],
        unique=True,
        schema="geography",
        postgresql_where=sa.text("valid_from IS NOT NULL"),
    )

    op.execute(_CURRENT_VIEW_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW geography.current_place_hierarchy")
    op.drop_index(
        "uq_place_hierarchy_dated_start",
        table_name="place_hierarchy",
        schema="geography",
    )
    op.drop_index(
        "uq_place_hierarchy_current_snapshot",
        table_name="place_hierarchy",
        schema="geography",
    )
    op.drop_constraint(
        "place_hierarchy_pkey",
        "place_hierarchy",
        schema="geography",
        type_="primary",
    )

    # The previous schema could represent only one row per child/parent pair.
    # Keep the lowest-id row if a downgrade follows temporal ingestion.
    op.execute(
        """
        DELETE FROM geography.place_hierarchy newer
        USING geography.place_hierarchy older
        WHERE newer.child_id = older.child_id
          AND newer.parent_id = older.parent_id
          AND newer.id > older.id
        """
    )
    op.create_primary_key(
        "place_hierarchy_pkey",
        "place_hierarchy",
        ["child_id", "parent_id"],
        schema="geography",
    )
    op.drop_column("place_hierarchy", "id", schema="geography")
    op.execute(_CURRENT_VIEW_SQL)
