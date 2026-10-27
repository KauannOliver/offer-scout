from decimal import Decimal
from pathlib import Path

import pytest

from backend.app.services.parsing import MercadoLivreAffiliateHubParser, parse_sold_count

FIXTURE = Path(__file__).parent / "fixtures" / "meli" / "hub.html"
FIXTURE_DIR = FIXTURE.parent


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+750mil vendidos", 750_000),
        ("+5mil vendidos", 5_000),
        ("+1000 vendidos", 1_000),
        ("+500 vendidos", 500),
        ("1,5 mil vendidos", 1_500),
        ("indisponível", None),
        (None, None),
    ],
)
def test_parse_sold_count(raw, expected):
    assert parse_sold_count(raw) == expected


def test_reference_product_contract():
    products = MercadoLivreAffiliateHubParser().parse(FIXTURE.read_text(encoding="utf-8"))
    product = next(item for item in products if item.item_id == "MLB2766771378")
    assert (
        product.title
        == "Creatina 1kg Suplemento Monohidratada em pó 100% Pura - Soldiers Nutrition"
    )
    assert product.item_id == "MLB2766771378"
    assert product.product_id == "MLB18725310"
    assert product.rating == Decimal("4.8")
    assert product.sold_text == "+750mil vendidos"
    assert product.sold_count == 750_000
    assert product.commission_percent == Decimal("12")
    assert product.previous_price == Decimal("239.90")
    assert product.current_price == Decimal("68.90")
    assert product.discount_percent == Decimal("71")
    assert product.highlight == "MAIS VENDIDO"


def test_all_three_contract_fixtures_are_sanitized():
    for name in ("hub.html", "linkbuilder.html", "coupons.html"):
        text = (FIXTURE_DIR / name).read_text(encoding="utf-8")
        lowered = text.lower()
        assert "authorization: bearer" not in lowered
        assert "access_token" not in lowered
        assert "refresh_token" not in lowered
        assert "sessionid" not in lowered
