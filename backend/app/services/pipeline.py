import hashlib
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from statistics import median

from sqlalchemy import desc, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.domain.types import NormalizedProduct
from backend.app.models import (
    AppSetting,
    OfferScore,
    Product,
    ProductCurrentState,
    ProductObservation,
    ProductSnapshot,
    PublicationKind,
    PublicationQueue,
    QueueStatus,
)
from backend.app.services.offer_policy import (
    confirmed_discounts,
    evaluate_score,
    policy_fingerprint,
    repetition_allowed,
    repetition_context,
)
from backend.app.services.scoring import OfferScoringEngine, ScoreConfig


class OfferPipeline:
    def __init__(self, scoring: OfferScoringEngine | None = None):
        self.scoring = scoring or OfferScoringEngine()
        self.metrics: dict = {}

    async def ingest(
        self,
        session: AsyncSession,
        offers: list[NormalizedProduct],
        *,
        force_rescore: bool = False,
        discovery_batch_id: uuid.UUID | None = None,
        source_strategy: str | None = None,
        enqueue: bool = True,
    ) -> int:
        if not offers:
            self.metrics = {"new_products": 0, "updated_products": 0, "unchanged_products": 0}
            return 0
        keys = {(offer.marketplace, offer.item_id) for offer in offers}
        existing_result = await session.scalars(
            select(Product).where(tuple_(Product.marketplace, Product.item_id).in_(keys)).order_by(Product.id).with_for_update()
        )
        products = {(item.marketplace, item.item_id): item for item in existing_result}
        new_products = 0
        for offer in offers:
            product = products.get((offer.marketplace, offer.item_id))
            if not product:
                product = Product(
                    marketplace=offer.marketplace,
                    source=offer.source,
                    item_id=offer.item_id,
                    product_id=offer.product_id,
                    user_product_id=offer.user_product_id,
                    variation_id=offer.variation_id,
                    title=offer.title,
                    product_url=offer.product_url,
                    image_url=offer.image_url,
                    raw_metadata=offer.raw_metadata,
                )
                session.add(product)
                await session.flush()
                products[(offer.marketplace, offer.item_id)] = product
                new_products += 1
            else:
                product.title = offer.title
                product.product_url = offer.product_url
                product.image_url = offer.image_url or product.image_url
                product.raw_metadata = offer.raw_metadata
            strategy = str(offer.raw_metadata.get("discovery_strategy") or "relevant")
            strategies = list(product.discovery_strategies or [])
            if strategy not in strategies:
                strategies.append(strategy)
            product.discovery_strategies = strategies[-40:]
            if strategy.startswith("category:"):
                product.category_name = strategy.removeprefix("category:")
            product.brand = _metadata_value(offer.raw_metadata, "brand") or product.brand
            product.last_seen_at = offer.observed_at
            product.discovery_count = (product.discovery_count or 0) + 1

        product_ids = [product.id for product in products.values()]
        histories: dict = {}
        states = {s.product_id: s for s in await session.scalars(
            select(ProductCurrentState).where(ProductCurrentState.product_id.in_(product_ids))
        )}
        latest = {snapshot.product_id: snapshot for snapshot in await session.scalars(
            select(ProductSnapshot).where(ProductSnapshot.id.in_(
                [state.snapshot_id for state in states.values() if state.snapshot_id]))
        )}
        existing_scores = {score.id: score for score in await session.scalars(
            select(OfferScore).where(OfferScore.id.in_(
                [state.score_id for state in states.values() if state.score_id]))
        )}
        from backend.app.services.price_history import sampled_prices
        histories = await sampled_prices(session, product_ids)

        coupon_percent = await confirmed_discounts(session, {
            products[(offer.marketplace, offer.item_id)].id: offer.current_price for offer in offers
        })
        publications, deduplication = await repetition_context(session, product_ids)
        fingerprint = policy_fingerprint(self.scoring)

        updated_products = 0
        unchanged_products = 0
        for offer in offers:
            product = products[(offer.marketplace, offer.item_id)]
            previous = latest.get(product.id)
            changed = not previous or _snapshot_changed(previous, offer)
            state = states.get(product.id)
            if not state:
                state = ProductCurrentState(product_id=product.id)
                session.add(state)
                states[product.id] = state
            state.last_verified_at = offer.observed_at
            state.price = offer.current_price
            state.discount_percent = offer.discount_percent
            # Stable facts can become eligible again (freshness, coupons, repetition
            # window). Re-evaluate every observed product, reusing identical scores.
            if changed:
                metrics = _price_metrics(histories.get(product.id, []), offer)
                snapshot = ProductSnapshot(
                    product_id=product.id,
                    previous_price=offer.previous_price,
                    price=offer.current_price,
                    discount_percent=offer.discount_percent,
                    rating=offer.rating,
                    sold_text=offer.sold_text,
                    sold_count=offer.sold_count,
                    commission_percent=offer.commission_percent,
                    extra_commission=offer.extra_commission,
                    brand_commission=offer.brand_commission,
                    highlight=offer.highlight,
                    currency=offer.currency,
                    observed_at=offer.observed_at,
                    metrics=metrics,
                )
                session.add(snapshot)
                await session.flush()
            else:
                snapshot = previous
                metrics = snapshot.metrics or {}
            state.snapshot_id = snapshot.id
            session.add(ProductObservation(product_id=product.id, snapshot_id=snapshot.id,
                observed_at=offer.observed_at, source=source_strategy or offer.source))
            scored_offer = replace(offer, raw_metadata={**offer.raw_metadata, "category_name": product.category_name, "brand": product.brand})
            result = evaluate_score(self.scoring, scored_offer, snapshot, offer.observed_at, coupon_percent.get(product.id))
            score = existing_scores.get(state.score_id)
            if not score or (score.snapshot_id, score.total, score.components, score.eligible, score.reasons, score.policy_fingerprint) != (
                snapshot.id, result.total, result.components, result.eligible, result.reasons, fingerprint):
                score = OfferScore(
                    product_id=product.id,
                    snapshot_id=snapshot.id,
                    total=result.total,
                    classification=result.classification,
                    components=result.components,
                    eligible=result.eligible,
                    reasons=result.reasons,
                    policy_fingerprint=fingerprint,
                )
                session.add(score)
                await session.flush()
            state.score_id = score.id
            state.score_total = score.total
            state.score_eligible = score.eligible
            if (
                enqueue
                and result.eligible
                and repetition_allowed(publications.get(product.id), snapshot.price, coupon_percent.get(product.id), deduplication)
            ):
                key = hashlib.sha256(
                    f"{product.item_id}:{snapshot.price}:{datetime.now(UTC).date()}".encode()
                ).hexdigest()
                existing = await session.scalar(
                    select(PublicationQueue).where(PublicationQueue.idempotency_key == key)
                )
                active = await session.scalar(
                    select(PublicationQueue.id).where(
                        PublicationQueue.product_id == product.id,
                        PublicationQueue.publication_kind == PublicationKind.good_offer.value,
                        PublicationQueue.status.in_(ACTIVE_QUEUE_STATUSES),
                    )
                )
                if not existing and not active:
                    try:
                        async with session.begin_nested():
                            session.add(
                                PublicationQueue(
                                    product_id=product.id,
                                    score_id=score.id,
                                    status=QueueStatus.selected,
                                    idempotency_key=key,
                                    discovery_batch_id=discovery_batch_id,
                                    source_strategy=source_strategy or strategy,
                                    priority=int(score.total * 100),
                                    publication_kind=PublicationKind.good_offer.value,
                                )
                            )
                            await session.flush()
                    except IntegrityError:
                        pass
            updated_products += int(changed and previous is not None)
            unchanged_products += int(not changed)
        await session.commit()
        self.metrics = {
            "new_products": new_products,
            "updated_products": updated_products,
            "unchanged_products": unchanged_products,
        }
        return len(offers)

    async def _can_queue(
        self,
        session: AsyncSession,
        product: Product,
        snapshot: ProductSnapshot,
        current_coupon_percent: Decimal | None = None,
        *, current_price: Decimal | None = None,
    ) -> bool:
        publications, policy = await repetition_context(session, [product.id])
        return repetition_allowed(publications.get(product.id),
            current_price if current_price is not None else snapshot.price,
            current_coupon_percent, policy)


