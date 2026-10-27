"""Daily history rollups, weekly reports and explicit scoring policy."""
from alembic import op

revision = "0010_operational_completion"
down_revision = "0009_current_projection_metrics"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE offer_scores ADD COLUMN policy_fingerprint varchar(64)")
    op.execute("""CREATE TABLE product_daily_history (
      product_id uuid NOT NULL REFERENCES products(id), day date NOT NULL,
      observation_count integer NOT NULL, first_observed_at timestamptz NOT NULL,
      last_observed_at timestamptz NOT NULL, minimum_price numeric(12,2),
      maximum_price numeric(12,2), median_price numeric(12,2),
      PRIMARY KEY(product_id,day))""")
    op.execute("""CREATE TABLE weekly_admin_reports (
      week_key varchar(16) PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
      payload json NOT NULL, delivery_status varchar(30) NOT NULL DEFAULT 'PREPARED',
      telegram_message_id varchar(80))""")
    op.execute("CREATE INDEX ix_publications_bot_cursor ON publications (published_at DESC, id DESC) WHERE delivery_status='SENT' AND publication_kind='GOOD_OFFER'")
    op.execute("CREATE INDEX ix_observations_product_time ON product_observations (product_id, observed_at DESC)")


def downgrade():
    op.execute("DROP TABLE weekly_admin_reports")
    op.execute("DROP TABLE product_daily_history")
    op.execute("DROP INDEX ix_observations_product_time")
    op.execute("DROP INDEX ix_publications_bot_cursor")
    op.execute("ALTER TABLE offer_scores DROP COLUMN policy_fingerprint")
