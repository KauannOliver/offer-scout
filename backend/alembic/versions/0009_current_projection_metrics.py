"""Cover dashboard sorting without scanning historical score records."""
from alembic import op

revision = "0009_current_projection_metrics"
down_revision = "0008_professional_foundation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE product_current_state ADD COLUMN score_total numeric(6,2), ADD COLUMN score_eligible boolean, ADD COLUMN price numeric(12,2), ADD COLUMN discount_percent numeric(6,2)")
    op.execute("UPDATE product_current_state c SET score_total=s.total,score_eligible=s.eligible FROM offer_scores s WHERE s.id=c.score_id")
    op.execute("UPDATE product_current_state c SET price=s.price,discount_percent=s.discount_percent FROM product_snapshots s WHERE s.id=c.snapshot_id")
    op.execute("CREATE INDEX ix_current_score ON product_current_state (score_eligible, score_total DESC, product_id)")
    op.execute("CREATE INDEX ix_current_price ON product_current_state (price, product_id)")


def downgrade():
    op.execute("DROP INDEX ix_current_score")
    op.execute("DROP INDEX ix_current_price")
    op.execute("ALTER TABLE product_current_state DROP COLUMN score_total,DROP COLUMN score_eligible,DROP COLUMN price,DROP COLUMN discount_percent")
