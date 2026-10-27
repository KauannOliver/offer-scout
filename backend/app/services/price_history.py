"""Deterministic hourly sampling avoids weighting frequently scraped products."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import text


async def sampled_prices(session, ids):
    if not ids:
        return {}
    result = await session.execute(text("""
      WITH events AS (
        SELECT product_id, observed_at, price FROM product_snapshots
        WHERE product_id = ANY(:ids) AND observed_at >= :cutoff
        UNION ALL
        SELECT o.product_id, o.observed_at, s.price FROM product_observations o
        JOIN product_snapshots s ON s.id=o.snapshot_id
        WHERE o.product_id = ANY(:ids) AND o.observed_at >= :cutoff
      ), hourly AS (
        SELECT DISTINCT ON (product_id,date_trunc('hour',observed_at))
          product_id,observed_at,price FROM events WHERE price > 0
        ORDER BY product_id,date_trunc('hour',observed_at),observed_at DESC,price
      ) SELECT product_id,price FROM hourly ORDER BY product_id,observed_at DESC
    """), {"ids": list(ids), "cutoff": datetime.now(UTC)-timedelta(days=30)})
    prices = {}
    for product_id, price in result:
        prices.setdefault(product_id, []).append(price)
    return prices
