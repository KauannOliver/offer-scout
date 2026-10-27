"""Bounded retention. Published/audited products keep their original evidence."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

# Conservative protection includes all historical queues with a publication, not only SENT.
PROTECTED = """NOT EXISTS (SELECT 1 FROM publication_queue q JOIN publications p ON p.queue_id=q.id WHERE q.product_id=o.product_id)
 AND NOT EXISTS (SELECT 1 FROM audit_events a WHERE a.entity_type='product' AND a.entity_id=o.product_id::text)"""


async def retain_history(session, days=90, dry_run=True):
    if not 90 <= int(days) <= 3650:
        raise ValueError("A retenção deve ficar entre 90 e 3650 dias")
    cutoff = (datetime.now(UTC)-timedelta(days=int(days))).replace(hour=0, minute=0, second=0, microsecond=0)
    params = {"cutoff": cutoff}
    # Transaction-scoped advisory lock also protects API/manual invocations.
    if not await session.scalar(text("SELECT pg_try_advisory_xact_lock(77260401)")):
        return {"state": "BUSY", "dry_run": dry_run}
    groups = list((await session.execute(text(f"""
      SELECT o.product_id,(o.observed_at AT TIME ZONE 'UTC')::date AS day,count(*) AS observations
      FROM product_observations o WHERE o.observed_at < :cutoff AND {PROTECTED}
      GROUP BY o.product_id,(o.observed_at AT TIME ZONE 'UTC')::date
      ORDER BY day,o.product_id LIMIT 200
    """), params)).mappings())
    result = {"dry_run": dry_run, "cutoff": cutoff.isoformat(), "days_selected": len(groups),
        "observations_selected": sum(row["observations"] for row in groups),
        "observations_removed": 0, "scores_removed": 0, "snapshots_removed": 0, "logs_removed": 0}
    if dry_run:
        return result
    # Same product-first locking order as ingestion/publication; recheck after waiting.
    await session.execute(text("SELECT id FROM products WHERE id=ANY(:ids) ORDER BY id FOR UPDATE"),
        {"ids": list({row["product_id"] for row in groups})})
    for row in groups:
        args = {"pid": row["product_id"], "day": row["day"]}
        if not await session.scalar(text(f"SELECT EXISTS (SELECT 1 FROM product_observations o WHERE o.product_id=:pid AND {PROTECTED})"), args):
            continue
        # Merge incremental late observations; normal runs delete the day's detail atomically.
        await session.execute(text("""
          INSERT INTO product_daily_history
            (product_id,day,observation_count,first_observed_at,last_observed_at,minimum_price,maximum_price,median_price)
          SELECT o.product_id,:day,count(*),min(o.observed_at),max(o.observed_at),min(s.price),max(s.price),
            percentile_disc(.5) WITHIN GROUP (ORDER BY s.price)
          FROM product_observations o JOIN product_snapshots s ON s.id=o.snapshot_id
          WHERE o.product_id=:pid AND (o.observed_at AT TIME ZONE 'UTC')::date=:day GROUP BY o.product_id
          ON CONFLICT (product_id,day) DO UPDATE SET
            observation_count=product_daily_history.observation_count+EXCLUDED.observation_count,
            first_observed_at=least(product_daily_history.first_observed_at,EXCLUDED.first_observed_at),
            last_observed_at=greatest(product_daily_history.last_observed_at,EXCLUDED.last_observed_at),
            minimum_price=least(product_daily_history.minimum_price,EXCLUDED.minimum_price),
            maximum_price=greatest(product_daily_history.maximum_price,EXCLUDED.maximum_price),
            median_price=NULL
        """), args)
        # On late data the exact combined median is unknown; never average medians.
        removed = await session.execute(text("""DELETE FROM product_observations
          WHERE product_id=:pid AND (observed_at AT TIME ZONE 'UTC')::date=:day"""), args)
        result["observations_removed"] += removed.rowcount
    removed = await session.execute(text("""
      DELETE FROM offer_scores WHERE id IN (
        SELECT s.id FROM offer_scores s WHERE s.created_at < :cutoff
        AND NOT EXISTS (SELECT 1 FROM product_current_state c WHERE c.score_id=s.id)
        AND NOT EXISTS (SELECT 1 FROM publication_queue q WHERE q.score_id=s.id)
        AND NOT EXISTS (SELECT 1 FROM publication_queue q JOIN publications p ON p.queue_id=q.id WHERE q.product_id=s.product_id)
        AND NOT EXISTS (SELECT 1 FROM audit_events a WHERE a.entity_id IN (s.id::text,s.product_id::text))
        ORDER BY s.created_at LIMIT 3000)
    """), params)
    result["scores_removed"] = removed.rowcount
    removed = await session.execute(text("""
      DELETE FROM product_snapshots WHERE id IN (
        SELECT s.id FROM product_snapshots s WHERE s.observed_at < :cutoff
        AND EXISTS (SELECT 1 FROM product_daily_history d WHERE d.product_id=s.product_id AND d.day=(s.observed_at AT TIME ZONE 'UTC')::date)
        AND NOT EXISTS (SELECT 1 FROM product_current_state c WHERE c.snapshot_id=s.id)
        AND NOT EXISTS (SELECT 1 FROM offer_scores x WHERE x.snapshot_id=s.id)
        AND NOT EXISTS (SELECT 1 FROM product_observations o WHERE o.snapshot_id=s.id)
        AND NOT EXISTS (SELECT 1 FROM editorial_candidates e WHERE e.latest_snapshot_id=s.id)
        AND NOT EXISTS (SELECT 1 FROM publication_queue q JOIN publications p ON p.queue_id=q.id WHERE q.product_id=s.product_id)
        AND NOT EXISTS (SELECT 1 FROM audit_events a WHERE a.entity_id IN (s.id::text,s.product_id::text))
        ORDER BY s.observed_at LIMIT 3000)
    """), params)
    result["snapshots_removed"] = removed.rowcount
    removed = await session.execute(text("""DELETE FROM task_runs WHERE id IN (
      SELECT id FROM task_runs WHERE finished_at < :cutoff AND status IN ('SUCCEEDED','SUCCESS','SKIPPED')
      ORDER BY finished_at LIMIT 3000)"""), params)
    result["logs_removed"] = removed.rowcount
    # Caller commits result/audit together with deletion; never delete publications or audit.
    return result
