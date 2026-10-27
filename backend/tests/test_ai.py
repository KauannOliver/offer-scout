from decimal import Decimal

import pytest

from backend.app.domain.types import CouponData, NormalizedProduct
from backend.app.services.ai import GroqCopyGenerator


@pytest.mark.parametrize("joke", [
    "Com este lençol queen, a economia entra em modo de sono profundo: durma em 0,01% do custo.",
    "Com este lençol a economia rende pela metade do preço, que alegria para o bolso!",
    "Com este tênis você economiza energia na conta de luz e a carteira agradece.",
])
def test_cached_creative_cannot_publish_invented_numbers_or_benefits(joke):
    from backend.app.services.ai import CreativeLines, _validated_lines
    product = NormalizedProduct(marketplace="mercado_livre", source="fixture", item_id="MLB5",
        title="Lençol Queen", product_url="https://www.mercadolivre.com.br/p/MLB5", current_price=Decimal("41.99"))
    lines = CreativeLines(joke=joke, call_to_action="CONFIRA A OFERTA")
    with pytest.raises(ValueError):
        _validated_lines(lines, product)
    result = GroqCopyGenerator().render(product, lines)
    assert result.creative.joke != joke
    assert "0,01%" not in result.caption_html and "conta de luz" not in result.caption_html
    assert "R$ 41,99" in result.caption_html


def test_fallback_copy_is_factual_and_hides_commission():
    product = NormalizedProduct(
        marketplace="mercado_livre",
        source="fixture",
        item_id="MLB1",
        title="Produto teste",
        product_url="https://www.mercadolivre.com.br/produto/p/MLB18725310",
        previous_price=Decimal("100"),
        current_price=Decimal("70"),
        discount_percent=Decimal("30"),
        commission_percent=Decimal("12"),
    )
    copy = GroqCopyGenerator().fallback(product)
    text = copy.telegram_text()
    assert "R$ 70,00" in text
    assert "30% OFF" in text
    assert "12%" not in text
    assert "Publicidade • Anúncios • Promoções" in text
    assert text.splitlines()[0] == "🔥 <b>PRODUTO TESTE</b>"
    assert "ESSA COMPRA CABE" not in text
    assert "“" not in text
    assert "”" not in text


def test_confirmed_coupon_is_rendered_and_unconfirmed_coupon_is_omitted():
    product = NormalizedProduct(
        marketplace="mercado_livre",
        source="fixture",
        item_id="MLB1",
        title="Produto <teste>",
        product_url="https://www.mercadolivre.com.br/produto/p/MLB1",
        previous_price=Decimal("239.90"),
        current_price=Decimal("68.90"),
        discount_percent=Decimal("71"),
    )
    generator = GroqCopyGenerator()
    unconfirmed = CouponData(marketplace_coupon_id="c1", title="Cupom", code="POUPE10")
    assert (
        "POUPE10"
        not in generator.render(
            product, generator.fallback_lines(product), unconfirmed
        ).telegram_text()
    )
    confirmed = CouponData(
        marketplace_coupon_id="c1",
        title="Cupom",
        code="POUPE10",
        eligibility_confirmed=True,
        discount_percent=Decimal("10"),
        minimum_purchase=Decimal("50"),
        maximum_discount=Decimal("20"),
    )
    text = generator.render(product, generator.fallback_lines(product), confirmed).telegram_text(
        "https://meli.la/test"
    )
    assert "<code>POUPE10</code>" in text
    assert "ECONOMIA DE R$ 171,00" in text
    assert "Produto <teste>" not in text
    assert len(text) <= 1024


def test_joke_wrapping_quotes_are_removed():
    product = NormalizedProduct(
        marketplace="mercado_livre",
        source="fixture",
        item_id="MLB1",
        title="Produto",
        product_url="https://www.mercadolivre.com.br/produto/p/MLB1",
        current_price=Decimal("10"),
    )
    generator = GroqCopyGenerator()
    lines = generator.fallback_lines(product).model_copy(
        update={"joke": "“Meu bolso não aceita desperdício! 😅”"}
    )
    text = generator.render(product, lines).telegram_text()
    assert "Meu bolso não aceita desperdício! 😅" in text
    assert "“" not in text
    assert "”" not in text


def test_contextual_fallback_mentions_the_product_and_has_no_html_entities():
    product = NormalizedProduct(
        marketplace="mercado_livre",
        source="fixture",
        item_id="MLB2",
        title="Spray pum peido fedorento para pegadinha",
        product_url="https://www.mercadolivre.com.br/produto/p/MLB2",
        current_price=Decimal("19"),
    )
    lines = GroqCopyGenerator().fallback_lines(product)
    assert "spray" in lines.joke.lower()
    assert "carteira" in lines.joke.lower()
    assert "&#" not in lines.hook + lines.joke + lines.call_to_action


def test_legacy_hook_is_not_rendered_in_telegram_v3():
    product = NormalizedProduct(
        marketplace="mercado_livre",
        source="fixture",
        item_id="MLB3",
        title="Seringa insulina 1ml",
        product_url="https://www.mercadolivre.com.br/produto/p/MLB3",
        current_price=Decimal("21.02"),
    )
    generator = GroqCopyGenerator()
    lines = generator.fallback_lines(product).model_copy(update={"hook": "PENSANDO EM POUPAR?"})
    text = generator.render(product, lines).telegram_text()
    assert text.startswith("🔥 <b>SERINGA INSULINA 1ML</b>")
    assert "PENSANDO EM POUPAR" not in text
    assert "&#x20;" not in text
