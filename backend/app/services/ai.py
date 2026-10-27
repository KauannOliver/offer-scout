import html
import json
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from backend.app.config import get_settings
from backend.app.domain.types import CouponData, NormalizedProduct


class CreativeLines(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hook: str = Field(default="", max_length=100)
    joke: str = Field(max_length=180)
    call_to_action: str = Field(max_length=90)


class GeneratedLines(BaseModel):
    model_config = ConfigDict(extra="forbid")
    joke: str = Field(max_length=180)
    call_to_action: str = Field(max_length=90)


class OfferCopy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    caption_html: str
    facts: dict
    creative: CreativeLines

    def telegram_text(self, link: str | None = None) -> str:
        if not link:
            return self.caption_html.replace("\n\n👉 <b>PEGUE A OFERTA:</b>\n{LINK}", "")
        return self.caption_html.replace("{LINK}", html.escape(link, quote=True))


class GroqCopyGenerator:
    endpoint = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.settings = get_settings()
        self.client = client or httpx.AsyncClient(timeout=30)

    async def generate(
        self,
        product: NormalizedProduct,
        coupon: CouponData | None = None,
        content_config: dict | None = None,
    ) -> tuple[OfferCopy, str]:
        creative = self.fallback_lines(product)
        generator = "template"
        if self.settings.groq_api_key:
            for _ in range(2):
                try:
                    creative = await self._generate_lines(product, content_config or {})
                    generator = "groq"
                    break
                except (httpx.HTTPError, KeyError, ValueError):
                    continue
        rendered = self.render(product, creative, coupon)
        if rendered.creative != creative:
            generator = "template_safe"
        return rendered, generator

    async def _generate_lines(
        self, product: NormalizedProduct, content_config: dict
    ) -> CreativeLines:
        schema = GeneratedLines.model_json_schema()
        response = await self.client.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.settings.groq_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.settings.groq_model,
                "reasoning_effort": "low",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Escreva apenas duas linhas curtas em português do Brasil para uma oferta no Telegram. "
                            "Use humor original de um pai extremamente econômico, divertido e obcecado por poupar. "
                            "Não cite personagens, atores ou séries e não copie bordões. Use 1 ou 2 emojis "
                            f"no total; o anúncio completo deve ficar entre {content_config.get('emoji_min', 4)} e "
                            f"{content_config.get('emoji_max', 7)} emojis. Não mencione preço, desconto, cupom, estoque, benefício do produto, comissão, "
                            "urgência ou qualquer fato: esses dados serão inseridos por código. "
                            "É proibido usar números, percentuais, frações, vantagens de saúde ou prometer redução "
                            "de consumo/contas. A economia é comparar a compra e evitar impulso, não um efeito do produto. "
                            "A piada precisa mencionar o tipo do produto ou seu uso e relacionar a compra à economia; "
                            "não use frases genéricas que serviriam para qualquer produto. Escreva com gramática e "
                            "concordância corretas do português brasileiro. Não crie título ou gancho, não coloque a piada entre aspas e não "
                            "use entidades HTML. Limites: piada com até 140 caracteres e chamada com até 70."
                        ),
                    },
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "product_title": product.title,
                                "category": product.raw_metadata.get("category")
                                or product.raw_metadata.get("discovery_strategy"),
                                "relevant_terms": _relevant_terms(product),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "creative_lines", "strict": True, "schema": schema},
                },
            },
        )
        response.raise_for_status()
        generated = GeneratedLines.model_validate_json(
            response.json()["choices"][0]["message"]["content"]
        )
        lines = CreativeLines(joke=generated.joke, call_to_action=generated.call_to_action)
        return _validated_lines(lines, product)

    def render(
        self, product: NormalizedProduct, creative: CreativeLines, coupon: CouponData | None = None
    ) -> OfferCopy:
        # Applies to cached legacy creatives as well as freshly generated lines.
        try:
            creative = _validated_lines(creative, product)
        except ValueError:
            creative = self.fallback_lines(product)
        facts = offer_facts(product, coupon)
        blocks = [
            f"🔥 <b>{html.escape(_caps(_short_title(product.title)))}</b>",
        ]
        price_lines: list[str] = []
        if product.previous_price:
            price_lines.append(f"DE: <s>{_money(product.previous_price)}</s>")
        if product.current_price:
            price_lines.append(f"POR: <b>{_money(product.current_price)}</b>")
        if product.discount_percent:
            detail = f"{_percent(product.discount_percent)}% OFF"
            if facts.get("savings"):
                detail += f" — ECONOMIA DE {_money(Decimal(facts['savings']))}"
            price_lines.append(f"✅ <b>{detail}</b>")
        if price_lines:
            blocks.append("\n".join(price_lines))
        if coupon and coupon.eligibility_confirmed and coupon.code:
            conditions = coupon_conditions(coupon)
            coupon_block = f"🎟 <b>CUPOM:</b> <code>{html.escape(coupon.code)}</code>"
            if conditions:
                coupon_block += f"\nCondição: {html.escape(conditions)}"
            blocks.append(coupon_block)
        social: list[str] = []
        if product.rating is not None:
            social.append(f"⭐ {_decimal_pt(product.rating)}")
        if product.sold_text:
            social.append(html.escape(product.sold_text.upper()))
        elif product.sold_count is not None:
            social.append(f"{product.sold_count:,} VENDIDOS".replace(",", "."))
        if social:
            blocks.append(" | ".join(social))
        blocks.extend(
            [
                f"😅 <i>{html.escape(_without_wrapping_quotes(creative.joke))}</i>",
                f"👉 <b>{html.escape(_caps(creative.call_to_action))}</b>\n{{LINK}}",
                "Publicidade • Anúncios • Promoções",
            ]
        )
        caption = "\n\n".join(blocks)
        if len(caption.replace("{LINK}", "https://meli.la/XXXXXXXX")) > 1024:
            blocks[0] = f"🔥 <b>{html.escape(_caps(_short_title(product.title, 55)))}</b>"
            blocks = [block for block in blocks if not block.startswith("⭐")]
            caption = "\n\n".join(blocks)
        return OfferCopy(caption_html=caption, facts=facts, creative=creative)

    def fallback_lines(self, product: NormalizedProduct) -> CreativeLines:
        normalized = _plain(product.title).casefold()
        rules = [
            (
                ("lençol", "lencol", "fronha", "edredom"),
                "ORÇAMENTO ACORDADO! 🛏️",
                "Na roupa de cama, eu olho o preço antes de fechar os olhos. Meu orçamento não dorme em serviço. 😅",
            ),
            (
                ("tenis", "tênis", "sapato", "sandália", "chinelo"),
                "PASSO FIRME NO ORÇAMENTO! 👟",
                "Na compra de calçado, quem precisa dar o primeiro passo é a comparação de preços. A carteira agradece. 😅",
            ),
            (
                ("spray", "pegadinha", "peido"),
                "ATÉ A PEGADINHA ENTROU NO ORÇAMENTO! 😂",
                "Com esse spray, o susto fica para a vítima — não para a carteira. 😅",
            ),
            (
                ("creatina", "suplemento", "whey"),
                "TREINO FORTE, DESPESA LEVE! 💪",
                "A força vai para o treino; o aperto não precisa ir para o bolso. 😅",
            ),
            (
                ("pneu", "compressor", "calibrador"),
                "PREÇO MURCHO? A ECONOMIA ESTÁ CHEIA! 🚗",
                "Na compra para o veículo, eu também calibro o orçamento: impulso não entra nessa conta. 😅",
            ),
            (
                ("cama", "colchão", "travesseiro"),
                "DÁ PARA DESCANSAR ATÉ DO PREÇO! 🛏️",
                "Antes de descansar, eu comparo os preços. Quem não pode perder o sono é o orçamento. 😅",
            ),
            (
                ("celular", "smartphone", "fone"),
                "TECNOLOGIA BOA SEM DAR CURTO NO ORÇAMENTO! 📱",
                "O aparelho pode ser inteligente, mas inteligente mesmo é poupar na compra. 😅",
            ),
        ]
        hook = "ESSA COMPRA CABE NO PLANO DE ECONOMIA! 💸"
        noun = _relevant_terms(product)[0] if _relevant_terms(product) else "produto"
        joke = f"Na compra de {noun}, eu comparo o preço antes de abrir a carteira. Comprar por impulso não ganha minha assinatura. 😅"
        for terms, candidate_hook, candidate_joke in rules:
            if any(term in normalized for term in terms):
                hook, joke = candidate_hook, candidate_joke
                break
        return CreativeLines(
            hook=hook,
            joke=joke,
            call_to_action="CONFIRA A OFERTA E PRESERVE O ORÇAMENTO",
        )

    def fallback(self, product: NormalizedProduct, coupon_line: str | None = None) -> OfferCopy:
        return self.render(product, self.fallback_lines(product))


