import asyncio
import hashlib
import html
import inspect
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import wraps
from zoneinfo import ZoneInfo

from celery import shared_task
from redis.asyncio import Redis
from sqlalchemy import desc, exists, func, select, update

from backend.app.config import get_settings
from backend.app.db import SessionLocal
from backend.app.domain.types import NormalizedProduct
from backend.app.models import (
    AffiliateLink,
    AppSetting,
    AuditEvent,
    BotCampaignMessage,
    BrowserAuthState,
    BrowserSession,
    ChannelEditorialMessage,
    Coupon,
    CouponPublication,
    Creative,
    EditorialCandidate,
    EditorialSlot,
    OfferScore,
    Product,
    ProductSnapshot,
    Publication,
    PublicationKind,
    PublicationQueue,
    QueueStatus,
    TaskRun,
)
from backend.app.providers import MercadoLivreProvider
from backend.app.services.ai import GroqCopyGenerator
from backend.app.services.coupons import applicable_coupon, coupon_data, sync_coupons
from backend.app.services.creative import ProductImageService
from backend.app.services.editorial import (
    DEFAULT_MANIFEST,
    DEFAULT_WEEKLY_REMINDER,
    evaluate_bad_offer,
    render_bad_offer,
)
from backend.app.services.observation_history import editorial_history
from backend.app.services.offer_policy import effective_coupon_percent
from backend.app.services.pipeline import OfferPipeline, scoring_engine_from_settings
from backend.app.services.redis_lock import DistributedLock
from backend.app.services.reprocessing import reprocess_after_settings
from backend.app.services.telegram import TelegramService
from backend.app.services.urls import is_official_short_link

settings = get_settings()


@shared_task(name="backend.app.tasks.maintain_operational_data")
def maintain_operational_data() -> dict:
    async def maintain():
        from backend.app.services.retention import retain_history
        async with SessionLocal() as session:
            stored = await session.scalar(select(AppSetting).where(AppSetting.key == "retention"))
            policy = stored.value if stored else {}
            result = await retain_history(session, int(policy.get("days", 90)), dry_run=not policy.get("enabled", True))
            session.add(AuditEvent(action="HISTORY_RETENTION", details=result))
            await session.commit()
            return result
    return run(maintain())


@shared_task(name="backend.app.tasks.sync_editorial_calendar")
def sync_editorial_calendar() -> dict:
    async def sync():
        from backend.app.services.calendar import sync_calendar
        async with SessionLocal() as session:
            specs = await sync_calendar(session)
            await session.commit()
            return {"slots_checked": len(specs)}
    return run(sync())


@shared_task(name="backend.app.tasks.send_weekly_admin_report")
def send_weekly_admin_report() -> dict:
    async def report():
        from sqlalchemy import text

        from backend.app.services.admin_report import prepare_report, render_report, report_period
        now = datetime.now(UTC)
        _, end = report_period(now)
        if now < end + timedelta(hours=9):
            return {"reason": "outside_slot"}
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            async with DistributedLock(client, "lock:weekly_admin_report", ttl=120, wait_timeout=1):
                async with SessionLocal() as session:
                    stored = await session.scalar(select(AppSetting).where(AppSetting.key == "channel_experience"))
                    if not (stored.value if stored else {}).get("admin_report_enabled", True):
                        return {"reason": "disabled"}
                    if not settings.telegram_admin_chat_id or not settings.telegram_bot_token:
                        return {"reason": "admin_not_configured"}
                    key, payload = await prepare_report(session, now)
                    if payload is None:
                        return {"reason": "already_prepared"}
                    await session.execute(text("UPDATE weekly_admin_reports SET delivery_status='SENDING' WHERE week_key=:key"), {"key": key})
                    await session.commit()
                    try:
                        async with TelegramService()._bot() as bot:
                            chat = await bot.get_chat(settings.telegram_admin_chat_id)
                            if chat.type != "private":
                                raise ValueError("Administrative report requires private chat")
                            message = await bot.send_message(chat.id, render_report(payload))
                    except Exception:
                        await session.execute(text("UPDATE weekly_admin_reports SET delivery_status='UNCERTAIN' WHERE week_key=:key"), {"key": key})
                        await session.commit()
                        return {"reason": "delivery_uncertain"}
                    await session.execute(text("UPDATE weekly_admin_reports SET delivery_status='SENT',telegram_message_id=:message WHERE week_key=:key"),
                        {"key": key, "message": str(message.message_id)})
                    await session.commit()
                    return {"sent": True, "week": key}
        except TimeoutError:
            return {"reason": "busy"}
        finally:
            await client.aclose()
    return run(report())


@shared_task(name="backend.app.tasks.process_settings_outbox")
def process_settings_outbox() -> dict:
    async def process():
        from backend.app.services.settings_jobs import process_settings_outbox as consume
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            return await consume(client)
        except TimeoutError:
            return {"state": "BUSY"}
        finally:
            await client.aclose()
    return run(process())


@shared_task(name="backend.app.tasks.scheduler_heartbeat")
def scheduler_heartbeat() -> dict:
    return run(_scheduler_heartbeat())


async def _scheduler_heartbeat() -> dict:
    client = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.set("scheduler:heartbeat", datetime.now(UTC).isoformat(), ex=150)
    finally:
        await client.aclose()
    return {"status": "UP"}


ACTIVE_QUEUE_STATUSES = [
    QueueStatus.discovered,
    QueueStatus.selected,
    QueueStatus.link_pending,
    QueueStatus.creative_pending,
    QueueStatus.ready,
    QueueStatus.dry_run,
    QueueStatus.retry,
    QueueStatus.publishing,
]
AUTH_BLOCKING_STATES = {
    state.value
    for state in (
        BrowserAuthState.auth_required,
        BrowserAuthState.challenge_required,
        BrowserAuthState.session_expired,
    )
}


def run(coro):
    return asyncio.run(coro)


def serialized_channel_task(function):
    """One renewable channel lease across all kinds, including manual sends."""
    signature = inspect.signature(function)
    @wraps(function)
    async def guarded(*args, **kwargs):
        arguments = signature.bind_partial(*args, **kwargs).arguments
        if arguments.get("dry_run"):
            return await function(*args, **kwargs)
        if not _public_armed():
            return {"published": False, "reason": "public_not_armed"}
        client = Redis.from_url(settings.redis_url, decode_responses=True)
        try:
            async with DistributedLock(client, "lock:channel_delivery", ttl=240, wait_timeout=1):
                from backend.app.services.calendar import (
                    TERMINAL,
                    TIMED,
                    current_slot,
                    reconcile_confirmed,
                    record_outcome,
                    slot_for_call,
                )
                slot_id = None
                async with SessionLocal() as check:
                    slot = await slot_for_call(check, function.__name__, arguments, datetime.now(UTC))
                    if slot is None and function.__name__ in TIMED:
                        await check.commit()
                        return {"published": False, "reason": "outside_slot"}
                    if slot:
                        if slot.state == "RUNNING":
                            if not await reconcile_confirmed(check, function.__name__, arguments, slot):
                                record_outcome(slot, {"reason": "interrupted"}, datetime.now(UTC))
                            await check.commit()
                        if slot.state in TERMINAL:
                            return {"published": False, "reason": "delivery_uncertain" if slot.state == "RECONCILIATION" else "slot_completed", "slot": slot.slot_key}
                        slot_id = slot.id
                    if not await _global_spacing_available(check, datetime.now(UTC)):
                        if slot:
                            record_outcome(slot, {"reason": "global_spacing"}, datetime.now(UTC))
                            from backend.app.services.channel_policy import channel_window
                            last, minutes = await channel_window(check)
                            if last:
                                slot.next_attempt_at = min(last + timedelta(minutes=minutes), slot.deadline_at)
                        await check.commit()
                        return {"published": False, "reason": "global_spacing"}
                    if slot:
                        slot.state = "RUNNING"
                        slot.attempts += 1
                    await check.commit()
                context = current_slot.set(slot_id)
                try:
                    result = await function(*args, **kwargs)
                except BaseException:
                    if slot_id:
                        async with SessionLocal() as check:
                            slot = await check.get(EditorialSlot, slot_id)
                            record_outcome(slot, {"reason": "unexpected_error"}, datetime.now(UTC))
                            await check.commit()
                    raise
                finally:
                    current_slot.reset(context)
                if slot_id:
                    async with SessionLocal() as check:
                        slot = await check.get(EditorialSlot, slot_id)
                        record_outcome(slot, result, datetime.now(UTC))
                        await check.commit()
                return result
        except TimeoutError:
            return {"published": False, "reason": "channel_busy"}
        finally:
            await client.aclose()
    return guarded


