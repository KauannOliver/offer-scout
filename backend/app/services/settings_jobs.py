"""Transactional settings outbox. A cursor and each batch commit together."""
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import select

from backend.app.db import SessionLocal
from backend.app.models import Product, ReprocessingJob
from backend.app.services.redis_lock import DistributedLock
from backend.app.services.reprocessing import reprocess_after_settings


async def process_settings_outbox(redis: Redis) -> dict:
    async with DistributedLock(redis, "lock:settings_reprocess", ttl=120, wait_timeout=1):
        async with SessionLocal() as session:
            jobs = list(await session.scalars(select(ReprocessingJob).where(
                ReprocessingJob.state.in_(["PENDING", "RUNNING"])
            ).order_by(ReprocessingJob.revision).with_for_update()))
            if not jobs:
                return {"state": "IDLE"}
            # Coalesce only unstarted jobs. Interrupted jobs retain their checkpoints.
            running = next((job for job in jobs if job.state == "RUNNING"), None)
            if running:
                job = running
            else:
                job = jobs[-1]
                merged = {}
                for previous in jobs:
                    merged.update(previous.changes)
                    if previous.id != job.id:
                        previous.state = "COALESCED"
                        previous.result = {"superseded_by": str(job.id)}
                job.changes = merged
            job.state = "RUNNING"
            await session.commit()
            job_id = job.id
        # Bounded turn: resume next minute; never scan the complete history in memory.
        for _ in range(8):
            async with SessionLocal() as session:
                job = await session.get(ReprocessingJob, job_id, with_for_update=True)
                query = select(Product.id).where(Product.active.is_(True)).order_by(Product.id).limit(200)
                if job.cursor:
                    query = query.where(Product.id > job.cursor)
                ids = list(await session.scalars(query))
                if not ids:
                    job.state = "SUCCEEDED"
                    job.error = None
                    await session.commit()
                    return {"state": job.state, "id": str(job_id)}
                try:
                    result = await reprocess_after_settings(session, job.changes, product_ids=set(ids), commit=False,
                        settings_values=job.settings_values)
                    totals = dict(job.result or {})
                    for key, value in result.items():
                        if isinstance(value, int):
                            totals[key] = totals.get(key, 0) + value
                    totals["updated_at"] = datetime.now(UTC).isoformat()
                    job.result = totals
                    job.cursor = ids[-1]
                    job.attempts = 0  # consecutive failures, not successful resumptions
                    job.error = None
                    await session.commit()
                except Exception as exc:
                    await session.rollback()
                    failed = await session.get(ReprocessingJob, job_id)
                    failed.error = type(exc).__name__  # no database params or credentials
                    failed.attempts += 1
                    if failed.attempts >= 5:
                        failed.state = "FAILED"
                    await session.commit()
                    raise
        return {"state": "RUNNING", "id": str(job_id)}