def offer_facts(product: NormalizedProduct, coupon: CouponData | None = None) -> dict:
    savings = None
    if (
        product.previous_price
        and product.current_price
        and product.previous_price > product.current_price
    ):
        savings = str((product.previous_price - product.current_price).quantize(Decimal("0.01")))
    return {
        "item_id": product.item_id,
        "title": product.title,
        "previous_price": str(product.previous_price)
        if product.previous_price is not None
        else None,
        "current_price": str(product.current_price) if product.current_price is not None else None,
        "discount_percent": str(product.discount_percent)
        if product.discount_percent is not None
        else None,
        "savings": savings,
        "rating": str(product.rating) if product.rating is not None else None,
        "sold_count": product.sold_count,
        "coupon_id": coupon.marketplace_coupon_id if coupon else None,
        "coupon_code": coupon.code if coupon and coupon.eligibility_confirmed else None,
        "coupon_discount_percent": str(coupon.discount_percent)
        if coupon and coupon.eligibility_confirmed and coupon.discount_percent is not None
        else None,
    }


def coupon_conditions(coupon: CouponData) -> str:
    parts: list[str] = []
    if coupon.discount_percent:
        parts.append(f"{_percent(coupon.discount_percent)}% de desconto")
    elif coupon.discount_amount:
        parts.append(f"{_money(coupon.discount_amount)} de desconto")
    if coupon.minimum_purchase:
        parts.append(f"em compras a partir de {_money(coupon.minimum_purchase)}")
    if coupon.maximum_discount:
        parts.append(f"limite de {_money(coupon.maximum_discount)}")
    return " • ".join(parts)