@shared_task(name="backend.app.tasks.dispatch_bot_promo")
def dispatch_bot_promo(force: bool = False) -> dict:
    return run(_dispatch_bot_promo(force))


@serialized_channel_task
async def _dispatch_bot_promo(force: bool = False) -> dict:
    local = datetime.now(ZoneInfo("America/Sao_Paulo"))
    async with SessionLocal() as session:
        stored = await session.scalar(select(AppSetting).where(AppSetting.key == "bot"))
        config = stored.value if stored else {}
        if not config.get("enabled", True) or not config.get("promo_enabled", True):
            return {"published": False, "reason": "disabled"}
        times = [str(value) for value in config.get("promo_times", ["10:30", "15:30", "20:30"])]
        from backend.app.services.calendar import current_slot
        scheduled = await session.get(EditorialSlot, current_slot.get()) if current_slot.get() else None
        effective = scheduled.scheduled_for.astimezone(ZoneInfo("America/Sao_Paulo")) if scheduled else local
        current = effective.strftime("%H:%M")
        if not force and not scheduled and current not in times:
            return {"published": False, "reason": "outside_slot"}
        slot = f"{effective:%Y-%m-%d}:{current if not force else 'manual'}"
        if await session.scalar(
            select(BotCampaignMessage).where(BotCampaignMessage.slot_key == slot)
        ):
            return {"published": False, "reason": "already_published"}
        if not force and not await _global_spacing_available(session, local.astimezone(UTC)):
            return {"published": False, "reason": "global_spacing"}
        variants = [
            "💸 <b>QUER ECONOMIZAR SEM PROCURAR O DIA INTEIRO?</b>\n\nChame o bot no privado, escolha uma categoria e veja as ofertas mais recentes. 🤖\n\n👉 {BOT_LINK}",
            "🤖 <b>O CAÇADOR DE PREÇOS TAMBÉM ATENDE NO PRIVADO!</b>\n\nConsulte ofertas por categoria e cupons ativos sem lotar o canal. 💰\n\n👉 {BOT_LINK}",
            "🏷️ <b>PROCURANDO UMA OFERTA ESPECÍFICA?</b>\n\nAbra o atendimento privado e receba as últimas promoções da categoria que você escolher. 💸\n\n👉 {BOT_LINK}",
        ]
        custom_text = str(config.get("promo_text") or "").strip()
        if custom_text:
            if "{BOT_LINK}" not in custom_text:
                custom_text = f"{custom_text}\n\n👉 {{BOT_LINK}}"
            variants = [custom_text]
        text = variants[
            (local.toordinal() + max(times.index(current), 0) if current in times else local.hour)
            % len(variants)
        ]
        intent = BotCampaignMessage(
                slot_key=slot,
                destination=settings.telegram_public_channel_id or "",
                payload={"template": variants.index(text), "delivery_status": "SENDING"},
            )
        session.add(intent)
        await session.commit()
        try:
            message_id, username = await TelegramService().send_public_bot_promo(text)
        except Exception:
            intent.payload = {**intent.payload, "delivery_status": "UNCERTAIN"}
            await session.commit()
            return {"published": False, "reason": "delivery_uncertain"}
        intent.telegram_message_id = message_id
        intent.published_at = datetime.now(UTC)
        intent.payload = {**intent.payload, "delivery_status": "SENT", "bot_username": username}
        await session.commit()
        return {"published": True, "message_id": message_id, "bot_username": username}


@shared_task(name="backend.app.tasks.reprocess_settings")
def reprocess_settings(key: str, version: int) -> dict:
    return run(_reprocess_settings(key, version))


async def _reprocess_settings(key: str, version: int) -> dict:
    return await _reprocess_settings_batch({key: version})


@shared_task(name="backend.app.tasks.reprocess_settings_batch")
def reprocess_settings_batch(changes: dict[str, int]) -> dict:
    return run(_reprocess_settings_batch(changes))


async def _reprocess_settings_batch(changes: dict[str, int]) -> dict:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with DistributedLock(redis, "lock:settings_reprocess", ttl=900, wait_timeout=60):
            async with SessionLocal() as session:
                try:
                    result = await reprocess_after_settings(session, changes)
                    await _record_run(session, "reprocess_settings", "SUCCEEDED", result)
                    return result
                except Exception as exc:
                    await session.rollback()
                    await _record_run(
                        session, "reprocess_settings", "FAILED",
                        {"changes": changes}, error=f"{type(exc).__name__}: {exc}",
                    )
                    raise
    finally:
        await redis.aclose()


@shared_task(
    name="backend.app.tasks.check_browser_auth",
    autoretry_for=(TimeoutError,),
    retry_backoff=True,
    max_retries=3,
)
def check_browser_auth() -> str:
    return run(_check_browser_auth())


async def _check_browser_auth() -> str:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    provider = MercadoLivreProvider(redis)
    try:
        state = await provider.authentication_state()
        await _store_browser_state(state)
        return state
    finally:
        await provider.aclose()
        await redis.aclose()


@shared_task(
    name="backend.app.tasks.discover_offers",
    autoretry_for=(TimeoutError,),
    retry_backoff=True,
    max_retries=3,
)
def discover_offers(strategy: str | None = None, manual: bool = False) -> dict:
    return run(_discover_offers(strategy, manual))


async def _discover_offers(strategy: str | None = None, manual: bool = False) -> dict:
    if not manual:
        from backend.app.services.discovery_schedule import claim_discovery
        async with SessionLocal() as session:
            if not await claim_discovery(session, strategy or "auto"):
                return {"discovered": 0, "reason": "not_due"}
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    provider = MercadoLivreProvider(redis)
    try:
        try:
            offers = await provider.discover_offers(strategy=strategy)
        except RuntimeError as exc:
            state = str(exc)
            if state in AUTH_BLOCKING_STATES:
                await _store_browser_state(state)
                return {"discovered": 0, "ingested": 0, "suspended": True, "reason": state}
            raise
        async with SessionLocal() as session:
            scoring = await scoring_engine_from_settings(session)
            pipeline = OfferPipeline(scoring)
            batch_id = uuid.uuid4()
            resolved_strategy = str(
                provider.discovery_metrics.get("strategy") or strategy or "relevant"
            )
            ingested = await pipeline.ingest(
                session,
                offers,
                force_rescore=manual,
                discovery_batch_id=batch_id,
                source_strategy=resolved_strategy,
            )
            result = {
                "discovered": len(offers),
                "ingested": ingested,
                "batch_id": str(batch_id),
                "requested_strategy": strategy or "auto",
                "resolved_strategy": resolved_strategy,
                "manual": manual,
                **provider.discovery_metrics,
                **pipeline.metrics,
            }
            await _record_run(session, "discover_offers", "SUCCEEDED", result)
        return result
    finally:
        await provider.aclose()
        await redis.aclose()


