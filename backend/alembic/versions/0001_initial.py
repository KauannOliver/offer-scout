"""Initial schema."""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Frozen baseline: never import live application models into historical migrations.
    op.execute("CREATE TYPE queuestatus AS ENUM ('discovered','selected','link_pending','creative_pending','ready','publishing','dry_run','published','retry','skipped','failed')")
    op.execute("CREATE TYPE browserauthstate AS ENUM ('auth_required','authenticated','challenge_required','session_expired')")
    for table, fields in TABLES:
        op.execute(f"CREATE TABLE {table} (id uuid PRIMARY KEY, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL, {fields})")
    for table, fields in INDEXES.items():
        for column in fields:
            op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    for table, _ in reversed(TABLES):
        op.drop_table(table)
    op.execute("DROP TYPE browserauthstate")
    op.execute("DROP TYPE queuestatus")


TABLES = [
    ("admin_users", "username varchar(80) UNIQUE NOT NULL, password_hash varchar(255) NOT NULL, active boolean NOT NULL"),
    ("products", "marketplace varchar(40) NOT NULL, source varchar(80) NOT NULL, item_id varchar(64) NOT NULL, product_id varchar(64), user_product_id varchar(64), variation_id varchar(64), title text NOT NULL, product_url text NOT NULL, image_url text, raw_metadata json NOT NULL, active boolean NOT NULL, UNIQUE(marketplace,item_id)"),
    ("product_snapshots", "product_id uuid NOT NULL REFERENCES products(id), previous_price numeric(12,2), price numeric(12,2), discount_percent numeric(6,2), rating numeric(3,2), sold_text varchar(120), sold_count integer, commission_percent numeric(6,2), extra_commission boolean NOT NULL, brand_commission boolean NOT NULL, highlight varchar(120), currency varchar(8) NOT NULL, observed_at timestamptz NOT NULL"),
    ("coupons", "marketplace_coupon_id varchar(100) UNIQUE NOT NULL, title text NOT NULL, code varchar(100), discount_percent numeric(6,2), discount_amount numeric(12,2), minimum_purchase numeric(12,2), starts_at timestamptz, expires_at timestamptz, eligibility_confirmed boolean NOT NULL, raw_metadata json NOT NULL"),
    ("offer_scores", "product_id uuid NOT NULL REFERENCES products(id), snapshot_id uuid REFERENCES product_snapshots(id), total numeric(6,2) NOT NULL, classification varchar(40) NOT NULL, components json NOT NULL, eligible boolean NOT NULL, reasons json NOT NULL"),
    ("affiliate_links", "product_id uuid NOT NULL REFERENCES products(id), full_url text, short_url text NOT NULL, affiliate_tag varchar(100) NOT NULL, source varchar(40) NOT NULL, validated_format boolean NOT NULL"),
    ("creatives", "product_id uuid NOT NULL REFERENCES products(id), copy_text text NOT NULL, image_path text, generator varchar(40) NOT NULL, payload json NOT NULL"),
    ("publication_queue", "product_id uuid NOT NULL REFERENCES products(id), score_id uuid REFERENCES offer_scores(id), link_id uuid REFERENCES affiliate_links(id), creative_id uuid REFERENCES creatives(id), status queuestatus NOT NULL, idempotency_key varchar(255) UNIQUE NOT NULL, scheduled_for timestamptz, attempts integer NOT NULL, last_error text"),
    ("publications", "queue_id uuid UNIQUE NOT NULL REFERENCES publication_queue(id), destination varchar(255) NOT NULL, telegram_message_id varchar(80), mode varchar(40) NOT NULL, published_at timestamptz, payload_hash varchar(64) NOT NULL"),
    ("app_settings", "key varchar(100) UNIQUE NOT NULL, value json NOT NULL, description text"),
    ("browser_sessions", "state browserauthstate NOT NULL, last_authenticated_at timestamptz, last_checked_at timestamptz, message text"),
    ("audit_events", "actor varchar(100) NOT NULL, action varchar(100) NOT NULL, entity_type varchar(80), entity_id varchar(100), details json NOT NULL"),
]
INDEXES = {
    "products": ["item_id", "product_id"], "product_snapshots": ["product_id"],
    "offer_scores": ["product_id"], "affiliate_links": ["product_id"],
    "creatives": ["product_id"], "publication_queue": ["product_id"],
    "admin_users": ["username"], "audit_events": ["action"],
}
