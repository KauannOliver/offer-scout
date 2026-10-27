"""Opt-in live integration smoke tests. Never prints configured secrets."""

import argparse
import asyncio
from decimal import Decimal

from backend.app.domain.types import NormalizedProduct
from backend.app.services.ai import GroqCopyGenerator
from backend.app.services.telegram import TelegramService


async def groq_smoke() -> None:
    product = NormalizedProduct(
        marketplace="mercado_livre",
        source="integration_smoke",
        item_id="SMOKE",
        title="Produto demonstrativo para teste administrativo",
        product_url="https://www.mercadolivre.com.br/produto/p/MLB18725310",
        current_price=Decimal("99.90"),
        discount_percent=Decimal("20"),
    )
    copy, generator = await GroqCopyGenerator().generate(product)
    if generator != "groq":
        raise RuntimeError("Groq is not configured; deterministic fallback was used")
    print({"groq": "ok", "schema": sorted(copy.model_dump())})


async def telegram_smoke() -> None:
    service = TelegramService()
    validation = await service.validate_private_smoke()
    message_id = await service.send_private_smoke()
    print({"telegram": "ok", **validation, "message_id": message_id})


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("integration", choices=["groq", "telegram"])
    args = parser.parse_args()
    await (groq_smoke() if args.integration == "groq" else telegram_smoke())


if __name__ == "__main__":
    asyncio.run(main())
