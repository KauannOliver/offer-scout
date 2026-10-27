"""Record evidence from the official affiliate Link Builder."""

import sqlalchemy as sa
from alembic import op

revision = "0003_affiliate_link_evidence"
down_revision = "0002_operational_dashboard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "affiliate_links",
        sa.Column("generation_metadata", sa.JSON(), nullable=False, server_default="{}"),
    )


def downgrade() -> None:
    op.drop_column("affiliate_links", "generation_metadata")
