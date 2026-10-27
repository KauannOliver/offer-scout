from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    app_env: str = "development"
    app_secret_key: str
    admin_username: str = "admin"
    admin_password: str
    database_url: str = "postgresql+asyncpg://affiliate:affiliate@localhost:5432/affiliate"
    redis_url: str = "redis://localhost:6379/0"
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-20b"
    telegram_bot_token: str | None = None
    telegram_admin_chat_id: int | None = None
    telegram_public_channel_id: str | None = None
    telegram_delivery_mode: str = "private_smoke"
    telegram_affiliate_publish_enabled: bool = False
    telegram_channel_declared: bool = False
    meli_affiliate_tag: str = "telegram"
    meli_hub_url: str = "https://www.mercadolivre.com.br/afiliados/hub"
    meli_link_builder_url: str = "https://www.mercadolivre.com.br/afiliados/linkbuilder#hub"
    meli_coupons_url: str = "https://www.mercadolivre.com.br/afiliados/coupons#hub"
    meli_marketplace_coupons_url: str = "https://www.mercadolivre.com.br/cupons?source_page=mperfil#nav-header"
    discovery_max_items: int = 400
    discovery_specialized_max_items: int = 250
    discovery_max_scrolls: int = 20
    discovery_timeout_seconds: int = 45
    discovery_stable_rounds: int = 3
    browser_profile_path: Path = Path("/data/browser-profile")
    browser_cdp_url: str = "http://127.0.0.1:9222"
    artifacts_dir: Path = Path("/data/artifacts")
    cors_origins: str = "http://localhost:3080"

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
