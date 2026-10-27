"""Add queue control, coupon provenance and bot campaigns."""

import sqlalchemy as sa
from alembic import op

revision = "0005_operational_completion"
down_revision = "0004_autonomous_discovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("publication_queue", sa.Column("discovery_batch_id", sa.Uuid(), nullable=True))
    op.add_column("publication_queue", sa.Column("source_strategy", sa.String(180), nullable=True))
    op.add_column(
        "publication_queue", sa.Column("priority", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column("publication_queue", sa.Column("manual_position", sa.Integer(), nullable=True))
    op.create_index(
        "ix_publication_queue_discovery_batch_id", "publication_queue", ["discovery_batch_id"]
    )
    op.create_index(
        "ix_publication_queue_source_strategy", "publication_queue", ["source_strategy"]
    )
    op.create_index("ix_publication_queue_priority", "publication_queue", ["priority"])
    op.create_index(
        "ix_publication_queue_manual_position", "publication_queue", ["manual_position"]
    )
    op.add_column(
        "coupons",
        sa.Column(
            "source_kind", sa.String(40), nullable=False, server_default="affiliate_generated"
        ),
    )
    op.add_column(
        "coupons", sa.Column("shareable", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    op.add_column(
        "coupons",
        sa.Column("lifecycle_status", sa.String(30), nullable=False, server_default="ACTIVE"),
    )
    op.add_column(
        "coupons",
        sa.Column("activation_status", sa.String(30), nullable=False, server_default="GENERATED"),
    )
    op.add_column("coupons", sa.Column("scope", sa.String(240), nullable=True))
    op.add_column("coupons", sa.Column("remaining_budget", sa.Numeric(14, 2), nullable=True))
    op.create_index("ix_coupons_source_kind", "coupons", ["source_kind"])
    op.create_index("ix_coupons_shareable", "coupons", ["shareable"])
    op.create_index("ix_coupons_lifecycle_status", "coupons", ["lifecycle_status"])
    op.add_column(
        "app_settings", sa.Column("version", sa.Integer(), nullable=False, server_default="1")
    )
    op.create_table(
        "bot_campaign_messages",
        sa.Column("slot_key", sa.String(80), nullable=False),
        sa.Column("destination", sa.String(255), nullable=False),
        sa.Column("telegram_message_id", sa.String(80), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slot_key"),
    )
    op.create_index("ix_bot_campaign_messages_slot_key", "bot_campaign_messages", ["slot_key"])
    op.create_index(
        "ix_bot_campaign_messages_published_at", "bot_campaign_messages", ["published_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_bot_campaign_messages_published_at", table_name="bot_campaign_messages")
    op.drop_index("ix_bot_campaign_messages_slot_key", table_name="bot_campaign_messages")
    op.drop_table("bot_campaign_messages")
    op.drop_column("app_settings", "version")
    op.drop_index("ix_coupons_lifecycle_status", table_name="coupons")
    op.drop_index("ix_coupons_shareable", table_name="coupons")
    op.drop_index("ix_coupons_source_kind", table_name="coupons")
    for column in (
        "remaining_budget",
        "scope",
        "activation_status",
        "lifecycle_status",
        "shareable",
        "source_kind",
    ):
        op.drop_column("coupons", column)
    for index in ("manual_position", "priority", "source_strategy", "discovery_batch_id"):
        op.drop_index(f"ix_publication_queue_{index}", table_name="publication_queue")
        op.drop_column("publication_queue", index)
