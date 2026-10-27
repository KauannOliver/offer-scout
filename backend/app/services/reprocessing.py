import hashlib
import json

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain.types import NormalizedProduct
from backend.app.models import (
    AppSetting,
    AuditEvent,
    OfferScore,
    Product,
    ProductCurrentState,
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
from backend.app.services.pipeline import scoring_engine_from_settings, scoring_engine_from_values

ACTIVE = {
    QueueStatus.discovered, QueueStatus.selected, QueueStatus.link_pending,
    QueueStatus.creative_pending, QueueStatus.ready, QueueStatus.dry_run,
    QueueStatus.retry, QueueStatus.publishing,
}


async def reprocess_after_setting(session: AsyncSession, key: str, version: int) -> dict:
    return await reprocess_after_settings(session, {key: version})


async def preview_settings_impact(session: AsyncSession, values: dict[str, dict]) -> dict:
    active_items = list(await session.scalars(select(PublicationQueue).where(
        PublicationQueue.publication_kind == PublicationKind.good_offer.value,
        PublicationQueue.status.in_(ACTIVE),
    )))
    products = list(await session.scalars(select(Product).where(Product.active.is_(True))))
    snapshots = list(await session.scalars(
        select(ProductSnapshot).where(ProductSnapshot.product_id.in_([p.id for p in products]))
        .distinct(ProductSnapshot.product_id)
        .order_by(ProductSnapshot.product_id, desc(ProductSnapshot.observed_at), desc(ProductSnapshot.id))
    ))
    latest: dict = {}
    for snapshot in snapshots:
        latest.setdefault(snapshot.product_id, snapshot)
    states = {s.product_id: s for s in await session.scalars(select(ProductCurrentState))}
    discounts = await confirmed_discounts(session, {pid: snapshot.price for pid, snapshot in latest.items()})
    stored = await session.scalar(select(AppSetting).where(AppSetting.key == "scoring"))
    discovery = values.get("discovery", await _stored_value(session, "discovery"))
    engine = scoring_engine_from_values(values.get("scoring", stored.value if stored else {}), discovery)
    active_by_product = {item.product_id for item in active_items}
    publishing = {item.product_id for item in active_items if item.status == QueueStatus.publishing}
    publications, deduplication = await repetition_context(session, [p.id for p in products])
    deduplication = values.get("deduplication", deduplication)
    approved = entering = leaving = 0
    for product in products:
        snapshot = latest.get(product.id)
        if not snapshot:
            continue
        score = evaluate_score(engine, _normalized(product, snapshot), snapshot,
            states[product.id].last_verified_at if product.id in states else snapshot.observed_at,
            discounts.get(product.id))
        permitted = repetition_allowed(publications.get(product.id), snapshot.price, discounts.get(product.id), deduplication)
        if score.eligible:
            approved += 1
            entering += int(product.id not in active_by_product and permitted)
        if (not score.eligible or not permitted) and product.id in active_by_product and product.id not in publishing:
            leaving += 1
    return {
        "products_evaluated": len(products), "approved": approved,
        "estimated_entering": entering, "estimated_leaving": leaving,
        "creative_regeneration": sum(item.status != QueueStatus.publishing for item in active_items)
            if "content" in values and values["content"] != await _stored_value(session, "content") else 0,
        "rescheduled": sum(item.status != QueueStatus.publishing for item in active_items)
            if "cadence" in values and values["cadence"] != await _stored_value(session, "cadence") else 0,
    }


async def reprocess_after_settings(
    session: AsyncSession, changes: dict[str, int], *, product_ids: set | None = None,
    commit: bool = True,
    settings_values: dict | None = None,
) -> dict:
    product_scoped = {
        "scoring", "deduplication", "discovery", "editorial", "coupons",
        "content", "cadence",
    }
    if not product_scoped.intersection(changes):
        result = {
            "changes": changes, "processed": 0, "approved": 0, "rejected": 0,
            "newly_queued": 0, "creative_invalidated": 0, "rescheduled": 0,
        }
        await _audit(session, changes, result, commit=commit)
        return result
    query = select(Product).where(Product.active.is_(True))
    if product_ids is not None:
        query = query.where(Product.id.in_(product_ids))
    products = list(await session.scalars(query.order_by(Product.id).with_for_update()))
    ids = [item.id for item in products]
    if not ids:
        result = {"changes": changes, "processed": 0, "approved": 0, "rejected": 0}
        await _audit(session, changes, result, commit=commit)
        return result

    snapshots = list(await session.scalars(
        select(ProductSnapshot).where(ProductSnapshot.product_id.in_(ids))
        .distinct(ProductSnapshot.product_id)
        .order_by(ProductSnapshot.product_id, desc(ProductSnapshot.observed_at), desc(ProductSnapshot.id))
    ))
    latest_snapshot: dict = {}
    for snapshot in snapshots:
        latest_snapshot.setdefault(snapshot.product_id, snapshot)
    queue_rows = list(await session.scalars(
        select(PublicationQueue).where(
            PublicationQueue.product_id.in_(ids),
            PublicationQueue.publication_kind == PublicationKind.good_offer.value,
        ).order_by(PublicationQueue.product_id, desc(PublicationQueue.created_at)).with_for_update()
    ))
    by_product: dict = {}
    states = {s.product_id: s for s in await session.scalars(
        select(ProductCurrentState).where(ProductCurrentState.product_id.in_(ids))
    )}
    existing_scores = {score.id: score for score in await session.scalars(
        select(OfferScore).where(OfferScore.id.in_([state.score_id for state in states.values() if state.score_id]))
    )}
    for item in queue_rows:
        by_product.setdefault(item.product_id, []).append(item)

    invalidated = rescheduled = 0
    if "content" in changes:
        for item in queue_rows:
            if item.status in ACTIVE - {QueueStatus.publishing}:
                item.creative_id = None
                item.status = QueueStatus.creative_pending
                item.last_error = None
                invalidated += 1
    if "cadence" in changes:
        for item in queue_rows:
            if item.status in ACTIVE - {QueueStatus.publishing}:
                item.scheduled_for = None
                rescheduled += 1

    scoring_change = bool(
        {"scoring", "deduplication", "discovery", "editorial", "coupons"} & set(changes)
    )
    approved = rejected = queued = 0
    if scoring_change:
        engine = scoring_engine_from_values(settings_values.get("scoring", {}), settings_values.get("discovery", {})) if settings_values is not None else await scoring_engine_from_settings(session)
        coupons = await confirmed_discounts(session, {pid: snap.price for pid, snap in latest_snapshot.items()})
        publications, deduplication = await repetition_context(session, ids)
        if settings_values is not None:
            deduplication = settings_values.get("deduplication", deduplication)
        fingerprint = policy_fingerprint(engine)
        version_token = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()[:20]
        for product in products:
            snapshot = latest_snapshot.get(product.id)
            if not snapshot:
                continue
            result = evaluate_score(engine, _normalized(product, snapshot), snapshot,
                states[product.id].last_verified_at if product.id in states else snapshot.observed_at,
                coupons.get(product.id))
            score = existing_scores.get(states[product.id].score_id) if product.id in states else None
            if not score or (score.snapshot_id, score.total, score.components, score.eligible, score.reasons, score.policy_fingerprint) != (snapshot.id, result.total, result.components, result.eligible, result.reasons, fingerprint):
                score = OfferScore(
                    product_id=product.id, snapshot_id=snapshot.id, total=result.total,
                    classification=result.classification, components=result.components,
                    eligible=result.eligible, reasons=result.reasons, policy_fingerprint=fingerprint,
                )
                session.add(score)
                await session.flush()
            if product.id in states:
                states[product.id].score_id = score.id
                states[product.id].score_total = score.total
                states[product.id].score_eligible = score.eligible
            rows = by_product.get(product.id, [])
            active = next((row for row in rows if row.status in ACTIVE), None)
            permitted = repetition_allowed(publications.get(product.id), snapshot.price, coupons.get(product.id), deduplication)
            if result.eligible and permitted:
                approved += 1
                if active:
                    if active.status != QueueStatus.publishing:
                        active.score_id = score.id
                        active.priority = int(score.total * 100)
                        active.operational_reason = None
                else:
                    reusable = next((
                        row for row in rows if row.status == QueueStatus.skipped
                        and (row.operational_reason or row.last_error or "").startswith("Configuração v")
                    ), None)
                    if reusable:
                        reusable.status = QueueStatus.selected
                        reusable.score_id = score.id
                        reusable.priority = int(score.total * 100)
                        reusable.last_error = None
                        reusable.operational_reason = None
                    else:
                        key = hashlib.sha256(f"settings:{version_token}:{product.item_id}".encode()).hexdigest()
                        if any(row.idempotency_key == key for row in rows):
                            continue
                        session.add(PublicationQueue(
                            product_id=product.id, score_id=score.id,
                            status=QueueStatus.selected, priority=int(score.total * 100),
                            source_strategy="settings_reprocess",
                            idempotency_key=key, publication_kind=PublicationKind.good_offer.value,
                        ))
                        queued += 1
            else:
                rejected += 1
                for item in rows:
                    if item.status in ACTIVE - {QueueStatus.publishing}:
                        item.status = QueueStatus.skipped
                        reasons = result.reasons if not result.eligible else ["repetition_window"]
                        item.operational_reason = f"Configuração v{version_token}: {', '.join(reasons)}"
                        item.last_error = None

    result_data = {
        "changes": changes, "processed": len(products), "approved": approved,
        "rejected": rejected, "newly_queued": queued,
        "creative_invalidated": invalidated, "rescheduled": rescheduled,
    }
    await _audit(session, changes, result_data, commit=commit)
    return result_data


async def _audit(session: AsyncSession, changes: dict[str, int], details: dict, *, commit=True) -> None:
    session.add(AuditEvent(
        actor="system", action="SETTINGS_REPROCESSED", entity_type="app_setting",
        entity_id=",".join(sorted(changes)), details=details,
    ))
    if commit:
        await session.commit()


def _normalized(product: Product, snapshot: ProductSnapshot) -> NormalizedProduct:
    return NormalizedProduct(
        marketplace=product.marketplace, source=product.source, item_id=product.item_id,
        product_id=product.product_id, title=product.title, product_url=product.product_url,
        image_url=product.image_url, previous_price=snapshot.previous_price,
        current_price=snapshot.price, discount_percent=snapshot.discount_percent,
        rating=snapshot.rating, sold_text=snapshot.sold_text, sold_count=snapshot.sold_count,
        commission_percent=snapshot.commission_percent,
        extra_commission=snapshot.extra_commission,
        brand_commission=snapshot.brand_commission, highlight=snapshot.highlight,
        currency=snapshot.currency, observed_at=snapshot.observed_at,
        raw_metadata={**(product.raw_metadata or {}), "category_name": product.category_name, "brand": product.brand},
    )


async def _stored_value(session, key):
    value = await session.scalar(select(AppSetting).where(AppSetting.key == key))
    return value.value if value else {}
