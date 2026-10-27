import re
from urllib.parse import urlparse

SHORT_LINK_RE = re.compile(r"^https://meli\.la/[A-Za-z0-9_-]{4,64}$")
ITEM_PATH_RE = re.compile(r"/(?:p/MLB-?\d{6,}|up/MLBU\d{6,}|MLB-?\d{6,})", re.IGNORECASE)
BLOCKED_SEGMENTS = {
    "search",
    "ofertas",
    "ofertas-do-dia",
    "categorias",
    "mais-vendidos",
    "carrinho",
    "checkout",
    "seller",
    "loja",
    "veiculos",
    "imoveis",
}


def is_eligible_product_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "www.mercadolivre.com.br",
        "produto.mercadolivre.com.br",
    }:
        return False
    parts = {part.lower() for part in parsed.path.split("/") if part}
    if parts & BLOCKED_SEGMENTS:
        return False
    return bool(ITEM_PATH_RE.search(parsed.path))


def is_official_short_link(url: str) -> bool:
    return bool(SHORT_LINK_RE.fullmatch(url.strip()))
