"""Explicit, bounded rollout probes. Discovery never queues or publishes offers."""
import argparse
import asyncio
import json
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import select

from backend.app.config import get_settings
from backend.app.db import SessionLocal
from backend.app.models import PublicationQueue, QueueStatus, TaskRun
from backend.app.providers import MercadoLivreProvider
from backend.app.services.pipeline import OfferPipeline, scoring_engine_from_settings


async def probe(mode):
    settings = get_settings()
    if mode == "public":
        from backend.app.tasks import _publish_ready_offer
        async with SessionLocal() as session:
            item = await session.scalar(select(PublicationQueue).where(PublicationQueue.status == QueueStatus.ready)
                .order_by(PublicationQueue.priority.desc(), PublicationQueue.created_at).limit(1))
        return await _publish_ready_offer(str(item.id)) if item else {"published": False, "reason": "no_ready_offer"}
    if mode == "editorial":
        from backend.app.tasks import _dispatch_bad_offer
        return await _dispatch_bad_offer(force=True, dry_run=True)
    if mode == "private":
        from backend.app.bot import send_offers
        from backend.app.services.telegram import TelegramService
        await TelegramService().validate_private_smoke()
        async with TelegramService()._bot() as bot:
            await send_offers(bot, int(settings.telegram_admin_chat_id))
        return {"private_preview": "sent", "links": "public_channel_only"}
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    provider = MercadoLivreProvider(client)
    try:
        if mode == "inspect":
            async with provider.browser._context() as context:
                page = context.pages[0]
                return await page.locator("#recommendations_card button, #recommendations_card [role=button]").evaluate_all(
                    "els => els.slice(0, 35).map(e => ({tag:e.tagName, cls:e.className, text:e.innerText, expanded:e.getAttribute('aria-expanded')}))")
        started = datetime.now(UTC)
        offers = await provider.discover_offers(strategy="relevant")
        ids = {offer.item_id for offer in offers}
        sample = []
        for product in offers[:3]:
            fresh = await provider.revalidate(product)
            sample.append(bool(fresh and fresh.current_price and fresh.current_price > 0))
        async with SessionLocal() as session:
            pipeline = OfferPipeline(await scoring_engine_from_settings(session))
            await pipeline.ingest(session, offers, enqueue=False, source_strategy="canary:relevant")
            result = {"unique_products": len(ids), "duplicates": len(offers)-len(ids),
                "sample_valid": sample, "discovery": provider.discovery_metrics,
                "ingestion": pipeline.metrics, "publication_enabled": False}
            session.add(TaskRun(task_name="rollout_discovery_canary", correlation_id=f"rollout-{started:%Y%m%d}",
                status="SUCCEEDED" if len(ids)>=100 and all(sample) else "REVIEW_REQUIRED",
                started_at=started, finished_at=datetime.now(UTC), result=result))
            await session.commit()
        return result
    finally:
        await provider.aclose()
        await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["discovery", "private", "public", "editorial", "inspect"])
    args = parser.parse_args()
    print(json.dumps(asyncio.run(probe(args.mode)), default=str))
