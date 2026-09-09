"""add canonical current place hierarchy view

Revision ID: 0014_current_place_hierarchy_view
Revises: 0013_temporal_place_hierarchy
Create Date: 2026-09-09
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_current_place_hierarchy_view"
down_revision: str | Sequence[str] | None = "0013_temporal_place_hierarchy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CURRENT_VIEW_SQL = """
CREATE VIEW geography.current_place_hierarchy AS
SELECT child_id, parent_id
FROM geography.place_hierarchy
WHERE (valid_from IS NULL OR valid_from <= CURRENT_DATE)
  AND (valid_to IS NULL OR valid_to > CURRENT_DATE)
"""


def upgrade() -> None:
    op.execute(_CURRENT_VIEW_SQL)


def downgrade() -> None:
    op.execute("DROP VIEW geography.current_place_hierarchy")