@shared_task(name="backend.app.tasks.prepare_selected_offers")
def prepare_selected_offers() -> dict:
    return run(_prepare_selected_offers())


async def _prepare_selected_offers() -> dict:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    prepared = 0
    provider = MercadoLivreProvider(redis)
    try:
        async with SessionLocal() as session:
            result = await session.scalars(
                select(PublicationQueue)
                .where(
                    PublicationQueue.status.in_([
                        QueueStatus.selected, QueueStatus.retry, QueueStatus.creative_pending
                    ]),
                    PublicationQueue.publication_kind == PublicationKind.good_offer.value,
                )
                .order_by(PublicationQueue.created_at)
                .limit(5)
            )
            for queue_item in result:
                product = await session.get(Product, queue_item.product_id)
                snapshot = await session.scalar(
                    select(ProductSnapshot)
                    .where(ProductSnapshot.product_id == product.id)
                    .order_by(desc(ProductSnapshot.observed_at))
                    .limit(1)
                )
                if not product or not snapshot:
                    queue_item.status = QueueStatus.failed
                    queue_item.last_error = "Product or snapshot not found"
                    continue
                regenerating = bool(
                    queue_item.status == QueueStatus.creative_pending and queue_item.link_id
                )
                queue_item.status = QueueStatus.creative_pending if regenerating else QueueStatus.link_pending
                await session.commit()
                try:
                    link = await session.get(AffiliateLink, queue_item.link_id) if regenerating else None
                    if not link:
                        link_data = await provider.generate_affiliate_link(
                            product.product_url, settings.meli_affiliate_tag
                        )
                        if not is_official_short_link(link_data.short_url):
                            raise RuntimeError("Invalid official short link format")
                        link = AffiliateLink(
                            product_id=product.id, full_url=product.product_url,
                            short_url=link_data.short_url, affiliate_tag=link_data.tracking_tag,
                            source=link_data.source, validated_format=True,
                            generation_metadata=link_data.evidence,
                        )
                        session.add(link)
                        await session.flush()
                    normalized = _normalized(product, snapshot)
                    coupon, applicability = await applicable_coupon(session, product.id)
                    coupon_value = coupon_data(coupon) if coupon else None
                    if coupon_value and not coupon_value.code:
                        generated = await provider.generate_coupon(coupon_value)
                        coupon.code = generated.code
                        coupon_value = generated
                    copy, generator = await GroqCopyGenerator().generate(
                        normalized, coupon_value, await _content_config(session)
                    )
                    image_path = await ProductImageService().download(normalized)
                    creative = Creative(
                        product_id=product.id,
                        copy_text=copy.telegram_text(link.short_url),
                        image_path=str(image_path),
                        image_source_url=normalized.image_url,
                        template_version="telegram-v3",
                        content_setting_version=await _setting_version(session, "content"),
                        generator=generator,
                        payload={
                            **copy.model_dump(),
                            "coupon_applicability_id": str(applicability.id)
                            if applicability
                            else None,
                        },
                    )
                    session.add(creative)
                    await session.flush()
                    queue_item.link_id = link.id
                    queue_item.creative_id = creative.id
                    queue_item.status = (
                        QueueStatus.ready if _public_armed() else QueueStatus.dry_run
                    )
                    queue_item.last_error = None
                    queue_item.attempts = 0
                    prepared += 1
                except (RuntimeError, ValueError) as exc:
                    message = str(exc)
                    queue_item.last_error = message[:1000]
                    if message in AUTH_BLOCKING_STATES:
                        queue_item.status = QueueStatus.selected
                        await _store_browser_state(message)
                    else:
                        queue_item.attempts += 1
                        queue_item.status = (
                            QueueStatus.retry if queue_item.attempts < 3 else QueueStatus.failed
                        )
                await session.commit()
        return {"prepared": prepared}
    finally:
        await provider.aclose()
        await redis.aclose()


@shared_task(name="backend.app.tasks.telegram_private_smoke")
def telegram_private_smoke() -> dict:
    return run(_telegram_private_smoke())


async def _telegram_private_smoke() -> dict:
    if not settings.telegram_bot_token or not settings.telegram_admin_chat_id:
        return {"skipped": True, "reason": "telegram_not_configured"}
    service = TelegramService()
    validation = await service.validate_private_smoke()
    message_id = await service.send_private_smoke()
    return {**validation, "message_id": message_id}


@shared_task(name="backend.app.tasks.send_private_offer_preview")
def send_private_offer_preview() -> dict:
    return run(_send_private_offer_preview())


async def _send_private_offer_preview() -> dict:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    provider = MercadoLivreProvider(redis)
    try:
        offers = await provider.discover_offers(strategy="relevant")
        if not offers:
            return {"sent": False, "reason": "no_offers"}
        async with SessionLocal() as session:
            scoring = await scoring_engine_from_settings(session)
            ranked = [(scoring.score(offer), offer) for offer in offers]
            score, product = max(ranked, key=lambda item: item[0].total)
            copy, generator = await GroqCopyGenerator().generate(product)
            image_path = await ProductImageService().download(product)
            caption = copy.telegram_text().replace(
                "Publicidade • Anúncios • Promoções", "Prévia administrativa • sem link afiliado"
            )
            message_id = await TelegramService().send_private_offer_preview(image_path, caption)
            session.add(
                AuditEvent(
                    actor="admin",
                    action="PRIVATE_OFFER_PREVIEW_SENT",
                    entity_type="product",
                    entity_id=product.item_id,
                    details={
                        "message_id": message_id,
                        "score": str(score.total),
                        "generator": generator,
                        "contains_affiliate_link": False,
                    },
                )
            )
            await session.commit()
            return {
                "sent": True,
                "message_id": message_id,
                "item_id": product.item_id,
                "score": str(score.total),
            }
    finally:
        await provider.aclose()
        await redis.aclose()


@shared_task(name="backend.app.tasks.publish_ready_offer")
def publish_ready_offer(queue_id: str) -> dict:
    return run(_publish_ready_offer(queue_id))


