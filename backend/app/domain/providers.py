from abc import ABC, abstractmethod

from backend.app.domain.types import AffiliateLinkData, CouponData, NormalizedProduct


class MarketplaceProvider(ABC):
    @abstractmethod
    async def discover_offers(self, strategy: str | None = None) -> list[NormalizedProduct]: ...

    @abstractmethod
    async def revalidate(self, product: NormalizedProduct) -> NormalizedProduct | None: ...


class AffiliateProvider(ABC):
    @abstractmethod
    async def list_coupons(self) -> list[CouponData]: ...

    @abstractmethod
    async def generate_coupon(self, coupon: CouponData) -> CouponData: ...

    @abstractmethod
    async def generate_affiliate_link(self, product_url: str, tag: str) -> AffiliateLinkData: ...

    @abstractmethod
    async def authentication_state(self) -> str: ...
