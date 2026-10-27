import html
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.app.models import Product, ProductSnapshot

DEFAULT_MANIFEST = """🛡️ <b>COMO FUNCIONA O RADAR DO JULIUS</b>

Nós pesquisamos ofertas, registramos o histórico de preço e revalidamos preço e disponibilidade antes de publicar.

✅ O score prioriza economia real, confiança dos dados, popularidade e avaliação.
✅ Cupons só aparecem quando a aplicação foi confirmada na interface oficial.
✅ Os links comerciais são gerados pelas ferramentas oficiais e ficam apenas neste canal público.
✅ Você não paga nada a mais por usar o link; o canal pode receber uma comissão pela compra.

Também mostramos quando uma oferta <b>não compensa naquele momento</b>, sempre com motivos financeiros verificáveis — sem avaliar qualidade, segurança ou eficácia do produto.

🤖 Para navegar pelas últimas publicações por categoria, fale com o bot no privado."""

DEFAULT_WEEKLY_REMINDER = """🛡️ <b>TRANSPARÊNCIA TAMBÉM FAZ PARTE DA ECONOMIA</b>

O radar compara histórico, score e dados revalidados antes de publicar. Cupons só entram quando estão confirmados, e os links comerciais ficam exclusivamente nas mensagens deste canal.

Consulte o manifesto fixado para conhecer todos os critérios. 🤖"""


@dataclass(frozen=True)
class BadOfferEvidence:
    eligible: bool
    reasons: tuple[str, ...]
    observation_count: int
    history_hours: float
    median_price: Decimal | None
    current_price: Decimal | None
    latest_observed_at: datetime | None
    price_confidence: float
    blocking_reasons: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "eligible": self.eligible,
            "reasons": list(self.reasons),
            "observation_count": self.observation_count,
            "history_hours": round(self.history_hours, 1),
            "median_price": str(self.median_price) if self.median_price is not None else None,
            "current_price": str(self.current_price) if self.current_price is not None else None,
            "latest_observed_at": self.latest_observed_at.isoformat()
            if self.latest_observed_at
            else None,
            "price_confidence": round(self.price_confidence, 2),
            "blocking_reasons": list(self.blocking_reasons),
        }


def evaluate_bad_offer(
    snapshots: list[ProductSnapshot],
    score: Decimal,
    *,
    maximum_score: Decimal = Decimal("45"),
    min_observations: int = 3,
    min_history_hours: int = 24,
    min_reasons: int = 2,
    minimum_confidence: float = 0.7,
) -> BadOfferEvidence:
    ordered = sorted(
        (item for item in snapshots if item.price is not None), key=lambda item: item.observed_at
    )
    if not ordered:
        return BadOfferEvidence(False, (), 0, 0, None, None, None, 0, ("Sem histórico de preço.",))
    first, latest = ordered[0], ordered[-1]
    history_hours = max(0.0, (latest.observed_at - first.observed_at).total_seconds() / 3600)
    prices = [Decimal(item.price) for item in ordered]
    median_price = Decimal(str(statistics.median(prices))).quantize(Decimal("0.01"))
    current_price = Decimal(latest.price)
    metrics = latest.metrics or {}
    reasons: list[str] = []
    if current_price >= median_price:
        reasons.append("O preço atual está igual ou acima da mediana observada no histórico.")
    discount = latest.discount_percent
    if discount is not None and Decimal(discount) < Decimal("5"):
        reasons.append("O desconto confirmado é inferior a 5%.")
    confidence = float(metrics.get("price_confidence", 0.6))
    drop = metrics.get("drop_from_last_percent")
    if drop is not None and Decimal(str(drop)) <= Decimal("0"):
        reasons.append("Não houve queda real de preço desde a observação anterior.")
    blocking: list[str] = []
    if score > maximum_score:
        blocking.append("Score acima do máximo editorial.")
    if len(ordered) < min_observations:
        blocking.append("Histórico ainda tem poucas observações.")
    if history_hours < min_history_hours:
        blocking.append("Período histórico ainda é insuficiente.")
    if confidence < minimum_confidence:
        blocking.append("Confiança do histórico abaixo do mínimo editorial.")
    if len(reasons) < min_reasons:
        blocking.append("Ainda faltam motivos financeiros objetivos.")
    eligible = bool(
        score <= maximum_score
        and len(ordered) >= min_observations
        and history_hours >= min_history_hours
        and confidence >= minimum_confidence
        and len(reasons) >= min_reasons
    )
    return BadOfferEvidence(
        eligible,
        tuple(reasons[:3]),
        len(ordered),
        history_hours,
        median_price,
        current_price,
        latest.observed_at,
        confidence,
        tuple(blocking),
    )


def render_bad_offer(product: Product, score: Decimal, evidence: BadOfferEvidence) -> str:
    title = " ".join(product.title.split())[:95].upper()
    blocks = [f"🔎 <b>{html.escape(title)}</b>", "🚫 <b>NÃO COMPENSA AGORA</b>"]
    price_lines: list[str] = []
    if evidence.current_price is not None:
        price_lines.append(f"Preço atual: <b>{money(evidence.current_price)}</b>")
    if evidence.median_price is not None:
        price_lines.append(f"Mediana observada: <b>{money(evidence.median_price)}</b>")
    price_lines.append(f"Score do radar: <b>{Decimal(score):.0f}/100</b>")
    blocks.append("\n".join(price_lines))
    blocks.append(
        "📉 <b>POR QUE ESPERAR:</b>\n"
        + "\n".join(f"• {html.escape(reason)}" for reason in evidence.reasons)
    )
    blocks.append(
        "💡 Nossa recomendação financeira é <b>não comprar neste momento</b>. "
        "Espere uma queda real ou compare com ofertas melhor avaliadas pelo radar."
    )
    blocks.append("Análise financeira • sem link do produto")
    return "\n\n".join(blocks)[:1024]


def money(value: Decimal) -> str:
    whole, cents = f"{Decimal(value):.2f}".split(".")
    return f"R$ {int(whole):,}".replace(",", ".") + f",{cents}"


def is_fresh(observed_at: datetime | None, minutes: int) -> bool:
    return bool(observed_at and observed_at >= datetime.now(UTC) - timedelta(minutes=minutes))
