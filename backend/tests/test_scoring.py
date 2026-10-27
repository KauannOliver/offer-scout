from decimal import Decimal

from backend.app.domain.types import NormalizedProduct
from backend.app.services.scoring import OfferScoringEngine, ScoreConfig


def product(**changes):
    values = dict(
        marketplace="mercado_livre",
        source="fixture",
        item_id="MLB2766771378",
        title="Produto",
        product_url="https://www.mercadolivre.com.br/produto/p/MLB18725310",
        rating=Decimal("4.8"),
        sold_count=750_000,
        commission_percent=Decimal("16"),
        discount_percent=Decimal("70"),
        current_price=Decimal("68.90"),
        highlight="MAIS VENDIDO",
        extra_commission=True,
    )
    values.update(changes)
    return NormalizedProduct(**values)


def test_high_quality_offer_is_eligible():
    result = OfferScoringEngine().score(product(), coupon_percent=Decimal("20"))
    assert result.total >= 85
    assert result.classification == "EXCELENTE"
    assert result.eligible


def test_missing_rating_is_not_zero_and_weights_are_renormalized():
    with_rating = OfferScoringEngine().score(product())
    missing_rating = OfferScoringEngine().score(product(rating=None))
    assert missing_rating.components["rating"] is None
    assert missing_rating.total > 0
    assert abs(float(with_rating.total) - float(missing_rating.total)) < 20


def test_missing_coupon_is_null_and_weight_is_redistributed():
    result = OfferScoringEngine().score(product())
    assert result.components["coupon"] is None
    assert result.total > 0


def test_hard_filter_blocks_offer():
    engine = OfferScoringEngine(ScoreConfig(minimum_score=0, minimum_discount=50))
    result = engine.score(product(discount_percent=Decimal("20")))
    assert not result.eligible
    assert "discount_below_minimum" in result.reasons