@serialized_channel_task
async def _publish_ready_offer(queue_id: str) -> dict:
    if not _public_armed():
        raise RuntimeError("Public publishing is not armed")
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    provider = MercadoLivreProvider(redis)
    try:
        async with SessionLocal() as bootstrap:
            queued = await bootstrap.get(PublicationQueue, queue_id)
            if not queued:
                raise RuntimeError("Queue item was not found")
            product_id = queued.product_id
        async with DistributedLock(
            redis, f"lock:publish:product:{product_id}", ttl=240, wait_timeout=2
        ):
            async with SessionLocal() as session:
                # All writers lock product before queue; avoid reprocessing/publisher inversion.
                await session.scalar(select(Product).where(Product.id == product_id).with_for_update())
                queue_item = await session.scalar(
                    select(PublicationQueue)
                    .where(PublicationQueue.id == uuid.UUID(queue_id))
                    .with_for_update()
                )
                if not queue_item:
                    raise RuntimeError("Queue item was not found")
                existing = await session.scalar(
                    select(Publication).where(Publication.queue_id == queue_item.id)
                )
                if existing:
                    if existing.delivery_status == "SENT":
                        queue_item.status = QueueStatus.published
                        queue_item.last_error = None
                    else:
                        queue_item.status = QueueStatus.failed
                        queue_item.last_error = "DELIVERY_UNCERTAIN_REQUIRES_RECONCILIATION"
                    await session.commit()
                    return {
                        "published": False,
                        "reason": existing.delivery_status.lower(),
                        "message_id": existing.telegram_message_id,
                    }
                if queue_item.status != QueueStatus.ready:
                    raise RuntimeError("Queue item is not ready")
                product = await session.get(Product, queue_item.product_id)
                creative = await session.get(Creative, queue_item.creative_id)
                from backend.app.services.editorial_policy import (
                    diversity_allowed,
                    editorial_context,
                )
                editorial, recent = await editorial_context(session)
                if product and not diversity_allowed(product, editorial, recent):
                    return {"published": False, "reason": "editorial_diversity"}
                snapshot = (
                    await session.scalar(
                        select(ProductSnapshot)
                        .where(ProductSnapshot.product_id == product.id)
                        .order_by(desc(ProductSnapshot.observed_at))
                        .limit(1)
                    )
                    if product
                    else None
                )
                if not product or not snapshot or not creative or not creative.image_path:
                    raise RuntimeError("Product, snapshot or creative is incomplete")

                live = await provider.revalidate(_normalized(product, snapshot))
                if not live:
                    queue_item.status = QueueStatus.skipped
                    queue_item.last_error = (
                        "Product was not found during pre-publication revalidation"
                    )
                    await session.commit()
                    return {"published": False, "reason": "not_found_during_revalidation"}
                coupon, applicability = await applicable_coupon(session, product.id, price=live.current_price)
                coupon_value = coupon_data(coupon) if coupon else None
                effective_discount = effective_coupon_percent(coupon, live.current_price, datetime.now(UTC)) if coupon else None
                from backend.app.services.offer_policy import evaluate_score
                score = evaluate_score(await scoring_engine_from_settings(session), live, snapshot,
                    live.observed_at, effective_discount)
                if not score.eligible:
                    queue_item.status = QueueStatus.skipped
                    queue_item.last_error = "Offer failed pre-publication scoring"
                    await session.commit()
                    return {"published": False, "reason": "score_changed"}
                if not await OfferPipeline()._can_queue(
                    session,
                    product,
                    snapshot,
                    effective_discount,
                    current_price=live.current_price,
                ):
                    queue_item.status = QueueStatus.skipped
                    queue_item.last_error = "SKIPPED_DUPLICATE_PRE_PUBLICATION"
                    await session.commit()
                    return {"published": False, "reason": "duplicate_window"}
                queue_item.status = QueueStatus.publishing
                await OfferPipeline(await scoring_engine_from_settings(session)).ingest(session, [live], enqueue=False)

                link = await session.get(AffiliateLink, queue_item.link_id)
                if not link:
                    raise RuntimeError("Affiliate link is missing")
                preserved = creative.payload.get("creative") if creative.payload else None
                generator_service = GroqCopyGenerator()
                if preserved:
                    from backend.app.services.ai import CreativeLines

                    copy = generator_service.render(
                        live, CreativeLines.model_validate(preserved), coupon_value
                    )
                    generator = creative.generator
                    if copy.creative.model_dump() != CreativeLines.model_validate(preserved).model_dump():
                        generator = "template_safe"
                else:
                    copy, generator = await generator_service.generate(
                        live, coupon_value, await _content_config(session)
                    )
                image_path = await ProductImageService().download(live)
                creative.copy_text = copy.telegram_text(link.short_url)
                creative.image_path = str(image_path)
                creative.image_source_url = live.image_url
                creative.generator = generator
                creative.template_version = "telegram-v3"
                creative.payload = copy.model_dump()
                queue_item.status = QueueStatus.publishing
                payload_hash = hashlib.sha256(creative.copy_text.encode()).hexdigest()
                publication = Publication(
                    queue_id=queue_item.id,
                    destination=settings.telegram_public_channel_id or "",
                    mode="PUBLIC_AUTO",
                    payload_hash=payload_hash,
                    payload={
                        "caption": creative.copy_text,
                        "facts": copy.facts,
                        "effective_coupon_percent": str(effective_discount) if effective_discount is not None else None,
                        "image_source_url": live.image_url,
                        "coupon_applicability_id": str(applicability.id) if applicability else None,
                    },
                    publication_kind=PublicationKind.good_offer.value,
                    delivery_status="SENDING",
                    dedupe_key=f"GOOD_OFFER:{product.id}:{queue_item.id}",
                )
                session.add(publication)
                await session.commit()
                try:
                    telegram = TelegramService()
                    message_id = await telegram.send_public_offer(
                        image=image_path,
                        caption=creative.copy_text,
                    )
                except Exception as exc:
                    publication.delivery_status = "UNCERTAIN"
                    queue_item.status = QueueStatus.failed
                    queue_item.last_error = (
                        f"DELIVERY_UNCERTAIN_REQUIRES_RECONCILIATION: {type(exc).__name__}"
                    )
                    await session.commit()
                    return {"published": False, "reason": "delivery_uncertain"}
                publication.telegram_message_id = message_id
                publication.public_message_url = (
                    telegram.last_public_message_url or _channel_message_url(message_id)
                )
                publication.published_at = datetime.now(UTC)
                publication.delivery_status = "SENT"
                queue_item.status = QueueStatus.published
                siblings = list(
                    await session.scalars(
                        select(PublicationQueue).where(
                            PublicationQueue.product_id == product.id,
                            PublicationQueue.id != queue_item.id,
                            PublicationQueue.publication_kind == PublicationKind.good_offer.value,
                            PublicationQueue.status.in_(
                                [
                                    QueueStatus.discovered,
                                    QueueStatus.selected,
                                    QueueStatus.link_pending,
                                    QueueStatus.creative_pending,
                                    QueueStatus.ready,
                                    QueueStatus.dry_run,
                                    QueueStatus.retry,
                                ]
                            ),
                        )
                    )
                )
                for sibling in siblings:
                    sibling.status = QueueStatus.skipped
                    sibling.last_error = "SKIPPED_DUPLICATE_AFTER_PUBLICATION"
                await session.commit()
                return {
                    "published": True,
                    "message_id": message_id,
                    "public_message_url": publication.public_message_url,
                }
    except TimeoutError:
        return {"published": False, "reason": "product_publication_locked"}
    finally:
        await provider.aclose()
        await redis.aclose()


@shared_task(name="backend.app.tasks.dispatch_publication")
def dispatch_publication() -> dict:
    return run(_dispatch_publication())


async def _dispatch_publication() -> dict:
    if not _public_armed():
        return {"dispatched": False, "reason": "public_not_armed"}
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        async with DistributedLock(redis, "lock:publication_dispatch", ttl=60, wait_timeout=1):
            return await _dispatch_publication_locked()
    except TimeoutError:
        return {"dispatched": False, "reason": "dispatch_in_progress"}
    finally:
        await redis.aclose()


async def _dispatch_publication_locked() -> dict:
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        stored = await session.scalar(select(AppSetting).where(AppSetting.key == "cadence"))
        cadence = stored.value if stored else {}
        daily_limit = int(cadence.get("daily_limit", 30))
        publish_minutes = int(cadence.get("publish_minutes", 15))
        daily_count = await session.scalar(
            select(func.count(Publication.id)).where(
                Publication.published_at >= now - timedelta(days=1),
                Publication.publication_kind == PublicationKind.good_offer.value,
                Publication.delivery_status == "SENT",
            )
        )
        if (daily_count or 0) >= daily_limit:
            return {"dispatched": False, "reason": "daily_limit"}
        in_flight = await session.scalar(
            select(func.count(PublicationQueue.id)).where(
                PublicationQueue.status == QueueStatus.publishing
            )
        )
        if in_flight:
            return {"dispatched": False, "reason": "publication_in_progress"}
        last_publication = await session.scalar(
            select(Publication)
            .where(
                Publication.publication_kind == PublicationKind.good_offer.value,
                Publication.delivery_status == "SENT",
            )
            .order_by(desc(Publication.published_at))
            .limit(1)
        )
        if (
            last_publication
            and last_publication.published_at
            and last_publication.published_at > now - timedelta(minutes=publish_minutes)
        ):
            return {"dispatched": False, "reason": "cadence_limit"}
        if not await _global_spacing_available(session, now, publish_minutes):
            return {"dispatched": False, "reason": "global_spacing"}
        candidates = list(await session.scalars(
            select(PublicationQueue)
            .where(
                PublicationQueue.status.in_([QueueStatus.dry_run, QueueStatus.ready]),
                PublicationQueue.publication_kind == PublicationKind.good_offer.value,
            )
            .order_by(
                PublicationQueue.manual_position.is_(None),
                PublicationQueue.manual_position,
                desc(PublicationQueue.priority),
                PublicationQueue.created_at,
            )
            .limit(100)
        ))
        from backend.app.services.editorial_policy import diversity_allowed, editorial_context
        editorial, recent = await editorial_context(session)
        products = {p.id: p for p in await session.scalars(select(Product).where(Product.id.in_([q.product_id for q in candidates])))}
        item = next((q for q in candidates if q.product_id in products and diversity_allowed(products[q.product_id], editorial, recent)), None)
        if not item:
            return {"dispatched": False, "reason": "editorial_diversity" if candidates else "empty_queue"}
        item.status = QueueStatus.ready
        await session.commit()
        from backend.app.worker import celery_app

        task = celery_app.send_task(
            "backend.app.tasks.publish_ready_offer", args=[str(item.id)], queue="browser"
        )
        return {"dispatched": True, "task_id": task.id}


