import asyncio
from pathlib import Path

from backend.app.db import SessionLocal
from backend.app.services.parsing import MercadoLivreAffiliateHubParser
from backend.app.services.pipeline import OfferPipeline


async def main() -> None:
    fixture = Path("backend/tests/fixtures/meli/hub.html")
    offers = MercadoLivreAffiliateHubParser().parse(fixture.read_text(encoding="utf-8"))
    async with SessionLocal() as session:
        ingested = await OfferPipeline().ingest(session, offers)
    print({"parsed": len(offers), "ingested": ingested})


if __name__ == "__main__":
    asyncio.run(main())
