from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.app.domain.types import NormalizedProduct
from backend.app.services.browser import MercadoLivreBrowserService
from backend.app.services.pipeline import _price_metrics


def offer(item_id: str, price: str = "80") -> NormalizedProduct:
    return NormalizedProduct(
        marketplace="mercado_livre",
        source="affiliate_hub_dom",
        item_id=item_id,
        title=item_id,
        product_url=f"https://www.mercadolivre.com.br/p/MLB12345678?wid={item_id}",
        current_price=Decimal(price),
    )


class FakeParser:
    def parse(self, html: str) -> list[NormalizedProduct]:
        index = int(html)
        start = index * 18
        return [offer(f"MLB{1_000_000 + value}") for value in range(start, start + 18)]


class FakeLocator:
    @property
    def last(self):
        return self

    async def count(self):
        return 18

    async def scroll_into_view_if_needed(self):
        return None


class FakePage:
    def __init__(self):
        self.index = 0

    async def content(self):
        return str(self.index)

    def locator(self, _selector):
        return FakeLocator()

    async def evaluate(self, _script):
        self.index += 1

    async def wait_for_timeout(self, _milliseconds):
        return None

    async def wait_for_load_state(self, _state, **_kwargs):
        return None


@pytest.mark.asyncio
async def test_virtualized_batches_are_accumulated_before_old_cards_disappear():
    service = MercadoLivreBrowserService.__new__(MercadoLivreBrowserService)
    service.settings = SimpleNamespace(
        discovery_max_scrolls=20,
        discovery_stable_rounds=3,
        discovery_timeout_seconds=45,
    )
    service.parser = FakeParser()

    async def authenticated(_page):
        return "AUTHENTICATED"

    service._detect_auth = authenticated
    products, metrics = await service._collect_virtualized_products(FakePage(), "relevant", 100)
    assert len(products) == 100
    assert len({product.item_id for product in products}) == 100
    assert metrics["cards_seen"] >= 108
    assert metrics["stop_reason"] == "item_limit"


def test_price_confidence_penalizes_inflated_claim_and_rewards_real_low_price():
    suspicious = offer("MLB1000001", "120")
    suspicious.previous_price = Decimal("200")
    suspicious.discount_percent = Decimal("40")
    suspicious_metrics = _price_metrics([Decimal("100"), Decimal("101")], suspicious)
    assert suspicious_metrics["price_confidence"] == 0.25

    real = offer("MLB1000002", "80")
    real.previous_price = Decimal("100")
    real.discount_percent = Decimal("20")
    real_metrics = _price_metrics([Decimal("100"), Decimal("95")], real)
    assert real_metrics["price_confidence"] == 1.0
