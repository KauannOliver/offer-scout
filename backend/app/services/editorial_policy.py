from sqlalchemy import desc, select

from backend.app.models import AppSetting, Product, Publication, PublicationQueue


async def editorial_context(session):
    setting = await session.scalar(select(AppSetting).where(AppSetting.key == "editorial"))
    policy = setting.value if setting else {}
    rows = (await session.execute(select(Product.category_name, Product.brand).join(
        PublicationQueue, PublicationQueue.product_id == Product.id).join(Publication, Publication.queue_id == PublicationQueue.id)
        .where(Publication.delivery_status == "SENT", Publication.publication_kind == "GOOD_OFFER")
        .order_by(desc(Publication.published_at), desc(Publication.id)).limit(100))).all()
    return policy, rows


def diversity_allowed(product, policy, recent):
    for index, field, key, default in ((0, "category_name", "max_same_category", 2), (1, "brand", "max_same_brand", 1)):
        limit = max(1, int(policy.get(key, default)))
        value = str(getattr(product, field, None) or "").strip().casefold()
        if value and len(recent) >= limit and all(str(row[index] or "").strip().casefold() == value for row in recent[:limit]):
            return False
    return True