@shared_task(name="backend.app.tasks.dispatch_bad_offer")
def dispatch_bad_offer(force: bool = False, dry_run: bool = False) -> dict:
    return run(_dispatch_bad_offer(force, dry_run))


@serialized_channel_task
async def _dispatch_bad_offer(force: bool = False, dry_run: bool = False) -> dict:
    started = datetime.now(UTC)
    try:
        result = await _dispatch_bad_offer_impl(force, dry_run)
        async with SessionLocal() as run_session:
            await _record_run(run_session, "dispatch_bad_offer", "SUCCEEDED", result)
        return result
    except Exception as exc:
        async with SessionLocal() as run_session:
            await _record_run(
                run_session, "dispatch_bad_offer", "FAILED",
                {"force": force, "dry_run": dry_run, "started_at": started.isoformat()},
                error=f"{type(exc).__name__}: {exc}",
            )
        raise


async def _dispatch_bad_offer_impl(force: bool = False, dry_run: bool = False) -> dict:
    if not _public_armed() and not dry_run:
        return {"published": False, "reason": "public_not_armed"}
    local = datetime.now(ZoneInfo("America/Sao_Paulo"))
    now = local.astimezone(UTC)
    async with SessionLocal() as session:
        stored = await session.scalar(select(AppSetting).where(AppSetting.key == "bad_offers"))
        policy = stored.value if stored else {}
        if not policy.get("enabled", True):
            return {"published": False, "reason": "disabled"}
        times = [str(value) for value in policy.get("times", ["12:30", "19:30"])]
        slot = None
        if not force:
            slot = await _due_editorial_slot(session, local, times, stored.version if stored else 1,
                int(policy.get("retry_window_minutes", 90)))
            if not slot:
                return {"published": False, "reason": "outside_slot"}
            if slot.state in {"PUBLISHED", "SKIPPED"}:
                return {"published": False, "reason": "slot_completed", "slot": slot.slot_key}
            if slot.next_attempt_at > now:
                return {"published": False, "reason": "slot_waiting", "slot": slot.slot_key}
        daily_count = (
            await session.scalar(
                select(func.count(Publication.id)).where(
                    Publication.publication_kind == PublicationKind.bad_offer.value,
                    Publication.delivery_status == "SENT",
                    Publication.published_at >= now - timedelta(days=1),
                )
            )
            or 0
        )
        if daily_count >= int(policy.get("daily_limit", 2)):
            return {"published": False, "reason": "daily_limit"}
        if not force and not await _global_spacing_available(session, now):
            slot.attempts += 1
            slot.outcome_reason = "GLOBAL_SPACING"
            slot.next_attempt_at = now + timedelta(minutes=5)
            await session.commit()
            return {"published": False, "reason": "global_spacing", "slot": slot.slot_key}

        latest_per_product = (
            select(
                OfferScore.product_id,
                func.max(OfferScore.created_at).label("created_at"),
            )
            .group_by(OfferScore.product_id)
            .subquery()
        )
        latest_scores = list(
            await session.scalars(
                select(OfferScore)
                .join(
                    latest_per_product,
                    (latest_per_product.c.product_id == OfferScore.product_id)
                    & (latest_per_product.c.created_at == OfferScore.created_at),
                )
                .where(OfferScore.total <= Decimal(str(policy.get("maximum_score", 45))))
                .order_by(desc(OfferScore.created_at))
                .limit(500)
            )
        )
        seen: set[uuid.UUID] = set()
        for score in latest_scores:
            if score.product_id in seen:
                continue
            seen.add(score.product_id)
            product = await session.get(Product, score.product_id)
            if not product or not product.active or not product.image_url:
                continue
            repeated = await session.scalar(
                select(Publication.id)
                .join(PublicationQueue, PublicationQueue.id == Publication.queue_id)
                .where(
                    PublicationQueue.product_id == product.id,
                    Publication.publication_kind == PublicationKind.bad_offer.value,
                    Publication.delivery_status == "SENT",
                    Publication.published_at
                    >= now - timedelta(days=int(policy.get("repeat_days", 14))),
                )
                .limit(1)
            )
            if repeated:
                continue
            snapshots = await editorial_history(session, product.id)
            evidence = evaluate_bad_offer(
                snapshots,
                Decimal(score.total),
                maximum_score=Decimal(str(policy.get("maximum_score", 45))),
                min_observations=int(policy.get("min_observations", 3)),
                min_history_hours=int(policy.get("min_history_hours", 24)),
                min_reasons=int(policy.get("min_reasons", 2)),
                minimum_confidence=float(policy.get("minimum_confidence", 0.7)),
            )
            candidate = await session.scalar(select(EditorialCandidate).where(
                EditorialCandidate.product_id == product.id
            ))
            if not candidate:
                candidate = EditorialCandidate(product_id=product.id)
                session.add(candidate)
            candidate.latest_snapshot_id = snapshots[-1].id if snapshots else None
            candidate.score = score.total
            candidate.price_confidence = Decimal(str(evidence.price_confidence))
            candidate.observation_count = evidence.observation_count
            candidate.history_hours = Decimal(str(evidence.history_hours))
            candidate.reasons = [*evidence.reasons, *evidence.blocking_reasons]
            candidate.last_evaluated_at = now
            candidate.next_revalidation_at = now + timedelta(minutes=30)
            candidate.state = "READY" if evidence.eligible else (
                "LOW_CONFIDENCE" if evidence.price_confidence < float(policy.get("minimum_confidence", 0.7))
                else "WAITING_HISTORY"
            )
            if not evidence.eligible:
                continue
            redis = Redis.from_url(settings.redis_url, decode_responses=True)
            provider = MercadoLivreProvider(redis)
            try:
                live = await provider.revalidate(_normalized(product, snapshots[-1]))
            finally:
                await provider.aclose()
                await redis.aclose()
            if not live:
                candidate.state = "REVALIDATION_FAILED"
                continue
            await OfferPipeline(await scoring_engine_from_settings(session)).ingest(
                session, [live], force_rescore=True, enqueue=False
            )
            snapshots = await editorial_history(session, product.id)
            score = await session.scalar(
                select(OfferScore).where(OfferScore.product_id == product.id)
                .order_by(desc(OfferScore.created_at)).limit(1)
            )
            evidence = evaluate_bad_offer(
                snapshots, Decimal(score.total),
                maximum_score=Decimal(str(policy.get("maximum_score", 45))),
                min_observations=int(policy.get("min_observations", 3)),
                min_history_hours=int(policy.get("min_history_hours", 24)),
                min_reasons=int(policy.get("min_reasons", 2)),
                minimum_confidence=float(policy.get("minimum_confidence", 0.7)),
            )
            if not evidence.eligible:
                candidate.state = "REJECTED_AFTER_REVALIDATION"
                candidate.reasons = [*evidence.reasons, *evidence.blocking_reasons]
                await session.commit()
                continue
            candidate.last_evaluated_at = datetime.now(UTC)
            candidate.next_revalidation_at = datetime.now(UTC) + timedelta(minutes=30)
            caption = render_bad_offer(product, Decimal(score.total), evidence)
            alternative_url = await _alternative_message_url(
                session, product, Decimal(str(policy.get("alternative_min_score", 85)))
            )
            if dry_run:
                await session.commit()
                return {
                    "published": False,
                    "dry_run": True,
                    "product_id": str(product.id),
                    "caption": caption,
                    "evidence": evidence.as_dict(),
                    "alternative_url": alternative_url,
                }
            queue_item = PublicationQueue(
                product_id=product.id,
                score_id=score.id,
                status=QueueStatus.publishing,
                idempotency_key=f"bad-offer:{product.id}:{now:%Y%m%d}",
                publication_kind=PublicationKind.bad_offer.value,
                source_strategy="editorial_bad_offer",
                editorial_payload={
                    "caption": caption,
                    "evidence": evidence.as_dict(),
                    "alternative_url": alternative_url,
                },
            )
            session.add(queue_item)
            await session.flush()
            publication = Publication(
                queue_id=queue_item.id,
                destination=settings.telegram_public_channel_id or "",
                mode="PUBLIC_EDITORIAL",
                payload_hash=hashlib.sha256(caption.encode()).hexdigest(),
                payload=queue_item.editorial_payload,
                publication_kind=PublicationKind.bad_offer.value,
                delivery_status="SENDING",
                dedupe_key=f"BAD_OFFER:{product.id}:{now:%Y%m%d}",
            )
            session.add(publication)
            await session.commit()
            image_path = await ProductImageService().download(_normalized(product, snapshots[-1]))
            try:
                message_id, public_url = await TelegramService().send_public_bad_offer(
                    image_path, caption, alternative_url
                )
            except Exception as exc:
                publication.delivery_status = "UNCERTAIN"
                queue_item.status = QueueStatus.failed
                queue_item.last_error = (
                    f"DELIVERY_UNCERTAIN_REQUIRES_RECONCILIATION: {type(exc).__name__}"
                )
                await session.commit()
                return {"published": False, "reason": "delivery_uncertain"}
            publication.telegram_message_id = message_id
            publication.public_message_url = public_url
            publication.published_at = datetime.now(UTC)
            publication.delivery_status = "SENT"
            queue_item.status = QueueStatus.published
            await session.commit()
            if slot:
                slot.state = "PUBLISHED"
                slot.outcome_reason = "SENT"
                slot.product_id = product.id
                slot.publication_id = publication.id
                await session.commit()
            return {
                "published": True,
                "message_id": message_id,
                "public_message_url": public_url,
                "product_id": str(product.id),
            }
        if slot:
            slot.attempts += 1
            slot.candidate_count = len(latest_scores)
            slot.outcome_reason = "INSUFFICIENT_EVIDENCE"
            if now >= slot.deadline_at:
                slot.state = "SKIPPED"
            else:
                slot.next_attempt_at = now + timedelta(minutes=15)
            await session.commit()
        else:
            await session.commit()
        return {"published": False, "reason": "insufficient_evidence", "slot": slot.slot_key if slot else None}


