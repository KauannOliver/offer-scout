import html
from datetime import datetime
from decimal import Decimal


def offer_summary(title: str, price: Decimal | None) -> str:
    clean_title = html.escape(" ".join(title.split())[:180])
    price_line = money(price) if price is not None else "Consulte na publicação do canal"
    return f"<b>{clean_title}</b>\n💰 {price_line}\n\nAbra a publicação completa no canal para conferir detalhes e condições."


def coupon_summary(
    discount_percent: Decimal | None,
    discount_amount: Decimal | None,
    scope: str | None,
    expires_at: datetime | None,
) -> str:
    if discount_percent is not None:
        discount = f"{Decimal(discount_percent):g}% OFF"
    elif discount_amount is not None:
        discount = f"{money(discount_amount)} DE DESCONTO"
    else:
        discount = "DESCONTO CONFIRMADO"
    lines = [f"🎟️ <b>{discount}</b>", html.escape(scope or "Produtos selecionados")]
    if expires_at:
        lines.append(f"Válido até {expires_at.strftime('%d/%m/%Y')}")
    lines.append("\nO código e as condições completas estão na publicação do canal.")
    return "\n".join(lines)


def money(value: Decimal) -> str:
    whole, cents = f"{Decimal(value):.2f}".split(".")
    return f"R$ {int(whole):,}".replace(",", ".") + f",{cents}"


def contains_commercial_link(text: str) -> bool:
    lowered = text.casefold()
    return any(
        marker in lowered for marker in ("meli.la", "mercadolivre.com", "http://", "https://")
    )


def public_post_url(
    destination: str | None, message_id: str | None, channel_username: str | None = None
) -> str | None:
    if not message_id:
        return None
    username = destination[1:] if destination and destination.startswith("@") else channel_username
    return f"https://t.me/{username}/{message_id}" if username else None


def paginate_categories(
    categories: list[tuple[str, int]], page: int, page_size: int = 6
) -> tuple[list[tuple[str, int]], int, int]:
    safe_size = max(1, min(page_size, 10))
    total_pages = max(1, (len(categories) + safe_size - 1) // safe_size)
    safe_page = max(0, min(page, total_pages - 1))
    start = safe_page * safe_size
    return categories[start : start + safe_size], safe_page, total_pages
