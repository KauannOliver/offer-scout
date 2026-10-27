import re
from decimal import Decimal, InvalidOperation

RATE_SCRIPT = """
local minute = tonumber(redis.call('get', KEYS[1]) or '0')
local day = tonumber(redis.call('get', KEYS[2]) or '0')
if minute >= tonumber(ARGV[1]) or day >= tonumber(ARGV[2]) then return 0 end
redis.call('incr', KEYS[1]); redis.call('expire', KEYS[1], 120)
redis.call('incr', KEYS[2]); redis.call('expire', KEYS[2], 172800)
return 1
"""

# Cleanup and toggle are one operation: concurrent callbacks cannot lose updates.
FAVORITE_SCRIPT = """
redis.call('zremrangebyscore', KEYS[1], '-inf', ARGV[1])
if redis.call('zscore', KEYS[1], ARGV[2]) then
  redis.call('zrem', KEYS[1], ARGV[2]); return 0
end
if redis.call('zcard', KEYS[1]) >= 100 then return -1 end
redis.call('zadd', KEYS[1], tonumber(ARGV[1]) + 604800, ARGV[2])
redis.call('expire', KEYS[1], 604800)
return 1
"""


def published_offer(payload: dict) -> tuple[str, Decimal | None, str | None]:
    facts = payload.get("facts") or {}
    title = str(facts.get("title") or "Oferta publicada no canal")
    try:
        price = Decimal(str(facts.get("current_price")))
        if not price.is_finite() or price <= 0:
            price = None
    except (InvalidOperation, TypeError, ValueError):
        price = None
    return title, price, payload.get("image_source_url")


def parse_search(query: str) -> tuple[list[str], Decimal | None, Decimal | None]:
    """Explicit min:/max: syntax; not unrestricted natural language."""
    bounds = {}
    for key, value in re.findall(r"\b(min|max):\s*(\d+(?:[.,]\d{1,2})?)", query, re.I):
        bounds[key.lower()] = Decimal(value.replace(",", "."))
    text = re.sub(r"\b(?:min|max):\s*\d+(?:[.,]\d{1,2})?", "", query, flags=re.I)
    return text.split()[:10], bounds.get("min"), bounds.get("max")
