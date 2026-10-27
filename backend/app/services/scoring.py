import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from backend.app.domain.types import NormalizedProduct, ScoreResult


@dataclass(slots=True)
class ScoreConfig:
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "discount": 25,
            "popularity": 18,
            "rating": 14,
            "commission": 12,
            "coupon": 10,
            "bonus": 8,
            "price_confidence": 8,
            "freshness": 5,
        }
    )
    discount_anchor: float = 70
    sold_anchor: int = 750_000
    commission_anchor: float = 16
    coupon_anchor: float = 20
    minimum_score: float = 85
    minimum_discount: float | None = None
    minimum_rating: float | None = None
    minimum_sold_count: int | None = None
    minimum_commission: float | None = None
    minimum_price: float | None = None
    maximum_price: float | None = None
    excluded_categories: list[str] = field(default_factory=list)
    excluded_brands: list[str] = field(default_factory=list)


class OfferScoringEngine:
    def __init__(self, config: ScoreConfig | None = None):
        self.config = config or ScoreConfig()

    def score(
        self,
        product: NormalizedProduct,
        *,
        coupon_percent: Decimal | None = None,
        monitored_price_drop_percent: Decimal | None = None,
        price_confidence: float = 0.6,
        freshness: float = 1.0,
    ) -> ScoreResult:
        c = self.config
        components: dict[str, float | None] = {
            "discount": self._ratio(product.discount_percent, c.discount_anchor),
            "popularity": self._sales(product.sold_count, c.sold_anchor),
            "rating": self._rating(product.rating),
            "commission": self._ratio(product.commission_percent, c.commission_anchor),
            "coupon": self._ratio(coupon_percent, c.coupon_anchor)
            if coupon_percent is not None
            else None,
            "bonus": self._bonus(product, monitored_price_drop_percent),
            "price_confidence": max(0.0, min(price_confidence, 1.0)),
            "freshness": max(0.0, min(freshness, 1.0)),
        }
        available_weight = sum(
            c.weights[key] for key, value in components.items() if value is not None
        )
        total = (
            sum(
                c.weights[key] * float(value)
                for key, value in components.items()
                if value is not None
            )
            / available_weight
            * 100
            if available_weight
            else 0
        )
        total_decimal = Decimal(str(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        reasons = self._filter_reasons(product, total)
        classification = (
            "EXCELENTE"
            if total >= 85
            else "MUITO BOM"
            if total >= 75
            else "BOM"
            if total >= 65
            else "NAO_PUBLICAR"
        )
        return ScoreResult(
            total=total_decimal,
            classification=classification,
            components=components,
            eligible=not reasons,
            reasons=reasons,
        )

    def _ratio(self, value: Decimal | None, anchor: float) -> float | None:
        if value is None:
            return None
        return max(0.0, min(float(value) / anchor, 1.0))

    def _sales(self, value: int | None, anchor: int) -> float | None:
        if value is None:
            return None
        return max(0.0, min(math.log10(value + 1) / math.log10(anchor + 1), 1.0))

    def _rating(self, value: Decimal | None) -> float | None:
        if value is None:
            return None
        return max(0.0, min((float(value) - 3.0) / 2.0, 1.0))

    def _bonus(self, product: NormalizedProduct, price_drop: Decimal | None) -> float:
        points = 0.0
        if product.highlight:
            points += 0.4
        if product.extra_commission or product.brand_commission:
            points += 0.3
        if price_drop is not None and price_drop >= 10:
            points += 0.3
        return min(points, 1.0)

    def _filter_reasons(self, product: NormalizedProduct, score: float) -> list[str]:
        c = self.config
        reasons: list[str] = []
        checks = [
            (score, c.minimum_score, "score_below_minimum", lambda a, b: a < b),
            (
                float(product.discount_percent) if product.discount_percent is not None else None,
                c.minimum_discount,
                "discount_below_minimum",
                lambda a, b: a < b,
            ),
            (
                float(product.rating) if product.rating is not None else None,
                c.minimum_rating,
                "rating_below_minimum",
                lambda a, b: a < b,
            ),
            (product.sold_count, c.minimum_sold_count, "sales_below_minimum", lambda a, b: a < b),
            (
                float(product.commission_percent)
                if product.commission_percent is not None
                else None,
                c.minimum_commission,
                "commission_below_minimum",
                lambda a, b: a < b,
            ),
            (
                float(product.current_price) if product.current_price is not None else None,
                c.minimum_price,
                "price_below_minimum",
                lambda a, b: a < b,
            ),
            (
                float(product.current_price) if product.current_price is not None else None,
                c.maximum_price,
                "price_above_maximum",
                lambda a, b: a > b,
            ),
        ]
        for value, threshold, reason, comparison in checks:
            if threshold is not None:
                if value is None:
                    reasons.append(reason + "_unverified")
                elif comparison(value, threshold):
                    reasons.append(reason)
        if product.current_price is None or product.current_price <= 0:
            reasons.append("invalid_price")
        import unicodedata
        def normalized(value):
            return "".join(c for c in unicodedata.normalize("NFD", str(value or "")) if not unicodedata.combining(c)).casefold().strip()
        metadata = product.raw_metadata or {}
        category = metadata.get("category_name") or str(metadata.get("discovery_strategy", "")).removeprefix("category:")
        if normalized(category) in {normalized(v) for v in c.excluded_categories}:
            reasons.append("category_excluded")
        if normalized(metadata.get("brand")) in {normalized(v) for v in c.excluded_brands}:
            reasons.append("brand_excluded")
        return reasons
