import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.asyncio import Redis
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.db import get_session
from backend.app.dependencies import csrf_protected, current_user
from backend.app.models import (
    AdminUser,
    AppSetting,
    AuditEvent,
    BrowserSession,
    Coupon,
    CouponApplicabilityStatus,
    CouponProductApplicability,
    Creative,
    DiscoverySchedule,
    EditorialCandidate,
    EditorialSlot,
    OfferScore,
    Product,
    ProductCurrentState,
    ProductObservation,
    ProductSnapshot,
    Publication,
    PublicationKind,
    PublicationQueue,
    QueueStatus,
    ReprocessingJob,
    SettingsRevision,
    TaskRun,
)
from backend.app.schemas import (
    ActionResponse,
    DashboardStatus,
    DiscoveryRequest,
    ProductResponse,
    QueueBulkRequest,
    QueueReorderRequest,
    SettingsBatchUpdate,
    SettingUpdate,
)
from backend.app.services.browser import MercadoLivreBrowserService
from backend.app.services.pipeline import list_products
from backend.app.services.queue_actions import can_mutate, lock_queue_items
from backend.app.services.reprocessing import preview_settings_impact
from backend.app.services.telegram import TelegramService
from backend.app.worker import celery_app

router = APIRouter(tags=["dashboard"])
settings = get_settings()