ACTIVE_QUEUE_STATUSES = (
    QueueStatus.discovered,
    QueueStatus.selected,
    QueueStatus.link_pending,
    QueueStatus.creative_pending,
    QueueStatus.ready,
    QueueStatus.publishing,
    QueueStatus.dry_run,
    QueueStatus.retry,
)


async def list_products(
    session: AsyncSession, *, offset: int = 0, limit: int = 100
) -> list[Product]:
    result = await session.scalars(
        select(Product)
        .options(selectinload(Product.snapshots))
        .order_by(desc(Product.updated_at))
        .offset(offset)
        .limit(limit)
    )
    return list(result.unique())


async def scoring_engine_from_settings(session: AsyncSession) -> OfferScoringEngine:
    stored = await session.scalar(select(AppSetting).where(AppSetting.key == "scoring"))
    discovery = await session.scalar(select(AppSetting).where(AppSetting.key == "discovery"))
    values = stored.value if stored else {}
    return scoring_engine_from_values(values, discovery.value if discovery else {})


def scoring_engine_from_values(values: dict, discovery: dict | None = None) -> OfferScoringEngine:
    config = ScoreConfig()
    config.excluded_categories = list((discovery or {}).get("excluded_categories", []))
    config.excluded_brands = list((discovery or {}).get("excluded_brands", []))
    if isinstance(values.get("weights"), dict):
        config.weights.update(
            {key: float(value) for key, value in values["weights"].items() if key in config.weights}
        )
    for field_name in (
        "minimum_score",
        "minimum_discount",
        "minimum_rating",
        "minimum_sold_count",
        "minimum_commission",
        "minimum_price",
        "maximum_price",
        "discount_anchor",
        "sold_anchor",
        "commission_anchor",
        "coupon_anchor",
    ):
        if values.get(field_name) is not None:
            setattr(config, field_name, values[field_name])
    return OfferScoringEngine(config)


