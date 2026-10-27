"""Read benchmark against the explicitly isolated restored database only."""
import asyncio
import json
import os
import platform
import statistics
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import httpx
from sqlalchemy import func, select, text

from backend.app.db import SessionLocal
from backend.app.models import OfferScore, Product, ProductCurrentState, ProductSnapshot


async def main():
    async with SessionLocal() as session:
        if await session.scalar(text("SELECT current_database()")) != "verification_restore":
            raise RuntimeError("Benchmark is restricted to verification_restore")
        count = await session.scalar(select(func.count()).select_from(Product))
        for _ in range(max(0, 10000-count)):
            pid, sid, scoreid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            session.add(Product(id=pid, item_id=f"BENCH-{pid}", title="Produto sintético para carga",
                product_url="https://www.mercadolivre.com.br/p/MLB000000", source="isolated_benchmark"))
            await session.flush()
            session.add(ProductSnapshot(id=sid, product_id=pid, price=Decimal(100)))
            await session.flush()
            session.add(OfferScore(id=scoreid, product_id=pid, snapshot_id=sid, total=Decimal(30),
                classification="NAO_PUBLICAR", components={}, eligible=False))
            await session.flush()
            session.add(ProductCurrentState(product_id=pid, snapshot_id=sid, score_id=scoreid, last_verified_at=datetime.now(UTC),
                score_total=Decimal(30), score_eligible=False, price=Decimal(100)))
        await session.commit()
        count = await session.scalar(select(func.count()).select_from(Product))
    async with httpx.AsyncClient(base_url="http://api-test:8000", timeout=30) as client:
        response = await client.post("/api/v1/auth/login", json={"username": "verification_operator", "password": "verification-local-only"})
        response.raise_for_status()
        cold_started = time.perf_counter()
        warmup = await asyncio.gather(*(client.get("/api/v1/opportunities?page=1&page_size=30&sort=score&format=page") for _ in range(5)))
        for response in warmup:
            response.raise_for_status()
        cold_batch_ms = (time.perf_counter()-cold_started)*1000
        measurements = {}
        for endpoint in ("opportunities", "queue", "products", "coupons", "publications", "operations/calendar"):
            samples = []
            async def reader(endpoint=endpoint, samples=samples):
                for page in range(1, 11):
                    start = time.perf_counter()
                    response = await client.get(f"/api/v1/{endpoint}?page={page}&page_size=30&format=page")
                    response.raise_for_status()
                    if endpoint == "opportunities":
                        assert response.json()["total"] >= 10000
                    samples.append((time.perf_counter()-start)*1000)
            await asyncio.gather(*(reader() for _ in range(5)))
            samples.sort()
            measurements[endpoint] = {"requests": len(samples), "median_ms": round(statistics.median(samples), 2),
                "p95_ms": round(samples[int(len(samples)*.95)-1], 2)}
    print(json.dumps({"products": count, "clients": 5, "warmup_requests": 5, "cold_batch_ms": round(cold_batch_ms, 2),
        "endpoints": measurements, "cpu_count": os.cpu_count(), "platform": platform.platform()}))


if __name__ == "__main__":
    asyncio.run(main())
