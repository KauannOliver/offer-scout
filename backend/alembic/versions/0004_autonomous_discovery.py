"""Add autonomous discovery and price intelligence fields."""

import sqlalchemy as sa
from alembic import op

revision = "0004_autonomous_discovery"
down_revision = "0003_affiliate_link_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("products", sa.Column("category_name", sa.String(160), nullable=True))
    op.add_column("products", sa.Column("brand", sa.String(160), nullable=True))
    op.add_column("products", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "products", sa.Column("discovery_count", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(
        "products",
        sa.Column("discovery_strategies", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.create_index("ix_products_category_name", "products", ["category_name"])
    op.create_index("ix_products_brand", "products", ["brand"])
    op.create_index("ix_products_last_seen_at", "products", ["last_seen_at"])
    op.add_column(
        "product_snapshots", sa.Column("metrics", sa.JSON(), nullable=False, server_default="{}")
    )
    op.add_column("coupons", sa.Column("source_fingerprint", sa.String(64), nullable=True))
    op.add_column("coupons", sa.Column("rules_version", sa.String(64), nullable=True))
    op.add_column(
        "coupons", sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_coupons_source_fingerprint", "coupons", ["source_fingerprint"])
    op.create_index("ix_coupons_rules_version", "coupons", ["rules_version"])
    op.create_table(
        "coupon_publications",
        sa.Column("coupon_id", sa.Uuid(), nullable=False),
        sa.Column("rules_version", sa.String(64), nullable=False),
        sa.Column("destination", sa.String(255), nullable=False),
        sa.Column("telegram_message_id", sa.String(80), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["coupon_id"], ["coupons.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("coupon_id", "rules_version"),
    )
    op.create_index("ix_coupon_publications_coupon_id", "coupon_publications", ["coupon_id"])
    op.create_index("ix_coupon_publications_published_at", "coupon_publications", ["published_at"])


def downgrade() -> None:
    op.drop_index("ix_coupon_publications_published_at", table_name="coupon_publications")
    op.drop_index("ix_coupon_publications_coupon_id", table_name="coupon_publications")
    op.drop_table("coupon_publications")
    op.drop_index("ix_coupons_rules_version", table_name="coupons")
    op.drop_index("ix_coupons_source_fingerprint", table_name="coupons")
    op.drop_column("coupons", "last_verified_at")
    op.drop_column("coupons", "rules_version")
    op.drop_column("coupons", "source_fingerprint")
    op.drop_column("product_snapshots", "metrics")
    op.drop_index("ix_products_last_seen_at", table_name="products")
    op.drop_index("ix_products_brand", table_name="products")
    op.drop_index("ix_products_category_name", table_name="products")
    op.drop_column("products", "discovery_strategies")
    op.drop_column("products", "discovery_count")
    op.drop_column("products", "last_seen_at")
    op.drop_column("products", "brand")
    op.drop_column("products", "category_name")