SNAPSHOT_FIELDS = (
    "previous_price",
    "price",
    "discount_percent",
    "rating",
    "sold_count",
    "commission_percent",
    "extra_commission",
    "brand_commission",
    "highlight",
)


def _snapshot_changed(snapshot: ProductSnapshot, offer: NormalizedProduct) -> bool:
    mapping = {"price": "current_price"}
    for field_name in SNAPSHOT_FIELDS:
        current = getattr(snapshot, field_name)
        incoming = getattr(offer, mapping.get(field_name, field_name))
        if current != incoming:
            return True
    return False


def _price_metrics(history: list[Decimal], offer: NormalizedProduct) -> dict:
    current = offer.current_price
    median_30 = Decimal(str(median(history))) if history else None
    minimum_30 = min(history) if history else None
    last = history[0] if history else None
    drop = None
    if last and current and last > 0:
        drop = (last - current) / last * Decimal(100)
    confidence = Decimal("0.60")
    if current and median_30:
        if current <= median_30:
            confidence = Decimal("1.00")
        elif offer.discount_percent and offer.discount_percent >= 20:
            confidence = Decimal("0.25")
        else:
            confidence = Decimal("0.55")
    if offer.previous_price and current and offer.previous_price > 0 and offer.discount_percent:
        calculated = (offer.previous_price - current) / offer.previous_price * Decimal(100)
        if abs(calculated - offer.discount_percent) > 3:
            confidence = min(confidence, Decimal("0.35"))
    return {
        "median_30d": str(median_30) if median_30 is not None else None,
        "minimum_30d": str(minimum_30) if minimum_30 is not None else None,
        "drop_from_last_percent": str(drop.quantize(Decimal("0.01"))) if drop is not None else None,
        "price_confidence": float(confidence),
    }


def _metadata_value(metadata: dict, key: str) -> str | None:
    stack: list = [metadata]
    while stack:
        value = stack.pop()
        if isinstance(value, dict):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()[:160]
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)
    return None


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError):
        return None
