import pytest

from backend.app.services.urls import is_eligible_product_url, is_official_short_link


@pytest.mark.parametrize(
    "url",
    [
        "https://www.mercadolivre.com.br/produto/p/MLB18725310",
        "https://produto.mercadolivre.com.br/MLB-2766771378-produto-_JM",
        "https://www.mercadolivre.com.br/lavadora/up/MLBU605239077?pdp_filters=item_id%3AMLB1",
    ],
)
def test_eligible_product_urls(url):
    assert is_eligible_product_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.mercadolivre.com.br/",
        "https://www.mercadolivre.com.br/ofertas-do-dia",
        "https://www.mercadolivre.com.br/search?q=creatina",
        "https://example.com/p/MLB18725310",
    ],
)
def test_blocked_urls(url):
    assert not is_eligible_product_url(url)


def test_short_link_is_validated_without_requesting_it():
    assert is_official_short_link("https://meli.la/2w1oFtz")
    assert not is_official_short_link("http://meli.la/2w1oFtz")
    assert not is_official_short_link("https://evil.example/2w1oFtz")
