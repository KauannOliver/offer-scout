import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain.types import CouponData
from backend.app.models import (
    Coupon,
    CouponApplicabilityStatus,
    CouponProductApplicability,
    Product,
    ProductSnapshot,
)
from backend.app.services.offer_policy import effective_coupon_percent


async def sync_coupons(session: AsyncSession, provider, discovered: list[CouponData]) -> dict:
    now = datetime.now(UTC)
    synced = 0
    applicable = 0
    products = list(await session.scalars(select(Product).where(Product.active.is_(True))))
    product_by_external_id: dict[str, Product] = {}
    for product in products:
        product_by_external_id[product.item_id.upper()] = product
        if product.product_id:
            product_by_external_id[product.product_id.upper()] = product
    snapshots_result = await session.scalars(
        select(ProductSnapshot)
        .where(ProductSnapshot.product_id.in_([product.id for product in products]))
        .distinct(ProductSnapshot.product_id)
        .order_by(ProductSnapshot.product_id, desc(ProductSnapshot.observed_at))
    )
    latest_snapshots: dict = {}
    for snapshot in snapshots_result:
        latest_snapshots.setdefault(snapshot.product_id, snapshot)
    discovered_ids = [item.marketplace_coupon_id for item in discovered]
    existing_coupons = list(
        await session.scalars(
            select(Coupon).where(Coupon.marketplace_coupon_id.in_(discovered_ids))
        )
    )
    coupon_by_external_id = {item.marketplace_coupon_id: item for item in existing_coupons}
    normalized: list[tuple[CouponData, Coupon]] = []
    for data in discovered:
        coupon = coupon_by_external_id.get(data.marketplace_coupon_id)
        if not coupon:
            coupon = Coupon(marketplace_coupon_id=data.marketplace_coupon_id, title=data.title)
            session.add(coupon)
            coupon_by_external_id[data.marketplace_coupon_id] = coupon
        coupon.title = data.title
        coupon.code = data.code or coupon.code
        coupon.discount_percent = data.discount_percent
        coupon.discount_amount = data.discount_amount
        coupon.minimum_purchase = data.minimum_purchase
        coupon.maximum_discount = data.maximum_discount
        coupon.starts_at = data.starts_at
        coupon.expires_at = data.expires_at
        coupon.availability_text = data.availability_text
        coupon.restrictions = data.restrictions
        coupon.eligibility_confirmed = data.eligibility_confirmed
        coupon.raw_metadata = data.raw_metadata
        coupon.source_kind = str(data.raw_metadata.get("source_kind") or "affiliate_generated")
        coupon.shareable = bool(data.raw_metadata.get("shareable") and data.code)
        coupon.activation_status = str(data.raw_metadata.get("activation_status") or "GENERATED")
        coupon.scope = data.raw_metadata.get("scope")
        coupon.remaining_budget = _metadata_money(data.raw_metadata.get("remaining_budget"))
        coupon.lifecycle_status = (
            "EXPIRED"
            if data.expires_at and data.expires_at <= now
            else "EXHAUSTED"
            if coupon.remaining_budget is not None and coupon.remaining_budget <= 0
            else "ACTIVE"
        )
        fingerprint_payload = {
            "title": data.title,
            "code": data.code,
            "discount_percent": str(data.discount_percent),
            "discount_amount": str(data.discount_amount),
            "minimum_purchase": str(data.minimum_purchase),
            "maximum_discount": str(data.maximum_discount),
            "expires_at": data.expires_at.isoformat() if data.expires_at else None,
            "restrictions": data.restrictions,
        }
        digest = hashlib.sha256(
            json.dumps(fingerprint_payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        coupon.source_fingerprint = hashlib.sha256(
            f"mercado_livre:{data.marketplace_coupon_id}".encode()
        ).hexdigest()
        coupon.rules_version = digest
        coupon.last_verified_at = now
        await session.flush()
        normalized.append((data, coupon))

    coupon_ids = [coupon.id for _, coupon in normalized]
    existing_links = list(
        await session.scalars(
            select(CouponProductApplicability).where(
                CouponProductApplicability.coupon_id.in_(coupon_ids)
            )
        )
    )
    links_by_key = {(item.coupon_id, item.product_id): item for item in existing_links}
    for data, coupon in normalized:
        eligible_ids = {
            value.upper() for value in data.raw_metadata.get("eligible_product_ids", [])
        }
        eligible_products = {
            product.id: product
            for external_id in eligible_ids
            if (product := product_by_external_id.get(external_id)) is not None
        }
        linked_product_ids = {
            product_id for coupon_id, product_id in links_by_key if coupon_id == coupon.id
        }
        products_to_check = list(eligible_products.values()) + [
            product
            for product in products
            if product.id in linked_product_ids and product.id not in eligible_products
        ]
        for product in products_to_check:
            snapshot = latest_snapshots.get(product.id)
            is_officially_listed = product.id in eligible_products
            status = CouponApplicabilityStatus.not_applicable
            reason = "Produto não aparece mais na listagem oficial do cupom"
            evidence: dict = {
                "source": data.raw_metadata.get("applicability_source"),
                "products_view_url": data.raw_metadata.get("products_view_url"),
                "verified_external_ids": sorted(
                    value
                    for value in (product.item_id, product.product_id)
                    if value and value.upper() in eligible_ids
                ),
            }
            if data.expires_at and data.expires_at <= now:
                status = CouponApplicabilityStatus.expired
                reason = "Cupom expirado"
            elif is_officially_listed:
                if data.minimum_purchase and (
                    not snapshot or not snapshot.price or snapshot.price < data.minimum_purchase
                ):
                    status = CouponApplicabilityStatus.not_applicable
                    reason = "Preço abaixo da compra mínima"
                else:
                    status = CouponApplicabilityStatus.applicable
                    reason = "Produto sinalizado como elegível na área oficial"
                    applicable += 1
            link = links_by_key.get((coupon.id, product.id))
            if not link:
                link = CouponProductApplicability(coupon_id=coupon.id, product_id=product.id)
                session.add(link)
                links_by_key[(coupon.id, product.id)] = link
            link.status = status
            link.reason = reason
            link.evidence = evidence
            link.checked_at = now
        synced += 1
    await session.commit()
    return {"synced": synced, "applicable_links": applicable,
        "affected_product_ids": sorted({str(product_id) for _, product_id in links_by_key})}


async def applicable_coupon(
    session: AsyncSession, product_id, *, price: Decimal | None = None
) -> tuple[Coupon | None, CouponProductApplicability | None]:
    now = datetime.now(UTC)
    row = await session.execute(
        select(Coupon, CouponProductApplicability)
        .join(CouponProductApplicability, CouponProductApplicability.coupon_id == Coupon.id)
        .where(
            CouponProductApplicability.product_id == product_id,
            CouponProductApplicability.status == CouponApplicabilityStatus.applicable,
            Coupon.eligibility_confirmed.is_(True),
            (Coupon.expires_at.is_(None) | (Coupon.expires_at > now)),
        )
        .order_by(desc(Coupon.discount_percent), desc(Coupon.discount_amount))
    )
    if price is None:
        snapshot = await session.scalar(select(ProductSnapshot).where(ProductSnapshot.product_id == product_id)
            .order_by(desc(ProductSnapshot.observed_at), desc(ProductSnapshot.id)).limit(1))
        if not snapshot:
            return None, None
        price = snapshot.price
    best = (None, None)
    best_discount = Decimal(0)
    for coupon, applicability in row:
        discount = effective_coupon_percent(coupon, price, now)
        if applicability.evidence and applicability.checked_at and discount is not None:
            from datetime import timedelta
            if applicability.checked_at >= now - timedelta(minutes=30) and discount > best_discount:
                best, best_discount = (coupon, applicability), discount
    return best


def coupon_data(coupon: Coupon) -> CouponData:
    return CouponData(
        marketplace_coupon_id=coupon.marketplace_coupon_id,
        title=coupon.title,
        code=coupon.code,
        discount_percent=Decimal(coupon.discount_percent)
        if coupon.discount_percent is not None
        else None,
        discount_amount=Decimal(coupon.discount_amount)
        if coupon.discount_amount is not None
        else None,
        minimum_purchase=Decimal(coupon.minimum_purchase)
        if coupon.minimum_purchase is not None
        else None,
        maximum_discount=Decimal(coupon.maximum_discount)
        if coupon.maximum_discount is not None
        else None,
        starts_at=coupon.starts_at,
        expires_at=coupon.expires_at,
        availability_text=coupon.availability_text,
        restrictions=coupon.restrictions or [],
        eligibility_confirmed=coupon.eligibility_confirmed,
        raw_metadata=coupon.raw_metadata or {},
    )


def _metadata_money(value) -> Decimal | None:
    if not value:
        return None
    compact = re.sub(r"\s+", "", str(value))
    match = re.search(r"R\$([\d.]+(?:,\d{1,2})?)", compact)
    if not match:
        return None
    return Decimal(match.group(1).replace(".", "").replace(",", "."))