@shared_task(name="backend.app.tasks.publish_channel_manifest")
def publish_channel_manifest() -> dict:
    return run(_publish_channel_manifest())


@serialized_channel_task
async def _publish_channel_manifest() -> dict:
    if not _public_armed():
        return {"published": False, "reason": "public_not_armed"}
    async with SessionLocal() as session:
        stored = await session.scalar(
            select(AppSetting).where(AppSetting.key == "channel_experience")
        )
        config = stored.value if stored else {}
        text = str(config.get("manifest_text") or DEFAULT_MANIFEST)
        version = hashlib.sha256(text.encode()).hexdigest()[:16]
        entry = await session.scalar(
            select(ChannelEditorialMessage).where(
                ChannelEditorialMessage.message_key == "trust-manifest"
            )
        )
        if entry and not entry.telegram_message_id:
            return {"published": False, "reason": "delivery_uncertain"}
        if not entry:
            entry = ChannelEditorialMessage(
                message_key="trust-manifest",
                kind="MANIFEST",
                version=version,
                destination=settings.telegram_public_channel_id or "",
            )
            session.add(entry)
            entry.payload = {"text": text, "delivery_status": "SENDING"}
            await session.commit()
        try:
            message_id, public_url = await TelegramService().publish_manifest(text, entry.telegram_message_id)
        except Exception:
            entry.payload = {**entry.payload, "delivery_status": "UNCERTAIN"}
            await session.commit()
            return {"published": False, "reason": "delivery_uncertain"}
        entry.version = version
        entry.telegram_message_id = message_id
        entry.public_message_url = public_url
        entry.published_at = datetime.now(UTC)
        entry.payload = {"text": text, "delivery_status": "SENT"}
        await session.commit()
        return {"published": True, "message_id": message_id, "public_message_url": public_url}


@shared_task(name="backend.app.tasks.dispatch_trust_reminder")
def dispatch_trust_reminder(force: bool = False) -> dict:
    return run(_dispatch_trust_reminder(force))


@serialized_channel_task
async def _dispatch_trust_reminder(force: bool = False) -> dict:
    if not _public_armed():
        return {"published": False, "reason": "public_not_armed"}
    local = datetime.now(ZoneInfo("America/Sao_Paulo"))
    async with SessionLocal() as session:
        stored = await session.scalar(
            select(AppSetting).where(AppSetting.key == "channel_experience")
        )
        config = stored.value if stored else {}
        if not config.get("weekly_enabled", True):
            return {"published": False, "reason": "disabled"}
        from backend.app.services.calendar import current_slot
        scheduled = await session.get(EditorialSlot, current_slot.get()) if current_slot.get() else None
        if not force and not scheduled and (
            local.isoweekday() != int(config.get("weekly_day", 7))
            or local.strftime("%H:%M") != str(config.get("weekly_time", "18:00"))
        ):
            return {"published": False, "reason": "outside_slot"}
        effective = scheduled.scheduled_for.astimezone(ZoneInfo("America/Sao_Paulo")) if scheduled else local
        slot = f"trust:{effective:%G-W%V}"
        if await session.scalar(
            select(BotCampaignMessage.id).where(BotCampaignMessage.slot_key == slot)
        ):
            return {"published": False, "reason": "already_published"}
        if not force and not await _global_spacing_available(session, local.astimezone(UTC)):
            return {"published": False, "reason": "global_spacing"}
        text = str(config.get("weekly_text") or DEFAULT_WEEKLY_REMINDER)
        intent = BotCampaignMessage(
                slot_key=slot,
                destination=settings.telegram_public_channel_id or "",
                payload={"kind": "TRUST_REMINDER", "delivery_status": "SENDING"},
            )
        session.add(intent)
        await session.commit()
        try:
            message_id, public_url = await TelegramService().send_public_trust_message(text)
        except Exception:
            intent.payload = {**intent.payload, "delivery_status": "UNCERTAIN"}
            await session.commit()
            return {"published": False, "reason": "delivery_uncertain"}
        intent.telegram_message_id = message_id
        intent.published_at = datetime.now(UTC)
        intent.payload = {**intent.payload, "delivery_status": "SENT", "public_message_url": public_url}
        await session.commit()
        return {"published": True, "message_id": message_id, "public_message_url": public_url}


