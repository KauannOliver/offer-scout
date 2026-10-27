import io
from decimal import Decimal

import httpx
import pytest
from PIL import Image

from backend.app.domain.types import NormalizedProduct
from backend.app.services.creative import ProductImageService


@pytest.mark.asyncio
async def test_product_image_preserves_aspect_ratio_without_composition(tmp_path):
    original = Image.new("RGB", (320, 180), "red")
    body = io.BytesIO()
    original.save(body, "WEBP")
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body.getvalue()))
    )
    service = ProductImageService(client)
    service.settings.artifacts_dir = tmp_path
    product = NormalizedProduct(
        marketplace="mercado_livre",
        source="fixture",
        item_id="MLB1",
        title="Produto",
        product_url="https://www.mercadolivre.com.br/p/MLB1",
        image_url="https://http2.mlstatic.com/test.webp",
        current_price=Decimal("10"),
    )
    path = await service.download(product)
    with Image.open(path) as saved:
        assert saved.size == (320, 180)
    await client.aclose()


@pytest.mark.asyncio
async def test_product_image_rejects_non_official_host():
    service = ProductImageService()
    product = NormalizedProduct(
        marketplace="mercado_livre",
        source="fixture",
        item_id="MLB1",
        title="Produto",
        product_url="https://www.mercadolivre.com.br/p/MLB1",
        image_url="https://example.com/image.jpg",
    )
    with pytest.raises(ValueError, match="official"):
        await service.download(product)
