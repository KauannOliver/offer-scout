"""Stabilize settings, editorial scheduling and private bot telemetry."""

import sqlalchemy as sa
from alembic import op

revision = "0007_stability_editorial_bot"
down_revision = "0006_editorial_trust_and_dedup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("creatives", sa.Column("content_setting_version", sa.Integer(), nullable=True))
    op.create_table(
        "editorial_candidates",
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("latest_snapshot_id", sa.Uuid(), sa.ForeignKey("product_snapshots.id")),
        sa.Column("state", sa.String(40), nullable=False, server_default="WATCHING"),
        sa.Column("score", sa.Numeric(6, 2)),
        sa.Column("price_confidence", sa.Numeric(5, 4)),
        sa.Column("observation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("history_hours", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("reasons", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True)),
        sa.Column("next_revalidation_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("product_id"),
    )
    for column in ("product_id", "state", "last_evaluated_at", "next_revalidation_at"):
        op.create_index(f"ix_editorial_candidates_{column}", "editorial_candidates", [column])
    op.create_table(
        "editorial_slots",
        sa.Column("slot_key", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(40), nullable=False, server_default="PENDING"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("outcome_reason", sa.String(120)),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id")),
        sa.Column("publication_id", sa.Uuid(), sa.ForeignKey("publications.id")),
        sa.Column("policy_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("slot_key"),
    )
    for column in ("slot_key", "kind", "scheduled_for", "deadline_at", "next_attempt_at", "state"):
        op.create_index(f"ix_editorial_slots_{column}", "editorial_slots", [column])
    op.create_table(
        "bot_interaction_metrics",
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("category", sa.String(160), nullable=False, server_default=""),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("day", "action", "category"),
    )
    op.create_index("ix_bot_interaction_metrics_day", "bot_interaction_metrics", ["day"])
    op.create_index("ix_bot_interaction_metrics_action", "bot_interaction_metrics", ["action"])
    op.execute(
        """
        UPDATE publication_queue
        SET last_error = NULL
        WHERE last_error LIKE 'Criativo invalidado pela configuração v%'
          AND creative_id IS NOT NULL
          AND link_id IS NOT NULL
          AND status::text IN ('ready', 'dry_run', 'published')
        """
    )


def downgrade() -> None:
    op.drop_table("bot_interaction_metrics")
    op.drop_table("editorial_slots")
    op.drop_table("editorial_candidates")
    op.drop_column("creatives", "content_setting_version")