@shared_task(name="backend.app.tasks.sync_coupons")
def sync_available_coupons() -> dict:
    return run(_sync_available_coupons())


async def _sync_available_coupons() -> dict:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    provider = MercadoLivreProvider(redis)
    try:
        discovered = await provider.list_coupons()
        async with SessionLocal() as session:
            pipeline = OfferPipeline(await scoring_engine_from_settings(session))
            coupon_products_ingested = await pipeline.ingest(session, provider.coupon_products)
            result = await sync_coupons(session, provider, discovered)
            scoring_setting = await session.scalar(
                select(AppSetting).where(AppSetting.key == "scoring")
            )
            result["rescoring"] = await reprocess_after_settings(
                session, {"coupons": scoring_setting.version if scoring_setting else 1},
                product_ids={uuid.UUID(value) for value in result.pop("affected_product_ids", [])},
            )
            result.update(provider.coupon_metrics)
            result["coupon_products_ingested"] = coupon_products_ingested
            await _record_run(session, "sync_coupons", "SUCCEEDED", result)
            return result
    finally:
        await provider.aclose()
        await redis.aclose()


@shared_task(name="backend.app.tasks.publish_coupon")
def publish_coupon(coupon_id: str) -> dict:
    return run(_publish_coupon(coupon_id))


@serialized_channel_task
async def _publish_coupon(coupon_id: str) -> dict:
    if not _public_armed():
        return {"published": False, "reason": "public_not_armed"}
    async with SessionLocal() as session:
        coupon = await session.get(Coupon, coupon_id)
        if not coupon or not coupon.code or not coupon.rules_version or not coupon.shareable:
            return {"published": False, "reason": "coupon_incomplete"}
        if coupon.lifecycle_status != "ACTIVE":
            return {"published": False, "reason": "coupon_not_active"}
        now = datetime.now(UTC)
        if coupon.expires_at and coupon.expires_at <= now:
            return {"published": False, "reason": "coupon_expired"}
        if (coupon.starts_at and coupon.starts_at > now) or (coupon.remaining_budget is not None and coupon.remaining_budget <= 0):
            return {"published": False, "reason": "coupon_unavailable"}
        if not coupon.last_verified_at or coupon.last_verified_at < now - timedelta(minutes=30):
            return {"published": False, "reason": "coupon_stale"}
        existing = await session.scalar(
            select(CouponPublication).where(
                CouponPublication.coupon_id == coupon.id,
                CouponPublication.rules_version == coupon.rules_version,
            )
        )
        if existing:
            return {"published": False, "reason": "already_published" if existing.telegram_message_id else "delivery_uncertain"}
        caption = _coupon_caption(coupon)
        intent = CouponPublication(
                coupon_id=coupon.id,
                rules_version=coupon.rules_version,
                destination=settings.telegram_public_channel_id or "",
                payload={"caption": caption, "source": coupon.source_kind, "delivery_status": "SENDING"},
            )
        session.add(intent)
        await session.commit()
        try:
            message_id = await TelegramService().send_public_coupon(caption)
        except Exception:
            intent.payload = {**intent.payload, "delivery_status": "UNCERTAIN"}
            await session.commit()
            return {"published": False, "reason": "delivery_uncertain"}
        intent.telegram_message_id = message_id
        intent.published_at = datetime.now(UTC)
        intent.payload = {**intent.payload, "delivery_status": "SENT"}
        session.add(
            AuditEvent(
                action="COUPON_PUBLISHED",
                entity_type="coupon",
                entity_id=str(coupon.id),
                details={"message_id": message_id, "rules_version": coupon.rules_version},
            )
        )
        await session.commit()
        return {"published": True, "message_id": message_id}


@shared_task(name="backend.app.tasks.dispatch_coupon_campaign")
def dispatch_coupon_campaign() -> dict:
    return run(_dispatch_coupon_campaign())


async def _dispatch_coupon_campaign() -> dict:
    if not _public_armed():
        return {"dispatched": False, "reason": "public_not_armed"}
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        stored = await session.scalar(select(AppSetting).where(AppSetting.key == "campaigns"))
        policy = stored.value if stored else {}
        if not policy.get("coupon_auto_publish", True):
            return {"dispatched": False, "reason": "disabled"}
        daily_limit = int(policy.get("coupon_daily_limit", 3))
        spacing = int(policy.get("coupon_spacing_minutes", 120))
        recent_count = (
            await session.scalar(
                select(func.count(CouponPublication.id)).where(
                    CouponPublication.published_at >= now - timedelta(days=1)
                )
            )
            or 0
        )
        if recent_count >= daily_limit:
            return {"dispatched": False, "reason": "daily_limit"}
        last = await session.scalar(
            select(CouponPublication).order_by(desc(CouponPublication.published_at)).limit(1)
        )
        if last and last.published_at and last.published_at > now - timedelta(minutes=spacing):
            return {"dispatched": False, "reason": "spacing_limit"}
        if not await _global_spacing_available(session, now):
            return {"dispatched": False, "reason": "global_spacing"}
        candidate = await session.scalar(
            select(Coupon)
            .where(
                Coupon.code.is_not(None),
                Coupon.shareable.is_(True),
                Coupon.lifecycle_status == "ACTIVE",
                Coupon.rules_version.is_not(None),
                Coupon.last_verified_at >= now - timedelta(minutes=45),
                Coupon.expires_at.is_(None) | (Coupon.expires_at > now),
                ~exists().where(
                    CouponPublication.coupon_id == Coupon.id,
                    CouponPublication.rules_version == Coupon.rules_version,
                ),
            )
            .order_by(desc(Coupon.discount_percent), Coupon.expires_at)
            .limit(1)
        )
        if not candidate:
            return {"dispatched": False, "reason": "no_verified_coupon"}
        from backend.app.worker import celery_app

        task = celery_app.send_task(
            "backend.app.tasks.publish_coupon", args=[str(candidate.id)], queue="default"
        )
        return {"dispatched": True, "task_id": task.id, "coupon_id": str(candidate.id)}


def _coupon_caption(coupon: Coupon) -> str:
    lines = [
        "🎟️ <b>CUPOM OFICIAL DISPONÍVEL!</b>",
        "",
        f"💸 <b>CÓDIGO:</b> <code>{html.escape(coupon.code or '')}</code>",
    ]
    if coupon.discount_percent is not None:
        lines.append(f"🔥 <b>{_pt_number(coupon.discount_percent)}% OFF</b>")
    elif coupon.discount_amount is not None:
        lines.append(f"🔥 <b>R$ {_pt_number(coupon.discount_amount)} DE DESCONTO</b>")
    if coupon.minimum_purchase is not None:
        lines.append(f"🛒 Compra mínima: R$ {_pt_number(coupon.minimum_purchase)}")
    if coupon.maximum_discount is not None:
        lines.append(f"📉 Desconto máximo: R$ {_pt_number(coupon.maximum_discount)}")
    if coupon.scope:
        lines.append(f"🏷️ {html.escape(coupon.scope)}")
    eligible_count = int((coupon.raw_metadata or {}).get("eligible_products_count") or 0)
    if eligible_count:
        lines.append(f"✅ {eligible_count} produtos compatíveis verificados")
    if coupon.expires_at:
        lines.append(f"⏰ Válido até {coupon.expires_at.astimezone().strftime('%d/%m às %H:%M')}")
    if coupon.restrictions:
        lines.append("ℹ️ " + html.escape(" • ".join(coupon.restrictions[:3])))
    lines.extend(
        [
            "",
            "😅 ECONOMIA BOA É DINHEIRO QUE CONTINUA NO BOLSO!",
            "",
            "Publicidade • Anúncios • Promoções",
        ]
    )
    return "\n".join(lines)


def _pt_number(value: Decimal) -> str:
    return f"{Decimal(value):,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


