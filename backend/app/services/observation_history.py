from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from backend.app.models import ProductObservation, ProductSnapshot


async def editorial_history(session, product_id):
    """One real observation per UTC hour; no synthetic historical verification."""
    cutoff = datetime.now(UTC) - timedelta(days=30)
    snapshots = list(await session.scalars(select(ProductSnapshot).where(
        ProductSnapshot.product_id == product_id, ProductSnapshot.observed_at >= cutoff
    ).order_by(ProductSnapshot.observed_at)))
    rows = await session.execute(select(ProductObservation.observed_at, ProductSnapshot).join(
        ProductSnapshot, ProductObservation.snapshot_id == ProductSnapshot.id
    ).where(ProductObservation.product_id == product_id, ProductObservation.observed_at >= cutoff))
    events = [(s.observed_at, s) for s in snapshots] + list(rows)
    hourly = {}
    for timestamp, snapshot in sorted(events, key=lambda item: item[0]):
        hourly[timestamp.replace(minute=0, second=0, microsecond=0)] = SimpleNamespace(
            **{column.name: getattr(snapshot, column.name) for column in ProductSnapshot.__table__.columns}
        )
        hourly[timestamp.replace(minute=0, second=0, microsecond=0)].observed_at = timestamp
    result = list(hourly.values())
    for previous, current in zip(result, result[1:], strict=False):
        metrics = dict(current.metrics or {})
        if previous.price and current.price:
            metrics["drop_from_last_percent"] = str((previous.price-current.price)/previous.price*100)
        current.metrics = metrics
    return result
