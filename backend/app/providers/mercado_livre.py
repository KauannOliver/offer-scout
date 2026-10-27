from redis.asyncio import Redis

from backend.app.domain.providers import AffiliateProvider, MarketplaceProvider
from backend.app.domain.types import AffiliateLinkData, CouponData, NormalizedProduct
from backend.app.services.browser import MercadoLivreBrowserService


class MercadoLivreProvider(MarketplaceProvider, AffiliateProvider):
    """Provider restricted to authenticated, official Mercado Livre web interfaces."""

    def __init__(
        self,
        redis: Redis,
        browser: MercadoLivreBrowserService | None = None,
    ):
        self.browser = browser or MercadoLivreBrowserService(redis)

    @property
    def discovery_metrics(self) -> dict:
        return self.browser.discovery_metrics

    @property
    def coupon_products(self) -> list[NormalizedProduct]:
        return self.browser.coupon_products

    @property
    def coupon_metrics(self) -> dict:
        return self.browser.coupon_metrics

    async def discover_offers(self, strategy: str | None = None) -> list[NormalizedProduct]:
        return await self.browser.discover_offers(strategy=strategy)

    async def revalidate(self, product: NormalizedProduct) -> NormalizedProduct | None:
        return await self.browser.revalidate_product(product)

    async def list_coupons(self) -> list[CouponData]:
        return await self.browser.list_coupons()

    async def generate_coupon(self, coupon: CouponData) -> CouponData:
        return await self.browser.generate_coupon(coupon)

    async def generate_affiliate_link(self, product_url: str, tag: str) -> AffiliateLinkData:
        return await self.browser.generate_affiliate_link(product_url, tag)

    async def authentication_state(self) -> str:
        return await self.browser.authentication_state()

    async def aclose(self) -> None:
        return None
