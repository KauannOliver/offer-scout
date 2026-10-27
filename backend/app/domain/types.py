from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal


@dataclass(slots=True)
class NormalizedProduct:
    marketplace: str
    source: str
    item_id: str
    title: str
    product_url: str
    product_id: str | None = None
    user_product_id: str | None = None
    variation_id: str | None = None
    image_url: str | None = None
    rating: Decimal | None = None
    sold_text: str | None = None
    sold_count: int | None = None
    highlight: str | None = None
    commission_percent: Decimal | None = None
    extra_commission: bool = False
    brand_commission: bool = False
    previous_price: Decimal | None = None
    current_price: Decimal | None = None
    discount_percent: Decimal | None = None
    currency: str = "BRL"
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw_metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class CouponData:
    marketplace_coupon_id: str
    title: str
    code: str | None = None
    discount_percent: Decimal | None = None
    discount_amount: Decimal | None = None
    minimum_purchase: Decimal | None = None
    maximum_discount: Decimal | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    availability_text: str | None = None
    restrictions: list[str] = field(default_factory=list)
    eligibility_confirmed: bool = False
    raw_metadata: dict = field(default_factory=dict)


@dataclass(slots=True)
class ScoreResult:
    total: Decimal
    classification: str
    components: dict[str, float | None]
    eligible: bool
    reasons: list[str]


@dataclass(slots=True)
class AffiliateLinkData:
    short_url: str
    tracking_tag: str
    source: str = "official_link_builder"
    evidence: dict = field(default_factory=dict)
