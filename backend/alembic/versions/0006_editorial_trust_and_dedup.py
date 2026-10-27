"""Add editorial publications, public permalinks and strong queue deduplication."""

import sqlalchemy as sa
from alembic import op

revision = "0006_editorial_trust_and_dedup"
down_revision = "0005_operational_completion"
branch_labels = None
depends_on = None

ACTIVE_STATUSES = (
    "discovered",
    "selected",
    "link_pending",
    "creative_pending",
    "ready",
    "publishing",
    "dry_run",
    "retry",
)


def upgrade() -> None:
    op.add_column(
        "publication_queue",
        sa.Column("publication_kind", sa.String(30), nullable=False, server_default="GOOD_OFFER"),
    )
    op.add_column(
        "publication_queue",
        sa.Column("editorial_payload", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index(
        "ix_publication_queue_publication_kind", "publication_queue", ["publication_kind"]
    )

    op.add_column(
        "publications",
        sa.Column("publication_kind", sa.String(30), nullable=False, server_default="GOOD_OFFER"),
    )
    op.add_column("publications", sa.Column("public_message_url", sa.Text(), nullable=True))
    op.add_column(
        "publications",
        sa.Column("delivery_status", sa.String(30), nullable=False, server_default="SENT"),
    )
    op.add_column("publications", sa.Column("dedupe_key", sa.String(255), nullable=True))
    op.add_column(
        "publications",
        sa.Column("historical_duplicate", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_publications_publication_kind", "publications", ["publication_kind"])
    op.create_index("ix_publications_delivery_status", "publications", ["delivery_status"])
    op.create_index(
        "ix_publications_historical_duplicate", "publications", ["historical_duplicate"]
    )
    op.create_unique_constraint("uq_publications_dedupe_key", "publications", ["dedupe_key"])

    op.execute(
        """
        UPDATE publications
        SET public_message_url = 'https://t.me/' || substring(destination from 2) || '/' || telegram_message_id
        WHERE destination LIKE '@%' AND telegram_message_id IS NOT NULL
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT p.id,
                   row_number() OVER (
                       PARTITION BY q.product_id
                       ORDER BY p.published_at, p.created_at, p.id
                   ) AS rn
            FROM publications p
            JOIN publication_queue q ON q.id = p.queue_id
            WHERE p.published_at IS NOT NULL
        )
        UPDATE publications p
        SET historical_duplicate = true
        FROM ranked r
        WHERE p.id = r.id AND r.rn > 1
        """
    )
    active = ", ".join(f"'{value}'" for value in ACTIVE_STATUSES)
    op.execute(
        f"""
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY product_id, publication_kind
                       ORDER BY
                           CASE WHEN status::text = 'publishing' THEN 0 ELSE 1 END,
                           CASE WHEN manual_position IS NULL THEN 1 ELSE 0 END,
                           manual_position,
                           priority DESC,
                           created_at,
                           id
                   ) AS rn
            FROM publication_queue
            WHERE status::text IN ({active})
        )
        UPDATE publication_queue q
        SET status = 'skipped', last_error = 'SKIPPED_DUPLICATE'
        FROM ranked r
        WHERE q.id = r.id AND r.rn > 1
        """
    )
    op.create_index(
        "uq_publication_queue_active_product_kind",
        "publication_queue",
        ["product_id", "publication_kind"],
        unique=True,
        postgresql_where=sa.text(f"status IN ({active})"),
    )

    op.create_table(
        "channel_editorial_messages",
        sa.Column("message_key", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("destination", sa.String(255), nullable=False),
        sa.Column("telegram_message_id", sa.String(80), nullable=True),
        sa.Column("public_message_url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_key"),
    )
    op.create_index(
        "ix_channel_editorial_messages_message_key",
        "channel_editorial_messages",
        ["message_key"],
    )
    op.create_index("ix_channel_editorial_messages_kind", "channel_editorial_messages", ["kind"])
    op.create_index(
        "ix_channel_editorial_messages_published_at",
        "channel_editorial_messages",
        ["published_at"],
    )


def downgrade() -> None:
    for index in (
        "ix_channel_editorial_messages_published_at",
        "ix_channel_editorial_messages_kind",
        "ix_channel_editorial_messages_message_key",
    ):
        op.drop_index(index, table_name="channel_editorial_messages")
    op.drop_table("channel_editorial_messages")
    op.drop_index("uq_publication_queue_active_product_kind", table_name="publication_queue")
    op.drop_index("ix_publication_queue_publication_kind", table_name="publication_queue")
    op.drop_column("publication_queue", "editorial_payload")
    op.drop_column("publication_queue", "publication_kind")
    op.drop_constraint("uq_publications_dedupe_key", "publications", type_="unique")
    for index in (
        "ix_publications_historical_duplicate",
        "ix_publications_delivery_status",
        "ix_publications_publication_kind",
    ):
        op.drop_index(index, table_name="publications")
    for column in (
        "historical_duplicate",
        "dedupe_key",
        "delivery_status",
        "public_message_url",
        "publication_kind",
    ):
        op.drop_column("publications", column)
