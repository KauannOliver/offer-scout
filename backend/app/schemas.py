import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    username: str
    csrf_token: str


class SnapshotResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    previous_price: Decimal | None
    price: Decimal | None
    discount_percent: Decimal | None
    rating: Decimal | None
    sold_text: str | None
    sold_count: int | None
    commission_percent: Decimal | None
    highlight: str | None
    observed_at: datetime


class ProductResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    marketplace: str
    source: str
    item_id: str
    product_id: str | None
    title: str
    product_url: str
    image_url: str | None
    active: bool
    created_at: datetime
    updated_at: datetime
    snapshots: list[SnapshotResponse] = Field(default_factory=list)


class QueueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    product_id: uuid.UUID
    product_title: str
    product_item_id: str
    product_image_url: str | None
    status: str
    scheduled_for: datetime | None
    attempts: int
    last_error: str | None
    created_at: datetime


class SettingUpdate(BaseModel):
    value: dict


class SettingsBatchUpdate(BaseModel):
    expected_revision: int | None = Field(default=None, ge=0)
    values: dict[str, dict] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_operational_values(self):
        import math
        def finite(value):
            if isinstance(value, dict):
                for entry in value.values():
                    finite(entry)
            elif isinstance(value, list):
                for entry in value:
                    finite(entry)
            elif isinstance(value, (float, int)) and not math.isfinite(value):
                raise ValueError("Valores numéricos devem ser finitos")
        finite(self.values)
        bounds = {
            "retention": {"days": (90, 3650)},
            "channel_experience": {"weekly_day": (1, 7), "global_spacing_minutes": (15, 1440), "retry_window_minutes": (1, 180)},
            "bot": {"rate_per_minute": (1, 60), "rate_per_day": (1, 1000), "retry_window_minutes": (1, 180)},
            "bad_offers": {"retry_window_minutes": (1, 180), "min_observations": (3, 1000), "min_history_hours": (24, 8760),
                "freshness_minutes": (1, 30), "minimum_confidence": (.7, 1)},
        }
        for section, rules in bounds.items():
            for key, (low, high) in rules.items():
                value = self.values.get(section, {}).get(key)
                if value is not None and (isinstance(value, bool) or not low <= float(value) <= high):
                    raise ValueError(f"{section}.{key}: informe um valor entre {low} e {high}")
        for key in ("discount_anchor", "sold_anchor", "commission_anchor", "coupon_anchor"):
            value = self.values.get("scoring", {}).get(key)
            if value is not None and float(value) <= 0:
                raise ValueError(f"{key} deve ser maior que zero")
        weekly_time = self.values.get("channel_experience", {}).get("weekly_time")
        if weekly_time is not None:
            import re
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", str(weekly_time)):
                raise ValueError("Horário semanal inválido")
        for key in ("discovery_minutes", "specialized_minutes", "publish_minutes"):
            value = self.values.get("cadence", {}).get(key)
            if value is not None and (not isinstance(value, int) or not 1 <= value <= 1440):
                raise ValueError(f"{key}: informe minutos inteiros entre 1 e 1440")
        for section, key in (("scoring", "minimum_score"), ("bad_offers", "maximum_score")):
            value = self.values.get(section, {}).get(key)
            if value is not None and not 0 <= float(value) <= 100:
                raise ValueError(f"{key}: informe um valor entre 0 e 100")
        allowed = {
            "scoring", "cadence", "content", "bot", "bad_offers",
            "channel_experience", "discovery", "editorial", "campaigns",
            "deduplication", "retention",
        }
        unknown = set(self.values) - allowed
        if unknown:
            raise ValueError(f"Configurações desconhecidas: {', '.join(sorted(unknown))}")
        scoring = self.values.get("scoring", {})
        weights = scoring.get("weights")
        if weights is not None:
            if not isinstance(weights, dict) or not weights:
                raise ValueError("Os pesos de score devem ser informados")
            if any(not math.isfinite(float(value)) or float(value) < 0 for value in weights.values()) or sum(
                float(value) for value in weights.values()
            ) <= 0:
                raise ValueError("Os pesos devem ser positivos e ter soma maior que zero")
        minimum_price, maximum_price = scoring.get("minimum_price"), scoring.get("maximum_price")
        if minimum_price is not None and maximum_price is not None and float(minimum_price) > float(maximum_price):
            raise ValueError("O preço mínimo não pode ser maior que o máximo")
        content = self.values.get("content", {})
        if int(content.get("emoji_min", 0)) > int(content.get("emoji_max", 99)):
            raise ValueError("A quantidade mínima de emojis não pode superar a máxima")
        for key in ("bot", "bad_offers"):
            for value in self.values.get(key, {}).get("times", self.values.get(key, {}).get("promo_times", [])):
                try:
                    hour, minute = str(value).split(":", 1)
                    if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                        raise ValueError
                except (TypeError, ValueError):
                    raise ValueError(f"Horário inválido em {key}: {value}") from None
        return self


class DashboardStatus(BaseModel):
    environment: str
    delivery_mode: str
    affiliate_publish_enabled: bool
    channel_declared: bool
    groq_configured: bool
    telegram_configured: bool
    browser_state: str
    queue_counts: dict[str, int]
    product_count: int
    eligible_count: int = 0
    published_24h: int = 0
    coupon_coverage: int = 0
    failed_count: int = 0
    daily_limit: int = 30
    next_publication_at: datetime | None = None
    next_publication_reason: str = "WAITING_READY_OFFER"
    last_discovery_at: datetime | None = None
    last_coupon_sync_at: datetime | None = None
    last_publication_at: datetime | None = None
    public_channel_configured: bool = False


class ActionResponse(BaseModel):
    accepted: bool
    task_id: str | None = None
    detail: str


class DiscoveryRequest(BaseModel):
    strategy: str | None = None


class QueueReorderRequest(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class QueueBulkRequest(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    action: str
