import html as html_lib
import json
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from backend.app.domain.types import NormalizedProduct

SOLD_RE = re.compile(r"\+?\s*([\d.,]+)\s*(mil|mi(?:lh(?:ão|ões))?)?", re.IGNORECASE)
ITEM_RE = re.compile(r"\b(MLB\d{6,})\b", re.IGNORECASE)
PRODUCT_RE = re.compile(r"/p/(MLB\d{6,})", re.IGNORECASE)


def parse_sold_count(value: str | None) -> int | None:
    if not value:
        return None
    match = SOLD_RE.search(value.lower().replace("vendidos", "").strip())
    if not match:
        return None
    raw = match.group(1).replace(".", "").replace(",", ".")
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return None
    suffix = (match.group(2) or "").lower()
    multiplier = 1_000 if suffix == "mil" else 1_000_000 if suffix.startswith("mi") else 1
    result = number * multiplier
    return int(result) if result >= 0 else None


def parse_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    match = re.search(r"-?[\d.]+(?:,\d+)?", value)
    if not match:
        return None
    raw = match.group(0)
    normalized = raw.replace(".", "").replace(",", ".") if "," in raw else raw
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def _money_from_tag(tag: Tag | None) -> Decimal | None:
    if not tag:
        return None
    label = tag.get("aria-label")
    if label:
        match = re.search(r"(\d+)\s+reais(?:\s+com\s+(\d+)\s+centavos)?", label)
        if match:
            return Decimal(match.group(1)) + Decimal(match.group(2) or 0) / 100
    fraction = tag.select_one("[data-andes-money-amount-fraction]")
    cents = tag.select_one("[data-andes-money-amount-cents]")
    if fraction:
        return (
            Decimal(fraction.get_text(strip=True))
            + Decimal(cents.get_text(strip=True) if cents else "0") / 100
        )
    return parse_decimal(tag.get_text(" ", strip=True))


def _extract_item_id(card: Tag, url: str) -> str | None:
    hidden = card.select_one('[data-testid="form-data-id"]')
    if hidden and hidden.get("value"):
        return str(hidden["value"])
    fragment = parse_qs(urlparse(url).fragment)
    if fragment.get("wid"):
        return fragment["wid"][0]
    match = ITEM_RE.search(url)
    return match.group(1).upper() if match else None


