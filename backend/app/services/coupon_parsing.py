import hashlib
import re
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from backend.app.domain.types import CouponData

MONTHS = {
    "janeiro": 1,
    "fevereiro": 2,
    "março": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


def parse_generated_coupon(
    *,
    code_text: str,
    discount_text: str,
    category_text: str,
    expiry_text: str,
    conditions_text: str,
    budget_text: str | None,
    eligible_product_ids: list[str] | None = None,
    eligible_products_count: int = 0,
    products_view_url: str | None = None,
    now: datetime | None = None,
) -> CouponData:
    conditions = _clean(conditions_text)
    category = _clean(category_text) or "Produtos selecionados"
    code_match = re.search(r"#?([A-Z0-9][A-Z0-9_-]{3,99})", code_text.upper())
    code = code_match.group(1) if code_match else None
    percent_match = re.search(r"(\d{1,3}(?:[.,]\d+)?)\s*%", discount_text)
    amount_match = re.search(r"R\$\s*([\d.,]+)", discount_text, re.I)
    date_range = re.search(r"de\s+(\d{2}/\d{2}/\d{4})\s+a\s+(\d{2}/\d{2}/\d{4})", conditions, re.I)
    starts_at = _numeric_date(date_range.group(1), end_of_day=False) if date_range else None
    expires_at = (
        _numeric_date(date_range.group(2), end_of_day=True)
        if date_range
        else _natural_expiry(expiry_text, now=now)
    )
    minimum = _money_match(
        conditions,
        r"(?:compras?\s+m[ií]nimas?.{0,100}?(?:igual ou superior a|de)|compra m[ií]nima\s*:)\s*R\$\s*([\d.,]+)",
    )
    maximum = _money_match(
        conditions,
        r"(?:m[aá]ximo de desconto(?: de)?|desconto m[aá]ximo\s*:)\s*R\$\s*([\d.,]+)",
    )
    official = re.search(r"\bID\s*(\d{5,})\b", conditions, re.I)
    stable_seed = "|".join((code or "", discount_text, category, conditions))
    external_id = (
        f"meli-{official.group(1)}"
        if official
        else "dom-" + hashlib.sha256(stable_seed.encode()).hexdigest()[:24]
    )
    eligible_ids = _eligible_ids(eligible_product_ids)
    restrictions = _restrictions(conditions, category)
    stock = re.search(r"estoque de\s+([\d.]+)\s+cupons", conditions, re.I)
    availability = f"Estoque informado: {stock.group(1)} cupons" if stock else None
    return CouponData(
        marketplace_coupon_id=external_id,
        title=f"{_clean(discount_text)} • {category}",
        code=code,
        discount_percent=_decimal(percent_match.group(1)) if percent_match else None,
        discount_amount=_decimal(amount_match.group(1))
        if amount_match and not percent_match
        else None,
        minimum_purchase=minimum,
        maximum_discount=maximum,
        starts_at=starts_at,
        expires_at=expires_at,
        availability_text=availability,
        restrictions=restrictions,
        eligibility_confirmed=bool(eligible_ids),
        raw_metadata={
            "source": "official_affiliate_coupons_ui",
            "source_kind": "affiliate_generated",
            "shareable": bool(code),
            "activation_status": "GENERATED",
            "official_coupon_id": official.group(1) if official else None,
            "scope": category,
            "discount_label": _clean(discount_text),
            "expiry_label": _clean(expiry_text),
            "remaining_budget": _clean(budget_text or "") or None,
            "conditions_text": conditions[:4_000],
            "products_view_url": products_view_url,
            "eligible_product_ids": eligible_ids,
            "eligible_products_count": eligible_products_count,
            "applicability_source": "official_coupon_products_view" if eligible_ids else None,
        },
    )


def parse_marketplace_coupon(
    *,
    title_text: str,
    scope_text: str,
    minimum_text: str | None,
    maximum_text: str | None,
    expiry_text: str,
    action_text: str,
    conditions_text: str = "",
    code_text: str | None = None,
    eligible_product_ids: list[str] | None = None,
    eligible_products_count: int = 0,
    products_view_url: str | None = None,
    now: datetime | None = None,
) -> CouponData:
    title = _clean(title_text)
    scope = _clean(scope_text) or "Escopo não informado"
    percent = re.search(r"(\d{1,3}(?:[.,]\d+)?)\s*%", title)
    amount = re.search(r"R\$\s*([\d.,]+)\s*OFF", title, re.I)
    code_match = re.search(r"#?([A-Z0-9][A-Z0-9_-]{3,99})", (code_text or "").upper())
    code = code_match.group(1) if code_match else None
    conditions = _clean(conditions_text)
    eligible_ids = _eligible_ids(eligible_product_ids)
    fingerprint = hashlib.sha256(
        "|".join((title, scope, conditions, expiry_text)).encode()
    ).hexdigest()
    action = _clean(action_text).upper()
    shareable = bool(
        code and ("COMPARTILH" in conditions.upper() or "CÓDIGO" in conditions.upper())
    )
    return CouponData(
        marketplace_coupon_id=f"marketplace-{fingerprint[:24]}",
        title=title,
        code=code,
        discount_percent=_decimal(percent.group(1)) if percent else None,
        discount_amount=_decimal(amount.group(1)) if amount and not percent else None,
        minimum_purchase=_money_match(minimum_text or "", r"R\$\s*([\d.,]+)"),
        maximum_discount=_money_match(maximum_text or "", r"R\$\s*([\d.,]+)"),
        expires_at=_natural_expiry(expiry_text, now=now),
        restrictions=_restrictions(conditions, scope),
        eligibility_confirmed=bool(eligible_ids),
        raw_metadata={
            "source": "official_marketplace_coupons_ui",
            "source_kind": "marketplace_general",
            "scope": scope,
            "action": action,
            "activation_status": "ACTIVATED" if "CONFERIR" in action else "AVAILABLE",
            "shareable": shareable,
            "conditions_text": conditions[:4_000],
            "products_view_url": products_view_url,
            "eligible_product_ids": eligible_ids,
            "eligible_products_count": eligible_products_count,
            "applicability_source": "official_coupon_products_view" if eligible_ids else None,
        },
    )


def _clean(value: str) -> str:
    return " ".join((value or "").split())


def _decimal(value: str) -> Decimal:
    clean = re.sub(r"[^\d,.]", "", value).strip(".,")
    if "," in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif clean.count(".") > 1 or ("." in clean and len(clean.rsplit(".", 1)[1]) == 3):
        clean = clean.replace(".", "")
    return Decimal(clean)


def _money_match(text: str, pattern: str) -> Decimal | None:
    match = re.search(pattern, text, re.I)
    return _decimal(match.group(1)) if match else None


def _eligible_ids(values: list[str] | None) -> list[str]:
    return sorted(
        {value.upper() for value in values or [] if re.fullmatch(r"MLB\d{6,}", value, re.I)}
    )


def _numeric_date(value: str, *, end_of_day: bool) -> datetime:
    parsed = datetime.strptime(value, "%d/%m/%Y").replace(tzinfo=SAO_PAULO)
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59)
    return parsed.astimezone(UTC)


def _natural_expiry(value: str, *, now: datetime | None = None) -> datetime | None:
    match = re.search(r"(\d{1,2})\s+de\s+([a-zç]+)", value, re.I)
    if not match or match.group(2).casefold() not in MONTHS:
        return None
    reference = (now or datetime.now(UTC)).astimezone(SAO_PAULO)
    month = MONTHS[match.group(2).casefold()]
    year = reference.year + ((month, int(match.group(1))) < (reference.month, reference.day))
    return datetime(year, month, int(match.group(1)), 23, 59, 59, tzinfo=SAO_PAULO).astimezone(UTC)


def _restrictions(conditions: str, category: str) -> list[str]:
    lowered = conditions.casefold()
    result = [category]
    if "produtos selecionados" in lowered:
        result.append("Somente produtos selecionados pelo Mercado Livre")
    if "1 (um) uso por cpf" in lowered or "1 uso por cpf" in lowered:
        result.append("Limite de 1 uso por CPF")
    if "enquanto durar" in lowered:
        result.append("Enquanto durarem os estoques")
    if "aplicado automaticamente" in lowered:
        result.append("Aplicação automática no carrinho elegível")
    return list(dict.fromkeys(result))
