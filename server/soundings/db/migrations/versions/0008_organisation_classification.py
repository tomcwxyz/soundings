"""organisation classification codes

Revision ID: 0008_organisation_classification
Revises: 0007_postcode_latlon
Create Date: 2026-07-12

Stores structured CC classification codes (What/Who/How) per charity,
sourced from publicextract.charity_classification.zip.
"""

from alembic import op

revision = "0008_organisation_classification"
down_revision = "0007_postcode_latlon"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE data.organisation_classification (
            organisation_id  VARCHAR(64) NOT NULL
                REFERENCES data.organisation(id) ON DELETE CASCADE,
            classification_type  VARCHAR(16) NOT NULL,
            classification_code  VARCHAR(8)  NOT NULL,
            classification_label VARCHAR(128) NOT NULL,
            PRIMARY KEY (organisation_id, classification_type, classification_code)
        )
        """
    )
    op.execute(
        """
        CREATE INDEX ix_org_classification_type_code
            ON data.organisation_classification (classification_type, classification_code)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS data.organisation_classification;")
