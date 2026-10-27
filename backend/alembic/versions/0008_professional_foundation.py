"""Durable settings outbox, observations and current product projection."""
import sqlalchemy as sa
from alembic import op

revision = "0008_professional_foundation"
down_revision = "0007_stability_editorial_bot"
branch_labels = None
depends_on = None


def upgrade():
    for statement in SCHEMA_DDL:
        op.execute(statement)
    op.execute("INSERT INTO settings_revision (id, revision) VALUES (1, 0)")
    op.add_column("publication_queue", sa.Column("operational_reason", sa.Text(), nullable=True))
    op.execute("""UPDATE publication_queue SET operational_reason=last_error, last_error=NULL
        WHERE last_error LIKE 'Configuração v%' OR last_error LIKE 'Criativo invalidado%'""")
    op.execute("""INSERT INTO product_current_state (product_id, snapshot_id, score_id, last_verified_at)
        SELECT p.id, s.id, sc.id, s.observed_at FROM products p
        LEFT JOIN LATERAL (SELECT id, observed_at FROM product_snapshots WHERE product_id=p.id
          ORDER BY observed_at DESC, id DESC LIMIT 1) s ON true
        LEFT JOIN LATERAL (SELECT id FROM offer_scores WHERE product_id=p.id
          ORDER BY created_at DESC, id DESC LIMIT 1) sc ON true""")
    for table, name, columns in (
        ("product_snapshots", "ix_snapshot_latest", "product_id, observed_at DESC, id DESC"),
        ("offer_scores", "ix_score_latest", "product_id, created_at DESC, id DESC"),
        ("publication_queue", "ix_queue_dispatch", "status, manual_position, priority DESC, created_at"),
        ("publications", "ix_publication_recent", "publication_kind, delivery_status, published_at DESC, id DESC"),
        ("product_observations", "ix_observation_history", "product_id, observed_at DESC"),
    ):
        op.execute(f"CREATE INDEX {name} ON {table} ({columns})")
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("""CREATE FUNCTION search_normalize(text) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
        AS $$ SELECT lower(public.unaccent('public.unaccent', $1)) $$""")
    op.execute("CREATE INDEX ix_product_title_search ON products USING gin (search_normalize(title) gin_trgm_ops)")
    op.execute("""UPDATE editorial_slots SET state='EXPIRED', outcome_reason=COALESCE(outcome_reason,'WINDOW_EXPIRED')
        WHERE state='PENDING' AND deadline_at < now()""")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_product_title_search")
    op.execute("DROP FUNCTION IF EXISTS search_normalize(text)")
    for name in ("ix_snapshot_latest", "ix_score_latest", "ix_queue_dispatch", "ix_publication_recent"):
        op.drop_index(name)
    op.drop_column("publication_queue", "operational_reason")
    for table in ("discovery_schedule", "reprocessing_jobs", "settings_revision", "product_current_state", "product_observations"):
        op.drop_table(table)

# Frozen DDL: replaying migrations must not depend on future application models.
SCHEMA_DDL = [
  "\nCREATE TABLE product_observations (\n\tproduct_id UUID NOT NULL, \n\tsnapshot_id UUID NOT NULL, \n\tobserved_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tsource VARCHAR(180) NOT NULL, \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(product_id) REFERENCES products (id), \n\tFOREIGN KEY(snapshot_id) REFERENCES product_snapshots (id)\n)\n\n",
  "\nCREATE TABLE product_current_state (\n\tproduct_id UUID NOT NULL, \n\tsnapshot_id UUID, \n\tscore_id UUID, \n\tlast_verified_at TIMESTAMP WITH TIME ZONE, \n\tPRIMARY KEY (product_id), \n\tFOREIGN KEY(product_id) REFERENCES products (id), \n\tFOREIGN KEY(snapshot_id) REFERENCES product_snapshots (id), \n\tFOREIGN KEY(score_id) REFERENCES offer_scores (id)\n)\n\n",
  "\nCREATE TABLE settings_revision (\n\tid INTEGER NOT NULL, \n\trevision INTEGER NOT NULL, \n\tPRIMARY KEY (id)\n)\n\n",
  "\nCREATE TABLE reprocessing_jobs (\n\trevision INTEGER NOT NULL, \n\tchanges JSON NOT NULL, \n\tsettings_values JSON NOT NULL, \n\tstate VARCHAR(30) NOT NULL, \n\tcursor UUID, \n\tresult JSON NOT NULL, \n\tattempts INTEGER NOT NULL, \n\terror VARCHAR(255), \n\tid UUID NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tPRIMARY KEY (id), \n\tUNIQUE (revision)\n)\n\n",
  "\nCREATE TABLE discovery_schedule (\n\tstrategy VARCHAR(40) NOT NULL, \n\tnext_due_at TIMESTAMP WITH TIME ZONE NOT NULL, \n\tlast_started_at TIMESTAMP WITH TIME ZONE, \n\tinterval_minutes INTEGER NOT NULL, \n\tPRIMARY KEY (strategy)\n)\n\n",
  "CREATE INDEX ix_product_observations_observed_at ON product_observations (observed_at)",
  "CREATE INDEX ix_product_observations_product_id ON product_observations (product_id)",
  "CREATE INDEX ix_reprocessing_jobs_state ON reprocessing_jobs (state)"
]