class MercadoLivreAffiliateHubParser:
    """Parse server-rendered cards, using embedded structured data before DOM fallback."""

    def parse(self, html: str) -> list[NormalizedProduct]:
        products = self._parse_structured_data(html)
        dom_products = self._parse_dom(html)
        by_item = {product.item_id: product for product in products}
        for product in dom_products:
            existing = by_item.get(product.item_id)
            if not existing:
                by_item[product.item_id] = product
                continue
            for field_name in product.__dataclass_fields__:
                current = getattr(existing, field_name)
                incoming = getattr(product, field_name)
                if current in (None, "", {}, False) and incoming not in (None, "", {}):
                    setattr(existing, field_name, incoming)
        return list(by_item.values())

    def _parse_structured_data(self, html: str) -> list[NormalizedProduct]:
        soup = BeautifulSoup(html, "lxml")
        candidates: list[dict] = []
        for script in soup.find_all("script"):
            text = script.string or script.get_text()
            if not text or "recommend" not in text.lower() or "MLB" not in text:
                continue
            decoded = html_lib.unescape(text).strip()
            values: list[object] = []
            if decoded.startswith(("{", "[")):
                try:
                    values.append(json.loads(decoded))
                except json.JSONDecodeError:
                    pass
            for match in re.finditer(r"\{[^{}]{0,4000}\"(?:item_id|id)\"[^{}]{0,4000}\}", decoded):
                try:
                    values.append(json.loads(match.group(0)))
                except json.JSONDecodeError:
                    continue
            for value in values:
                self._walk(value, candidates)

        products: list[NormalizedProduct] = []
        for data in candidates:
            item_id = str(data.get("item_id") or data.get("id") or "").upper()
            title = data.get("title") or data.get("name")
            url = data.get("permalink") or data.get("url") or data.get("product_url")
            if not ITEM_RE.fullmatch(item_id) or not title or not url:
                continue
            products.append(
                NormalizedProduct(
                    marketplace="mercado_livre",
                    source="affiliate_hub_structured",
                    item_id=item_id,
                    product_id=str(data.get("product_id") or "") or None,
                    title=str(title),
                    product_url=str(url),
                    image_url=self._picture_url(data),
                    current_price=parse_decimal(str(data.get("price") or "")),
                    raw_metadata={"structured": data},
                )
            )
        return products

    def _walk(self, value: object, output: list[dict]) -> None:
        if isinstance(value, dict):
            if any(key in value for key in ("item_id", "product_id", "permalink")):
                output.append(value)
            for child in value.values():
                self._walk(child, output)
        elif isinstance(value, list):
            for child in value:
                self._walk(child, output)

    def _picture_url(self, data: dict) -> str | None:
        picture = data.get("picture") or data.get("thumbnail") or data.get("image")
        if isinstance(picture, str):
            return picture
        if isinstance(picture, dict):
            return picture.get("url") or picture.get("src")
        pictures = data.get("pictures")
        if isinstance(pictures, list) and pictures:
            first = pictures[0]
            return first if isinstance(first, str) else first.get("url")
        return None

    def _parse_dom(self, html: str) -> list[NormalizedProduct]:
        soup = BeautifulSoup(html, "lxml")
        products: list[NormalizedProduct] = []
        for card in soup.select("li.poly-card, .poly-card"):
            title_node = card.select_one("a.poly-component__title")
            if not title_node or not title_node.get("href"):
                continue
            url = html_lib.unescape(str(title_node["href"]))
            item_id = _extract_item_id(card, url)
            if not item_id:
                continue
            product_match = PRODUCT_RE.search(url)
            review = card.select_one(".poly-component__review-compacted")
            review_text = review.get_text(" ", strip=True) if review else ""
            rating_match = re.search(r"\b([0-5][.,]\d)\b", review_text)
            sold_match = re.search(
                r"(\+?\s*[\d.,]+\s*(?:mil|milhão|milhões)?\s+vendidos)", review_text, re.I
            )
            commission_node = card.select_one(".poly-component__chip")
            commission_match = re.search(
                r"GANHOS\s*([\d.,]+)%",
                commission_node.get_text(" ", strip=True) if commission_node else "",
                re.I,
            )
            previous = _money_from_tag(card.select_one(".poly-price__previous"))
            current = _money_from_tag(card.select_one(".poly-price__amount"))
            discount_node = card.select_one(".poly-price__disc-label")
            image = card.select_one('[data-testid="picture"], img.poly-component__picture')
            highlight = card.select_one(".poly-component__highlight")
            sold_text = sold_match.group(1).strip() if sold_match else None
            products.append(
                NormalizedProduct(
                    marketplace="mercado_livre",
                    source="affiliate_hub_dom",
                    item_id=item_id,
                    product_id=product_match.group(1).upper() if product_match else None,
                    title=title_node.get_text(" ", strip=True),
                    product_url=url,
                    image_url=str(image.get("src")) if image and image.get("src") else None,
                    rating=parse_decimal(rating_match.group(1)) if rating_match else None,
                    sold_text=sold_text,
                    sold_count=parse_sold_count(sold_text),
                    highlight=highlight.get_text(" ", strip=True) if highlight else None,
                    commission_percent=parse_decimal(commission_match.group(1))
                    if commission_match
                    else None,
                    extra_commission="extra_comm=true" in url.lower(),
                    brand_commission="brand_comm=true" in url.lower(),
                    previous_price=previous,
                    current_price=current,
                    discount_percent=parse_decimal(discount_node.get_text(" ", strip=True))
                    if discount_node
                    else None,
                    raw_metadata={"parser": "dom"},
                )
            )
        return products
