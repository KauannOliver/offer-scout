from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from bs4 import BeautifulSoup

from backend.app.services.coupon_parsing import parse_generated_coupon, parse_marketplace_coupon

CONDITIONS = (
    "15% OFF ID 13483465 - Cupom válido no Brasil, de 17/08/2026 a 16/09/2026, "
    "incluindo ambas as datas. Válido apenas para os produtos selecionados e enquanto "
    "durarem os estoques. O cupom será aplicado automaticamente no carrinho elegível. "
    "O cupom é aplicável apenas para compras mínimas de produtos selecionados cujo valor "
    "seja igual ou superior a R$ 299. O cupom é limitado a 1 (um) uso por CPF. "
    "Máximo de desconto de R$ 999. Válido enquanto durar o estoque de 334 cupons."
)
FIXTURES = Path(__file__).parent / "fixtures" / "meli"


def test_generated_percent_coupon_with_official_products():
    coupon = parse_generated_coupon(
        code_text="#EXEMPLOTESTE15",
        discount_text="15% OFF",
        category_text="Em produtos selecionados",
        expiry_text="Vence em 17 de setembro",
        conditions_text=CONDITIONS,
        budget_text="Orçamento restante: R$ 48.894,73",
        eligible_product_ids=["MLB4640008387", "MLB67880366", "invalid"],
        eligible_products_count=2,
        products_view_url="https://lista.mercadolivre.com.br/campanha-oficial",
    )
    assert coupon.marketplace_coupon_id == "meli-13483465"
    assert coupon.code == "EXEMPLOTESTE15"
    assert coupon.discount_percent == Decimal("15")
    assert coupon.discount_amount is None
    assert coupon.minimum_purchase == Decimal("299")
    assert coupon.maximum_discount == Decimal("999")
    assert coupon.starts_at == datetime(2026, 8, 17, 3, tzinfo=UTC)
    assert coupon.expires_at == datetime(2026, 9, 17, 2, 59, 59, tzinfo=UTC)
    assert coupon.eligibility_confirmed is True
    assert coupon.raw_metadata["eligible_products_count"] == 2
    assert coupon.raw_metadata["eligible_product_ids"] == ["MLB4640008387", "MLB67880366"]


def test_generated_fixed_amount_coupon_and_natural_expiry_fallback():
    coupon = parse_generated_coupon(
        code_text="#EXEMPLOTESTE10",
        discount_text="R$ 20 OFF",
        category_text="Em produtos selecionados",
        expiry_text="Vence em 1 de outubro",
        conditions_text="Cupom para produtos selecionados.",
        budget_text="Orçamento restante: R$ 982.160",
        now=datetime(2026, 9, 2, tzinfo=UTC),
    )
    assert coupon.code == "EXEMPLOTESTE10"
    assert coupon.discount_percent is None
    assert coupon.discount_amount == Decimal("20")
    assert coupon.expires_at == datetime(2026, 10, 2, 2, 59, 59, tzinfo=UTC)
    assert coupon.eligibility_confirmed is False


def test_marketplace_account_coupon_is_catalogued_but_not_shareable():
    coupon = parse_marketplace_coupon(
        title_text="25% OFF em Itens para Casa",
        scope_text="Em produtos selecionados",
        minimum_text="Compra mínima R$ 25",
        maximum_text="Limite de R$ 20",
        expiry_text="Vence 31 de outubro",
        action_text="Aplicar",
        now=datetime(2026, 9, 3, tzinfo=UTC),
    )
    assert coupon.discount_percent == Decimal("25")
    assert coupon.minimum_purchase == Decimal("25")
    assert coupon.maximum_discount == Decimal("20")
    assert coupon.code is None
    assert coupon.raw_metadata["source_kind"] == "marketplace_general"
    assert coupon.raw_metadata["shareable"] is False
    assert coupon.eligibility_confirmed is False


def test_marketplace_coupon_accepts_brazilian_thousands_and_trailing_punctuation():
    coupon = parse_marketplace_coupon(
        title_text="20% OFF em Eletrônicos",
        scope_text="Produtos selecionados",
        minimum_text="Compra mínima R$ 149.",
        maximum_text="Limite de R$ 1.000.",
        expiry_text="Vence 30 de setembro",
        action_text="Conferir",
    )
    assert coupon.minimum_purchase == Decimal("149")
    assert coupon.maximum_discount == Decimal("1000")


def test_sanitized_html_contracts_match_live_semantic_selectors():
    generated = BeautifulSoup((FIXTURES / "generated_coupons_v2.html").read_text(), "lxml")
    card = generated.select_one(".generated-coupon-item")
    assert card.select_one("b").get_text(strip=True) == "#EXEMPLOTESTE15"
    assert (
        card.select_one(".generated-coupon-item__inner-details-title").get_text(strip=True)
        == "15% OFF"
    )
    marketplace = BeautifulSoup((FIXTURES / "marketplace_coupons.html").read_text(), "lxml")
    public_card = marketplace.select_one(".coupon-card")
    assert public_card.select_one(".title")["title"] == "25% OFF em Itens para Casa"
    assert public_card.select_one("button").get_text(strip=True) == "Aplicar"
    for name in ("generated_coupons_v2.html", "marketplace_coupons.html"):
        lowered = (FIXTURES / name).read_text().lower()
        assert "cookie" not in lowered and "access_token" not in lowered
