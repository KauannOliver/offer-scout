"""Shared locking order for administrative queue mutations and publishers."""
from sqlalchemy import select

from backend.app.models import Product, Publication, PublicationQueue, QueueStatus
from backend.app.services.pipeline import ACTIVE_QUEUE_STATUSES


async def lock_queue_items(session, ids):
    product_ids = list(await session.scalars(select(PublicationQueue.product_id).where(PublicationQueue.id.in_(ids))))
    await session.execute(select(Product.id).where(Product.id.in_(product_ids)).order_by(Product.id).with_for_update())
    return list(await session.scalars(select(PublicationQueue).where(PublicationQueue.id.in_(ids))
        .order_by(PublicationQueue.id).with_for_update().execution_options(populate_existing=True)))


async def can_mutate(session, item, reactivate=False):
    if item.status in {QueueStatus.published, QueueStatus.publishing}:
        return False
    # Keep confirmed and ambiguous delivery reservations immutable through generic actions.
    if await session.scalar(select(Publication.id).where(Publication.queue_id == item.id).limit(1)):
        return False
    if reactivate and await session.scalar(select(PublicationQueue.id).where(
        PublicationQueue.product_id == item.product_id,
        PublicationQueue.publication_kind == item.publication_kind,
        PublicationQueue.id != item.id, PublicationQueue.status.in_(ACTIVE_QUEUE_STATUSES)).limit(1)):
        return False
    return True
