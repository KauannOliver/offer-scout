"""Read-only deployment checks. Never print configuration, cookies or payloads."""
import asyncio
import json

import httpx
from sqlalchemy import text

from backend.app.config import get_settings
from backend.app.db import SessionLocal


async def main():
    settings = get_settings()
    async with SessionLocal() as session:
        summary = {}
        for name, query in {
            "migration": "SELECT version_num FROM alembic_version",
            "products": "SELECT count(*) FROM products",
            "snapshots": "SELECT count(*) FROM product_snapshots",
            "observations": "SELECT count(*) FROM product_observations",
            "current_states": "SELECT count(*) FROM product_current_state",
            "publications": "SELECT count(*) FROM publications",
            "active_duplicate_groups": "SELECT count(*) FROM (SELECT product_id, publication_kind FROM publication_queue WHERE status IN ('discovered','selected','link_pending','creative_pending','ready','publishing','dry_run','retry') GROUP BY product_id, publication_kind HAVING count(*) > 1) x",
            "uncertain_offers": "SELECT count(*) FROM publications WHERE delivery_status IN ('SENDING','UNCERTAIN')",
            "pending_config_jobs": "SELECT count(*) FROM reprocessing_jobs WHERE state IN ('PENDING','RUNNING')",
        }.items():
            summary[name] = await session.scalar(text(query))
    async with httpx.AsyncClient(base_url="http://affiliate-api:8000", timeout=30) as client:
        response = await client.post("/api/v1/auth/login", json={"username": settings.admin_username, "password": settings.admin_password})
        summary["login_http"] = response.status_code
        for endpoint in ("/health", "/ready", "/api/v1/operations/health", "/api/v1/operations/calendar", "/api/v1/operations/retention", "/api/v1/operations/weekly-report", "/api/v1/opportunities?format=page&page_size=1", "/api/v1/queue?format=page&page_size=1"):
            result = await client.get(endpoint)
            summary[endpoint] = {"http": result.status_code}
            if endpoint == "/ready" and result.status_code == 200:
                summary[endpoint]["details"] = result.json()
    print(json.dumps(summary, default=str))


if __name__ == "__main__":
    asyncio.run(main())
