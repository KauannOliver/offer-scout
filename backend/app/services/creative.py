import hashlib
import io
from pathlib import Path

import httpx
from PIL import Image

from backend.app.config import get_settings
from backend.app.domain.types import NormalizedProduct


class ProductImageService:
    """Downloads the official photo without overlays, cropping or composition."""

    allowed_host = "http2.mlstatic.com"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.settings = get_settings()
        self.client = client

    async def download(self, product: NormalizedProduct) -> Path:
        if not product.image_url:
            raise RuntimeError("Product has no official image")
        url = httpx.URL(product.image_url)
        if url.scheme != "https" or url.host != self.allowed_host:
            raise ValueError("Only official Mercado Livre product images are accepted")
        client = self.client or httpx.AsyncClient(timeout=20, follow_redirects=False)
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 10 * 1024 * 1024:
                        raise ValueError("Product image exceeds Telegram size limit")
        finally:
            if self.client is None:
                await client.aclose()
        image = Image.open(io.BytesIO(body))
        if image.width * image.height > 25_000_000:
            raise ValueError("Product image dimensions exceed safe limit")
        image.load()
        if image.width < 100 or image.height < 100:
            raise ValueError("Product image is too small")
        self.settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(body).hexdigest()[:16]
        item_digest = hashlib.sha256(product.item_id.encode()).hexdigest()[:12]
        output = self.settings.artifacts_dir / f"product-{item_digest}-{digest}.jpg"
        if not output.exists():
            image.convert("RGB").save(output, "JPEG", quality=95, optimize=True)
        return output


CreativeImageGenerator = ProductImageService
