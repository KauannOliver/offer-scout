"""Shared, deterministic coupon and freshness policy for previews and workers."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import desc, select

from backend.app.models import (
    AppSetting,
    Coupon,
    CouponApplicabilityStatus,
    CouponProductApplicability,
    Publication,
    PublicationQueue,
)


def repetition_allowed(publication, price, coupon_percent, policy, now=None):
    now = now or datetime.now(UTC)
    if not publication or publication.published_at <= now - timedelta(hours=int(policy.get("hours", 72))):
        return True
    facts = (publication.payload or {}).get("facts") or {}
    old = Decimal(str(facts.get("current_price") or 0))
    if old > 0 and price and (old - price) / old * 100 >= Decimal(str(policy.get("price_drop_percent", 10))):
        return True
    old_coupon = Decimal(str((publication.payload or {}).get("effective_coupon_percent")
        or facts.get("coupon_discount_percent") or 0))
    return coupon_percent is not None and coupon_percent - old_coupon >= Decimal(str(policy.get("coupon_improvement_points", 5)))


async def repetition_context(session, ids):
    if not ids:
        return {}, {}
    rows = await session.execute(select(PublicationQueue.product_id, Publication).join(
        PublicationQueue, Publication.queue_id == PublicationQueue.id).where(
        PublicationQueue.product_id.in_(ids), Publication.publication_kind == "GOOD_OFFER",
        Publication.delivery_status == "SENT", Publication.published_at.is_not(None)
    ).distinct(PublicationQueue.product_id).order_by(PublicationQueue.product_id,
        desc(Publication.published_at), desc(Publication.id)))
    stored = await session.scalar(select(AppSetting).where(AppSetting.key == "deduplication"))
    return {pid: publication for pid, publication in rows}, stored.value if stored else {}


def evaluate_score(engine, product, snapshot, verified_at, coupon_percent=None):
    metrics = snapshot.metrics or {}
    drop = metrics.get("drop_from_last_percent")
    return engine.score(product, coupon_percent=coupon_percent,
        monitored_price_drop_percent=Decimal(str(drop)) if drop is not None else None,
        freshness=freshness_factor(verified_at),
        price_confidence=float(metrics.get("price_confidence", .6)))


def policy_fingerprint(engine):
    import hashlib
    import json
    from dataclasses import asdict
    return hashlib.sha256(json.dumps(asdict(engine.config), sort_keys=True, default=str).encode()).hexdigest()


def effective_coupon_percent(coupon, price: Decimal | None, now: datetime) -> Decimal | None:
    if (price is None or price <= 0 or not coupon.shareable or not coupon.code
        or not coupon.eligibility_confirmed or coupon.lifecycle_status != "ACTIVE"
        or (coupon.starts_at and coupon.starts_at > now)
        or (coupon.expires_at and coupon.expires_at <= now)
        or not coupon.last_verified_at or coupon.last_verified_at < now - timedelta(minutes=30)
        or (coupon.remaining_budget is not None and coupon.remaining_budget <= 0)
        or (coupon.minimum_purchase is not None and price < coupon.minimum_purchase)):
        return None
    saving = price * coupon.discount_percent / 100 if coupon.discount_percent is not None else coupon.discount_amount
    if saving is None or saving <= 0:
        return None
    if coupon.maximum_discount is not None:
        saving = min(saving, coupon.maximum_discount)
    return min(saving, price) / price * 100


async def confirmed_discounts(session, prices: dict) -> dict:
    if not prices:
        return {}
    now = datetime.now(UTC)
    rows = await session.execute(select(CouponProductApplicability, Coupon).join(
        Coupon, Coupon.id == CouponProductApplicability.coupon_id
    ).where(CouponProductApplicability.product_id.in_(prices),
        CouponProductApplicability.status == CouponApplicabilityStatus.applicable,
        CouponProductApplicability.checked_at >= now - timedelta(minutes=30)))
    result = {}
    for applicability, coupon in rows:
        if not applicability.evidence:
            continue
        value = effective_coupon_percent(coupon, prices[applicability.product_id], now)
        if value is not None:
            result[applicability.product_id] = max(result.get(applicability.product_id, Decimal(0)), value)
    return result


def freshness_factor(verified_at: datetime | None, now: datetime | None = None) -> float:
    if verified_at is None:
        return 0
    age = max(0, ((now or datetime.now(UTC)) - verified_at).total_seconds())
    # Hour buckets prevent a new score for every millisecond of otherwise identical facts.
    return max(0, 1 - int(age // 3600) / 24)