def _money(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole, cents = f"{normalized:.2f}".split(".")
    return f"R$ {f'{int(whole):,}'.replace(',', '.')},{cents}"


def _percent(value: Decimal) -> str:
    return _decimal_pt(value.quantize(Decimal("0.1"))).removesuffix(",0")


def _decimal_pt(value: Decimal) -> str:
    return format(value, "f").replace(".", ",")


def _caps(value: str) -> str:
    return value.strip().upper()


def _short_title(value: str, limit: int = 85) -> str:
    clean = " ".join(value.split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def _without_wrapping_quotes(value: str) -> str:
    clean = _plain(value)
    quote_pairs = (("“", "”"), ('"', '"'), ("‘", "’"), ("'", "'"))
    while len(clean) >= 2:
        pair = next(
            (
                (opening, closing)
                for opening, closing in quote_pairs
                if clean.startswith(opening) and clean.endswith(closing)
            ),
            None,
        )
        if pair is None:
            break
        clean = clean[1:-1].strip()
    return clean


STOPWORDS = {
    "para",
    "com",
    "sem",
    "produto",
    "oficial",
    "novo",
    "nova",
    "kit",
    "cor",
    "de",
    "da",
    "do",
    "em",
    "e",
}


def _plain(value: str) -> str:
    return " ".join(html.unescape(value).replace("\xa0", " ").split())


def _fold(value: str) -> str:
    return "".join(
        char
        for char in unicodedata.normalize("NFD", value.casefold())
        if unicodedata.category(char) != "Mn"
    )


def _relevant_terms(product: NormalizedProduct) -> list[str]:
    words = re.findall(r"[a-záéíóúâêôãõç]{4,}", _plain(product.title).casefold())
    return list(dict.fromkeys(word for word in words if word not in STOPWORDS))[:8]


def _validated_lines(lines: CreativeLines, product: NormalizedProduct) -> CreativeLines:
    clean = CreativeLines(
        hook=_without_wrapping_quotes(lines.hook),
        joke=_without_wrapping_quotes(lines.joke),
        call_to_action=_without_wrapping_quotes(lines.call_to_action).rstrip(" :!?."),
    )
    combined = _fold(clean.joke)
    free_text = _fold(clean.joke + " " + clean.call_to_action)
    if re.search(r"\d|%|r\$|https?://|por cento|pela metade|metade do|dobro|conta de luz|economiz\w* energia|cura\w*|emagrec\w*|dosagem|propria dose", free_text):
        raise ValueError("Creative contains an unverified number, link or product benefit")
    terms = [_fold(term) for term in _relevant_terms(product)]
    if "&#" in lines.hook + lines.joke + lines.call_to_action:
        raise ValueError("Creative contains HTML entities")
    if terms and not any(term in combined for term in terms):
        raise ValueError("Creative is not related to the product")
    if not re.search(r"econom|poup|bolso|carteira|orcamento|preco|barat|despesa", combined):
        raise ValueError("Creative is not related to saving money")
    if len(clean.joke) < 20 or len(clean.call_to_action) < 8:
        raise ValueError("Creative is too generic")
    return clean
