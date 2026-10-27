"""Coupon applicability, immutable publication facts and task runs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_operational_dashboard"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    applicability = postgresql.ENUM(
        "pending",
        "applicable",
        "not_applicable",
        "expired",
        name="couponapplicabilitystatus",
        create_type=False,
    )
    applicability.create(op.get_bind(), checkfirst=True)
    op.add_column("coupons", sa.Column("maximum_discount", sa.Numeric(12, 2)))
    op.add_column("coupons", sa.Column("availability_text", sa.Text()))
    op.add_column(
        "coupons", sa.Column("restrictions", sa.JSON(), nullable=False, server_default="[]")
    )
    op.create_table(
        "coupon_product_applicability",
        sa.Column("coupon_id", sa.Uuid(), sa.ForeignKey("coupons.id"), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("status", applicability, nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("reason", sa.Text()),
        sa.Column("checked_at", sa.DateTime(timezone=True)),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("coupon_id", "product_id"),
    )
    op.create_index(
        "ix_coupon_product_applicability_coupon_id", "coupon_product_applicability", ["coupon_id"]
    )
    op.create_index(
        "ix_coupon_product_applicability_product_id", "coupon_product_applicability", ["product_id"]
    )
    op.add_column("creatives", sa.Column("image_source_url", sa.Text()))
    op.add_column(
        "creatives",
        sa.Column("template_version", sa.String(40), nullable=False, server_default="telegram-v2"),
    )
    op.add_column(
        "publications", sa.Column("payload", sa.JSON(), nullable=False, server_default="{}")
    )
    op.create_table(
        "task_runs",
        sa.Column("task_name", sa.String(120), nullable=False),
        sa.Column("correlation_id", sa.String(80), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("result", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text()),
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_runs_task_name", "task_runs", ["task_name"])
    op.create_index("ix_task_runs_correlation_id", "task_runs", ["correlation_id"])
    op.create_index("ix_task_runs_status", "task_runs", ["status"])


def downgrade() -> None:
    op.drop_table("task_runs")
    op.drop_column("publications", "payload")
    op.drop_column("creatives", "template_version")
    op.drop_column("creatives", "image_source_url")
    op.drop_table("coupon_product_applicability")
    op.drop_column("coupons", "restrictions")
    op.drop_column("coupons", "availability_text")
    op.drop_column("coupons", "maximum_discount")
    postgresql.ENUM(name="couponapplicabilitystatus").drop(op.get_bind(), checkfirst=True)