@router.get("/operations/retention")
async def retention_preview(_: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    from backend.app.services.retention import retain_history
    stored = await session.scalar(select(AppSetting).where(AppSetting.key == "retention"))
    return await retain_history(session, int((stored.value if stored else {}).get("days", 90)), dry_run=True)


@router.get("/operations/weekly-report")
async def weekly_report(_: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    from backend.app.services.admin_report import aggregate_report
    payload = await aggregate_report(session)
    latest = (await session.execute(text("SELECT week_key,created_at,delivery_status FROM weekly_admin_reports ORDER BY created_at DESC LIMIT 1"))).mappings().first()
    return {**payload, "last_delivery": dict(latest) if latest else None}


@router.get("/products/{product_id}/daily-history")
async def daily_history(product_id: uuid.UUID, page: int = Query(1, ge=1),
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    params = {"pid": product_id, "offset": (page-1)*30}
    total = await session.scalar(text("SELECT count(*) FROM product_daily_history WHERE product_id=:pid"), params)
    rows = (await session.execute(text("SELECT * FROM product_daily_history WHERE product_id=:pid ORDER BY day DESC LIMIT 30 OFFSET :offset"), params)).mappings()
    return {"page": page, "total": total, "items": [dict(row) for row in rows]}


@router.get("/operations/calendar")
async def operations_calendar(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    kind: str | None = None, state: str | None = None,
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    query = select(EditorialSlot).where(
        EditorialSlot.scheduled_for >= datetime.now(UTC)-timedelta(days=7)
    )
    if kind:
        query = query.where(EditorialSlot.kind == kind)
    if state:
        query = query.where(EditorialSlot.state == state)
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    slots = list(await session.scalars(query.order_by(EditorialSlot.state != "PENDING",
        EditorialSlot.scheduled_for, EditorialSlot.id).offset((page-1)*page_size).limit(page_size)))
    discoveries = list(await session.scalars(select(DiscoverySchedule).order_by(DiscoverySchedule.strategy)))
    return {"total": total, "page": page, "page_size": page_size, "timezone": "America/Sao_Paulo", "discoveries": [{"strategy": row.strategy,
        "next_due_at": row.next_due_at, "interval_minutes": row.interval_minutes} for row in discoveries],
        "slots": [{"id": str(row.id), "kind": row.kind, "scheduled_for": row.scheduled_for,
        "next_attempt_at": row.next_attempt_at if row.state == "PENDING" else None,
        "state": row.state, "reason": row.outcome_reason} for row in slots]}


@router.get("/products/{product_id}/overview")
async def product_overview(product_id: uuid.UUID, page: int = Query(1, ge=1),
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    product = await session.get(Product, product_id)
    if not product:
        raise HTTPException(404, "Produto não encontrado")
    state = await session.get(ProductCurrentState, product_id)
    score = await session.get(OfferScore, state.score_id) if state and state.score_id else None
    total = await session.scalar(select(func.count()).select_from(ProductSnapshot).where(ProductSnapshot.product_id == product_id))
    snapshots = list(await session.scalars(select(ProductSnapshot).where(ProductSnapshot.product_id == product_id)
        .order_by(desc(ProductSnapshot.observed_at), desc(ProductSnapshot.id)).offset((page-1)*30).limit(30)))
    queue = list(await session.scalars(select(PublicationQueue).where(PublicationQueue.product_id == product_id)
        .order_by(desc(PublicationQueue.created_at)).limit(20)))
    publications = list(await session.scalars(select(Publication).join(PublicationQueue,
        Publication.queue_id == PublicationQueue.id).where(PublicationQueue.product_id == product_id)
        .order_by(desc(Publication.published_at)).limit(20)))
    coupons = (await session.execute(select(Coupon, CouponProductApplicability).join(CouponProductApplicability,
        CouponProductApplicability.coupon_id == Coupon.id).where(CouponProductApplicability.product_id == product_id).limit(30))).all()
    observations = await session.scalar(select(func.count()).select_from(ProductObservation).where(ProductObservation.product_id == product_id))
    return {"id": str(product.id), "title": product.title, "image_url": product.image_url,
        "category": product.category_name, "brand": product.brand, "sources": product.discovery_strategies,
        "last_verified_at": state.last_verified_at if state else None, "observations": observations,
        "score": {"total": score.total, "components": score.components, "reasons": score.reasons, "eligible": score.eligible} if score else None,
        "history": {"total": total, "page": page, "page_size": 30, "items": [{"price": item.price, "observed_at": item.observed_at} for item in snapshots]},
        "queue": [{"status": item.status.value, "reason": item.operational_reason, "error": item.last_error} for item in queue],
        "coupons": [{"code": coupon.code, "status": applicability.status.value, "reason": applicability.reason, "verified_at": applicability.checked_at} for coupon, applicability in coupons],
        "publications": [{"url": item.public_message_url, "published_at": item.published_at, "delivery_status": item.delivery_status} for item in publications]}


@router.get("/status", response_model=DashboardStatus)
async def dashboard_status(
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> DashboardStatus:
    product_count = await session.scalar(select(func.count(Product.id))) or 0
    counts = await session.execute(
        select(PublicationQueue.status, func.count(PublicationQueue.id)).group_by(
            PublicationQueue.status
        )
    )
    browser = await session.scalar(
        select(BrowserSession).order_by(BrowserSession.updated_at.desc())
    )
    now = datetime.now(UTC)
    eligible_count = await session.scalar(select(func.count()).select_from(ProductCurrentState)
        .join(Product, Product.id == ProductCurrentState.product_id)
        .where(Product.active.is_(True), ProductCurrentState.score_eligible.is_(True))) or 0
    published_24h = (
        await session.scalar(
            select(func.count(Publication.id)).where(
                Publication.published_at >= now - timedelta(days=1),
                Publication.delivery_status == "SENT",
            )
        )
        or 0
    )
    coupon_coverage = (
        await session.scalar(
            select(func.count(func.distinct(CouponProductApplicability.product_id))).where(
                CouponProductApplicability.status == CouponApplicabilityStatus.applicable
            )
        )
        or 0
    )
    failed_count = (
        await session.scalar(
            select(func.count(PublicationQueue.id)).where(
                PublicationQueue.status == QueueStatus.failed
            )
        )
        or 0
    )
    cadence = await session.scalar(select(AppSetting).where(AppSetting.key == "cadence"))
    cadence_value = cadence.value if cadence else {}
    last_publication = await session.scalar(
        select(Publication).where(Publication.delivery_status == "SENT").order_by(desc(Publication.published_at).nulls_last()).limit(1)
    )
    from backend.app.services.channel_policy import channel_window
    last_channel_message_at, effective_spacing = await channel_window(session, int(cadence_value.get("publish_minutes", 15)))
    publications_24h = list(
        await session.scalars(
            select(Publication)
            .where(
                Publication.published_at >= now - timedelta(days=1),
                Publication.publication_kind == PublicationKind.good_offer.value,
                Publication.delivery_status == "SENT",
            )
            .order_by(Publication.published_at)
        )
    )
    ready_count = (
        await session.scalar(
            select(func.count(PublicationQueue.id)).where(
                PublicationQueue.status.in_([QueueStatus.ready, QueueStatus.dry_run])
            )
        )
        or 0
    )
    publishing_count = (
        await session.scalar(
            select(func.count(PublicationQueue.id)).where(
                PublicationQueue.status == QueueStatus.publishing
            )
        )
        or 0
    )
    last_discovery = await session.scalar(
        select(TaskRun)
        .where(TaskRun.task_name == "discover_offers")
        .order_by(desc(TaskRun.finished_at))
        .limit(1)
    )
    last_coupon_sync = await session.scalar(
        select(TaskRun)
        .where(TaskRun.task_name == "sync_coupons")
        .order_by(desc(TaskRun.finished_at))
        .limit(1)
    )
    publish_minutes = effective_spacing
    next_publication_at, next_publication_reason = _next_publication_window(
        now=now,
        last_publication=last_publication,
        publications_24h=publications_24h,
        daily_limit=int(cadence_value.get("daily_limit", 30)),
        publish_minutes=publish_minutes,
        ready_count=ready_count,
        publishing_count=publishing_count,
        public_armed=bool(
            settings.telegram_affiliate_publish_enabled
            and settings.telegram_channel_declared
            and settings.telegram_public_channel_id
            and settings.telegram_delivery_mode == "public_auto"
        ),
        last_channel_message_at=last_channel_message_at,
    )
    return DashboardStatus(
        environment=settings.app_env,
        delivery_mode=settings.telegram_delivery_mode.upper(),
        affiliate_publish_enabled=settings.telegram_affiliate_publish_enabled,
        channel_declared=settings.telegram_channel_declared,
        groq_configured=bool(settings.groq_api_key),
        telegram_configured=bool(settings.telegram_bot_token and settings.telegram_admin_chat_id),
        browser_state=browser.state.value if browser else "AUTH_REQUIRED",
        queue_counts={status.value: count for status, count in counts.all()},
        product_count=product_count,
        eligible_count=eligible_count,
        published_24h=published_24h,
        coupon_coverage=coupon_coverage,
        failed_count=failed_count,
        daily_limit=int(cadence_value.get("daily_limit", 30)),
        next_publication_at=next_publication_at,
        next_publication_reason=next_publication_reason,
        last_discovery_at=last_discovery.finished_at if last_discovery else None,
        last_coupon_sync_at=last_coupon_sync.finished_at if last_coupon_sync else None,
        last_publication_at=last_publication.published_at if last_publication else None,
        public_channel_configured=bool(settings.telegram_public_channel_id),
    )


def _next_publication_window(
    *,
    now: datetime,
    last_publication: Publication | None,
    publications_24h: list[Publication],
    daily_limit: int,
    publish_minutes: int,
    ready_count: int,
    publishing_count: int,
    public_armed: bool,
    last_channel_message_at: datetime | None = None,
) -> tuple[datetime | None, str]:
    if not public_armed:
        return None, "PUBLICATION_DISARMED"
    if publishing_count:
        return None, "PUBLICATION_IN_PROGRESS"
    if not ready_count:
        return None, "WAITING_READY_OFFER"
    if len(publications_24h) >= daily_limit and publications_24h:
        return _ceil_next_minute(
            publications_24h[0].published_at + timedelta(days=1)
        ), "DAILY_LIMIT"
    last_commercial_at = last_publication.published_at if last_publication else None
    last_any_at = max(
        (value for value in (last_commercial_at, last_channel_message_at) if value is not None),
        default=None,
    )
    cadence_at = last_any_at + timedelta(minutes=publish_minutes) if last_any_at else now
    return _ceil_next_minute(max(now, cadence_at)), "SCHEDULED"


def _ceil_next_minute(value: datetime) -> datetime:
    return (value + timedelta(minutes=1)).replace(second=0, microsecond=0)


@router.get("/opportunities")
async def opportunities(
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=500),
    search: str | None = None,
    category: str | None = None,
    eligible: bool | None = None,
    sort: str = Query("updated", pattern="^(updated|score|discount|price)$"),
    response_format: str = Query("legacy", alias="format", pattern="^(legacy|page)$"),
    _: AdminUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict] | dict:
    product_query = (select(Product).outerjoin(ProductCurrentState, ProductCurrentState.product_id == Product.id)
        .where(Product.active.is_(True)))
    if search:
        for term in search[:160].split()[:10]:
            term = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            product_query = product_query.where(func.search_normalize(Product.title).like(func.search_normalize(f"%{term}%"), escape="\\"))
    if category:
        product_query = product_query.where(Product.category_name == category)
    latest_score_eligible = ProductCurrentState.score_eligible
    if eligible is not None:
        product_query = product_query.where(latest_score_eligible.is_(eligible))
    total = int(await session.scalar(select(func.count()).select_from(product_query.subquery())) or 0)
    order = desc(Product.updated_at)
    if sort == "score":
        order = desc(ProductCurrentState.score_total).nullslast()
    elif sort == "discount":
        order = desc(ProductCurrentState.discount_percent).nullslast()
    elif sort == "price":
        order = ProductCurrentState.price.asc().nullslast()
    products_result = list(
        await session.scalars(
            product_query.order_by(order, Product.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    product_ids = [product.id for product in products_result]
    snapshots = list(
        await session.scalars(
            select(ProductSnapshot).join(ProductCurrentState, ProductCurrentState.snapshot_id == ProductSnapshot.id)
            .where(ProductCurrentState.product_id.in_(product_ids))
        )
    )
    scores = list(
        await session.scalars(
            select(OfferScore).join(ProductCurrentState, ProductCurrentState.score_id == OfferScore.id)
            .where(ProductCurrentState.product_id.in_(product_ids))
        )
    )
    queue_items = list(
        await session.scalars(
            select(PublicationQueue)
            .where(PublicationQueue.product_id.in_(product_ids))
            .order_by(PublicationQueue.product_id, desc(PublicationQueue.created_at))
        )
    )
    coupon_rows = await session.execute(
        select(Coupon, CouponProductApplicability)
        .join(CouponProductApplicability, CouponProductApplicability.coupon_id == Coupon.id)
        .where(CouponProductApplicability.product_id.in_(product_ids))
        .order_by(
            CouponProductApplicability.product_id, desc(CouponProductApplicability.checked_at)
        )
    )
    latest_snapshot: dict = {}
    latest_score: dict = {}
    latest_queue: dict = {}
    latest_coupon: dict = {}
    for item in snapshots:
        latest_snapshot.setdefault(item.product_id, item)
    for item in scores:
        latest_score.setdefault(item.product_id, item)
    for item in queue_items:
        latest_queue.setdefault(item.product_id, item)
    for coupon, applicability in coupon_rows:
        latest_coupon.setdefault(applicability.product_id, (coupon, applicability))
    response: list[dict] = []
    for product in products_result:
        snapshot = latest_snapshot.get(product.id)
        score = latest_score.get(product.id)
        queue_item = latest_queue.get(product.id)
        coupon_pair = latest_coupon.get(product.id)
        response.append(
            {
                "id": str(product.id),
                "item_id": product.item_id,
                "title": product.title,
                "product_url": product.product_url,
                "image_url": product.image_url,
                "category_name": product.category_name,
                "brand": product.brand,
                "last_seen_at": product.last_seen_at,
                "discovery_count": product.discovery_count,
                "price": snapshot.price if snapshot else None,
                "previous_price": snapshot.previous_price if snapshot else None,
                "discount_percent": snapshot.discount_percent if snapshot else None,
                "rating": snapshot.rating if snapshot else None,
                "sold_text": snapshot.sold_text if snapshot else None,
                "price_metrics": snapshot.metrics if snapshot else {},
                "score": score.total if score else None,
                "classification": score.classification if score else None,
                "eligible": score.eligible if score else False,
                "queue_id": str(queue_item.id) if queue_item else None,
                "queue_status": queue_item.status.value if queue_item else None,
                "coupon": (
                    {
                        "id": str(coupon_pair[0].id),
                        "code": coupon_pair[0].code,
                        "discount_percent": coupon_pair[0].discount_percent,
                        "status": coupon_pair[1].status.value,
                        "reason": coupon_pair[1].reason,
                    }
                    if coupon_pair
                    else None
                ),
            }
        )
    if response_format == "page":
        return {"items": response, "page": page, "page_size": page_size, "total": total}
    return response


@router.get("/products", response_model=list[ProductResponse])
async def products(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    _: AdminUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Product]:
    return await list_products(session, offset=(page - 1) * page_size, limit=page_size)


@router.get("/queue")
async def queue(
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=1, le=500),
    status: str | None = None,
    category: str | None = None,
    strategy: str | None = None,
    search: str | None = None,
    sort: str = Query("priority", pattern="^(priority|score|discount|created|scheduled)$"),
    _: AdminUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    query = select(PublicationQueue, Product).join(
        Product, Product.id == PublicationQueue.product_id
    ).outerjoin(OfferScore, OfferScore.id == PublicationQueue.score_id).outerjoin(ProductSnapshot, ProductSnapshot.id == OfferScore.snapshot_id)
    if status:
        try:
            values = [
                QueueStatus(value.strip().upper()) for value in status.split(",") if value.strip()
            ]
            query = query.where(PublicationQueue.status.in_(values))
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid queue status") from None
    if category:
        query = query.where(Product.category_name == category)
    if strategy:
        query = query.where(PublicationQueue.source_strategy == strategy)
    if search:
        for word in search[:160].split()[:10]:
            word = word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            query = query.where(or_(func.search_normalize(Product.title).like(func.search_normalize(f"%{word}%"), escape="\\"), Product.item_id.ilike(f"%{word}%", escape="\\")))
    filtered = query.subquery()
    total = await session.scalar(select(func.count()).select_from(filtered)) or 0
    score_order = OfferScore.total
    discount_order = ProductSnapshot.discount_percent
    order = {
        "priority": (
            PublicationQueue.manual_position.is_(None),
            PublicationQueue.manual_position,
            desc(PublicationQueue.priority),
            desc(score_order),
        ),
        "score": (desc(score_order), desc(PublicationQueue.created_at)),
        "discount": (desc(discount_order), desc(PublicationQueue.created_at)),
        "created": (desc(PublicationQueue.created_at),),
        "scheduled": (PublicationQueue.scheduled_for.is_(None), PublicationQueue.scheduled_for),
    }[sort]
    rows = await session.execute(
        query.order_by(*order, PublicationQueue.id).offset((page - 1) * page_size).limit(page_size)
    )
    counts_rows = await session.execute(
        select(PublicationQueue.status, func.count(PublicationQueue.id)).group_by(
            PublicationQueue.status
        )
    )
    items = [
        {
            "id": item.id,
            "product_id": item.product_id,
            "product_title": product.title,
            "product_item_id": product.item_id,
            "product_image_url": product.image_url,
            "status": item.status.value,
            "scheduled_for": item.scheduled_for,
            "attempts": item.attempts,
            "last_error": item.last_error,
            "operational_reason": item.operational_reason,
            "created_at": item.created_at,
            "category_name": product.category_name,
            "source_strategy": item.source_strategy,
            "discovery_batch_id": item.discovery_batch_id,
            "priority": item.priority,
            "manual_position": item.manual_position,
            "publication_kind": item.publication_kind,
            "editorial_payload": item.editorial_payload,
        }
        for item, product in rows
    ]
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "counts": {state.value: count for state, count in counts_rows.all()},
    }


@router.patch("/queue/reorder")
async def reorder_queue(
    payload: QueueReorderRequest,
    _: AdminUser = Depends(csrf_protected),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = await lock_queue_items(session, payload.ids)
    by_id = {item.id: item for item in rows}
    changed = 0
    for position, item_id in enumerate(payload.ids, start=1):
        item = by_id.get(item_id)
        if item and await can_mutate(session, item):
            item.manual_position = position
            changed += 1
    session.add(
        AuditEvent(
            actor="admin", action="QUEUE_REORDERED", entity_type="queue", details={"items": changed}
        )
    )
    await session.commit()
    return {"updated": changed}


@router.get("/queue/options")
async def queue_options(
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    categories = list(
        await session.scalars(
            select(func.distinct(Product.category_name))
            .where(Product.category_name.is_not(None))
            .order_by(Product.category_name)
        )
    )
    strategies = list(
        await session.scalars(
            select(func.distinct(PublicationQueue.source_strategy))
            .where(PublicationQueue.source_strategy.is_not(None))
            .order_by(PublicationQueue.source_strategy)
        )
    )
    return {"categories": categories, "strategies": strategies}


@router.post("/queue/bulk")
async def bulk_queue_action(
    payload: QueueBulkRequest,
    _: AdminUser = Depends(csrf_protected),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.action not in {"discard", "revalidate", "regenerate", "retry", "publish-now"}:
        raise HTTPException(status_code=422, detail="Invalid bulk action")
    rows = await lock_queue_items(session, payload.ids)
    accepted: list[str] = []
    for item in rows:
        if not await can_mutate(session, item, reactivate=payload.action != "discard"):
            continue
        if payload.action == "discard":
            item.status = QueueStatus.skipped
        elif payload.action == "publish-now":
            if not item.creative_id or not item.link_id:
                continue
            item.status = QueueStatus.ready
        else:
            item.status = QueueStatus.selected
            item.last_error = None
        accepted.append(str(item.id))
    session.add(
        AuditEvent(
            actor="admin",
            action="QUEUE_BULK_ACTION",
            entity_type="queue",
            details={"action": payload.action, "items": len(accepted)},
        )
    )
    await session.commit()
    if payload.action in {"revalidate", "regenerate", "retry"} and accepted:
        celery_app.send_task("backend.app.tasks.prepare_selected_offers", queue="browser")
    elif payload.action == "publish-now":
        for item_id in accepted:
            celery_app.send_task(
                "backend.app.tasks.publish_ready_offer", args=[item_id], queue="browser"
            )
    return {"accepted": accepted, "count": len(accepted)}


@router.get("/settings")
async def get_app_settings(
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    result = await session.scalars(select(AppSetting).order_by(AppSetting.key))
    return {item.key: item.value for item in result}


@router.get("/coupons")
async def coupons(
    page: int = Query(1, ge=1), page_size: int = Query(500, ge=1, le=500),
    response_format: str = Query("legacy", alias="format", pattern="^(legacy|page)$"),
    source: str | None = None,
    status: str | None = None,
    shareable: bool | None = None,
    search: str | None = None,
    _: AdminUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> list[dict] | dict:
    query = select(Coupon)
    if source:
        query = query.where(Coupon.source_kind == source)
    if status:
        query = query.where(Coupon.lifecycle_status == status.upper())
    if shareable is not None:
        query = query.where(Coupon.shareable.is_(shareable))
    if search:
        query = query.where(
            or_(Coupon.title.ilike(f"%{search}%"), Coupon.code.ilike(f"%{search}%"))
        )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    result = list(await session.scalars(query.order_by(Coupon.updated_at.desc(), Coupon.id).offset((page-1)*page_size).limit(page_size)))
    counts = dict(
        (
            await session.execute(
                select(
                    CouponProductApplicability.coupon_id, func.count(CouponProductApplicability.id)
                )
                .where(CouponProductApplicability.status == CouponApplicabilityStatus.applicable, CouponProductApplicability.coupon_id.in_([c.id for c in result]))
                .group_by(CouponProductApplicability.coupon_id)
            )
        ).all()
    )
    items = [
        {
            "id": str(item.id),
            "title": item.title,
            "code": item.code,
            "discount_percent": item.discount_percent,
            "discount_amount": item.discount_amount,
            "expires_at": item.expires_at,
            "eligibility_confirmed": item.eligibility_confirmed,
            "minimum_purchase": item.minimum_purchase,
            "maximum_discount": item.maximum_discount,
            "availability_text": item.availability_text,
            "restrictions": item.restrictions,
            "applicable_products": counts.get(item.id, 0),
            "eligible_products_count": (item.raw_metadata or {}).get("eligible_products_count", 0),
            "scope": (item.raw_metadata or {}).get("scope"),
            "remaining_budget": (item.raw_metadata or {}).get("remaining_budget"),
            "official_coupon_id": (item.raw_metadata or {}).get("official_coupon_id"),
            "last_verified_at": item.last_verified_at,
            "rules_version": item.rules_version,
            "source_kind": item.source_kind,
            "shareable": item.shareable,
            "lifecycle_status": item.lifecycle_status,
            "activation_status": item.activation_status,
            "remaining_budget_value": item.remaining_budget,
        }
        for item in result
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size} if response_format == "page" else items


@router.get("/coupons/{coupon_id}/products")
async def coupon_products(
    coupon_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=100),
    _: AdminUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    coupon = await session.get(Coupon, coupon_id)
    if not coupon:
        raise HTTPException(status_code=404, detail="Cupom não encontrado")
    base = (
        select(CouponProductApplicability, Product)
        .join(Product, Product.id == CouponProductApplicability.product_id)
        .where(CouponProductApplicability.coupon_id == coupon_id)
    )
    total = (
        await session.scalar(
            select(func.count(CouponProductApplicability.id)).where(
                CouponProductApplicability.coupon_id == coupon_id
            )
        )
        or 0
    )
    rows = (
        await session.execute(
            base.order_by(Product.title).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    product_ids = [product.id for _, product in rows]
    snapshots = list(
        await session.scalars(
            select(ProductSnapshot)
            .where(ProductSnapshot.product_id.in_(product_ids))
            .order_by(ProductSnapshot.product_id, desc(ProductSnapshot.observed_at))
        )
    )
    latest: dict = {}
    for snapshot in snapshots:
        latest.setdefault(snapshot.product_id, snapshot)
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "items": [
            {
                "id": str(product.id),
                "item_id": product.item_id,
                "title": product.title,
                "image_url": product.image_url,
                "product_url": product.product_url,
                "price": latest.get(product.id).price if latest.get(product.id) else None,
                "status": applicability.status,
                "reason": applicability.reason,
                "checked_at": applicability.checked_at,
            }
            for applicability, product in rows
        ],
    }


@router.get("/publications")
async def publications(
    page: int = Query(1, ge=1), page_size: int = Query(200, ge=1, le=500),
    response_format: str = Query("legacy", alias="format", pattern="^(legacy|page)$"),
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[dict] | dict:
    total = await session.scalar(select(func.count()).select_from(Publication))
    rows = await session.execute(
        select(Publication, PublicationQueue, Product)
        .join(PublicationQueue, Publication.queue_id == PublicationQueue.id)
        .join(Product, PublicationQueue.product_id == Product.id)
        .order_by(Publication.created_at.desc(), Publication.id)
        .offset((page-1)*page_size).limit(page_size)
    )
    items = [
        {
            "id": str(item.id),
            "queue_id": str(item.queue_id),
            "product_title": product.title,
            "destination": item.destination,
            "telegram_message_id": item.telegram_message_id,
            "mode": item.mode,
            "published_at": item.published_at,
            "payload": item.payload,
            "publication_kind": item.publication_kind,
            "public_message_url": item.public_message_url,
            "delivery_status": item.delivery_status,
            "historical_duplicate": item.historical_duplicate,
        }
        for item, _queue, product in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size} if response_format == "page" else items


@router.get("/runs")
async def runs(
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    result = await session.scalars(select(TaskRun).order_by(desc(TaskRun.created_at)).limit(200))
    return [
        {
            "id": str(item.id),
            "task_name": item.task_name,
            "correlation_id": item.correlation_id,
            "status": item.status,
            "started_at": item.started_at,
            "finished_at": item.finished_at,
            "duration_ms": item.duration_ms,
            "result": item.result,
            "error": item.error,
        }
        for item in result
    ]


@router.put("/settings/batch")
async def update_settings_batch(
    payload: SettingsBatchUpdate,
    _: AdminUser = Depends(csrf_protected),
    session: AsyncSession = Depends(get_session),
) -> dict:
    revision = await session.get(SettingsRevision, 1, with_for_update=True)
    if payload.expected_revision is not None and payload.expected_revision != revision.revision:
        raise HTTPException(409, "As configurações mudaram. Recarregue antes de salvar.")
    rows = list(await session.scalars(
        select(AppSetting).where(AppSetting.key.in_(payload.values)).with_for_update()
    ))
    existing = {item.key: item for item in rows}
    changed: dict[str, int] = {}
    for key, value in payload.values.items():
        setting = existing.get(key)
        if not setting:
            setting = AppSetting(key=key, value=value, version=1)
            session.add(setting)
            changed[key] = 1
        elif setting.value != value:
            setting.value = value
            setting.version = int(setting.version or 0) + 1
            changed[key] = setting.version
    session.add(AuditEvent(
        actor="admin", action="SETTINGS_BATCH_UPDATED", entity_type="app_setting",
        entity_id=",".join(sorted(changed)), details={"versions": changed},
    ))
    if not changed:
        await session.rollback()
        return {"changed": {}, "reprocess_task_id": None, "detail": "Nenhuma alteração"}
    revision.revision += 1
    all_settings = list(await session.scalars(select(AppSetting)))
    job = ReprocessingJob(revision=revision.revision, changes=changed,
        settings_values={item.key: item.value for item in all_settings})
    session.add(job)
    await session.flush()
    result = {"revision": revision.revision, "reprocess_task_id": str(job.id)}
    await session.commit()
    return {
        "changed": changed, **result,
        "detail": "Configurações salvas; reprocessamento persistido para a próxima execução",
    }


@router.post("/settings/impact-preview")
async def settings_impact_preview(
    payload: SettingsBatchUpdate,
    _: AdminUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    return await preview_settings_impact(session, payload.values)


@router.get("/operations/reprocessing/{job_id}")
async def reprocessing_status(job_id: str, _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    try:
        job = await session.get(ReprocessingJob, uuid.UUID(job_id))
    except ValueError:
        raise HTTPException(400, "Identificador inválido") from None
    if job:
        return {"id": job_id, "state": job.state, "revision": job.revision, "result": job.result, "error": job.error}
    result = celery_app.AsyncResult(job_id)
    info = result.info if isinstance(result.info, dict) else None
    return {"id": job_id, "state": result.state, "result": info}


@router.get("/settings/revision")
async def settings_revision(_: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)) -> dict:
    revision = await session.get(SettingsRevision, 1)
    return {"revision": revision.revision}


@router.put("/settings/{key}")
async def update_setting(
    key: str,
    payload: SettingUpdate,
    _: AdminUser = Depends(csrf_protected),
    session: AsyncSession = Depends(get_session),
) -> dict:
    validated = SettingsBatchUpdate(values={key: payload.value})
    response = await update_settings_batch(validated, _, session)
    version = response["changed"].get(key)
    return {
        "key": key, "value": payload.value, "version": version,
        "reprocess_task_id": response["reprocess_task_id"],
    }


@router.post("/actions/discover", response_model=ActionResponse)
async def trigger_discovery(_: AdminUser = Depends(csrf_protected)) -> ActionResponse:
    task = celery_app.send_task("backend.app.tasks.discover_offers", queue="browser")
    return ActionResponse(accepted=True, task_id=task.id, detail="Discovery queued")


@router.post("/discovery/run", response_model=ActionResponse)
async def run_discovery(
    payload: DiscoveryRequest, _: AdminUser = Depends(csrf_protected)
) -> ActionResponse:
    allowed = {"relevant", "best_sellers", "extra_commission", "auto"} | {
        f"category:{name}" for name in MercadoLivreBrowserService.CATEGORIES
    }
    if payload.strategy and payload.strategy not in allowed:
        raise HTTPException(status_code=422, detail="Unknown discovery strategy")
    task = celery_app.send_task(
        "backend.app.tasks.discover_offers",
        args=[payload.strategy or "auto", True],
        queue="browser",
    )
    return ActionResponse(accepted=True, task_id=task.id, detail="Discovery queued")


@router.get("/discovery/strategies")
async def discovery_strategies(_: AdminUser = Depends(current_user)) -> dict:
    return {
        "default": "relevant",
        "strategies": ["relevant", "best_sellers", "extra_commission"],
        "categories": list(MercadoLivreBrowserService.CATEGORIES),
    }


@router.get("/operations/funnel")
async def operations_funnel(
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    since = datetime.now(UTC) - timedelta(days=1)
    runs = list(
        await session.scalars(
            select(TaskRun).where(
                TaskRun.task_name == "discover_offers", TaskRun.started_at >= since
            )
        )
    )
    queue_counts = await session.execute(
        select(PublicationQueue.status, func.count(PublicationQueue.id))
        .where(PublicationQueue.created_at >= since)
        .group_by(PublicationQueue.status)
    )
    published = (
        await session.scalar(
            select(func.count(Publication.id)).where(Publication.published_at >= since)
        )
        or 0
    )
    return {
        "cards_seen": sum(int((run.result or {}).get("cards_seen", 0)) for run in runs),
        "unique_discovered": sum(int((run.result or {}).get("unique_products", 0)) for run in runs),
        "new_products": sum(int((run.result or {}).get("new_products", 0)) for run in runs),
        "updated_products": sum(int((run.result or {}).get("updated_products", 0)) for run in runs),
        "unchanged_products": sum(
            int((run.result or {}).get("unchanged_products", 0)) for run in runs
        ),
        "queue": {status.value: count for status, count in queue_counts.all()},
        "published": published,
    }


@router.get("/operations/category-coverage")
async def category_coverage(
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    rows = await session.execute(
        select(Product.category_name, func.count(Product.id), func.max(Product.last_seen_at))
        .where(Product.active.is_(True))
        .group_by(Product.category_name)
        .order_by(desc(func.count(Product.id)))
    )
    return [
        {"category": category or "Não identificada", "products": count, "last_seen_at": last_seen}
        for category, count, last_seen in rows
    ]


@router.get("/operations/trends")
async def operation_trends(
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    recent = list(
        await session.scalars(
            select(ProductSnapshot).order_by(desc(ProductSnapshot.observed_at)).limit(1000)
        )
    )
    seen: set = set()
    latest: list[ProductSnapshot] = []
    for snapshot in recent:
        if snapshot.product_id in seen:
            continue
        seen.add(snapshot.product_id)
        latest.append(snapshot)
    products = list(
        await session.scalars(
            select(Product).where(Product.id.in_([item.product_id for item in latest]))
        )
    )
    product_map = {item.id: item for item in products}
    trends: list[dict] = []
    for snapshot in latest:
        metrics = snapshot.metrics or {}
        drop = metrics.get("drop_from_last_percent")
        if drop is not None and float(drop) > 0:
            product = product_map.get(snapshot.product_id)
            trends.append(
                {
                    "product_id": str(snapshot.product_id),
                    "title": product.title if product else "",
                    "category": product.category_name if product else None,
                    "price_drop_percent": drop,
                    "price_confidence": metrics.get("price_confidence"),
                }
            )
    return sorted(trends, key=lambda item: float(item["price_drop_percent"]), reverse=True)[:50]


@router.get("/operations/health")
async def operations_health(
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    try:
        redis_ok = bool(await redis.ping())
        bot_ok = bool(await redis.get("bot:heartbeat"))
        worker_keys = [value for value in await redis.mget("worker:role:general", "worker:role:browser") if value]
        scheduler_ok = bool(await redis.get("scheduler:loop_heartbeat"))
    except Exception:
        redis_ok = False
        bot_ok = False
        worker_keys = []
        scheduler_ok = False
    finally:
        await redis.aclose()
    browser = await session.scalar(
        select(BrowserSession).order_by(desc(BrowserSession.updated_at)).limit(1)
    )
    last_run = await session.scalar(
        select(TaskRun)
        .where(TaskRun.task_name == "discover_offers")
        .order_by(desc(TaskRun.finished_at))
        .limit(1)
    )
    return {
        "database": "UP",
        "redis": "UP" if redis_ok else "DOWN",
        "browser": browser.state.value if browser else "AUTH_REQUIRED",
        "crawler": "UP"
        if last_run
        and last_run.finished_at
        and last_run.finished_at > datetime.now(UTC) - timedelta(minutes=30)
        else "STALE",
        "groq": "CONFIGURED" if settings.groq_api_key else "DISABLED",
        "telegram": "CONFIGURED" if settings.telegram_bot_token else "DISABLED",
        "private_bot": "UP" if bot_ok else "DOWN",
        "workers": "UP" if len(worker_keys) >= 2 else "DEGRADED",
        "scheduler": "UP" if scheduler_ok else "DOWN",
    }


@router.post("/coupons/sync", response_model=ActionResponse)
async def sync_coupons_endpoint(_: AdminUser = Depends(csrf_protected)) -> ActionResponse:
    task = celery_app.send_task("backend.app.tasks.sync_coupons", queue="browser")
    return ActionResponse(accepted=True, task_id=task.id, detail="Coupon sync queued")


@router.post("/coupons/{coupon_id}/revalidate", response_model=ActionResponse)
async def revalidate_coupon(
    coupon_id: uuid.UUID,
    _: AdminUser = Depends(csrf_protected),
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    if not await session.get(Coupon, coupon_id):
        raise HTTPException(status_code=404, detail="Coupon not found")
    task = celery_app.send_task("backend.app.tasks.sync_coupons", queue="browser")
    return ActionResponse(accepted=True, task_id=task.id, detail="Coupon revalidation queued")


@router.post("/coupons/{coupon_id}/publish", response_model=ActionResponse)
async def publish_coupon_endpoint(
    coupon_id: uuid.UUID,
    _: AdminUser = Depends(csrf_protected),
    session: AsyncSession = Depends(get_session),
) -> ActionResponse:
    if not await session.get(Coupon, coupon_id):
        raise HTTPException(status_code=404, detail="Coupon not found")
    task = celery_app.send_task(
        "backend.app.tasks.publish_coupon", args=[str(coupon_id)], queue="default"
    )
    return ActionResponse(accepted=True, task_id=task.id, detail="Coupon publication queued")


@router.post("/actions/sync-coupons", response_model=ActionResponse)
async def trigger_coupon_sync(_: AdminUser = Depends(csrf_protected)) -> ActionResponse:
    task = celery_app.send_task("backend.app.tasks.sync_coupons", queue="browser")
    return ActionResponse(accepted=True, task_id=task.id, detail="Coupon sync queued")


@router.post("/actions/validate-public-channel")
async def validate_public_channel(_: AdminUser = Depends(csrf_protected)) -> dict:
    return await TelegramService().validate_public_channel()


@router.post("/actions/telegram-smoke", response_model=ActionResponse)
async def trigger_telegram_smoke(_: AdminUser = Depends(csrf_protected)) -> ActionResponse:
    task = celery_app.send_task("backend.app.tasks.telegram_private_smoke", queue="default")
    return ActionResponse(accepted=True, task_id=task.id, detail="Private smoke queued")


@router.post("/actions/private-offer-preview", response_model=ActionResponse)
async def trigger_private_offer_preview(
    _: AdminUser = Depends(csrf_protected),
) -> ActionResponse:
    task = celery_app.send_task("backend.app.tasks.send_private_offer_preview", queue="browser")
    return ActionResponse(accepted=True, task_id=task.id, detail="Private offer queued")


@router.get("/bot/status")
async def bot_status(
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    stored = await session.scalar(select(AppSetting).where(AppSetting.key == "bot"))
    try:
        live = await TelegramService().bot_status()
        return {"state": "UP", "config": stored.value if stored else {}, **live}
    except Exception as exc:
        return {
            "state": "DOWN",
            "config": stored.value if stored else {},
            "error": type(exc).__name__,
        }


@router.post("/bot/promo", response_model=ActionResponse)
async def trigger_bot_promo(_: AdminUser = Depends(csrf_protected)) -> ActionResponse:
    task = celery_app.send_task(
        "backend.app.tasks.dispatch_bot_promo", args=[True], queue="default"
    )
    return ActionResponse(accepted=True, task_id=task.id, detail="Bot promotion queued")


@router.get("/operations/duplicates")
async def duplicate_report(
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    published_rows = await session.execute(
        select(
            Product.id,
            Product.title,
            func.count(Publication.id).label("publication_count"),
        )
        .join(PublicationQueue, PublicationQueue.product_id == Product.id)
        .join(Publication, Publication.queue_id == PublicationQueue.id)
        .where(Publication.delivery_status == "SENT")
        .group_by(Product.id, Product.title)
        .having(func.count(Publication.id) > 1)
        .order_by(desc(func.count(Publication.id)))
    )
    neutralized_rows = await session.execute(
        select(Product.id, Product.title, func.count(PublicationQueue.id))
        .join(PublicationQueue, PublicationQueue.product_id == Product.id)
        .where(PublicationQueue.last_error.like("SKIPPED_DUPLICATE%"))
        .group_by(Product.id, Product.title)
        .order_by(desc(func.count(PublicationQueue.id)))
    )
    historical = [
        {"product_id": str(product_id), "title": title, "count": count}
        for product_id, title, count in published_rows
    ]
    neutralized = [
        {"product_id": str(product_id), "title": title, "count": count}
        for product_id, title, count in neutralized_rows
    ]
    active_duplicate_groups = (
        await session.scalar(
            select(func.count()).select_from(
                select(PublicationQueue.product_id, PublicationQueue.publication_kind)
                .where(
                    PublicationQueue.status.in_(
                        [
                            QueueStatus.discovered,
                            QueueStatus.selected,
                            QueueStatus.link_pending,
                            QueueStatus.creative_pending,
                            QueueStatus.ready,
                            QueueStatus.dry_run,
                            QueueStatus.retry,
                            QueueStatus.publishing,
                        ]
                    )
                )
                .group_by(PublicationQueue.product_id, PublicationQueue.publication_kind)
                .having(func.count(PublicationQueue.id) > 1)
                .subquery()
            )
        )
        or 0
    )
    return {
        "historical": historical,
        "historical_products": len(historical),
        "historical_extra_posts": sum(item["count"] - 1 for item in historical),
        "neutralized": neutralized,
        "neutralized_queue_items": sum(item["count"] for item in neutralized),
        "active_duplicate_groups": active_duplicate_groups,
    }


@router.get("/editorial/bad-offers")
async def bad_offer_history(
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[dict]:
    rows = await session.execute(
        select(PublicationQueue, Publication, Product)
        .join(Product, Product.id == PublicationQueue.product_id)
        .outerjoin(Publication, Publication.queue_id == PublicationQueue.id)
        .where(PublicationQueue.publication_kind == PublicationKind.bad_offer.value)
        .order_by(desc(PublicationQueue.created_at))
        .limit(100)
    )
    return [
        {
            "id": str(queue_item.id),
            "product_id": str(product.id),
            "product_title": product.title,
            "image_url": product.image_url,
            "status": queue_item.status.value,
            "created_at": queue_item.created_at,
            "published_at": publication.published_at if publication else None,
            "public_message_url": publication.public_message_url if publication else None,
            "delivery_status": publication.delivery_status if publication else None,
            "payload": queue_item.editorial_payload,
        }
        for queue_item, publication, product in rows
    ]


@router.post("/editorial/bad-offers/run", response_model=ActionResponse)
async def run_bad_offer(_: AdminUser = Depends(csrf_protected)) -> ActionResponse:
    task = celery_app.send_task(
        "backend.app.tasks.dispatch_bad_offer", args=[True, False], queue="browser"
    )
    return ActionResponse(accepted=True, task_id=task.id, detail="Editorial analysis queued")


@router.post("/editorial/bad-offers/{queue_id}/preview")
async def bad_offer_preview(
    queue_id: uuid.UUID,
    _: AdminUser = Depends(csrf_protected),
    session: AsyncSession = Depends(get_session),
) -> dict:
    item = await session.get(PublicationQueue, queue_id)
    if not item or item.publication_kind != PublicationKind.bad_offer.value:
        raise HTTPException(status_code=404, detail="Editorial analysis not found")
    return item.editorial_payload


@router.get("/editorial/bad-offers/status")
async def bad_offer_status(
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    states = await session.execute(
        select(EditorialCandidate.state, func.count(EditorialCandidate.id))
        .group_by(EditorialCandidate.state)
    )
    next_slot = await session.scalar(
        select(EditorialSlot).where(EditorialSlot.state == "PENDING")
        .order_by(EditorialSlot.next_attempt_at).limit(1)
    )
    recent_slots = list(await session.scalars(
        select(EditorialSlot).order_by(desc(EditorialSlot.scheduled_for)).limit(10)
    ))
    next_attempt_at = next_slot.next_attempt_at if next_slot else None
    if next_attempt_at is None:
        policy_row = await session.scalar(
            select(AppSetting).where(AppSetting.key == "bad_offers")
        )
        policy = policy_row.value if policy_row else {}
        local_now = datetime.now(ZoneInfo("America/Sao_Paulo"))
        future: list[datetime] = []
        for day_offset in (0, 1):
            day = local_now + timedelta(days=day_offset)
            for value in policy.get("times", ["12:30", "19:30"]):
                try:
                    hour, minute = (int(part) for part in str(value).split(":", 1))
                except (TypeError, ValueError):
                    continue
                candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate > local_now:
                    future.append(candidate.astimezone(UTC))
        next_attempt_at = min(future, default=None)
    return {
        "counts": {state: count for state, count in states.all()},
        "next_attempt_at": next_attempt_at,
        "slots": [{
            "id": str(slot.id), "slot_key": slot.slot_key, "state": slot.state,
            "scheduled_for": slot.scheduled_for, "next_attempt_at": slot.next_attempt_at,
            "outcome_reason": slot.outcome_reason, "attempts": slot.attempts,
        } for slot in recent_slots],
    }


@router.get("/editorial/bad-offers/candidates")
async def bad_offer_candidates(
    page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100),
    state: str | None = None,
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session),
) -> dict:
    query = select(EditorialCandidate, Product).join(
        Product, Product.id == EditorialCandidate.product_id
    )
    if state:
        query = query.where(EditorialCandidate.state == state.upper())
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = await session.execute(
        query.order_by(desc(EditorialCandidate.last_evaluated_at))
        .offset((page - 1) * page_size).limit(page_size)
    )
    return {"page": page, "page_size": page_size, "total": total, "items": [{
        "id": str(candidate.id), "product_id": str(product.id), "title": product.title,
        "image_url": product.image_url, "state": candidate.state,
        "score": candidate.score, "price_confidence": candidate.price_confidence,
        "observation_count": candidate.observation_count,
        "history_hours": candidate.history_hours, "reasons": candidate.reasons,
        "last_evaluated_at": candidate.last_evaluated_at,
        "next_revalidation_at": candidate.next_revalidation_at,
    } for candidate, product in rows]}


@router.get("/operations/activity")
async def operations_activity(
    _: AdminUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> dict:
    failed_queue = list(await session.execute(
        select(PublicationQueue, Product).join(Product, Product.id == PublicationQueue.product_id)
        .where(PublicationQueue.status == QueueStatus.failed)
        .order_by(desc(PublicationQueue.updated_at)).limit(12)
    ))
    failed_runs = list(await session.scalars(
        select(TaskRun).where(TaskRun.status != "SUCCEEDED")
        .order_by(desc(TaskRun.started_at)).limit(12)
    ))
    reprocessing = await session.scalar(
        select(TaskRun).where(TaskRun.task_name == "reprocess_settings")
        .order_by(desc(TaskRun.started_at)).limit(1)
    )
    return {
        "alerts": [{
            "kind": "QUEUE_FAILURE", "title": product.title,
            "detail": item.last_error, "at": item.updated_at, "entity_id": str(item.id),
        } for item, product in failed_queue] + [{
            "kind": "TASK_FAILURE", "title": run.task_name,
            "detail": run.error, "at": run.finished_at or run.started_at,
            "entity_id": str(run.id),
        } for run in failed_runs],
        "last_reprocessing": ({
            "id": str(reprocessing.id), "status": reprocessing.status,
            "result": reprocessing.result, "at": reprocessing.finished_at,
        } if reprocessing else None),
    }


@router.post("/channel/onboarding/publish", response_model=ActionResponse)
async def publish_channel_onboarding(
    _: AdminUser = Depends(csrf_protected),
) -> ActionResponse:
    task = celery_app.send_task("backend.app.tasks.publish_channel_manifest", queue="default")
    return ActionResponse(accepted=True, task_id=task.id, detail="Channel manifesto queued")


@router.post("/queue/{queue_id}/discard")
async def discard_queue_item(
    queue_id: uuid.UUID,
    _: AdminUser = Depends(csrf_protected),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = await lock_queue_items(session, [queue_id])
    item = rows[0] if rows else None
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    if not await can_mutate(session, item, reactivate=False):
        raise HTTPException(status_code=409, detail="Item enviado, em publicação, com entrega reservada ou substituído por outra fila ativa")
    item.status = QueueStatus.skipped
    await session.commit()
    return {"status": item.status.value}


@router.get("/queue/{queue_id}/preview")
async def queue_preview(
    queue_id: uuid.UUID,
    _: AdminUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    item = await session.get(PublicationQueue, queue_id)
    if not item or not item.creative_id:
        raise HTTPException(status_code=404, detail="Creative preview is not ready")
    creative = await session.get(Creative, item.creative_id)
    product = await session.get(Product, item.product_id)
    return {
        "caption_html": creative.copy_text,
        "image_url": creative.image_source_url or product.image_url,
        "generator": creative.generator,
        "template_version": creative.template_version,
    }


@router.post("/queue/{queue_id}/{action}")
async def queue_action(
    queue_id: uuid.UUID,
    action: str,
    _: AdminUser = Depends(csrf_protected),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if action not in {"regenerate", "retry", "publish-now", "revalidate"}:
        raise HTTPException(status_code=404, detail="Unknown queue action")
    rows = await lock_queue_items(session, [queue_id])
    item = rows[0] if rows else None
    if not item:
        raise HTTPException(status_code=404, detail="Queue item not found")
    if not await can_mutate(session, item, reactivate=True):
        raise HTTPException(status_code=409, detail="Item enviado, em publicação, com entrega reservada ou substituído por outra fila ativa")
    if action in {"regenerate", "retry", "revalidate"}:
        item.status = QueueStatus.selected
        item.last_error = None
        await session.commit()
        task = celery_app.send_task("backend.app.tasks.prepare_selected_offers", queue="browser")
    else:
        if not item.creative_id or not item.link_id:
            raise HTTPException(status_code=409, detail="Offer is not ready to publish")
        item.status = QueueStatus.ready
        await session.commit()
        task = celery_app.send_task(
            "backend.app.tasks.publish_ready_offer", args=[str(item.id)], queue="browser"
        )
    return {"status": item.status.value, "task_id": task.id}