@shared_task(name="backend.app.tasks.operations_watchdog")
def operations_watchdog() -> dict:
    return run(_operations_watchdog())


async def _operations_watchdog() -> dict:
    now = datetime.now(UTC)
    actions: list[str] = []
    async with SessionLocal() as session:
        await session.execute(update(EditorialSlot).where(EditorialSlot.state == "RUNNING",
            EditorialSlot.updated_at < now - timedelta(minutes=30)
        ).values(state="RECONCILIATION", outcome_reason="INTERRUPTED"))
        expired = await session.execute(update(EditorialSlot).where(
            EditorialSlot.state == "PENDING", EditorialSlot.deadline_at < now
        ).values(state="EXPIRED"))
        if expired.rowcount:
            actions.append(f"editorial_slots_expired:{expired.rowcount}")
        browser = await session.scalar(
            select(BrowserSession).order_by(desc(BrowserSession.updated_at)).limit(1)
        )
        last_discovery = await session.scalar(
            select(TaskRun)
            .where(TaskRun.task_name == "discover_offers")
            .order_by(desc(TaskRun.finished_at))
            .limit(1)
        )
        if (
            browser
            and browser.state == BrowserAuthState.authenticated
            and (
                not last_discovery
                or not last_discovery.finished_at
                or last_discovery.finished_at < now - timedelta(minutes=30)
            )
        ):
            from backend.app.worker import celery_app

            celery_app.send_task(
                "backend.app.tasks.discover_offers", args=["relevant"], queue="browser"
            )
            actions.append("discovery_requeued")
        stale_items = list(
            await session.scalars(
                select(PublicationQueue).where(
                    PublicationQueue.status == QueueStatus.publishing,
                    PublicationQueue.updated_at < now - timedelta(minutes=15),
                )
            )
        )
        for item in stale_items:
            intent = await session.scalar(
                select(Publication).where(Publication.queue_id == item.id)
            )
            if intent:
                if intent.delivery_status == "SENDING":
                    intent.delivery_status = "UNCERTAIN"
                item.status = QueueStatus.failed
                item.last_error = "DELIVERY_UNCERTAIN_REQUIRES_RECONCILIATION"
                actions.append(f"queue_reconciliation_required:{item.id}")
            else:
                item.status = QueueStatus.retry
                item.last_error = "Recovered by watchdog before Telegram delivery"
                actions.append(f"queue_recovered:{item.id}")
        await session.commit()
    return {"actions": actions}


def _normalized(product: Product, snapshot: ProductSnapshot) -> NormalizedProduct:
    return NormalizedProduct(
        marketplace=product.marketplace,
        source=product.source,
        item_id=product.item_id,
        product_id=product.product_id,
        title=product.title,
        product_url=product.product_url,
        image_url=product.image_url,
        rating=Decimal(snapshot.rating) if snapshot.rating is not None else None,
        sold_text=snapshot.sold_text,
        sold_count=snapshot.sold_count,
        highlight=snapshot.highlight,
        commission_percent=Decimal(snapshot.commission_percent)
        if snapshot.commission_percent is not None
        else None,
        previous_price=Decimal(snapshot.previous_price)
        if snapshot.previous_price is not None
        else None,
        current_price=Decimal(snapshot.price) if snapshot.price is not None else None,
        discount_percent=Decimal(snapshot.discount_percent)
        if snapshot.discount_percent is not None
        else None,
        extra_commission=snapshot.extra_commission,
        brand_commission=snapshot.brand_commission,
        raw_metadata={**(product.raw_metadata or {}), "price_metrics": snapshot.metrics or {}, "category_name": product.category_name, "brand": product.brand},
    )


def _public_armed() -> bool:
    return bool(
        settings.telegram_affiliate_publish_enabled
        and settings.telegram_channel_declared
        and settings.telegram_public_channel_id
        and settings.telegram_delivery_mode == "public_auto"
    )


async def _store_browser_state(state: str) -> None:
    async with SessionLocal() as session:
        entry = await session.scalar(
            select(BrowserSession).order_by(desc(BrowserSession.updated_at))
        )
        if not entry:
            entry = BrowserSession()
            session.add(entry)
        entry.state = BrowserAuthState(state)
        entry.last_checked_at = datetime.now(UTC)
        if state == BrowserAuthState.authenticated.value:
            entry.last_authenticated_at = datetime.now(UTC)
        await session.commit()


async def _record_run(
    session, task_name: str, status: str, result: dict, error: str | None = None
) -> None:
    now = datetime.now(UTC)
    session.add(
        TaskRun(
            task_name=task_name,
            correlation_id=hashlib.sha256(f"{task_name}:{now.isoformat()}".encode()).hexdigest()[
                :24
            ],
            status=status,
            started_at=now,
            finished_at=now,
            duration_ms=0,
            result=result,
            error=error,
        )
    )
    await session.commit()


async def _content_config(session) -> dict:
    setting = await session.scalar(select(AppSetting).where(AppSetting.key == "content"))
    return setting.value if setting else {}


async def _setting_version(session, key: str) -> int:
    setting = await session.scalar(select(AppSetting).where(AppSetting.key == key))
    return int(setting.version if setting else 1)


async def _due_editorial_slot(
    session, local: datetime, times: list[str], policy_version: int, retry_minutes: int
) -> EditorialSlot | None:
    from backend.app.services.calendar import current_slot
    if current_slot.get():
        return await session.get(EditorialSlot, current_slot.get())
    candidates: list[tuple[str, datetime]] = []
    for value in times:
        try:
            hour, minute = (int(part) for part in value.split(":", 1))
        except (TypeError, ValueError):
            continue
        scheduled_local = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        deadline_local = scheduled_local + timedelta(minutes=retry_minutes)
        if scheduled_local <= local <= deadline_local:
            candidates.append((value, scheduled_local))
    if not candidates:
        return None
    value, scheduled_local = max(candidates, key=lambda item: item[1])
    slot_key = f"BAD_OFFER:{local:%Y-%m-%d}:{value}"
    slot = await session.scalar(
        select(EditorialSlot).where(EditorialSlot.slot_key == slot_key).with_for_update()
    )
    if slot:
        return slot
    scheduled = scheduled_local.astimezone(UTC)
    slot = EditorialSlot(
        slot_key=slot_key, kind=PublicationKind.bad_offer.value,
        scheduled_for=scheduled, deadline_at=scheduled + timedelta(minutes=retry_minutes),
        next_attempt_at=scheduled, state="PENDING", policy_version=policy_version,
    )
    session.add(slot)
    await session.flush()
    return slot


def _channel_message_url(message_id: str) -> str | None:
    destination = (settings.telegram_public_channel_id or "").strip()
    if destination.startswith("@"):
        return f"https://t.me/{destination[1:]}/{message_id}"
    return None


async def _global_spacing_available(
    session, now: datetime, minimum_minutes: int | None = None
) -> bool:
    from backend.app.services.channel_policy import spacing_available
    return await spacing_available(session, now, minimum_minutes or 0)


async def _alternative_message_url(session, product: Product, minimum_score: Decimal) -> str | None:
    row = await session.execute(
        select(Publication, OfferScore)
        .join(PublicationQueue, PublicationQueue.id == Publication.queue_id)
        .join(OfferScore, OfferScore.id == PublicationQueue.score_id)
        .join(Product, Product.id == PublicationQueue.product_id)
        .where(
            Publication.publication_kind == PublicationKind.good_offer.value,
            Publication.delivery_status == "SENT",
            Publication.public_message_url.is_not(None),
            OfferScore.total >= minimum_score,
            PublicationQueue.product_id != product.id,
            Product.category_name == product.category_name,
        )
        .order_by(desc(Publication.published_at))
        .limit(1)
    )
    candidate = row.first()
    return candidate[0].public_message_url if candidate else None
