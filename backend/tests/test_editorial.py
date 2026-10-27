from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from backend.app.services.bot_content import (
    contains_commercial_link,
    coupon_summary,
    offer_summary,
    paginate_categories,
    public_post_url,
)
from backend.app.services.editorial import evaluate_bad_offer, render_bad_offer


def snapshot(hours_ago: int, price: str, discount: str = "0", confidence: float = 0.8):
    return SimpleNamespace(
        observed_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        price=Decimal(price),
        discount_percent=Decimal(discount),
        metrics={"price_confidence": confidence, "drop_from_last_percent": 0},
    )


def test_bad_offer_requires_history_and_two_financial_reasons():
    evidence = evaluate_bad_offer(
        [snapshot(30, "90"), snapshot(25, "95"), snapshot(0, "100")], Decimal("35")
    )
    assert evidence.eligible is True
    assert evidence.observation_count == 3
    assert evidence.history_hours >= 24
    assert len(evidence.reasons) >= 2


def test_bad_offer_is_skipped_with_insufficient_history():
    evidence = evaluate_bad_offer(
        [snapshot(2, "90"), snapshot(1, "95"), snapshot(0, "100")], Decimal("35")
    )
    assert evidence.eligible is False


def test_bad_offer_is_blocked_when_price_history_has_low_confidence():
    evidence = evaluate_bad_offer(
        [
            snapshot(30, "90", confidence=0.4),
            snapshot(25, "95", confidence=0.4),
            snapshot(0, "100", confidence=0.4),
        ],
        Decimal("35"),
    )
    assert evidence.eligible is False
    assert "Confiança do histórico abaixo do mínimo editorial." in evidence.blocking_reasons
    assert all("confiança" not in reason.casefold() for reason in evidence.reasons)


def test_bad_offer_caption_has_no_product_link_or_quality_claim():
    product = SimpleNamespace(title="Produto teste")
    evidence = evaluate_bad_offer(
        [snapshot(30, "90"), snapshot(25, "95"), snapshot(0, "100")], Decimal("35")
    )
    text = render_bad_offer(product, Decimal("35"), evidence)
    assert "NÃO COMPENSA AGORA" in text
    assert "não comprar neste momento" in text
    assert not contains_commercial_link(text)
    assert "qualidade" not in text.casefold()
    assert "segurança" not in text.casefold()


def test_private_bot_content_never_contains_commercial_link_or_coupon_code():
    offer = offer_summary("Produto teste", Decimal("19.90"))
    coupon = coupon_summary(Decimal("15"), None, "Produtos selecionados", None)
    assert not contains_commercial_link(offer)
    assert not contains_commercial_link(coupon)
    assert "EXEMPLO" not in coupon


def test_public_post_url_supports_numeric_channel_destination():
    assert (
        public_post_url("-1001234567890", "87", "canal_exemplo")
        == "https://t.me/canal_exemplo/87"
    )
    assert public_post_url("@canal_exemplo", "134") == "https://t.me/canal_exemplo/134"


def test_category_pagination_clamps_pages_and_preserves_all_items():
    categories = [(f"Categoria {index}", index) for index in range(8)]
    first, page, total = paginate_categories(categories, 0, 6)
    second, second_page, _ = paginate_categories(categories, 99, 6)
    assert (len(first), page, total) == (6, 0, 2)
    assert (len(second), second_page) == (2, 1)
    assert first + second == categories
